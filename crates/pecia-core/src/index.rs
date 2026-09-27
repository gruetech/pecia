//! The query index (v3.2): where each head sits in the log, recorded against
//! the identity of the log file it was built from.
//!
//! Derived and disposable. It is written beside the log after a read that
//! verified the whole chain clean, and after pecia's own writes. A query
//! uses it only while the log's identity is unchanged — the same device,
//! inode, size, modification and status-change times — and otherwise reads
//! and verifies the whole log as before, rebuilding it. A write of one
//! revision uses it only while, as well, the log's high-water mark (v3.3)
//! names the last entry recorded here, and links its entry to that entry's
//! hash. `check` never uses it.
//!
//! Every head carries its QUERY VIEW — the eight fields the query commands
//! read, and not the prose no query outputs — so a query never reads the log
//! at all: the index is the graph kept apart from the prose. A full read hands
//! the queries the same views, so the two paths agree by construction. The
//! heads are kept in two sections, each with its own digest: LIVE heads — the
//! non-terminal ones, the only records that can be ready or block anything —
//! lead, so the commonest queries read that section and nothing else; the
//! SETTLED heads follow.
//!
//! Pure: this module encodes and decodes bytes. Where they live, and what the
//! identity of a file is, belongs to the store.
//!
//! Layout, little-endian:
//!
//! ```text
//! header (HEADER_LEN bytes):
//!     magic "PECIAIX" 0x05
//!     identity of the log (dev u64, ino u64, size u64, mtime s i64, ns i64,
//!         ctime s i64, ns i64)
//!     entries u64, SHA-256 of the last entry's canonical form [32]
//!     live: count u64, bytes u64, digest [32]
//!     settled: count u64, bytes u64, digest [32]
//! live section, then settled section, each head:
//!     order u32, offset u64, length u32, id u16, status u8, type u8, view u32
//!     (lengths), then id, status, type and the view's canonical JSON
//! trailer "XIAICEP" 0x05
//! ```
//!
//! `order` is the head's first-seen position in the timeline; `offset` and
//! `length` are its line in the log, without the LF. A section's digest is
//! the SHA-256 of the SHA-256s of its successive 1 MiB chunks.

use crate::canonical::canonical_object;
use crate::check::Entries;
use crate::record::{as_obj, get, get_str, strict_int, Pairs};
use crate::write::is_terminal;
use std::collections::HashMap;

const MAGIC: &[u8; 8] = b"PECIAIX\x05";
const TRAILER: &[u8; 8] = b"XIAICEP\x05";
pub const HEADER_LEN: usize = 8 + 56 + 8 + 32 + 48 + 48;

/// The fields `ready`, `next`, `blocked`, `graph` and `gantt` read from a
/// head. None of them is prose; together they are a tenth of a record.
pub const QUERY_FIELDS: &[&str] = &["id", "type", "status", "title", "priority", "created", "target", "edges"];

/// A head as the queries see it: its query fields, in the record's order.
pub fn query_view(rec: &Pairs) -> Vec<(String, crate::Value)> {
    rec.iter().filter(|(k, _)| QUERY_FIELDS.contains(&k.as_str())).cloned().collect()
}

/// What a file's identity is taken to be: if any of it moved, the file may
/// have changed and nothing recorded against the old identity is trusted.
/// The status-change time is the part no ordinary program can set.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Identity {
    pub dev: u64,
    pub ino: u64,
    pub size: u64,
    pub mtime: (i64, i64),
    pub ctime: (i64, i64),
}

/// What an index was made against: the log, how many entries it held, and
/// the last of them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stamp {
    pub log: Identity,
    pub entries: u64,
    /// SHA-256 of the last entry's canonical form: the next entry's `prev`.
    pub head: [u8; 32],
}

fn put_identity(out: &mut Vec<u8>, i: &Identity) {
    for v in [i.dev, i.ino, i.size] {
        out.extend_from_slice(&v.to_le_bytes());
    }
    for v in [i.mtime.0, i.mtime.1, i.ctime.0, i.ctime.1] {
        out.extend_from_slice(&v.to_le_bytes());
    }
}

/// One head as a timeline yields it, for writing an index.
pub struct Source<'a> {
    pub order: u32,
    pub id: &'a str,
    pub status: &'a str,
    pub kind: &'a str,
    pub offset: u64,
    pub len: u32,
    pub rec: &'a Pairs,
}

