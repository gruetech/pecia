//! The query index (v3.2) through the built binary: it may make a query
//! faster, and it may never make one answer differently — not while it is
//! current, and not when the log changed under it or it was damaged.

use std::path::{Path, PathBuf};
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_pecia")
}

/// The commands that print JSON on `--json` (v3.5). These tests read JSON,
/// as scripts do, so every such command is asked for it.
const JSON_COMMANDS: &[&str] = &["init", "add", "edit", "close", "check", "ready", "blocked", "graph", "show", "next", "audit", "doctor", "publish", "sync", "snapshot", "migrate"];

fn run(dir: &Path, argv: &[&str]) -> (String, String, i32) {
    let mut argv = argv.to_vec();
    if argv.first().is_some_and(|c| JSON_COMMANDS.contains(c)) && !argv.contains(&"--json") {
        argv.push("--json");
    }
    let o = Command::new(bin()).args(&argv).current_dir(dir).output().expect("run");
    (String::from_utf8_lossy(&o.stdout).into(), String::from_utf8_lossy(&o.stderr).into(), o.status.code().unwrap_or(-1))
}

fn repo(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("pecia-index-it-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");
    assert!(Command::new("git").args(["init", "-q"]).current_dir(&dir).status().expect("git").success());
    let dir = dir.canonicalize().expect("dir");
    assert_eq!(run(&dir, &["init"]).2, 0);
    let mut ids = Vec::new();
    for (i, kind) in ["task", "task", "milestone", "question", "task"].iter().enumerate() {
        let title = format!("record {i}");
        let (out, _, code) = run(&dir, &["add", "--type", kind, "--title", &title, "--owner", "t", "--target", "2099-01-01"]);
        assert_eq!(code, 0, "{out}");
        ids.push(out.split("\"id\": \"").nth(1).expect("id").split('"').next().expect("id").to_string());
    }
    assert_eq!(run(&dir, &["edit", &ids[1], "--blocks", &ids[0]]).2, 0);
    assert_eq!(run(&dir, &["close", &ids[4], "--disposition", "closed so the index holds a terminal head"]).2, 0);
    dir
}

const QUERIES: &[&[&str]] = &[&["next"], &["ready"], &["blocked"], &["graph"], &["graph", "--format", "mermaid"], &["gantt", "--no-color"], &["gantt", "--mermaid"]];

fn index_of(dir: &Path) -> PathBuf {
    dir.join(".git/pecia/log.index")
}

/// Where the sections begin; and where the log's identity ends, after which
/// an index is a function of the log alone.
const HEADER: usize = 200;
const IDENTITIES_END: usize = 8 + 56;

/// The files a store holds after a write: the log and its high-water mark,
/// and the projection and its witness, which a write leaves alone (v3.3).
fn store_files(dir: &Path) -> Vec<Vec<u8>> {
    [".git/pecia/log.jsonl", ".git/pecia/log.mark", ".pecia/work.jsonl", ".pecia/snapshot.head"].iter().map(|f| std::fs::read(dir.join(f)).expect(f)).collect()
}

