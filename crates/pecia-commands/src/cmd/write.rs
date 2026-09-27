//! `add`, `edit`, `close`: the write path. Every write takes the store's lock,
//! re-reads the log under it, passes the write gate (or is branded forced),
//! and appends one entry whose `touched` is derived, never authored.

use crate::args::Parsed;
use crate::cmd::query::write_index;
use crate::cmd::store_cmds::{load_entries, mark_file};
use crate::ctx::{finding_value, Ctx};
use crate::sys;
use pecia_core::canonical::{canonical, canonical_object};
use pecia_core::check::{mark_text, mark_violation, records_of, MARK_REMEDY};
use pecia_core::config::{py_strip, Config};
use pecia_core::finding::{error, Code, Finding};
use pecia_core::index;
use pecia_core::record::{as_obj, get, get_str, record_is_sound, strict_int, Pairs, LIST_EDGES, SCALAR_EDGES};
use pecia_core::write::{
    cas, edges_mut, is_terminal, merge_retires, project, refusal_message, remove, require_heads, set, strs,
    write_gate, write_gate_local, Chain,
};
use pecia_core::Value;
use pecia_store::read_optional;
use std::collections::HashSet;

/// Hex characters an id is minted with (pc-1c65): wide enough that two clones
/// minting before either publishes collide at ~1.8e-11 in a 100-id window.
pub const MINT_HEX: usize = 12;

fn config(ctx: &Ctx) -> Result<Config, String> {
    Ok(read_optional(&ctx.store.config_path())?.map(|b| Config::parse(&String::from_utf8_lossy(&b))).unwrap_or_default())
}

fn mint_id(kind: &str, title: &str, created: &str, existing: &HashSet<&str>) -> Result<String, String> {
    let payload = format!("{kind}|{title}|{created}|{}", sys::nonce_hex()?);
    let digest = pecia_core::sha256_hex(payload.as_bytes());
    mint_from(&digest, existing)
}

/// The shortest prefix of `digest`, at least MINT_HEX wide, that no known id
/// already holds.
fn mint_from(digest: &str, existing: &HashSet<&str>) -> Result<String, String> {
    for len in MINT_HEX..=digest.len() {
        let candidate = format!("pc-{}", &digest[..len]);
        if !existing.contains(candidate.as_str()) {
            return Ok(candidate);
        }
    }
    Err("id space exhausted".into())
}

/// The commit this revision was written against, and whether the worktree
/// carried uncommitted tracked changes — stamped by the substrate, never
/// authored (v2.5).
///
/// One git process answers both: porcelain v2's `# branch.oid` header is the
/// commit (`(initial)` when there is none), and any line after the headers
/// is an uncommitted tracked change.
fn provenance_anchor(ctx: &Ctx, rec: &mut Vec<(String, Value)>) {
    let Some(status) = ctx.store.git(&["status", "--porcelain=v2", "--branch", "--untracked-files=no"]) else { return };
    let Some(head) = status.lines().find_map(|l| l.strip_prefix("# branch.oid ")) else { return };
    if head.len() != 40 || !head.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return;
    }
    set(rec, "anchor", Value::Str(head.to_string()));
    if status.lines().any(|l| !l.starts_with('#')) {
        set(rec, "anchor_dirty", Value::Bool(true));
    }
}