/// One head as an index records it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Head<'a> {
    pub order: u32,
    pub id: &'a str,
    pub status: &'a str,
    pub kind: &'a str,
    pub offset: u64,
    pub len: u32,
    /// The query view, canonical JSON.
    pub view: &'a str,
}

/// The heads of a clean, regular timeline — every id's revisions strictly
/// increasing in log order, so its head is its last line — in first-seen
/// order, or None when the entries do not carry their spans or the timeline
/// is not regular (it is then read in full every time, as before).
pub fn heads_of(entries: &Entries) -> Option<Vec<Source<'_>>> {
    let spans = entries.spans()?;
    let mut order: Vec<Source<'_>> = Vec::new();
    let mut at: HashMap<&str, (usize, i64)> = HashMap::new();
    for (e, &(offset, len)) in entries.iter().zip(spans) {
        let rec = e.get("rec").and_then(as_obj)?;
        let (id, rev) = (get_str(rec, "id")?, strict_int(get(rec, "rev"))?);
        let (status, kind) = (get_str(rec, "status")?, get_str(rec, "type")?);
        match at.get_mut(id) {
            None => {
                let seen = u32::try_from(order.len()).ok()?;
                at.insert(id, (order.len(), rev));
                order.push(Source { order: seen, id, status, kind, offset, len, rec });
            }
            Some((i, last)) if rev > *last => {
                *last = rev;
                let seen = order[*i].order;
                order[*i] = Source { order: seen, id, status, kind, offset, len, rec };
            }
            Some(_) => return None,
        }
    }
    Some(order)
}

fn put_head(out: &mut Vec<u8>, h: &Head<'_>) -> Option<()> {
    out.extend_from_slice(&h.order.to_le_bytes());
    out.extend_from_slice(&h.offset.to_le_bytes());
    out.extend_from_slice(&h.len.to_le_bytes());
    out.extend_from_slice(&u16::try_from(h.id.len()).ok()?.to_le_bytes());
    out.push(u8::try_from(h.status.len()).ok()?);
    out.push(u8::try_from(h.kind.len()).ok()?);
    out.extend_from_slice(&u32::try_from(h.view.len()).ok()?.to_le_bytes());
    out.extend_from_slice(h.id.as_bytes());
    out.extend_from_slice(h.status.as_bytes());
    out.extend_from_slice(h.kind.as_bytes());
    out.extend_from_slice(h.view.as_bytes());
    Some(())
}