/// A copy of a store. Every file in it is a new file, so its index stands
/// for nothing there and its first write reads the log in full.
fn copy_of(dir: &Path, tag: &str) -> PathBuf {
    let to = dir.with_file_name(format!("pecia-index-it-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&to);
    assert!(Command::new("cp").arg("-R").arg(dir).arg(&to).status().expect("cp").success());
    to
}

/// An index as a function of the log: all of it after the file identities.
fn content(dir: &Path) -> Vec<u8> {
    std::fs::read(index_of(dir)).expect("index")[IDENTITIES_END..].to_vec()
}

#[test]
fn a_query_answers_the_same_with_and_without_the_index() {
    let dir = repo("same");
    for q in QUERIES {
        let _ = std::fs::remove_file(index_of(&dir));
        let cold = run(&dir, q);
        assert!(index_of(&dir).exists(), "{q:?}: a clean read leaves an index");
        let warm = run(&dir, q);
        assert_eq!(cold, warm, "{q:?}");
    }
    // A write re-records it, and the next query agrees with a full read.
    assert_eq!(run(&dir, &["add", "--type", "task", "--title", "after", "--owner", "t"]).2, 0);
    let warm = run(&dir, &["next"]);
    let _ = std::fs::remove_file(index_of(&dir));
    assert_eq!(warm, run(&dir, &["next"]));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_changed_log_is_verified_whatever_the_index_says() {
    let dir = repo("tamper");
    assert_eq!(run(&dir, &["next"]).2, 0);
    assert!(index_of(&dir).exists());
    // One byte, same size, in the middle of the chain.
    let log = dir.join(".git/pecia/log.jsonl");
    let mut bytes = std::fs::read(&log).expect("log");
    let at = bytes.iter().rposition(|b| *b == b'\n').expect("lines") / 2;
    let at = at + bytes[at..].iter().position(|b| b.is_ascii_lowercase()).expect("a letter");
    bytes[at] = if bytes[at] == b'x' { b'y' } else { b'x' };
    std::fs::write(&log, &bytes).expect("write");
    // Whether the edit broke a link, a literal or a key, the query refuses.
    let (_, err, code) = run(&dir, &["next"]);
    assert_eq!(code, 2, "the query refuses: {err}");
    let _ = std::fs::remove_dir_all(&dir);
}

/// Damage, not forgery: the section digests are unkeyed, so an index
/// rewritten with fresh digests is believed until the log changes. That
/// grants nothing `--force` does not, and `check` never reads the index.
#[test]
fn a_damaged_index_is_no_index() {
    let dir = repo("damaged");
    let path = index_of(&dir);
    let honest_next = run(&dir, &["next"]);
    let honest_graph = run(&dir, &["graph"]);
    let pristine = std::fs::read(&path).expect("index");
    // The header: magic 8, the log's identity 56, entries 8, head 32, live
    // count 8, then the live section's length; the sections follow it.
    let live_len = u64::from_le_bytes(pristine[112..120].try_into().expect("u64")) as usize;
    assert!(live_len > 0, "the fixture has live heads");
    // A live record altered in place: its digest no longer matches.
    let mut ix = pristine.clone();
    ix[HEADER + live_len - 2] ^= 0x01;
    std::fs::write(&path, &ix).expect("write");
    assert_eq!(run(&dir, &["next"]), honest_next);
    // A settled head filed one byte into its line: its section's digest fails.
    let mut ix = pristine.clone();
    let first_settled = HEADER + live_len;
    let at = first_settled + 4;
    let off = u64::from_le_bytes(ix[at..at + 8].try_into().expect("u64"));
    ix[at..at + 8].copy_from_slice(&(off + 1).to_le_bytes());
    std::fs::write(&path, &ix).expect("write");
    assert_eq!(run(&dir, &["graph"]), honest_graph);
    // And a truncated one.
    std::fs::write(&path, &pristine[..pristine.len() / 2]).expect("write");
    assert_eq!(run(&dir, &["graph"]), honest_graph);
    let _ = std::fs::remove_dir_all(&dir);
}

/// The ids `repo` minted, in order, from its log.
fn ids(dir: &Path) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for line in std::fs::read_to_string(dir.join(".git/pecia/log.jsonl")).expect("log").lines() {
        let id = line.split("\"id\":\"").nth(1).expect("id").split('"').next().expect("id").to_string();
        if !out.contains(&id) {
            out.push(id);
        }
    }
    out
}

#[test]
fn a_write_on_the_index_is_the_write_a_full_read_makes() {
    // The same store twice: in `laned` the index stands for the log and the
    // projection its last write left; in `full` nothing does, and every
    // write reads the log in full first (and then re-records the index).
    let laned = repo("laned");
    let full = copy_of(&laned, "full");
    let ids = ids(&laned);
    let writes: Vec<Vec<&str>> = vec![
        vec!["edit", &ids[0], "--title", "retitled", "--body", "prose the index never carries"],
        vec!["edit", &ids[2], "--blocks", &ids[3]],
        // A cycle: refused by the gate, and nothing is written.
        vec!["edit", &ids[0], "--blocks", &ids[1]],
        // Past the gate, branded.
        vec!["edit", &ids[0], "--blocks", &ids[1], "--force"],
        // Refused: too thin a disposition, and a reopening.
        vec!["close", &ids[3], "--disposition", "answered"],
        vec!["edit", &ids[4], "--status", "open", "--disposition", ""],
        vec!["edit", "pc-nowhere", "--title", "x"],
        vec!["close", &ids[2], "--status", "nonsense", "--disposition", "d"],
        vec!["edit", &ids[2], "--status", "in-progress"],
        // Refused while the forced cycle still blocks it; then, the cycle
        // repaired, a live head settles and moves between the sections.
        vec!["close", &ids[1], "--disposition", "closed because the work it blocked on has landed and was checked"],
        vec!["edit", &ids[0], "--blocks", "none"],
        vec!["close", &ids[1], "--disposition", "closed because the work it blocked on has landed and was checked"],
    ];
    let mut wrote = 0;
    for w in &writes {
        let _ = std::fs::remove_file(index_of(&full));
        let before = store_files(&laned);
        assert_eq!(run(&laned, w), run(&full, w), "{w:?}");
        let after = store_files(&laned);
        assert_eq!(after, store_files(&full), "{w:?}");
        if after != before {
            wrote += 1;
            assert_eq!(content(&laned), content(&full), "{w:?}: the index follows the write it made");
        }
    }
    assert_eq!(wrote, 6, "the writes the gate passes landed");
    let _ = std::fs::remove_dir_all(&laned);
    let _ = std::fs::remove_dir_all(&full);
}

#[test]
fn records_added_on_the_index_leave_the_store_a_full_read_certifies() {
    let dir = repo("adds");
    for i in 0..6 {
        let title = format!("added {i}");
        let (out, err, code) = run(&dir, &["add", "--type", "task", "--title", &title, "--owner", "t"]);
        assert_eq!(code, 0, "{out}{err}");
    }
    let (out, err, code) = run(&dir, &["check"]);
    assert_eq!(code, 0, "the chain links and the projection is the log's: {out}{err}");
    // The index the writes kept equals the one a full read builds.
    let kept = content(&dir);
    let _ = std::fs::remove_file(index_of(&dir));
    assert_eq!(run(&dir, &["graph"]).2, 0);
    assert_eq!(kept, content(&dir));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_log_that_ends_before_its_mark_refuses_every_write() {
    let dir = repo("marked");
    let ids = ids(&dir);
    let (log, mark) = (dir.join(".git/pecia/log.jsonl"), dir.join(".git/pecia/log.mark"));
    let refused = |dir: &Path| {
        let (out, err, code) = run(dir, &["edit", &ids[0], "--title", "past the loss"]);
        assert_eq!(code, 2, "{out}{err}");
        assert!(err.contains("E019"), "{err}");
        let (out, _, code) = run(dir, &["check"]);
        assert_eq!(code, 1, "{out}");
        assert!(out.contains("\"E019\""), "{out}");
    };
    // The mark rewritten to name another entry at its seq: the log and its
    // index are untouched, so only the mark says anything happened — the lane
    // declines, and the full read refuses.
    let pristine = (std::fs::read(&log).expect("log"), std::fs::read(&mark).expect("mark"));
    let seq = String::from_utf8(pristine.1.clone()).expect("mark").split(' ').next().expect("seq").to_string();
    std::fs::write(&mark, format!("{seq} {}\n", "a".repeat(64))).expect("mark");
    refused(&dir);
    std::fs::write(&mark, &pristine.1).expect("mark");
    // The log cut back by one entry.
    let text = String::from_utf8(pristine.0).expect("log");
    let kept: String = text.lines().take(text.lines().count() - 1).map(|l| format!("{l}\n")).collect();
    std::fs::write(&log, kept).expect("truncate");
    refused(&dir);
    // Removing the mark is accepting the loss.
    std::fs::remove_file(&mark).expect("mark");
    assert_eq!(run(&dir, &["edit", &ids[0], "--title", "after accepting"]).2, 0);
    assert_eq!(run(&dir, &["check"]).2, 0);
    let _ = std::fs::remove_dir_all(&dir);
}