/// Append one revision to the timeline the command read under the store's
/// lock: the per-id CAS, the log's high-water mark, then the entry. `read` is
/// what that read found — a timeline is extended only as far as it reads
/// whole. `certified` says the timeline is one a query accepts
/// (`require_heads`), so that the index can follow the append.
///
/// The projection is not the write's (v3.3): `snapshot` regenerates it, so a
/// write can no longer erase the records it witnesses, and the witness of
/// this store's own appends between snapshots is the mark each one moves.
fn append_record(ctx: &Ctx, chain: &mut Chain, read: &[Finding], rec: Value, certified: bool) -> Result<(), String> {
    let log = ctx.store.log_path().ok_or("not inside a git repository — cannot write the timeline")?;
    if let Some(f) = read.first() {
        return Err(f.message.clone());
    }
    chain.admit(as_obj(&rec).expect("record")).map_err(|w| format!("compare-and-swap refused: {w}"))?;
    let sound = record_is_sound(&rec);
    let entry = chain.next_entry(rec);
    let (mark, shown) = mark_file(ctx)?;
    if let Some(gone) = mark_violation(chain.entries(), mark.as_deref(), &shown) {
        return Err(format!("write refused: {gone} (E019). {MARK_REMEDY}"));
    }
    if let Some(parent) = log.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let line = canonical(&entry) + "\n";
    chain.push(entry);
    // Staged before the append, so a mark that cannot be written refuses
    // with the log untouched, and committed straight after it.
    let staged = ctx
        .store
        .stage_mark(&mark_text(chain.entries()).expect("an entry was just pushed"))
        .map_err(|p| format!("write refused: {p}"))?;
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&log).map_err(|e| e.to_string())?;
    std::io::Write::write_all(&mut f, line.as_bytes()).map_err(|e| e.to_string())?;
    // The log is durable before the mark moves to it (pc-26a08d9c5d46); the
    // mark's commit then syncs the directory both live in.
    pecia_store::sync_file(&f, &log).map_err(|e| format!("the entry was appended, but {e}"))?;
    staged.commit()?;
    // The index follows pecia's own append — only if the file is exactly
    // what this write made it, so nothing else's change is recorded as ours.
    // A certified timeline with one sound revision appended is certified.
    if let (Ok(identity), Some(len)) = (pecia_store::identity(&f), chain.log().byte_len()) {
        if identity.size == len && certified && sound {
            write_index(ctx, chain.log(), &identity);
        }
    }
    Ok(())
}

/// The heads a certified timeline gates against, as the local gate takes them.
fn as_heads<'a>(heads: &'a [(String, &'a Pairs)]) -> Vec<(&'a str, &'a Pairs)> {
    heads.iter().map(|(k, h)| (k.as_str(), *h)).collect()
}

/// A write's fast lane (v3.2): the heads as the query index files them,
/// standing for the log, the projection and its witness as they are now.
/// A write of one revision reads the heads' query views and — for a revision
/// — its own head's line, and nothing else; the gate is the local one, which
/// equals the full gate over a timeline the index certifies.
struct Lane<'a> {
    log: &'a std::fs::File,
    stamp: index::Stamp,
    filed: Vec<index::Head<'a>>,
    heads: Vec<(&'a str, &'a Pairs)>,
}