fn section(heads: &[&Head<'_>]) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(heads.iter().map(|h| h.id.len() + h.view.len() + 40).sum());
    for h in heads {
        put_head(&mut out, h)?;
    }
    Some(out)
}

/// The index of a timeline's heads, as `heads_of` yields them.
pub fn encode(stamp: &Stamp, heads: &[Source<'_>]) -> Option<Vec<u8>> {
    let views: Vec<String> = crate::par::map(heads.len(), |i| canonical_object(&query_view(heads[i].rec)));
    let filed: Vec<Head<'_>> = heads
        .iter()
        .zip(&views)
        .map(|(h, view)| Head { order: h.order, id: h.id, status: h.status, kind: h.kind, offset: h.offset, len: h.len, view })
        .collect();
    encode_heads(stamp, &filed)
}

/// The index of heads already filed — in first-seen order, each view its
/// query view's canonical form: what a writer that changed one head has.
pub fn encode_heads(stamp: &Stamp, heads: &[Head<'_>]) -> Option<Vec<u8>> {
    let (live, settled): (Vec<&Head<'_>>, Vec<&Head<'_>>) = heads.iter().partition(|h| !is_terminal(Some(h.status)));
    let (live_bytes, settled_bytes) = (section(&live)?, section(&settled)?);
    let mut out = Vec::with_capacity(HEADER_LEN + live_bytes.len() + settled_bytes.len() + TRAILER.len());
    out.extend_from_slice(MAGIC);
    put_identity(&mut out, &stamp.log);
    out.extend_from_slice(&stamp.entries.to_le_bytes());
    out.extend_from_slice(&stamp.head);
    for (count, bytes) in [(live.len(), &live_bytes), (settled.len(), &settled_bytes)] {
        out.extend_from_slice(&(count as u64).to_le_bytes());
        out.extend_from_slice(&(bytes.len() as u64).to_le_bytes());
        out.extend_from_slice(&digest(bytes));
    }
    debug_assert_eq!(out.len(), HEADER_LEN);
    out.extend_from_slice(&live_bytes);
    out.extend_from_slice(&settled_bytes);
    out.extend_from_slice(TRAILER);
    Some(out)
}

/// How much of a section one digest covers.
const CHUNK: usize = 1 << 20;

/// A section's digest: SHA-256 over the SHA-256 of each CHUNK of it, so the
/// chunks hash in parallel — a section is read and written with every write.
fn digest(bytes: &[u8]) -> [u8; 32] {
    use sha2::{Digest, Sha256};
    let chunks: Vec<&[u8]> = bytes.chunks(CHUNK).collect();
    let digests = crate::par::map(chunks.len(), |i| Sha256::digest(chunks[i]));
    let mut outer = Sha256::new();
    for d in &digests {
        outer.update(d);
    }
    outer.finalize().into()
}

/// One section as the header describes it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Section {
    pub count: u64,
    pub bytes: u64,
    sha: [u8; 32],
}

/// What the fixed-size start of an index says.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Header {
    pub stamp: Stamp,
    pub live: Section,
    pub settled: Section,
}

pub fn header(bytes: &[u8]) -> Option<Header> {
    let mut r = Reader { b: bytes.get(..HEADER_LEN)?, at: 0 };
    if r.take(8)? != MAGIC {
        return None;
    }
    let mut identity = || -> Option<Identity> {
        Some(Identity { dev: r.u64()?, ino: r.u64()?, size: r.u64()?, mtime: (r.i64()?, r.i64()?), ctime: (r.i64()?, r.i64()?) })
    };
    let log = identity()?;
    let entries = r.u64()?;
    let head: [u8; 32] = r.take(32)?.try_into().ok()?;
    let mut section = || Some(Section { count: r.u64()?, bytes: r.u64()?, sha: r.take(32)?.try_into().ok()? });
    let (live, settled) = (section()?, section()?);
    Some(Header { stamp: Stamp { log, entries, head }, live, settled })
}

/// The heads in `bytes` — exactly the section `s` describes, checked against
/// its digest — in first-seen order.
pub fn heads<'a>(s: &Section, bytes: &'a [u8]) -> Option<Vec<Head<'a>>> {
    if bytes.len() as u64 != s.bytes || digest(bytes) != s.sha {
        return None;
    }
    let mut r = Reader { b: bytes, at: 0 };
    let heads = (0..s.count).map(|_| r.head()).collect::<Option<Vec<_>>>()?;
    (r.at == bytes.len()).then_some(heads)
}

/// Whether `bytes` is exactly the index's end: its trailer.
pub fn is_trailer(bytes: &[u8]) -> bool {
    bytes == TRAILER
}

/// A whole index — its header, and every head in first-seen order — or None
/// if any part of it fails its checks.
pub fn decode(bytes: &[u8]) -> Option<(Header, Vec<Head<'_>>)> {
    let h = header(bytes)?;
    let live_end = HEADER_LEN.checked_add(usize::try_from(h.live.bytes).ok()?)?;
    let settled_end = live_end.checked_add(usize::try_from(h.settled.bytes).ok()?)?;
    let mut all = heads(&h.live, bytes.get(HEADER_LEN..live_end)?)?;
    all.extend(heads(&h.settled, bytes.get(live_end..settled_end)?)?);
    if !is_trailer(bytes.get(settled_end..)?) {
        return None;
    }
    all.sort_unstable_by_key(|x| x.order);
    Some((h, all))
}

