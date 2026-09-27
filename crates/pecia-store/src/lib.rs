//! Where a store lives, and reading its files. `pecia-core` does no IO; this is
//! the layer that does, and nothing here interprets a record.
//!
//! Git is reached by running the user's own `git`, never a linked
//! reimplementation: the answers that matter here — the work tree's top level,
//! the common dir a worktree shares, the configured remotes — must be the ones
//! the user's git gives, and a second implementation is a place for them to
//! disagree. (Split into its own crate when `doctor` needs git without a store.)

use std::path::{Path, PathBuf};
use std::process::Command;

pub mod git {
    use super::*;

    /// Run git in `cwd`; stripped stdout on success, None on any failure —
    /// including git being absent, which is a state, not a crash.
    pub fn out(cwd: &Path, args: &[&str]) -> Option<String> {
        let o = Command::new("git").args(args).current_dir(cwd).output().ok()?;
        if !o.status.success() {
            return None;
        }
        Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
    }

    /// Run git with optional stdin: (exit code, stdout stripped, stderr
    /// stripped). A git that cannot be run is exit 1 with the reason, as the
    /// reference reports it — a failure, never a crash.
    pub fn run(cwd: &Path, args: &[&str], stdin: Option<&[u8]>) -> (i32, String, String) {
        let raw = run_raw(cwd, args, stdin);
        (raw.0, String::from_utf8_lossy(&raw.1).trim().to_string(), String::from_utf8_lossy(&raw.2).trim().to_string())
    }

    /// As `run`, with stdout left as bytes (a blob is not necessarily text).
    pub fn run_raw(cwd: &Path, args: &[&str], stdin: Option<&[u8]>) -> (i32, Vec<u8>, Vec<u8>) {
        use std::io::Write;
        use std::process::Stdio;
        let child = Command::new("git")
            .args(args)
            .current_dir(cwd)
            .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn();
        let mut child = match child {
            Ok(c) => c,
            Err(e) => return (1, Vec::new(), e.to_string().into_bytes()),
        };
        if let (Some(data), Some(mut pipe)) = (stdin, child.stdin.take()) {
            let _ = pipe.write_all(data);
        }
        match child.wait_with_output() {
            Ok(o) => (o.status.code().unwrap_or(1), o.stdout, o.stderr),
            Err(e) => (1, Vec::new(), e.to_string().into_bytes()),
        }
    }

    /// Run git in `cwd` for its exit code alone; None if git could not run.
    pub fn code(cwd: &Path, args: &[&str]) -> Option<i32> {
        Command::new("git").args(args).current_dir(cwd).output().ok()?.status.code()
    }
}

/// A path git printed, made absolute against the directory it ran in.
fn absolute(ran_in: &Path, printed: &str) -> PathBuf {
    let p = PathBuf::from(printed);
    if p.is_absolute() {
        p
    } else {
        let j = ran_in.join(&p);
        j.canonicalize().unwrap_or(j)
    }
}

pub struct Store {
    /// The work tree's top level, or the cwd outside one. Resolved once, as the
    /// reference resolves it, so a command run from a subdirectory reads the
    /// same store as one run from the top.
    pub root: PathBuf,
    pinned: Option<PathBuf>,
    /// The git common dir, asked for once: a write needs it several times,
    /// and every ask was a git process.
    common: std::sync::OnceLock<Option<PathBuf>>,
}

impl Store {
    pub fn resolve() -> Result<Store, String> {
        let cwd = std::env::current_dir().map_err(|e| format!("cannot read the working directory: {e}"))?;
        // One git process answers both questions a store asks. Outside a work
        // tree `--show-toplevel` fails the whole call, and the store falls
        // back to the cwd and asks for the common dir when it needs it. A
        // relative common dir is relative to the directory git ran in, which
        // is the cwd here, as it was the root when the two were asked apart.
        let found = git::out(&cwd, &["rev-parse", "--show-toplevel", "--git-common-dir"]);
        let (top, common) = match found.as_deref().and_then(|o| o.split_once('\n')) {
            Some((top, common)) if !top.is_empty() && !common.is_empty() => (Some(PathBuf::from(top)), Some(absolute(&cwd, common))),
            _ => (None, None),
        };
        let root = top.unwrap_or_else(|| cwd.clone());
        let root = root.canonicalize().unwrap_or(root);
        let pinned = match std::env::var_os("PECIA_LOG_DIR").filter(|v| !v.is_empty()) {
            None => None,
            Some(v) => {
                let d = PathBuf::from(&v);
                let d = if d.is_absolute() { d } else { root.join(d) };
                if d.exists() && !d.is_dir() {
                    return Err(format!(
                        "PECIA_LOG_DIR={} exists and is not a directory",
                        pecia_core::text::render_str(&v.to_string_lossy())
                    ));
                }
                Some(d)
            }
        };
        let store = Store::at(root);
        if let Some(c) = common {
            let _ = store.common.set(Some(c));
        }
        Ok(Store { pinned, ..store })
    }

    /// A store at a known root, nothing pinned — what `resolve` finds when
    /// run from inside `root` with PECIA_LOG_DIR unset.
    pub fn at(root: PathBuf) -> Store {
        Store { root, pinned: None, common: std::sync::OnceLock::new() }
    }