/// Run `f` on the fast lane, or None — before anything is written or shown —
/// when the index cannot stand in for reading the log: it is absent,
/// damaged, or stale against the log; or the log's high-water mark does not
/// name the last entry the index records, which a full read then judges.
/// `f` itself declines with None the same way.
fn on_lane<R>(ctx: &Ctx, f: impl FnOnce(&Lane<'_>) -> Option<R>) -> Option<R> {
    let log = std::fs::OpenOptions::new().read(true).append(true).open(ctx.store.log_path()?).ok()?;
    let identity = pecia_store::identity(&log).ok()?;
    let bytes = std::fs::read(ctx.store.index_path()?).ok()?;
    let stamp = index::header(&bytes)?.stamp;
    let mark = std::fs::read(ctx.store.mark_path()?).ok()?;
    let names_the_head = py_strip(&String::from_utf8_lossy(&mark)) == format!("{} {}", stamp.entries, pecia_core::hex(&stamp.head));
    if stamp.log != identity || !names_the_head {
        return None;
    }
    let (_, filed) = index::decode(&bytes)?;
    // Freed in the background, as a timeline's entries are.
    let views: pecia_core::check::Entries = index::views(&filed.iter().collect::<Vec<_>>())?.into();
    let heads = filed.iter().zip(views.iter()).map(|(h, v)| (h.id, as_obj(v).expect("a view is a record"))).collect();
    f(&Lane { log: &log, stamp, filed, heads })
}

impl Lane<'_> {
    /// The head filed at `i`, whole, read from its line in the log — or None
    /// unless that line holds exactly the view the index files it under.
    fn head(&self, i: usize) -> Option<Value> {
        crate::cmd::query::head_line(self.log, &self.filed[i])
    }

    /// Gate `rec` — the next revision of the head filed at `x`, or a new
    /// record — and append it: `gate_or_brand` and `append_record`, on the lane.
    fn write(&self, ctx: &Ctx, args: &Parsed, x: Option<(usize, &Pairs)>, mut rec: Value, cfg: &Config) -> Result<u8, String> {
        let head = x.map(|(_, h)| h);
        if let Some(h) = head.filter(|h| unchanged(args, h, &rec)) {
            ctx.emit(&unchanged_echo(h, cfg));
            return Ok(0);
        }
        let records = usize::try_from(self.stamp.entries).map_err(|e| e.to_string())?;
        if let Gate::Refused(code) = gate_or_brand(ctx, args, &mut rec, |r| write_gate_local(&self.heads, head, r, records, cfg)) {
            return Ok(code);
        }
        if args.tainted {
            return Ok(ctx.cannot_run("an argument is not valid UTF-8, so the record has no canonical bytes to write (v3.0, pc-ddd9)"));
        }
        let written = project("written", as_obj(&rec).expect("record"), cfg);
        self.append(ctx, x, rec)?;
        ctx.emit(&written);
        Ok(0)
    }

    /// The per-id CAS, then the entry, linked to the head the index records;
    /// the mark moved to it; then the index, with the one head this changed.
    fn append(&self, ctx: &Ctx, x: Option<(usize, &Pairs)>, rec: Value) -> Result<(), String> {
        let head = x.map(|(_, h)| h);
        let r = as_obj(&rec).expect("record");
        cas(head, r).map_err(|w| format!("compare-and-swap refused: {w}"))?;
        // What the index files this head as, if it can file it at all: a
        // sound record, as a full read would require.
        let filed = record_is_sound(&rec).then(|| {
            let text = |k| get_str(r, k).expect("sound").to_string();
            (text("id"), text("status"), text("type"), canonical_object(&index::query_view(r)))
        });
        let seq = usize::try_from(self.stamp.entries + 1).map_err(|e| e.to_string())?;
        let entry = pecia_core::write::entry(seq, Some(pecia_core::hex(&self.stamp.head)), head, rec);
        let line = canonical(&entry) + "\n";
        let digest = pecia_core::entry_digest(&entry);
        let staged = ctx
            .store
            .stage_mark(&format!("{seq} {}\n", pecia_core::hex(&digest)))
            .map_err(|p| format!("write refused: {p}"))?;
        std::io::Write::write_all(&mut &*self.log, line.as_bytes()).map_err(|e| e.to_string())?;
        // Durable before the mark moves to it (pc-26a08d9c5d46).
        let path = ctx.store.log_path().unwrap_or_default();
        pecia_store::sync_file(&self.log, &path).map_err(|e| format!("the entry was appended, but {e}"))?;
        staged.commit()?;
        // As on a full read: the index follows only if the log is exactly
        // what this append made it.
        let (Ok(after), Some((id, status, kind, view))) = (pecia_store::identity(self.log), &filed) else {
            return Ok(());
        };
        let (offset, len) = (self.stamp.log.size, line.len() as u64 - 1);
        if after.size != offset + len + 1 {
            return Ok(());
        }
        let Ok(len) = u32::try_from(len) else { return Ok(()) };
        let mut heads = self.filed.clone();
        let order = match x {
            Some((i, _)) => heads[i].order,
            None => u32::try_from(heads.len()).map_err(|e| e.to_string())?,
        };
        let now = index::Head { order, id, status, kind, offset, len, view };
        match x {
            Some((i, _)) => heads[i] = now,
            None => heads.push(now),
        }
        let stamp = index::Stamp { log: after, entries: self.stamp.entries + 1, head: digest };
        if let Some(bytes) = index::encode_heads(&stamp, &heads) {
            ctx.store.write_index(&bytes);
        }
        Ok(())
    }
}

/// Whether this revision changes nothing, and so is not written (v3.8,
/// pc-9e70b7933815): its derived touched set is empty. sync already skips
/// such a revision (claim 35), but only in the unpublished suffix. A forced
/// one is still written, because its brand records a use of the escape hatch
/// (pc-dacc). An argument that is not valid UTF-8 is left to the write gate,
/// which refuses it: the reference sees that value as a change.
fn unchanged(args: &Parsed, head: &Pairs, rec: &Value) -> bool {
    !args.flag("force") && !args.tainted && as_obj(rec).is_some_and(|r| pecia_core::check::diff_fields(Some(head), r).is_empty())
}