/// Each head's view as a record, in the order given — or None if any view
/// does not parse, or does not name the id, status and type it is filed
/// under.
pub fn views(filed: &[&Head<'_>]) -> Option<Vec<crate::Value>> {
    crate::par::map(filed.len(), |i| {
        let h = filed[i];
        let view = crate::parse(h.view).ok()?;
        let r = as_obj(&view)?;
        (get_str(r, "id") == Some(h.id) && get_str(r, "status") == Some(h.status) && get_str(r, "type") == Some(h.kind)).then_some(view)
    })
    .into_iter()
    .collect()
}

struct Reader<'a> {
    b: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let s = self.b.get(self.at..self.at.checked_add(n)?)?;
        self.at += n;
        Some(s)
    }
    fn text(&mut self, n: usize) -> Option<&'a str> {
        std::str::from_utf8(self.take(n)?).ok()
    }
    fn head(&mut self) -> Option<Head<'a>> {
        let (order, offset, len) = (self.u32()?, self.u64()?, self.u32()?);
        let (id_len, status_len, kind_len) = (usize::from(self.u16()?), usize::from(self.u8()?), usize::from(self.u8()?));
        let view_len = self.u32()? as usize;
        let (id, status, kind, view) = (self.text(id_len)?, self.text(status_len)?, self.text(kind_len)?, self.text(view_len)?);
        Some(Head { order, id, status, kind, offset, len, view })
    }
    fn u8(&mut self) -> Option<u8> {
        Some(self.take(1)?[0])
    }
    fn u16(&mut self) -> Option<u16> {
        Some(u16::from_le_bytes(self.take(2)?.try_into().ok()?))
    }
    fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }
    fn u64(&mut self) -> Option<u64> {
        Some(u64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    fn i64(&mut self) -> Option<i64> {
        Some(i64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse;

    fn identity() -> Identity {
        Identity { dev: 1, ino: 2, size: 3, mtime: (4, 5), ctime: (6, -7) }
    }

    fn sample() -> Vec<u8> {
        let recs = [
            parse(r#"{"body":"long prose","edges":{"blocks":[]},"id":"pc-a","owner":"o","status":"open","title":"t","type":"task"}"#).expect("record"),
            parse(r#"{"disposition":"why","id":"pc-bé","status":"done","type":"milestone"}"#).expect("record"),
        ];
        let heads: Vec<Source<'_>> = recs
            .iter()
            .enumerate()
            .map(|(i, r)| {
                let p = as_obj(r).expect("object");
                Source { order: i as u32, id: get_str(p, "id").unwrap(), status: get_str(p, "status").unwrap(), kind: get_str(p, "type").unwrap(), offset: i as u64 * 10, len: 9, rec: p }
            })
            .collect();
        encode(&Stamp { log: identity(), entries: 7, head: [9; 32] }, &heads).expect("encodes")
    }

    /// Every part of an index, or None.
    fn whole(b: &[u8]) -> Option<(Header, Vec<Head<'_>>, Vec<Head<'_>>)> {
        let (h, all) = decode(b)?;
        let (live, settled) = all.into_iter().partition(|x| !is_terminal(Some(x.status)));
        Some((h, live, settled))
    }

    #[test]
    fn an_index_round_trips_carrying_only_query_views() {
        let bytes = sample();
        let (h, live, settled) = whole(&bytes).expect("whole");
        assert_eq!((h.stamp.log, h.stamp.entries, h.stamp.head, h.live.count, h.settled.count), (identity(), 7, [9; 32], 1, 1));
        assert_eq!(live[0].view, r#"{"edges":{"blocks":[]},"id":"pc-a","status":"open","title":"t","type":"task"}"#);
        assert_eq!((settled[0].id, settled[0].order, settled[0].offset), ("pc-bé", 1, 10));
        assert_eq!(settled[0].view, r#"{"id":"pc-bé","status":"done","type":"milestone"}"#);
    }

    #[test]
    fn filed_heads_encode_back_to_the_same_index() {
        let bytes = sample();
        let (h, all) = decode(&bytes).expect("whole");
        assert_eq!(encode_heads(&h.stamp, &all).expect("encodes"), bytes);
    }

    #[test]
    fn a_damaged_index_is_no_index() {
        let bytes = sample();
        assert!(whole(&bytes).is_some());
        for cut in 0..bytes.len() {
            assert!(whole(&bytes[..cut]).is_none(), "a truncation at {cut} decoded");
        }
        for at in 0..bytes.len() {
            let mut flipped = bytes.clone();
            flipped[at] ^= 0x20;
            // The identity and entry count are not self-checking: a changed
            // identity is a stale index, which the reader already refuses.
            if !(8..HEADER_LEN - 96).contains(&at) {
                // (The stamp — identities, count, head — is not self-checking
                // either: a changed stamp is a stale index, refused as such.)
                assert!(whole(&flipped).is_none(), "a flipped byte at {at} decoded");
            }
        }
    }
}