    pub fn git(&self, args: &[&str]) -> Option<String> {
        git::out(&self.root, args)
    }

    fn common_dir(&self) -> Option<PathBuf> {
        self.common
            .get_or_init(|| {
                let out = self.git(&["rev-parse", "--git-common-dir"]).filter(|s| !s.is_empty())?;
                Some(absolute(&self.root, &out))
            })
            .clone()
    }

    /// The log's directory: an explicitly pinned store, else the git common
    /// dir — shared by every worktree, which is what makes the timeline single.
    pub fn log_dir(&self) -> Option<PathBuf> {
        self.pinned.clone().or_else(|| self.common_dir().map(|c| c.join("pecia")))
    }

    pub fn log_path(&self) -> Option<PathBuf> {
        self.log_dir().map(|d| d.join("log.jsonl"))
    }

    /// The projection follows the store: beside a pinned log, else `.pecia/`.
    pub fn snapshot_dir(&self) -> PathBuf {
        self.pinned.clone().unwrap_or_else(|| self.root.join(".pecia"))
    }

    pub fn snapshot_path(&self) -> PathBuf {
        self.snapshot_dir().join("work.jsonl")
    }

    pub fn snapshot_head_path(&self) -> PathBuf {
        self.snapshot_dir().join("snapshot.head")
    }

    pub fn config_path(&self) -> PathBuf {
        self.root.join(".pecia").join("config.yaml")
    }

    /// A store file as a message names it: relative to the root when it is
    /// under it, else as it is. Lexical, as the reference computes it.
    pub fn display(&self, p: &Path) -> String {
        p.strip_prefix(&self.root).map(|r| r.display().to_string()).unwrap_or_else(|_| p.display().to_string())
    }
}

/// The store's lock, held for a read-validate-append sequence so two writers
/// in one clone serialize. Beside the log, so every worktree of a clone takes
/// the same lock.
pub struct Lock {
    _file: std::fs::File,
}

impl Store {
    pub fn lock_path(&self) -> PathBuf {
        self.log_dir().map(|d| d.join(".lock")).unwrap_or_else(|| self.root.join(".pecia").join(".lock"))
    }

    pub fn lock(&self) -> Result<Lock, String> {
        let p = self.lock_path();
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
        }
        let f = std::fs::File::create(&p).map_err(|e| format!("cannot open the lock {}: {e}", p.display()))?;
        f.lock().map_err(|e| format!("cannot take the lock {}: {e}", p.display()))?;
        Ok(Lock { _file: f })
    }

    /// The projection and its witness are written TOGETHER: both staged beside
    /// their destinations, then both renamed, so neither is ever seen without
    /// the other. Returns the committer, or the refusal if staging failed —
    /// in which case nothing was written.
    pub fn stage_projection(&self, body: &str, head: &str) -> Result<Staged, String> {
        self.stage(|tmp| std::fs::write(tmp, body), head)
    }

    /// The log's high-water mark (v3.3), beside the log: in the git common
    /// dir for the default store, never tracked; beside a pinned store's log.
    pub fn mark_path(&self) -> Option<PathBuf> {
        self.log_dir().map(|d| d.join("log.mark"))
    }

    /// Stage the high-water mark beside the log, before the log moves: a
    /// directory it cannot be written to refuses while nothing has.
    pub fn stage_mark(&self, text: &str) -> Result<Staged, String> {
        let dest = self.mark_path().ok_or("not inside a git repository")?;
        let tmp = dest.with_file_name(format!(".log.mark.staged.{}", std::process::id()));
        let mut staged = Staged { files: Vec::new() };
        staged.files.push((tmp.clone(), dest));
        std::fs::write(&tmp, text).map_err(|e| format!("the log's high-water mark cannot be written: {e} — nothing was written, and the store is byte-identical to before"))?;
        Ok(staged)
    }

    /// The projection and its witness, staged beside their destinations.
    fn stage(&self, body: impl FnOnce(&Path) -> std::io::Result<()>, head: &str) -> Result<Staged, String> {
        let pid = std::process::id();
        let mut staged = Staged { files: Vec::new() };
        let fail = |e: std::io::Error| format!(
            "the projection cannot be written: {e} — {} and {} are derived from the log and are written together; nothing was written, and the store is byte-identical to before (repair the permissions and run the command again)",
            self.snapshot_path().display(), self.snapshot_head_path().display()
        );
        std::fs::create_dir_all(self.snapshot_dir()).map_err(fail)?;
        let staging = |dest: &Path| {
            let name = dest.file_name().expect("file").to_string_lossy().to_string();
            dest.with_file_name(format!(".{name}.staged.{pid}"))
        };
        let (body_dest, head_dest) = (self.snapshot_path(), self.snapshot_head_path());
        let (body_tmp, head_tmp) = (staging(&body_dest), staging(&head_dest));
        staged.files.push((body_tmp.clone(), body_dest));
        body(&body_tmp).map_err(fail)?;
        staged.files.push((head_tmp.clone(), head_dest));
        std::fs::write(&head_tmp, head).map_err(fail)?;
        Ok(staged)
    }
}