/// What a write that changed nothing reports: the record as it stands.
fn unchanged_echo(head: &Pairs, cfg: &Config) -> Value {
    let mut echo = project("written", head, cfg);
    if let Value::Object(pairs) = &mut echo {
        pairs.push(("unchanged".to_string(), Value::Bool(true)));
    }
    echo
}

enum Gate {
    Pass,
    Refused(u8),
}

/// `--force` brands the candidate and passes; otherwise the gate decides, and
/// each refusal is printed as the finding it is.
fn gate_or_brand(ctx: &Ctx, args: &Parsed, rec: &mut Value, gate: impl FnOnce(&Value) -> Vec<Finding>) -> Gate {
    if args.flag("force") {
        set(pecia_core::write::pairs_mut(rec), "forced", Value::Bool(true));
        return Gate::Pass;
    }
    if args.tainted {
        // The reference's gate refuses this as E001 because the value is a
        // lone surrogate; the Rust value cannot hold one, so the refusal is
        // produced here, in the same words.
        let obj = as_obj(rec).expect("record");
        let rev = strict_int(get(obj, "rev")).unwrap_or(1);
        let msg = format!("a string carries a lone surrogate — not valid Unicode, so UTF-8 cannot carry it and the canonical form has no bytes for it (v3.0, pc-ddd9) [rev {rev}]");
        ctx.emit(&finding_value(&error(Code::E001, get_str(obj, "id"), &refusal_message(&msg))));
        return Gate::Refused(1);
    }
    let refusals = gate(rec);
    if refusals.is_empty() {
        return Gate::Pass;
    }
    for r in &refusals {
        ctx.emit(&finding_value(r));
    }
    Gate::Refused(1)
}

pub fn add(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let _lock = ctx.store.lock()?;
    let laned = on_lane(ctx, |lane| {
        Some((|| {
            let existing: HashSet<&str> = lane.filed.iter().map(|h| h.id).collect();
            let cfg = config(ctx)?;
            let rec = new_record(ctx, args, &existing)?;
            lane.write(ctx, args, None, rec, &cfg)
        })())
    });
    if let Some(done) = laned {
        return done;
    }
    let (entries, findings) = load_entries(ctx)?;
    for f in &findings {
        if f.code == Code::E000 || f.code == Code::E013 {
            return Ok(ctx.cannot_run(&f.message));
        }
    }
    let records = records_of(&entries);
    let cfg = config(ctx)?;
    let existing: HashSet<&str> = records.iter().filter_map(|r| r.get("id").and_then(Value::as_str)).collect();
    let mut rec = new_record(ctx, args, &existing)?;
    // Over a certified timeline the local gate is the full one (tests/gate.rs).
    let certified = require_heads(&records, &findings).ok();
    let gate = |r: &Value| match &certified {
        Some(heads) => write_gate_local(&as_heads(heads), None, r, records.len(), &cfg),
        None => write_gate(&records, r, &cfg),
    };
    if let Gate::Refused(code) = gate_or_brand(ctx, args, &mut rec, gate) {
        return Ok(code);
    }
    if args.tainted {
        return Ok(ctx.cannot_run("an argument is not valid UTF-8, so the record has no canonical bytes to write (v3.0, pc-ddd9)"));
    }
    let written = project("written", as_obj(&rec).expect("record"), &cfg);
    let certified = certified.is_some() && findings.is_empty();
    drop(records);
    append_record(ctx, &mut Chain::new(entries), &findings, rec, certified)?;
    ctx.emit(&written);
    Ok(0)
}

/// Rev 1 of a new record, from the arguments, minted clear of `existing`.
fn new_record(ctx: &Ctx, args: &Parsed, existing: &HashSet<&str>) -> Result<Value, String> {
    let created = sys::today()?;
    let mut edges: Vec<(String, Value)> = LIST_EDGES.iter().map(|k| (k.to_string(), strs(&args.list(k)))).collect();
    for k in SCALAR_EDGES {
        edges.push((k.to_string(), args.str(k).map_or(Value::Null, |v| Value::Str(v.to_string()))));
    }
    let (kind, title) = (args.str("type").unwrap_or(""), args.str("title").unwrap_or(""));
    let mut rec: Vec<(String, Value)> = vec![
        ("id".into(), Value::Str(mint_id(kind, title, &created, existing)?)),
        ("rev".into(), Value::Int(1)),
        ("type".into(), Value::Str(kind.into())),
        ("title".into(), Value::Str(title.into())),
        ("status".into(), Value::Str("open".into())),
        ("priority".into(), Value::Int(args.int("priority").unwrap_or(2))),
        ("created".into(), Value::Str(created.clone())),
        ("updated".into(), Value::Str(created)),
        ("edges".into(), Value::Object(edges)),
        ("disposition".into(), Value::Null),
        ("evidence".into(), Value::Str("unknown".into())),
        ("owner".into(), Value::Str(args.str("owner").map(str::to_string).unwrap_or_else(sys::default_owner))),
        ("labels".into(), strs(&args.list("label"))),
        ("body".into(), Value::Str(args.str("body").unwrap_or("").into())),
    ];
    if let Some(t) = args.str("target").filter(|t| !t.is_empty()) {
        set(&mut rec, "target", Value::Str(t.into()));
    }
    if let Some(c) = args.str("context").filter(|c| !c.is_empty()) {
        set(&mut rec, "context", Value::Str(c.into()));
    }
    provenance_anchor(ctx, &mut rec);
    Ok(Value::Object(rec))
}

type Mutate<'a> = Box<dyn Fn(&mut Vec<(String, Value)>) + 'a>;

fn edit_mutate<'a>(args: &'a Parsed, today: String) -> Mutate<'a> {
    Box::new(move |rec| {
        for field in ["title", "status", "body", "owner", "evidence", "disposition", "target"] {
            if let Some(v) = args.str(field) {
                set(rec, field, Value::Str(v.into()));
            }
        }
        if let Some(p) = args.int("priority") {
            set(rec, "priority", Value::Int(p));
        }
        match args.str("context") {
            Some("none") => remove(rec, "context"),
            Some(c) => set(rec, "context", Value::Str(c.into())),
            None => {}
        }
        if args.flag("ratify") {
            set(rec, "ratified_by", Value::Str(sys::default_owner()));
            set(rec, "ratified", Value::Str(today.clone()));
        }
        let edges = edges_mut(rec);
        for k in LIST_EDGES {
            if args.has(k) {
                let v = args.list(k);
                set(edges, k, if v == ["none"] { Value::Array(Vec::new()) } else { strs(&v) });
            }
        }
        for k in SCALAR_EDGES {
            match args.str(k) {
                Some("none") => set(edges, k, Value::Null),
                Some(v) => set(edges, k, Value::Str(v.into())),
                None => {}
            }
        }
        if args.flag("no_edges") {
            set(edges, "no_edges", Value::Bool(true));
        }
        if args.has("label") {
            set(rec, "labels", strs(&args.list("label")));
        }
        merge_retires(rec, &args.list("also_closes"));
    })
}

fn close_mutate<'a>(args: &'a Parsed) -> Mutate<'a> {
    Box::new(move |rec| {
        set(rec, "status", Value::Str(args.str("status").unwrap_or("done").into()));
        set(rec, "disposition", Value::Str(args.str("disposition").unwrap_or("").into()));
        if let Some(e) = args.str("evidence") {
            set(rec, "evidence", Value::Str(e.into()));
        }
        merge_retires(rec, &args.list("also_closes"));
    })
}

/// A new revision of `head`: a deep copy with the per-revision stamps dropped
/// — the force brand and the anchor each name THIS revision, never inherited.
fn revision_of(ctx: &Ctx, head: &Pairs, mutate: &Mutate) -> Result<Value, String> {
    let mut rec: Vec<(String, Value)> = head.to_vec();
    for k in ["forced", "anchor", "anchor_dirty"] {
        remove(&mut rec, k);
    }
    mutate(&mut rec);
    let rev = strict_int(get(head, "rev")).unwrap_or(0) + 1;
    set(&mut rec, "rev", Value::Int(rev));
    set(&mut rec, "updated", Value::Str(sys::today()?));
    provenance_anchor(ctx, &mut rec);
    Ok(Value::Object(rec))
}