/// DURABILITY (pc-26a08d9c5d46). A write pecia reports as done must survive
/// a power loss, not only this process dying: the page cache outlives the
/// second and not the first, so neither a kill test nor an exit code can see
/// the difference. `sync_data` reaches the device (on macOS std issues
/// F_FULLFSYNC), and a directory is synced after a rename or a creation so
/// the new name is durable too. The derived index is exempt: it is rebuilt
/// from the log whenever it does not match it.
pub fn sync_file(file: &std::fs::File, what: &Path) -> Result<(), String> {
    file.sync_data().map_err(|e| format!("{} could not be made durable: {e}", what.display()))
}

/// Sync a file or a directory by its path.
pub fn sync_path(path: &Path) -> Result<(), String> {
    let f = std::fs::File::open(path).map_err(|e| format!("{} could not be made durable: {e}", path.display()))?;
    sync_file(&f, path)
}

/// Files written beside their destinations, renamed into place together by
/// `commit` and removed if they never are.
pub struct Staged {
    files: Vec<(PathBuf, PathBuf)>,
}

impl Staged {
    /// Each staged file is synced BEFORE any rename, so a sync that fails
    /// leaves every destination as it was; then the renames; then each
    /// destination directory, so the new names survive a power loss.
    pub fn commit(mut self) -> Result<(), String> {
        for (tmp, _) in &self.files {
            sync_path(tmp)?;
        }
        let mut dirs: Vec<PathBuf> = Vec::new();
        for (tmp, dest) in std::mem::take(&mut self.files) {
            std::fs::rename(&tmp, &dest).map_err(|e| format!("cannot write {}: {e}", dest.display()))?;
            if let Some(dir) = dest.parent().filter(|d| !dirs.iter().any(|x| x == d)) {
                dirs.push(dir.to_path_buf());
            }
        }
        for dir in dirs {
            sync_path(&dir).map_err(|e| format!("the write landed, but {e}"))?;
        }
        Ok(())
    }
}

impl Drop for Staged {
    fn drop(&mut self) {
        for (tmp, _) in &self.files {
            let _ = std::fs::remove_file(tmp);
        }
    }
}

/// A file's bytes, or None when it does not exist. Any other read failure is
/// an error the caller reports; absence is a state the format defines.
pub fn read_optional(p: &Path) -> Result<Option<Vec<u8>>, String> {
    match std::fs::read(p) {
        Ok(b) => Ok(Some(b)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("cannot read {}: {e}", p.display())),
    }
}

/// The identity of an open file, as the query index records it (v3.2).
pub fn identity(file: &std::fs::File) -> std::io::Result<pecia_core::index::Identity> {
    use std::os::unix::fs::MetadataExt;
    let m = file.metadata()?;
    Ok(pecia_core::index::Identity {
        dev: m.dev(),
        ino: m.ino(),
        size: m.size(),
        mtime: (m.mtime(), m.mtime_nsec()),
        ctime: (m.ctime(), m.ctime_nsec()),
    })
}

/// A file's bytes and — only if it held still while they were read — its
/// identity, or None when it does not exist.
pub fn read_noting_identity(p: &Path) -> Result<Option<(Vec<u8>, Option<pecia_core::index::Identity>)>, String> {
    use std::io::Read;
    let fail = |e: std::io::Error| format!("cannot read {}: {e}", p.display());
    let mut file = match std::fs::File::open(p) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(fail(e)),
    };
    let before = identity(&file).ok();
    let mut bytes = Vec::with_capacity(before.map_or(0, |i| i.size as usize));
    file.read_to_end(&mut bytes).map_err(fail)?;
    let after = identity(&file).ok();
    let still = before.filter(|b| Some(*b) == after && b.size == bytes.len() as u64);
    Ok(Some((bytes, still)))
}

/// Lines of an open file at the given (offset, length) spans, each read where
/// it sits. Any short read — the file shrank — is an error, never a partial line.
pub fn read_spans(file: &std::fs::File, spans: &[(u64, u32)]) -> std::io::Result<Vec<Vec<u8>>> {
    use std::os::unix::fs::FileExt;
    pecia_core::par::map(spans.len(), |i| {
        let (offset, len) = spans[i];
        let mut buf = vec![0u8; len as usize];
        file.read_exact_at(&mut buf, offset).map(|_| buf)
    })
    .into_iter()
    .collect()
}

impl Store {
    /// The query index, beside the log it describes.
    pub fn index_path(&self) -> Option<PathBuf> {
        self.log_dir().map(|d| d.join("log.index"))
    }

    /// Replace the index with `bytes`, whole or not at all. Best effort: the
    /// index is derived, and a store it cannot be written to only reads the
    /// log in full, as it always did.
    pub fn write_index(&self, bytes: &[u8]) {
        let Some(path) = self.index_path() else { return };
        let tmp = path.with_file_name(format!(".log.index.staged.{}", std::process::id()));
        if std::fs::write(&tmp, bytes).is_err() || std::fs::rename(&tmp, &path).is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
    }
}