fn revise(ctx: &Ctx, args: &Parsed, mutate: Mutate) -> Result<u8, String> {
    let _lock = ctx.store.lock()?;
    let id = args.str("id").unwrap_or("");
    let laned = on_lane(ctx, |lane| {
        let Some(i) = lane.filed.iter().position(|h| h.id == id) else {
            return Some(Ok(ctx.cannot_run(&format!("record {id} not found"))));
        };
        let head = lane.head(i)?;
        let head = as_obj(&head)?;
        Some((|| {
            let cfg = config(ctx)?;
            lane.write(ctx, args, Some((i, head)), revision_of(ctx, head, &mutate)?, &cfg)
        })())
    });
    if let Some(done) = laned {
        return done;
    }
    let (entries, findings) = load_entries(ctx)?;
    let records = records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(msg) => return Ok(ctx.cannot_run(&msg)),
    };
    let Some(&(_, head)) = heads.iter().find(|(h, _)| h == id) else {
        return Ok(ctx.cannot_run(&format!("record {id} not found")));
    };
    let cfg = config(ctx)?;
    let mut rec = revision_of(ctx, head, &mutate)?;
    if unchanged(args, head, &rec) {
        ctx.emit(&unchanged_echo(head, &cfg));
        return Ok(0);
    }
    // The heads are certified, so the local gate is the full one.
    if let Gate::Refused(code) = gate_or_brand(ctx, args, &mut rec, |r| write_gate_local(&as_heads(&heads), Some(head), r, records.len(), &cfg)) {
        return Ok(code);
    }
    if args.tainted {
        return Ok(ctx.cannot_run("an argument is not valid UTF-8, so the record has no canonical bytes to write (v3.0, pc-ddd9)"));
    }
    let written = project("written", as_obj(&rec).expect("record"), &cfg);
    let certified = findings.is_empty();
    drop((heads, records));
    append_record(ctx, &mut Chain::new(entries), &findings, rec, certified)?;
    ctx.emit(&written);
    Ok(0)
}

/// `--also-closes`: the retirer and every non-terminal target, gated as one
/// plan — any refusal writes nothing — then appended targets first.
fn write_batch(ctx: &Ctx, args: &Parsed, retirer: Mutate) -> Result<u8, String> {
    let _lock = ctx.store.lock()?;
    let (entries, findings) = load_entries(ctx)?;
    let records = records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(msg) => return Ok(ctx.cannot_run(&msg)),
    };
    let head_of = |id: &str| heads.iter().find(|(h, _)| h == id).map(|&(_, p)| p);
    let rid = args.str("id").unwrap_or("");
    let Some(head) = head_of(rid) else {
        return Ok(ctx.cannot_run(&format!("record {rid} not found")));
    };
    let mut targets: Vec<String> = Vec::new();
    for t in args.list("also_closes") {
        if !targets.contains(&t) {
            targets.push(t);
        }
    }
    if targets.iter().any(|t| t == rid) {
        return Ok(ctx.cannot_run("--also-closes names the record being closed — a record cannot retire itself"));
    }
    let missing = targets.iter().filter(|t| head_of(t).is_none()).count();
    if missing > 0 {
        return Ok(ctx.cannot_run(&format!(
            "--also-closes names {missing} record(s) this ledger does not carry; --also-closes CLOSES its targets, so every one must exist. (`--retires` alone accepts a `planned:` id.)"
        )));
    }
    let final_status = args.str("status").map(str::to_string).or_else(|| get_str(head, "status").map(str::to_string));
    if !is_terminal(final_status.as_deref()) {
        return Ok(ctx.cannot_run("--also-closes needs the retiring record to end up terminal: use `close --also-closes`, or `edit --also-closes` on a record that is already terminal. To record the edge without closing anything, use --retires."));
    }
    let disposition = args.str("disposition").map(str::to_string).or_else(|| get_str(head, "disposition").map(str::to_string));
    let Some(disposition) = disposition.filter(|d| !py_strip(d).is_empty()) else {
        return Ok(ctx.cannot_run("--also-closes gives each retired record the retirer's own disposition, and this one has none — pass --disposition"));
    };
    let cfg = config(ctx)?;
    let final_status = final_status.expect("terminal");
    let evidence = args.str("evidence").map(str::to_string);
    let mut plan: Vec<(String, Mutate)> = Vec::new();
    for t in &targets {
        if is_terminal(head_of(t).and_then(|h| get_str(h, "status"))) {
            continue;
        }
        let (status, disp, ev, cfg2, retirer_id) = (final_status.clone(), disposition.clone(), evidence.clone(), cfg.clone(), rid.to_string());
        plan.push((t.clone(), Box::new(move |rec: &mut Vec<(String, Value)>| {
            set(rec, "status", Value::Str(status.clone()));
            set(rec, "disposition", Value::Str(format!("Retired by {retirer_id}: {disp}")));
            if let Some(e) = &ev {
                let current = get(rec, "evidence").cloned();
                if !pecia_core::write::evidence_is_structural(current.as_ref(), &cfg2) {
                    set(rec, "evidence", Value::Str(e.clone()));
                }
            }
        })));
    }
    plan.push((rid.to_string(), retirer));
    // Each revision is gated against the timeline AND the plan's earlier
    // revisions, so any refusal writes nothing.
    // The heads are certified, and stay so while every planned revision is
    // sound: until one is not, the local gate is the full one.
    let mut written: Vec<Value> = Vec::new();
    let mut shown: Vec<Value> = Vec::new();
    for (id, mutate) in &plan {
        let head = head_of(id).expect("planned");
        let mut rec = revision_of(ctx, head, mutate)?;
        if unchanged(args, head, &rec) {
            shown.push(unchanged_echo(head, &cfg));
            continue;
        }
        let local = written.iter().all(record_is_sound);
        let gate = |r: &Value| {
            if local {
                let now = |k: &str| written.iter().rev().filter_map(as_obj).find(|w| get_str(w, "id") == Some(k));
                let pending: Vec<(&str, &Pairs)> = heads.iter().map(|(k, h)| (k.as_str(), now(k).unwrap_or(*h))).collect();
                write_gate_local(&pending, Some(head), r, records.len() + written.len(), &cfg)
            } else {
                let pending: Vec<&Value> = records.iter().copied().chain(&written).collect();
                write_gate(&pending, r, &cfg)
            }
        };
        if let Gate::Refused(code) = gate_or_brand(ctx, args, &mut rec, gate) {
            return Ok(code);
        }
        shown.push(project("written", as_obj(&rec).expect("record"), &cfg));
        written.push(rec);
    }
    let mut certified = findings.is_empty();
    drop((heads, records));
    let mut chain = Chain::new(entries);
    for rec in written {
        let sound = record_is_sound(&rec);
        append_record(ctx, &mut chain, &findings, rec, certified)?;
        certified &= sound;
    }
    ctx.emit(&Value::Array(shown));
    Ok(0)
}

pub fn edit(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let today = sys::today()?;
    if args.has("also_closes") {
        return write_batch(ctx, args, edit_mutate(args, today));
    }
    revise(ctx, args, edit_mutate(args, today))
}

pub fn close(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    if args.has("also_closes") {
        return write_batch(ctx, args, close_mutate(args));
    }
    revise(ctx, args, close_mutate(args))
}

#[cfg(test)]
mod tests {
    use super::*;

    // MintedIdWidth (pc-1c65), restated: the declared width, and the growth
    // past it that a local collision still forces.
    #[test]
    fn a_minted_id_carries_the_declared_width() {
        let id = mint_id("task", "t", "2026-09-22", &HashSet::new()).expect("minted");
        assert_eq!(id.len() - 3, MINT_HEX);
        assert!(id[3..].bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()), "{id}");
    }

    #[test]
    fn control_the_width_still_grows_against_a_local_collision() {
        let digest = "0123456789abcdef".repeat(4);
        let first = format!("pc-{}", &digest[..MINT_HEX]);
        let taken: HashSet<&str> = [first.as_str()].into();
        assert_eq!(mint_from(&digest, &taken).expect("minted"), format!("pc-{}", &digest[..MINT_HEX + 1]));
        assert_eq!(mint_from(&digest, &HashSet::new()).expect("minted"), format!("pc-{}", &digest[..MINT_HEX]));
    }
}
