# pecia format v2 — the consolidated contract

**GENERATED — do not edit.** Regenerate with `dev/spec-consolidate.py --write`;
`dev/spec-consolidate.py --check` fails when this file is stale.

- Generated from `spec/format-v2.md` (3936 lines, sha256 `ed469beb13fe36c6…`)
- Record contract from `spec/record.schema.json`; code glosses from `spec/vocabulary.json`
- 11 base sections, 26 amendment sittings, 113 amendment sections

## What this document is

A second view of `spec/format-v2.md`, organised for an implementer. The source
stays canonical and unedited: it is the record of what thirteen adversarial
review rounds found, and its dated-amendment convention exists so that record
survives. This projection exists because that convention makes the source a poor
thing to implement from — its base section 8 calls itself "the checker contract"
and does not state one for most of the codes below (`pc-0e2a`), and its base
section 7 says the record is v1's "unchanged" while the shipped schema carries
fields and edges v1 never had (`pc-c38d`).

Both counts belong to `dev/spec-agree.py`, which measures them, and are
deliberately not restated here: a count beside machinery is stale the moment the
machinery moves (v2.16, `pc-9611`), and this document and that check are
regenerated at different times.

**Rule 1 applies here as everywhere: well-formed is never true.** This document
reproduces what the spec says. Whether the implementation does it is the kill
matrix's question, and whether the two agree is `dev/spec-agree.py`'s.

Nothing here is rewritten, summarised or reconciled. Where two sittings say
different things you will see both, in document order, because a disagreement
between sittings carries information that deduplicating it away destroys.

### The index is incomplete, and this document says so rather than hiding it

Sections are carried by POSITION, in document order. The `<!-- vocab: -->`
markers are a secondary index only, because they do not cover the text
(`pc-4f6a`): 66 of 113 amendment sections carry no marker at
all — 1816 of 3272 amendment lines, 55% — and 8 name a code in
their title while marking nothing. A marker-driven generator omits all of that in
silence. Part 3's per-code tables therefore mark each attribution `marker` or
`title`, and Part 4 carries every section whether indexed or not.

Sections that amend a code without marking it:

| sitting | line | code | section |
|---|---|---|---|
| v2.7 amendments | 1198 | E013 | [The E013 base bullet is regenerated from its v2.6 narrowing](#the-e013-base-bullet-is-regenerated-from-its-v2-6-narrowing) |
| v2.10 amendments | 1689 | D012 | [D012 tests the resolution AND the execution the hook will run (defects `pc-2f19`, `pc-b418`)](#d012-tests-the-resolution-and-the-execution-the-hook-will-run-defects-pc) |
| v2.11 amendments | 1832 | D012 | [D012 identifies the chain on code, not comments (defect `pc-709d`)](#d012-identifies-the-chain-on-code-not-comments-defect-pc-709d) |
| v2.11 amendments | 1846 | E004 | [E004 anchors only on records a cycle passes through (defect `pc-86f9`)](#e004-anchors-only-on-records-a-cycle-passes-through-defect-pc-86f9) |
| v2.11 amendments | 1863 | E008 | [E008 groups over every well-typed `(id, rev)` (defect `pc-8291`)](#e008-groups-over-every-well-typed-id-rev-defect-pc-8291) |
| v2.11 amendments | 1907 | E015 | [E015's non-text diagnosis is independent of the witness's state (defect `pc-0d11`)](#e015-s-non-text-diagnosis-is-independent-of-the-witness-s-state-defect-p) |
| v2.11 amendments | 1922 | E015 | [E015 findings name THE file, wherever the store is (defect `pc-f61a`)](#e015-findings-name-the-file-wherever-the-store-is-defect-pc-f61a) |
| v2.12 amendments | 1967 | E001 | [E001's id and date anchors are full matches (defect `pc-9726`)](#e001-s-id-and-date-anchors-are-full-matches-defect-pc-9726) |

---

## Part 1 — The record

Generated from `spec/record.schema.json`, which is the shipped contract and the
most current statement of the record in the repository. Base section 7 of the
source says the record is v1's "unchanged" and sends the reader to
`spec/format-v1.md`; that sentence is false as of v2.17 and is the subject of
`pc-c38d`. The table below is what an implementation must accept.

| field | required | type | meaning |
|---|---|---|---|
| `body` | **yes** | `string` | — |
| `created` | **yes** | `isoDate` | — |
| `disposition` | **yes** | `string` \| `null` | Required on every terminal transition (E006, a head-state check): names what happened and why, sufficient for a reader with no session context. "Fixed" alone fails audit. |
| `edges` | **yes** | `object` | The typed edge object — see below. |
| `evidence` | **yes** | `string` \| `null` | A command runnable from repo root whose exit 0 licenses the closure, or a foreign reference <scheme>:<id> naming a fact another system owns (v1.7; claims:<id> is one instance), or "unknown" (non-terminal records only). Prose is not evidence. A reference is whitespace-free, which is what distinguishes it from a command. A command's SHAPE (E007, done defects) is read off the ledger and .pecia/config.yaml and never off the host (v1.8): the first token must contain "/" or be declared in extra_evidence_commands. Scheme DECLARATION is checked against .pecia/config.yaml resolvers (E011). Both are cross-record checks outside this schema; whether a command runs here, or a reference resolves, is audit's question, and truth is rule-1 territory. |
| `id` | **yes** | `recordId` | Minted once at creation (pc- + hash over type/title/created/nonce, at least 12 hex wide since v3.0 and widened further against a local collision, pc-1c65); stable across edits; never sequential. Hash derivation is mint-time discipline, not post-hoc checkable — the schema can only enforce the token shape. Importers: derive ids deterministically from source ids so re-import is idempotent. |
| `labels` | **yes** | `array` | — |
| `owner` | **yes** | `string` | Stable identity string. Machine authors use a vendor-qualified identity (e.g. claude:fable-5) so provider drift is distinguishable from sampling variance. |
| `priority` | **yes** | `integer` | 0 blocks-all-today .. 2 default .. 4 someday (excluded from `next`). Required since v1.2: the CLI always writes it; a foreign writer omitting it is malformed, not defaulted. |
| `rev` | **yes** | `integer` | 1 at creation, +1 per appended revision. The ordering relation — dates are data, not order. A record IS its highest-rev line. (JSON true/false are not integers; the checker excludes Python bools explicitly.) |
| `status` | **yes** | enum | Stored states only. `blocked` and `ready` are computed queries and may NEVER be stored, including as config extras. extra_statuses widen this enum under the checker. |
| `title` | **yes** | `string` | Non-empty (whitespace-only fails). |
| `type` | **yes** | enum | Core vocabulary. A repo's config may add extra_types (addition, never mutation of core meanings); records using them validate under the checker but not this schema. |
| `updated` | **yes** | `isoDate` | — |
| `anchor` | no | `string` | v2.5 (pc-05e4, closing pc-e039): the commit this revision was written against, stamped by the write path from `git rev-parse HEAD` — never authored, never required (rule 3), absent outside a git worktree and on an unborn branch. A cold agent runs `git diff <anchor>..HEAD` to see what moved under the record since it was written. Per-revision custody, like `forced`: sync re-chains carry the writer's anchor, and a new revision names its own commit rather than inheriting. |
| `anchor_dirty` | no | const `true` | v2.5: presence records that the worktree carried uncommitted tracked changes when the anchor was stamped, so the diff against the anchor under-reports what the writer saw. Presence, not a toggle (the `forced` convention), and only legal beside an `anchor`. |
| `context` | no | `string` | v2.5 (pc-813f, closing pc-7ab3): a foreign reference into a shared orientation document, normally doc:<path>#<anchor>, resolved through the v1.7 resolver registry — the scheme must be declared (E011, as amended). Pull-on-demand by design: nothing primes it, because primed context measurably decays across compaction. `audit` reports `unresolvable-context` when the referent is missing. |
| `forced` | no | const `true` | The write-gate bypass brand (v1.3): per-revision, never inherited. When present it must be exactly true. The checker is force-blind; audit enumerates every branded revision. CORRECTED 2026-08-18 (pc-b98b): this used to describe same-rev lines differing only in the brand as 'E010-divergent' (v1's merge algebra). E010 and the merge algebra it governed were deleted at pc-0033 (v2) — under the single hash-chained timeline, a repeated (id,rev) is log corruption, not a divergent duplicate to disposition. |
| `ratified` | no | `isoDate` | v2.5: the date of the ratifying revision. Travels with `ratified_by`. |
| `ratified_by` | no | `string` | v2.5 (pc-813f, closing pc-ff8f): who accepted this agent-authored decision — set by an explicit ratifying revision (`edit <id> --ratify`), never at creation. Travels with `ratified`; legal only on `decision` records. `audit` surfaces terminal decisions with a vendor-qualified owner and no ratification (`unratified-decision`). |
| `target` | no | `isoDate` | Optional milestone target date, consumed by `gantt`. E001 validates its shape on any record but never requires it. |

- **`type`** — one of `defect`, `task`, `decision`, `milestone`, `question`. Core vocabulary. A repo's config may add extra_types (addition, never mutation of core meanings); records using them validate under the checker but not this schema.
- **`status`** — one of `open`, `in-progress`, `done`, `dropped`, `superseded`. Stored states only. `blocked` and `ready` are computed queries and may NEVER be stored, including as config extras. extra_statuses widen this enum under the checker.

### Edges

`edges` is a closed object (`additionalProperties: false`) with 9 keys.

| edge | shape | meaning |
|---|---|---|
| `blocks` | list | Scheduling edge: each target cannot be `done` while this record is non-terminal. Targets are ids; existence is E003 (checker, heads only) — config `planned:` ids are legal targets, so the schema requires only strings. |
| `retires` | list | Scheduling edge, new in v1.13: each target is resolved BY this record, so closing this record closes it. Held by the RETIRER and never as a scalar retired_by on the retired, because the failure mode it detects is an over-promise — a claim about targets that may not exist, which is E003's dangling-target finding. A promise whose retirers have all gone terminal while the target has not is E012 (checker, heads only). Partial contribution is NOT this edge: use `blocks`. Targets are ids; config `planned:` ids are legal, so the schema requires only strings. |
| `parent` | `scalarEdge` |  |
| `duplicate_of` | `scalarEdge` |  |
| `discovered_from` | `scalarEdge` |  |
| `caused_by` | `scalarEdge` |  |
| `validates` | `scalarEdge` |  |
| `supersedes` | `scalarEdge` |  |
| `no_edges` | const `true` | Declares the record deliberately edgeless, silencing the untriaged audit finding. Presence, not a toggle: when present it must be exactly true (v1.6). |

### Shared definitions

- **`recordId`** — `string` pattern `^pc-[A-Za-z0-9][A-Za-z0-9.-]*$`.
- **`isoDate`** — `string` pattern `^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?)?$`.
- **`scalarEdge`** — `string` \| `null` pattern `\S`. Custody edges (parent additionally schedules): null or a single target id. v2.7 (pc-94d2): the empty string is not null and names no record. v2.9 (pc-f97f): pattern \S replaces minLength 1, which excluded only the truly empty string while E001 refuses any whitespace-only target — the divergence was exactly where claim 26 says schema and checker agree.

---

## Part 2 — The model, as the base states it

Base sections reproduced verbatim and in order. Sections 7 and 8 are carried
here too, unaltered, WITH their known staleness marked — this document does not
correct the source, it reports it. Part 1 supersedes section 7 in practice, and
Part 3 supersedes section 8.

<a id="1-why-v2-exists"></a>

## 1. Why v2 exists

v1 stored coordination state in a fork-and-reconcile medium. A tracked file in a git
working tree is branch-scoped by construction, and git branches exist precisely to
let content diverge. The ledger is the one artifact in a repo that must not diverge.

Every reconciliation mechanism in v1 is downstream of that single mismatch: `rev` as
a merge-direction fix, E010, `merge=union`, the compaction hazard, trap 2, trap 3,
and the CI job that validates the pull-request merge ref because branch-local green
is not merge clearance.

The mismatch was never argued. The 2026-07 landscape survey listed per-repo-versus-
constellation as an **open** decision point with a lean; `format-v0.md` transcribed
`.pecia/work.jsonl` into its Storage section the same day with no options block, over
a header stating that nothing in it was load-bearing; and it reached v1 unchanged.
`Q2-conspectus` §5.8 then rated the whole area **Underdetermined** — "not that the
evidence conflicts, but that there is no evidence" — and named it out of scope.

The observed harm is transient, not permanent: a record created on a branch is
invisible to every other branch until that branch merges. `pc-88c6` was committed at
13:25 on 2026-08-07 and reached main at 15:21, two hours during which `next` on main
could not see a defect record already driving a spec amendment. Merge latency is
unbounded, a long-lived branch is an arbitrarily long blind window, and nothing
reports the window's size. See `pc-1e88` rev 3 for the corrected measurement and the
stale claim it replaces.

**Zero E010 was never evidence the storage model was sound.** `pc-4331` reconstructed
every historical ledger state and measured divergent `(id, rev)` at zero, reasoning
correctly that the workload partitions naturally by record. That is the same fact as
the defect: agents working disjoint records never *collide*, they simply never *see*
each other. E010's frame is disagreement between copies, and a record living in
exactly one timeline disagrees with nothing — the size-one blind spot GP36 names.

---

<a id="2-foundational-semantics"></a>

## 2. Foundational semantics

Rules 1–4 are inherited from v1 verbatim and unchanged. Rule 5 is new.

1. **Exit-0 means "well-formed," never "true."**
2. **Derived state is computed, never stored.**
3. **Capture is cheap; triage is audited.**
4. **Absence is declared, not silent.**
5. **There are no alternative timelines.** A project has exactly one work ledger and
   that ledger has exactly one history. A fork is not reconciled — it is
   unrepresentable. Copies, caches, exports and projections are permitted and useful;
   what is forbidden is a second history that could be merged back. This rule is
   enforced by the substrate (§4, §5), never by prose.

Rule 5 is stronger than what v1 attempted. v1 offered a branchable DAG plus a rule
against branching it. v2 offers no branch to take.

---

<a id="3-storage"></a>

## 3. Storage

<a id="4-concurrency-compare-and-swap-on-a-field-set"></a>

## 4. Concurrency: compare-and-swap on a field set

<a id="5-publication-and-the-distributed-cas"></a>

## 5. Publication and the distributed CAS

`refs/pecia/log` is a **commit chain**, not a blob. Fast-forward-ness is defined by
commit ancestry, so a bare blob cannot serve as the serialization point. Each publish
extends the chain with a commit whose tree holds the log.

**A ref that rejects non-fast-forward pushes is a distributed compare-and-swap
register.** Push wins, or is refused. There is no third outcome and no merge.

On refusal:

1. Fetch the remote log.
2. Find the common prefix — entries identical by `prev` hash.
3. Take the local-only suffix.
4. Re-chain it onto the new head: recompute `seq` and `prev`, and re-run the `rev`
   CAS and the `touched` intersection test per entry (§4).
5. Entries that pass are re-published. Entries that fail the same-field test are
   surfaced to the writer with both revisions named.

Because disjoint-field appends commute, steps 1–4 are mechanical and lossless. A
human is involved only in step 5.

**Delivery is confirmed by reading the ref back, never by `git push`'s exit code.**
This is VP20, measured twice in one day in a sibling repo: a write that succeeds
against the wrong location succeeds, and `push` reported as done with no read-back is
the exact signature. The implementation re-reads `refs/pecia/log` after every publish
and asserts the expected head.

**CORRECTED 2026-08-18 (pc-66b6, off-lineage review pc-685f).** Step 1's "no
local-only suffix" case (a pure fast-forward, the common shape of a fresh clone
adopting an already-published timeline) validated the fetched log's hash chain
only, never the full checker of §4/§8. A remote log that is chain-valid but
semantically dirty — a forged `touched` set, a dangling edge — hydrated as
delivered, discovered only by a later `pecia check`. The fast-forward path now
runs the same full-checker pass step 4 already ran for the re-chain case, before
writing anything.

**CORRECTED 2026-08-18 (pc-7f4e, off-lineage review pc-685f).** Step 4's
same-field conflict test treated `edges.no_edges` as a field disjoint from
`edges.blocks` and the other edge fields, so one writer declaring a record
deliberately edgeless and another concurrently adding a real edge had
non-intersecting touched sets and re-chained cleanly — composing into a record
that asserted both. `no_edges` is a declaration about the absence of the
others, not a peer field: it now conflicts with any other `edges.*` field
touched on the other side, not only with itself.

**A fresh clone's local layer starts absent, not merely absent-and-safe-to-
rebuild (2026-08-18, pc-5f0f).** `git clone` fetches `refs/heads/*` and tags by
default and nothing under `refs/pecia/*` — a non-standard ref outside that set
— so a fresh clone of an already-published repo and a repo that has never run
pecia at all are locally indistinguishable at the log layer alone. `sync` is
what tells them apart, per this section: it fetches the published layer first
and treats a locally-absent log as the ordinary hydrate case. `migrate`
(undocumented elsewhere in this spec; see `pecia_cli.py:cmd_migrate` and
ledger record `pc-5c7c`) fetches nothing by contract — it rebuilds from
local history plus the
working tree, never adopting remote content — but now makes one best-effort,
non-fatal check of the published layer before deciding there is nothing to
extend, so it can refuse a rebuild that would fork rather than silently
writing one.

---

<a id="6-the-snapshot"></a>

## 6. The snapshot

`.pecia/work.jsonl` **survives**, demoted from authority to a generated projection.

- It is regenerated from the log, never hand-edited.
- It records the log head it was derived from (`.pecia/snapshot.head`).
- It is a permitted shadow. A stale shadow is not an alternative timeline.
- The checker distinguishes **stale** from **forked**: if the recorded head is an
  *ancestor* of the current log head, the snapshot is behind and that is clean; if it
  is not an ancestor, something wrote to the snapshot as if it were authority, and
  that is an error (E015).

This preserves what the working-tree file was actually good for — PR-diff review,
`grep`, CI without a ref fetch, a collaborator reading the ledger from a plain clone —
without letting any of it fork the timeline.

Per GP28 the snapshot is subject to all three axes, which do not substitute for each
other: **state** (regenerate and diff), **coverage** (a `--check` exists, runs in the
gate runner, and the artifact is in the documented repair path), and **content** (no
field of the snapshot restates a fact the log does not own).

CI gains one step: `git fetch origin 'refs/pecia/*:refs/pecia/*'`.

---

<a id="7-record"></a>

> **STALE (pc-c38d).** Reproduced as the source has it. See Part 1 for what the implementation is actually held to.

## 7. Record

The record inside `rec` is **v1's record, unchanged** — same required fields, same
types, statuses, priorities, edges, disposition and evidence semantics. See §9.

`rev`'s *meaning* changes (§4.2) but its shape and range do not, so
`spec/record.schema.json` needs no field change. Whether the log **entry** gets its
own schema alongside the record schema is an implementation question for `pc-0033`.

---

<a id="8-invariants-the-checker-contract"></a>

> **STALE (pc-0e2a).** Reproduced as the source has it. See Part 3 for what the implementation is actually held to.

## 8. Invariants (the checker contract)

Each new gate ships with a demonstrated kill at `pc-0033`, or its claim stays
`asserted`.

**Retained from v1, unchanged:** E001, E003, E004, E005, E006, E007, E008, E009, E011.

**Removed:**

- **E010** (same `(id, rev)`, different content). A total order with CAS makes two
  entries for one `(id, rev)` unreachable through any write path. VP4's *destructive*
  half applies and is the half that gets skipped, because deleting a check produces no
  visible artifact: **the E010 gate and its kill are deleted in the same commit that
  makes the state unrepresentable.** A permanently-green gate reads as assurance and
  provides none.
  <!-- vocab: {"action":"delete","code":"E010","at":"v2","by":"pc-0033","of":"commit:15fd91c"} -->

**Changed:**

- **E002** (duplicate `(id, rev)` with differing content) is retained but re-founded.
  Under v1 it signalled a merge that needed a human. Under v2 it can only mean the log
  is **corrupt**, and it is emitted as its own error for identical and divergent
  duplicates alike, beside whatever chain findings (E013/E014) accompany the damage.
  It keeps a live denominator — a corrupted or truncated log is a real condition —
  which is why it is re-pointed rather than deleted alongside E010. CORRECTED at
  v2.6 (`pc-439d`): this bullet said the condition is "folded into chain integrity
  and reported with E013", which the diagnostics never did.
  <!-- vocab: {"action":"amend","code":"E002","at":"v2","by":"pc-0033"} -->

**New.** These were drafted as E012/E013/E014 and **renumbered to E013/E014/E015 on
2026-08-12**. While this branch was unmerged, main landed v1.13 (the retires relation,
`pc-4d19`) and minted its own **E012** for an expired retirement promise. Two branches
allocated one code for two invariants, neither able to see the other. v1.13's E012
stands because it is on main; v2's codes moved. The asymmetry is worth naming: record
ids are hash-minted with a nonce precisely so two agents on two branches cannot collide,
a lesson taken from Backlog.md at v0 — and the E-code vocabulary, in the same file, is
hand-assigned and sequential with no such protection. A single timeline makes this
collision *visible immediately* rather than at merge, which is an improvement and is
not the same as making it impossible. Tracked as `pc-e9cf`.
  <!-- vocab: {"action":"mint","code":"E012","at":"v2","by":"pc-0033"} -->
  <!-- vocab: {"action":"renumber","code":"E012","at":"v2","by":"pc-0033","of":"pc-0033","to":"E013"} -->

- **E013 chain break** — an entry whose `prev` does not equal the hash of the preceding
  entry, or a `seq` that is not exactly one greater. Detects INTERIOR damage — a
  splice, an out-of-band edit, a broken link. A suffix truncation leaves an
  internally valid prefix, and a valid prefix is a valid chain, so E013 structurally
  cannot fire on it; that custody belongs to the snapshot-head witness (E015, §6)
  and §5's published prefix guard. (Base bullet regenerated at v2.7 from the v2.6
  narrowing amendment, `pc-130e` — the registry's gloss derives from this text and
  carried the superseded claim.)
  <!-- vocab: {"action":"mint","code":"E013","at":"v2","by":"pc-0033"} -->
- **E014 CAS violation** — an entry whose `rev` is not head+1 for its id at its
  position in the log, or an entry whose `touched` set does not match a recomputation
  of the diff against that head. The second half is what enforces §4.1's
  derived-not-authored rule structurally rather than by instruction.
  <!-- vocab: {"action":"mint","code":"E014","at":"v2","by":"pc-0033"} -->
- **E015 snapshot forked** — `.pecia/snapshot.head` is not an ancestor of the current
  log head (§6). Stale is clean; forked is an error.
  <!-- vocab: {"action":"mint","code":"E015","at":"v2","by":"pc-0033"} -->

Diagnostics are unchanged: one JSON object per finding on stdout,
`{severity, code, id, message}`; exit 0 clean / 1 findings / 2 cannot-run.

---

<a id="9-disposition-of-every-v1-section"></a>

## 9. Disposition of every v1 section

Nothing is ambiently inherited (rule 4).

| v1 section | v2 disposition |
|---|---|
| Foundational semantics (rules 1–4) | **Inherited verbatim.** Rule 5 added (§2). |
| Storage | **Replaced** by §3. |
| Record | **Inherited verbatim**, wrapped in a log entry (§3.2, §7). |
| Field operational definitions | **Inherited verbatim**, except `rev` (§4.2). |
| Invariants | **Amended** by §8. |
| Queries (`ready`/`blocked`/`next`/`audit`/`graph`/`gantt`) | **Inherited verbatim.** None of them touches storage. `ready`, `blocked`, `next` and `graph` are pure functions of the store: its resolved head set and its config. `gantt` and `audit` also read today's date, and `audit` asks the repository's declared resolvers whether each context reference resolves (its `unresolvable-context` kind), so a file on the host can change its findings with every stored byte unchanged. CORRECTED at v3.4 (`pc-74a2`): this row called all six queries pure functions over the resolved head set. |
| Harness integration | **Inherited verbatim.** On-demand over prime is unaffected. |
| v1.1–v1.12 amendments | **Inherited** except where §3, §4 or §8 supersede them. The merge-algebra content of v1.1, v1.2 and v1.5 is superseded; the rest stands. |
| Changes from v0 | **Historical.** Retained in v1 for the record. |

---

<a id="10-consequences-outside-this-document"></a>

## 10. Consequences outside this document

- **Formal model.** Eight expectations retire across three files: `pecia.als` loses
  `StrictOpsPreserveResolvable`, `DivergentMergeIsFlagged`, `DisjointEditsMergeClean`,
  `CompactPreservesCleanAndQueries`, `CompactIdempotent`; `pecia-traps.als` loses
  `BranchedCompactMergeIsClean` and `CrossBranchEdgeAdditionsMergeClean`;
  `pecia-seeded-kills.als` loses `LastWinsMatchesMaxRev`. **Subtraction alone thins the
  null arm**, which VP4(a) forbids — a formal gate ships with three expectations
  (theorems pass, pinned counterexamples keep failing, seeded kills keep failing). v2
  owes its own traps and its own seeded kills. Theorem candidates: a fork is
  unrepresentable; CAS rejection preserves `clean`; re-chaining a disjoint-`touched`
  suffix preserves the head set. Modelled **before** implementation (GP35 — whose
  catalog evidence is this repo's own trap 3, found only by the model).
- **`compact` is removed.** Under an append-only log with real history, compaction is a
  timeline rewrite — a force-push. Git history was the argument that v1's compaction was
  safe; the log now *is* the history.
- **CI's merge-ref job** loses its stated reason for existing (trap 3). Whether the job
  survives on other grounds is decided at `pc-0033`, not assumed here.
- **VP19** governs path resolution. `--git-common-dir` resolution is host-dependent by
  exactly the signature VP19 names, so the gates run at least once from a clean
  `git archive` export, not only from a working tree.

---

<a id="11-not-decided-here"></a>

## 11. Not decided here

- Whether the log entry carries its own JSON Schema (`pc-0033`).
- Whether the CI merge-ref job survives on other grounds (`pc-0033`).
- `pc-75ae` — records carry no format version and there is no stated migration
  position. v2 does not make it worse (§4.1) and does not answer it. It stays open,
  named, and unclaimed rather than quietly inherited.
- Cross-repo roll-up over N repos' refs. It is a read-only projection (M4,
  `pc-24fa`), not a storage decision, and `pc-1e88` rejected making it one.

---

---

## Part 3 — The diagnostic vocabulary

One entry per live code. The gloss is the spec's own current one-line meaning,
from the generated registry. The table beneath it is every section that bears on
the code, in document order — `marker` where the document declares the
allocation, `title` where the section announces the code and declares nothing
(`pc-4f6a`). Follow the links into Part 4 for the normative text.

### D001

repo ships a pre-commit hook but core.hooksPath is unset — the gate has never run in this clone.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D002

core.hooksPath is set but no pre-commit hook is there — commits run unguarded.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D003

the pre-commit hook is not executable — git skips it silently, which reads like a passing gate; also warns when it is executable but unreadable, where an interpreted hook dies at every commit and doctor cannot tell.

Stated in 2 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |
| v2.12 amendments | 2066 | `marker` | [A capability detector asks the question the runtime asks (defect `pc-acaf`)](#a-capability-detector-asks-the-question-the-runtime-asks-defect-pc-acaf) |

### D004

a second pre-commit hook exists outside the configured hooks path — one of them is dead code.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D005

.pecia/.lock is not git-ignored — permanent untracked noise trains readers to ignore noise.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D006

the snapshot still declares merge=union, a v1 attribute the v2 projection never merges.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D007

the ledger is not tracked by git — an uncommitted ledger is not custody.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D008

core.hooksPath resolves outside this working tree — the hook that runs is another checkout's copy.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D009

no local timeline and a remote is configured — the fresh-clone state, recoverable with sync.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D010

a ledger exists and NO write gate is active in this clone — every other D-code assumes a gate to report on.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D011

the config is not tracked by git — commit gates validate the staged ledger against the STAGED config, so the live and committed checks disagree.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1350 | `marker` | [The doctor's D-codes join the spec's vocabulary (defect `pc-085ad`)](#the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad) |

### D012

the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repository root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs — the hook's own shebang, and `python3` or the checker's shebang — resolving, derived from those files rather than listed and read with the lexical rules of whatever reads them, so an `env -S` string's quotes, escapes and `${VAR}` are env's syntax and a name computed from a variable this environment does not carry is reported undecided rather than missing). Every relative path this code reasons about resolves against the work-tree root, the way git will — the PATH's own relative entries included (v2.16) — so the answer does not depend on the directory doctor was invoked from. The PATH resolution it models is the one the shipped hook performs (`type -P`: an executable file, never a shell function, alias or builtin — v2.17); where a hook carries the pre-v2.17 `command -v` resolver instead, doctor ASKS the shell whether such a name exists and reports D012 when that divergence is live, rather than certifying on a model it cannot apply there. The gate fails closed.

Stated in 10 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.9 amendments | 1534 | `marker` | [Doctor tests the checker resolution the gate documents (defect `pc-87d8`)](#doctor-tests-the-checker-resolution-the-gate-documents-defect-pc-87d8) |
| v2.10 amendments | 1689 | `title` | [D012 tests the resolution AND the execution the hook will run (defects `pc-2f19`, `pc-b418`)](#d012-tests-the-resolution-and-the-execution-the-hook-will-run-defects-pc) |
| v2.10 amendments | 1748 | `marker` | [`sync`'s success report: `rechained` counts writes (defect `pc-1d45`)](#sync-s-success-report-rechained-counts-writes-defect-pc-1d45) |
| v2.11 amendments | 1832 | `title` | [D012 identifies the chain on code, not comments (defect `pc-709d`)](#d012-identifies-the-chain-on-code-not-comments-defect-pc-709d) |
| v2.12 amendments | 2066 | `marker` | [A capability detector asks the question the runtime asks (defect `pc-acaf`)](#a-capability-detector-asks-the-question-the-runtime-asks-defect-pc-acaf) |
| v2.13 amendments | 2318 | `marker` | [The doctor surface, and what a D-code asserts (defects `pc-cbda`, `pc-af3d`)](#the-doctor-surface-and-what-a-d-code-asserts-defects-pc-cbda-pc-af3d) |
| v2.14 amendments | 2592 | `marker` | [D012 derives its program names from the files that get executed (defect `pc-a2da`)](#d012-derives-its-program-names-from-the-files-that-get-executed-defect-p) |
| v2.15 amendments | 2779 | `marker` | [D012 reads an `env -S` string as env reads it (defect `pc-fc1e`)](#d012-reads-an-env-s-string-as-env-reads-it-defect-pc-fc1e) |
| v2.16 amendments | 3063 | `marker` | [D012 resolves every relative path against one base (defect `pc-2745`)](#d012-resolves-every-relative-path-against-one-base-defect-pc-2745) |
| v2.17 amendments | 3322 | `marker` | [The hook asks for the file it can run, not for what the shell would run (defect `pc-7acd`)](#the-hook-asks-for-the-file-it-can-run-not-for-what-the-shell-would-run-d) |

### E000

cannot-run — the checker could not read a timeline at all (no `.pecia/`, a fresh clone with nothing published, an unreadable log). Fatal, and distinct from a finding: it reports that no judgement was possible, never that the ledger is clean.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.3 amendments | 532 | `marker` | [The code namespaces are allocation-tracked, and the registry is generated](#the-code-namespaces-are-allocation-tracked-and-the-registry-is-generated) |

### E001

the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON within the declared nesting bound and the canonical domain (integer numbers within ±(2^53−1) and never -0, ASCII and unique object keys, valid Unicode), no blank lines, fields and edges typed and shaped; every finding carries its revision, or its source line where the revision cannot identify it, and the condemned value is bounded so the identity tag cannot be truncated away.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 14 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.6 amendments | 990 | `marker` | [E001 refuses `no_edges` beside a real edge (defect `pc-0c54`)](#e001-refuses-no-edges-beside-a-real-edge-defect-pc-0c54) |
| v2.7 amendments | 1012 | `marker` | [E001 refuses an empty edge target; E003's scan is total (defect `pc-94d2`)](#e001-refuses-an-empty-edge-target-e003-s-scan-is-total-defect-pc-94d2) |
| v2.7 amendments | 1030 | `marker` | [Extension changes the diff could not see: dotted names refused, null](#extension-changes-the-diff-could-not-see-dotted-names-refused-null) |
| v2.8 amendments | 1254 | `marker` | [E001 findings carry revision identity (defect `pc-3bdc`)](#e001-findings-carry-revision-identity-defect-pc-3bdc) |
| v2.8 amendments | 1269 | `marker` | [E001 refuses an empty top-level field name (defect `pc-2d40`)](#e001-refuses-an-empty-top-level-field-name-defect-pc-2d40) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |
| v2.10 amendments | 1599 | `marker` | [E001 admits strict JSON tokens only (defect `pc-6af9`)](#e001-admits-strict-json-tokens-only-defect-pc-6af9) |
| v2.10 amendments | 1728 | `marker` | [The two checker routes share one line discipline (defect `pc-5127`)](#the-two-checker-routes-share-one-line-discipline-defect-pc-5127) |
| v2.12 amendments | 1967 | `title` | [E001's id and date anchors are full matches (defect `pc-9726`)](#e001-s-id-and-date-anchors-are-full-matches-defect-pc-9726) |
| v2.12 amendments | 1983 | `marker` | [The line unit is the LF-delimited physical line (defect `pc-d96d`)](#the-line-unit-is-the-lf-delimited-physical-line-defect-pc-d96d) |
| v2.12 amendments | 2011 | `marker` | [The format declares a nesting bound (defect `pc-2e2f`)](#the-format-declares-a-nesting-bound-defect-pc-2e2f) |
| v2.12 amendments | 2041 | `marker` | [A record-contract finding is identified by its revision, or failing that its line (defect `pc-b60a`)](#a-record-contract-finding-is-identified-by-its-revision-or-failing-that-) |
| v2.16 amendments | 2891 | `marker` | [A diagnostic's contract is not at one end of it (defects `pc-eed1`, `pc-6b00`, `pc-1130`)](#a-diagnostic-s-contract-is-not-at-one-end-of-it-defects-pc-eed1-pc-6b00-) |
| v3.0 amendments | 3460 | `marker` | [E001 and the canonical form: RFC 8785 over a narrowed domain (defects `pc-ddd9`, `pc-71fb`)](#e001-and-the-canonical-form-rfc-8785-over-a-narrowed-domain-defects-pc-d) |

### E002

a repeated (id, rev) anywhere in the timeline, grouped over every well-typed pair (v2.7), identical or divergent content alike — the log is corrupt; see E013.

Stated in 4 sections:

| where | line | authority | section |
|---|---|---|---|
| base | 301 | `marker` | [8. Invariants (the checker contract)](#8-invariants-the-checker-contract) |
| v2.6 amendments | 786 | `marker` | [The vocabulary registry's meanings track amendments; the specs stay](#the-vocabulary-registry-s-meanings-track-amendments-the-specs-stay) |
| v2.7 amendments | 1229 | `marker` | [E002 fires on a repeated `(id, rev)` anywhere — the sound-only](#e002-fires-on-a-repeated-id-rev-anywhere-the-sound-only) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |

### E003

edge targets must exist or be declared planned; checked on heads only, and the scan is total — an empty-string scalar is E001's finding, never silently skipped.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 2 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.7 amendments | 1012 | `marker` | [E001 refuses an empty edge target; E003's scan is total (defect `pc-94d2`)](#e001-refuses-an-empty-edge-target-e003-s-scan-is-total-defect-pc-94d2) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |

### E004

a cycle in the `blocks` ∪ `parent` ∪ `retires` scheduling subgraph, reported with its whole cyclic component (v3.4): a write that leaves every cyclic component the same or smaller introduces no new E004.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 3 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.6 amendments | 786 | `marker` | [The vocabulary registry's meanings track amendments; the specs stay](#the-vocabulary-registry-s-meanings-track-amendments-the-specs-stay) |
| v2.11 amendments | 1846 | `title` | [E004 anchors only on records a cycle passes through (defect `pc-86f9`)](#e004-anchors-only-on-records-a-cycle-passes-through-defect-pc-86f9) |
| v3.4 amendments | 3733 | `marker` | [E004 names each cycle's whole component, and a write that grows none adds no cycle (defect `pc-c69d`)](#e004-names-each-cycle-s-whole-component-and-a-write-that-grows-none-adds) |

### E005

illegal status transition (legal: open→in-progress↔open, open/in-progress→done|dropped|superseded; terminal states are final — reopening means a new record with `discovered_from` the old one).

_No section of `format-v2.md` allocates or announces this code._ Base
section 8 lists it as retained from v1 and says nothing further, so its
ENTIRE contract is in `spec/format-v1.md` and nothing in v2 narrows it.
It carries no allocation marker either, so every generated view of the
source omits it in silence (`pc-0aaa`).

### E006

a head in a terminal status (done, dropped, superseded) without a non-empty disposition; a state check on heads (v1.4): a repaired head is legitimately clean, and custody of the violating revision stays with audit's historical-custody-violation.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v2.9 amendments | 1460 | `marker` | [E006 and E008 get usable registry meanings; a serving gloss may not be a fragment (defect `pc-fea8`)](#e006-and-e008-get-usable-registry-meanings-a-serving-gloss-may-not-be-a-) |

### E007

a done defect needs executable-shaped evidence or a declared reference; a warning-severity scan flags machine-local argument paths (v2.5; =-joined values v2.6).

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 3 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.5 amendments | 696 | `marker` | [E007 arguments — the portability scan reaches past the first token](#e007-arguments-the-portability-scan-reaches-past-the-first-token) |
| v2.6 amendments | 927 | `marker` | [E007's argument scan: scope stated, `=`-joined values covered](#e007-s-argument-scan-scope-stated-joined-values-covered) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |

### E008

revision gap(s): a record's revisions must be contiguous from the minimum present rev; a gap is an error, raised per record over well-typed (id, rev) pairs.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 2 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.9 amendments | 1460 | `marker` | [E006 and E008 get usable registry meanings; a serving gloss may not be a fragment (defect `pc-fea8`)](#e006-and-e008-get-usable-registry-meanings-a-serving-gloss-may-not-be-a-) |
| v2.11 amendments | 1863 | `title` | [E008 groups over every well-typed `(id, rev)` (defect `pc-8291`)](#e008-groups-over-every-well-typed-id-rev-defect-pc-8291) |

### E009

decision lineage with more than one active head.

_No section of `format-v2.md` allocates or announces this code._ Base
section 8 lists it as retained from v1 and says nothing further, so its
ENTIRE contract is in `spec/format-v1.md` and nothing in v2 narrows it.
It carries no allocation marker either, so every generated view of the
source omits it in silence (`pc-0aaa`).

### E011

an `evidence` or `context` reference naming a scheme this repo's `resolvers:` does not declare (v1.7 for evidence, extended to `context` at v2.5); a state check on heads (v2.17) — a repaired head is legitimately clean, exactly as for E003, E006, E007, E012 and E017, and the scheme a superseded revision named is one nothing will resolve.

Base section 8 keeps this code by listing it as retained from v1 and says
nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.
Everything below narrows that definition.

Stated in 3 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.5 amendments | 714 | `marker` | [The agent-assignee fields: `anchor`, ratification, `context`](#the-agent-assignee-fields-anchor-ratification-context) |
| v2.6 amendments | 786 | `marker` | [The vocabulary registry's meanings track amendments; the specs stay](#the-vocabulary-registry-s-meanings-track-amendments-the-specs-stay) |
| v2.17 amendments | 3383 | `marker` | [E011 says what it has always done: a state check on heads (defect `pc-95d0`)](#e011-says-what-it-has-always-done-a-state-check-on-heads-defect-pc-95d0) |

### E012

a retirement promise that expired unkept: the target is non-terminal and every record claiming to retire it is terminal; anchored on the target (v1.13).

Stated in 3 sections:

| where | line | authority | section |
|---|---|---|---|
| base | 301 | `marker` | [8. Invariants (the checker contract)](#8-invariants-the-checker-contract) |
| v2.3 amendments | 532 | `marker` | [The code namespaces are allocation-tracked, and the registry is generated](#the-code-namespaces-are-allocation-tracked-and-the-registry-is-generated) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |

### E013

chain break: prev or seq wrong against the preceding entry — INTERIOR damage only (v2.6); a clean suffix truncation is E015's witness territory, not this.

Stated in 4 sections:

| where | line | authority | section |
|---|---|---|---|
| base | 301 | `marker` | [8. Invariants (the checker contract)](#8-invariants-the-checker-contract) |
| v2.6 amendments | 818 | `marker` | [E013's truncation claim narrowed; the snapshot head is the witness](#e013-s-truncation-claim-narrowed-the-snapshot-head-is-the-witness) |
| v2.7 amendments | 1198 | `title` | [The E013 base bullet is regenerated from its v2.6 narrowing](#the-e013-base-bullet-is-regenerated-from-its-v2-6-narrowing) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |

### E014

CAS violation: rev is not head+1 at its log position, or touched differs from the recomputed diff, compared as the canonically sorted array (v2.9), presence-aware on extension fields (v2.7); a malformed envelope is a finding, never a crash; the printed arrays are bounded so the finding still names non-canonical order as what it refuses.

Stated in 7 sections:

| where | line | authority | section |
|---|---|---|---|
| base | 301 | `marker` | [8. Invariants (the checker contract)](#8-invariants-the-checker-contract) |
| v2.6 amendments | 862 | `marker` | [E014: the legacy coarse form closes, and extension fields join the](#e014-the-legacy-coarse-form-closes-and-extension-fields-join-the) |
| v2.7 amendments | 1030 | `marker` | [Extension changes the diff could not see: dotted names refused, null](#extension-changes-the-diff-could-not-see-dotted-names-refused-null) |
| v2.7 amendments | 1211 | `marker` | [Checker totality: two crash classes become findings](#checker-totality-two-crash-classes-become-findings) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |
| v2.9 amendments | 1482 | `marker` | [E014's two comparisons declared precisely: canonical order, and the presence scope (defects `pc-ebd2`, `pc-9997`)](#e014-s-two-comparisons-declared-precisely-canonical-order-and-the-presen) |
| v2.16 amendments | 2891 | `marker` | [A diagnostic's contract is not at one end of it (defects `pc-eed1`, `pc-6b00`, `pc-1130`)](#a-diagnostic-s-contract-is-not-at-one-end-of-it-defects-pc-eed1-pc-6b00-) |

### E015

snapshot forked, corrupted (non-text bytes in the projection or its witness included, v2.10), or the truncation witness: recorded head in the chain, content matching; the witness blocks regeneration by every writer (v2.8). The comparison runs only over a log that read WHOLE (v2.16): against the trusted prefix a stopped read returns, the question cannot be answered, and what is emitted says the projection was not compared rather than asserting a fork.

Stated in 10 sections:

| where | line | authority | section |
|---|---|---|---|
| base | 301 | `marker` | [8. Invariants (the checker contract)](#8-invariants-the-checker-contract) |
| v2.6 amendments | 818 | `marker` | [E013's truncation claim narrowed; the snapshot head is the witness](#e013-s-truncation-claim-narrowed-the-snapshot-head-is-the-witness) |
| v2.7 amendments | 1090 | `marker` | [Repeat `init` does not launder the snapshot witness (defect `pc-0ff7`)](#repeat-init-does-not-launder-the-snapshot-witness-defect-pc-0ff7) |
| v2.7 amendments | 1153 | `marker` | [A pinned store is self-contained: the snapshot follows `PECIA_LOG_DIR`](#a-pinned-store-is-self-contained-the-snapshot-follows-pecia-log-dir) |
| v2.8 amendments | 1280 | `marker` | [A detected E015 is evidence for EVERY writer (defect `pc-c6b8`)](#a-detected-e015-is-evidence-for-every-writer-defect-pc-c6b8) |
| v2.8 amendments | 1398 | `marker` | [The registry's meanings can no longer silently rot (defect `pc-c05a`)](#the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a) |
| v2.10 amendments | 1619 | `marker` | [A malformed snapshot is E015, never a crash (defect `pc-23a1`)](#a-malformed-snapshot-is-e015-never-a-crash-defect-pc-23a1) |
| v2.11 amendments | 1907 | `title` | [E015's non-text diagnosis is independent of the witness's state (defect `pc-0d11`)](#e015-s-non-text-diagnosis-is-independent-of-the-witness-s-state-defect-p) |
| v2.11 amendments | 1922 | `title` | [E015 findings name THE file, wherever the store is (defect `pc-f61a`)](#e015-findings-name-the-file-wherever-the-store-is-defect-pc-f61a) |
| v2.16 amendments | 3017 | `marker` | [A trusted prefix is not the chain (defect `pc-1667`)](#a-trusted-prefix-is-not-the-chain-defect-pc-1667) |

### E016

closed-while-blocked — a record in a terminal status while some non-terminal record holds a `blocks` edge to it. The message names as many blockers as its budget allows and states how many there are in all; the finding's participant set carries every one. Resolve it the way the edge is wrong or the closure was early: drop the `blocks` claim that no longer holds, or close the blocker(s); the terminal record cannot be reopened (E005: terminal is final).

Stated in 2 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.4 amendments | 610 | `marker` | [E016 — a record may not go terminal while an open record blocks it](#e016-a-record-may-not-go-terminal-while-an-open-record-blocks-it) |
| v2.16 amendments | 2891 | `marker` | [A diagnostic's contract is not at one end of it (defects `pc-eed1`, `pc-6b00`, `pc-1130`)](#a-diagnostic-s-contract-is-not-at-one-end-of-it-defects-pc-eed1-pc-6b00-) |

### E017

self-edge: an edge field, list or scalar, of a HEAD that names its own record; a state check on heads (v2.9) — a repaired head is legitimately clean.

Stated in 2 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.5 amendments | 678 | `marker` | [E017 — no edge relation is reflexive](#e017-no-edge-relation-is-reflexive) |
| v2.9 amendments | 1516 | `marker` | [E017's head-state scope declared (defect `pc-c96a`)](#e017-s-head-state-scope-declared-defect-pc-c96a) |

### E018

unprojectable value: a value a projection cannot place as the calendar day it needs. Three states, distinguished in the message: a date-shaped `created` or `target` on a milestone that is not a real calendar day, which the structural gates accept (E001 is shape-only); a value that is not date-shaped at all — including the empty string and any non-string, whose type the message names — which `check` refuses with E001 and which reaches a projection only through a bypassed or unrun gate; and a `created` that is ABSENT, which E001 also refuses and which the renderers read directly. Tested by PRESENCE, never truthiness: an absent `target` is the undated milestone, not a refusal. Emitted by the projection commands as an error finding at exit 1, naming the record and the field, BEFORE either rendering. NEVER emitted by `check`.

Stated in 3 sections:

| where | line | authority | section |
|---|---|---|---|
| v2.8 amendments | 1328 | `marker` | [E018 — a projection refusal is a finding, never a crash (defect](#e018-a-projection-refusal-is-a-finding-never-a-crash-defect) |
| v2.13 amendments | 2393 | `marker` | [A refusal's words are true of the value it refused (defect `pc-3942`)](#a-refusal-s-words-are-true-of-the-value-it-refused-defect-pc-3942) |
| v2.17 amendments | 3273 | `marker` | [A projection's guard discarded the states its own refusal was written for (defect `pc-5706`)](#a-projection-s-guard-discarded-the-states-its-own-refusal-was-written-fo) |

### E019

the log ends before its high-water mark: the mark records a `seq` the log does not reach, or the log's entry at that `seq` has another hash, or the mark does not parse. `check` reports it over a log that reads whole. `add`, `edit`, `close` and `init` refuse over it. `sync` refuses unless the timeline it adopts holds the marked entry, which is the recovery. `migrate` refuses unless its rebuild holds the marked entry, or `--force-drop` accepts the loss.

Stated in 1 section:

| where | line | authority | section |
|---|---|---|---|
| v3.3 amendments | 3683 | `marker` | [Ordinary writes leave the projection to `snapshot`; the log carries a high-water mark (decision `pc-25cca4980c47`, decided by Noah)](#ordinary-writes-leave-the-projection-to-snapshot-the-log-carries-a-high-) |

---

## Part 4 — The amendments in force, in document order

Every amendment section, carried by position. A section appears here whether or
not any marker indexes it, which is the whole point: half of this text is
invisible to the index (`pc-4f6a`).

### v2.3 amendments (2026-08-27 — how the format's own vocabulary is

<a id="the-code-namespaces-are-allocation-tracked-and-the-registry-is-generated"></a>

_v2.3 amendments, source line 532 — marks `E000`, `E012`._

Record ids are hash-minted with a nonce precisely so two agents on two
branches cannot collide — a lesson taken from Backlog.md at v0. It was
applied once, to the ids, and never generalised. Both other hand-assigned
namespaces then collided for real: two independent `E012`s (§8), and two
independent `v1.8`s, each discovered at merge rather than at authoring.

From v2.3, **every allocation event in the E-code namespace is declared in
the spec, in a machine-readable marker beside the prose that defines it**:

```
<!-- vocab: {"action":"mint","code":"E012","at":"v1.13","by":"pc-4d19"} -->
```

`action` is one of `mint`, `amend`, `delete`, `renumber`. A `delete` or
`renumber` names the allocation it retires with `of`, the minting anchor —
because **an allocation is a code claimed by a particular minter, not a code
alone.** That distinction is the whole mechanism: `E012` was claimed twice,
by `pc-4d19` and by `pc-0033`, and a model keyed on the number alone cannot
represent two simultaneous claims on one number, which is the only state
worth detecting.

`spec/vocabulary.json` is **generated** from those markers by
`dev/vocab-check.py`, never authored. It is a projection of this document,
so it cannot contradict it. Two rejected designs are recorded in that
script's docstring, because both looked like tuning problems from the inside
and neither was: inferring intent from prose adjectives, and authoring a
registry keyed by code — where duplicate JSON keys silently collapse, making
"no two entries share a code" true by construction.

**`E000` is registered here.** It was emitted by the implementation and
defined in neither spec — an unspecified code in the normative namespace,
found by the gate built for exactly that class, on its first run.

- **E000 cannot-run** — the checker could not read a timeline at all
  (no `.pecia/`, a fresh clone with nothing published, an unreadable log).
  Fatal, and distinct from a finding: it reports that no judgement was
  possible, never that the ledger is clean.
  <!-- vocab: {"action":"mint","code":"E000","at":"v2.3","by":"pc-e9cf"} -->

**D-codes** (`doctor`'s diagnostics) are tracked in the same registry and are
**explicitly NOT part of this format contract.** No adapter or sibling tool
may depend on a D-code's meaning; `doctor` reports posture, and rule 1
already says posture is never correctness. Internal tests may bind them, and
do — `DoctorPosture` asserts the cross-tree case reports D008 and not D004 —
so the narrower claim that nothing depends on them would be false.

<a id="records-carry-no-format-version-pc-75ae-answered"></a>

_v2.3 amendments, source line 580 — allocates no code._

§11 listed this as "not decided here". It is decided now, and the position is
the one that record argued for itself:

**A record carries no format-version field. The checker is authoritative, and
history is judged by current invariants.**

This keeps `check` a pure function of the ledger — the property v1.14 was
spent restoring — and it makes custody violations *visible* rather than
grandfathered. The alternative, versioning each record, buys the ability to
judge an old record by old rules, which is precisely the ability this ledger
does not want: a record written under a weaker rule is not thereby correct,
and `audit`'s historical-custody-violation surface exists to say so.

`REQUIRED_FIELDS` is unchanged. `spec/record.schema.json` moves to **2.3** as
an assertion of review, not of shape change, exactly as v1.9 established and
v2.1 exercised.

**Stated plainly, because rule 4 applies to process too:** this position was
resolved from the record's own pre-argued case and a general go-ahead, not
from a `DECIDED by Noah` on the question itself. That is a weaker warrant
than every other decision in this ledger carries, and it is recorded as such
rather than borrowing an authority that was not given.

---

### v2.4 amendments (2026-08-31 — the `blocks` invariant gets a write gate,

<a id="e016-a-record-may-not-go-terminal-while-an-open-record-blocks-it"></a>

_v2.4 amendments, source line 610 — marks `E016`._

The ledger has enforced the analogous promise for `retires` since v1.13:
E012 fires when every claimant of an open target has gone terminal. The
`blocks` edge had no equivalent. `closed-while-blocked` existed, but only as
an `audit` finding — computed from the reverse index, advisory by
construction, and `audit` is not among the gates `pre-commit` runs. It could
describe the violation and never refuse it.

The asymmetry was invisible at the point of use, which is why it survived.
**A `blocks` edge is stored on the BLOCKER, not on the record it blocks.** A
record with an open blocker therefore has `edges.blocks == []` of its own,
and both a human and an agent running `close` see a record with nothing
holding it. The live instance is the founding one: `pc-72c8` (open, a
question about whether `publish` and `sync` can report a chain head at all)
has blocked `pc-0033` (v2 storage, whose push path is the thing that
question governs) since 2026-08-07. `pc-0033` closed 2026-08-12. No gate
fired, because none existed.

- **E016 closed-while-blocked** — a record in a terminal status while some
  non-terminal record holds a `blocks` edge to it, naming every blocker.
  Resolve it the way the edge is wrong or the closure was early: drop the
  `blocks` claim that no longer holds, or close the blocker(s); the
  terminal record cannot be reopened (E005: terminal is final).
  <!-- vocab: {"action":"mint","code":"E016","at":"v2.4","by":"pc-0aa2"} -->
  <!-- vocab: {"action":"amend","code":"E016","at":"v2.15","by":"pc-d935"} -->

**Anchored on the BLOCKED record, not the blocker**, which is the opposite of
E012's anchor and for a reason that does not transfer. E012 reads from the
target because several records may each promise to retire one item, and
reading from the claimants would redden the first one to do its share.
`blocks` has no such fan-in problem: one open blocker is sufficient on its
own, so the finding belongs where the violation is — on the record that
closed.

**Scope, stated because the neighbouring edges are deliberately excluded.**
`compute_blockers` also treats an open child as blocking its parent
milestone's completion, and treats `retires` as blocking. E016 covers
`blocks` alone. Closing a milestone over an open child is a descope, which
is a decision and not a defect, and `retires` is already E012's. Widening
E016 to the full reverse index would convert two legitimate acts into errors
in order to catch a third — and `audit`'s broader advisory surface still
reports all three.

**This needs no new write-gate machinery, and that is the point.** Since v1.4
the write gate IS the checker (review-3 F12): a write is refused iff it
introduces a new error finding, so every E-code is a write gate the day it
is minted. Minting E016 closes the hole at `close`, at `edit`, and at
`check`, in one move, with no second code path to drift.

**It goes red on the live ledger the day it lands**, on the `pc-0033` case
above, and that is the intended behaviour rather than a migration problem:
§11 of this document already settled that history is judged by current
invariants and that custody violations are made visible rather than
grandfathered. A record written under a weaker rule is not thereby correct.
Note the write gate refuses only NEWLY introduced errors, so the standing
violation blocks no unrelated write — it blocks `check`, which is exactly
the surface that should refuse to call this ledger clean.

### v2.5 amendments (2026-09-07 — the v1 sitting: self-edges refused, evidence

<a id="e017-no-edge-relation-is-reflexive"></a>

_v2.5 amendments, source line 678 — marks `E017`._

v1.3 banned self-edges outright. v1.4 replaced the enumerated write gate
with check-diff — deliberately, so the gate and the checker cannot drift —
and in the rebuild the ban silently narrowed to the one case E004 can see,
the trivial cycle in its blocks∪parent subgraph. A custody self-edge (a
record discovered from itself, caused by itself, superseding itself)
remained representable and meaningless: E003 is satisfied because the
target exists (it is the record), and E004 never looks at scalar custody
edges. Demonstrated 2026-08-07: `edit pc-a --caused-by pc-a` was accepted
and `check` exited 0. Neither the v1.4 amendment nor any later one recorded
the narrowing; this one does, and closes it.

- **E017 self-edge** — an edge field, list or scalar, that names its own
  record. An error, and therefore a write gate the day it is minted (the
  v1.4 rule: a write is refused iff it introduces a new error finding).
  <!-- vocab: {"action":"mint","code":"E017","at":"v2.5","by":"pc-40b4"} -->

<a id="e007-arguments-the-portability-scan-reaches-past-the-first-token"></a>

_v2.5 amendments, source line 696 — marks `E007`._

E007 certified the evidence command's first token and never inspected the
arguments, so a command whose arguments name absolute machine-local paths —
the demonstrated case cites a sibling repository's file by absolute path,
twice — passed as clean while being unrunnable on any host but the one that
wrote it (`pc-0a66`).

Amended: argument tokens that are absolute-path-shaped (`/…`, `~…`) or
parent-escaping (`../`) draw an E007 finding at severity **warning**, never
error. Warning for two stated reasons: the scan is a heuristic over strings
and stays hermetic (nothing reads the host — the v1.14 rule is untouched),
and the standing corpus carries such records as custody that cannot be
edited, so an error would permanently redden `check` over history no write
can repair. The truth half — whether the command actually runs — stays with
`audit` (`unresolvable-evidence`), as split at v1.14.
  <!-- vocab: {"action":"amend","code":"E007","at":"v2.5","by":"pc-0a66"} -->

<a id="the-agent-assignee-fields-anchor-ratification-context"></a>

_v2.5 amendments, source line 714 — marks `E011`._

Three optional fields from the agent-assignee milestone (`pc-214b`), all
answering the same structural fact: an item assigned to a cold agent differs
from one assigned to a human, who remembers what is stale, negotiates
authority socially, and was present for the context. Rule 3 governs all
three — none is ever required on input, because a required generative field
gets fabricated to order and these exist to be trustworthy.

**`anchor` / `anchor_dirty`** (decision `pc-05e4`, closing `pc-e039`).
Every revision the write path appends is stamped with the commit it was
written against — `anchor`, forty hex characters from `git rev-parse HEAD`
— so a cold agent runs `git diff <anchor>..HEAD` and sees exactly what
moved under the record since it was written. This is the subtractive case
the practice catalog's scope warning carves out: the value is derived by
the substrate at write time, costs the author nothing, and is rejected as
an authored input by shape (E001 checks the hex form; the CLI offers no
flag). Absent outside a git worktree and on an unborn branch.
`anchor_dirty: true` (presence, not a toggle — the `forced` convention)
records that the worktree carried uncommitted **tracked** changes at write
time, so the diff under-reports what the writer saw: recorded, never
refused. Like the force brand, the anchor is per-revision custody — a
revision re-chained by `sync` keeps the writer's anchor, never the base's,
and each new revision names its own commit rather than inheriting.

**`ratified_by` / `ratified`** (decision `pc-813f`, closing `pc-ff8f`).
`owner` makes machine authorship visible; what was missing is whether a
human ACCEPTED an agent-authored decision — months later, an agent's
recommendation sitting in the ledger reads exactly like the user's own
call. The mechanism is an explicit **ratifying revision**: `edit <id>
--ratify` stamps `ratified_by` (the invoking identity — `PECIA_OWNER` or
the login user, deliberately not `--owner`, which edits authorship) and a
`ratified` date. The pair travels together, is legal only on `decision`
records (E001 otherwise — other types have evidence, which is stronger),
and is never required at creation. The absence is surfaced, not refused:
`audit` reports `unratified-decision` for every **terminal** decision whose
owner is vendor-qualified (a `:` in the owner string, the AGENTS.md
convention) and that no ratifying revision has touched. Both fields join
the `touched` vocabulary, so a ratifying revision is never a sync no-op and
concurrent ratifications conflict like any other same-field appends.

**`context`** (decision `pc-813f`, closing `pc-7ab3`). "Fix the trap bug"
suffices for a human who was there; a cold agent needs orientation, and the
alternative to shared orientation is a four-hundred-line body on every
item. The two-tier shape is item + shared orientation document, with the
item REFERENCING the document: `context` carries a foreign reference,
normally `doc:<path>#<anchor>`, resolved through the v1.7 resolver registry
like any other scheme. Nothing is primed and nothing is fetched at session
start — the constraint carried from v1's harness-integration section is
that primed context measurably decays across compaction, so the tier stays
pull-on-demand at the point of need. `check` verifies shape and scheme
declaration; `audit` runs the resolver and reports `unresolvable-context`
when the document is not where the record says; `board` marks it
`reference-not-attempted` like every declared reference, because a human
projection never executes resolvers (D9).

- **E011 amended**: the `context` field's reference must name a declared
  scheme, under exactly the rule evidence references have carried since
  v1.7 — an undeclared scheme is never a silent pass. The reference
  grammar's target charset gains `#` in the same amendment, so one grammar
  serves both fields; `#` was previously unreachable in evidence targets
  and nothing changes for them.
  <!-- vocab: {"action":"amend","code":"E011","at":"v2.5","by":"pc-7ab3"} -->

### v2.6 amendments (2026-09-09 — the round-2 fix sitting: closing the

<a id="the-vocabulary-registry-s-meanings-track-amendments-the-specs-stay"></a>

_v2.6 amendments, source line 786 — marks `E002`, `E004`, `E011`._

canonical for meaning (defect `pc-439d`)

Round 1 read `spec/vocabulary.json` carrying v1's meaning for E002 (the
`merge=union` warning) after v2 re-founded it as a corruption error, with
E004's gloss predating `retires` and E011's predating `context` — the
registry captured a gloss at **mint** only, so amendments moved semantics
the registry never followed. The registry is **generated** (v2.3) and stays
generated: an authored meanings register held against spec prose would
require reading English, the first design v2.3 already rejected. The
staleness is fixed mechanically instead — a gloss travels with `amend`
events exactly as with mints, and the registry gains a `meanings` view
holding the **latest** gloss per live code, byte-compared like the rest.
Authority, stated so it cannot be read backwards again: **the specs are
canonical for meaning; `vocabulary.json` is their projection and may not be
cited against them.** (The deletion guard's "canonical file" wording names
protection — a pinned gate input — never semantic authority.) The three
stale glosses are re-anchored here:

- **E002** — a repeated `(id, rev)` anywhere in the timeline, identical or
  divergent content alike: an error meaning the log is corrupt, since a
  CAS-admitted log cannot contain one (re-founded at v2). Emitted as its
  own finding beside the chain diagnostics, never folded into them.
  <!-- vocab: {"action":"amend","code":"E002","at":"v2.6","by":"pc-439d"} -->
- **E004** — a cycle in the `blocks` ∪ `parent` ∪ `retires` scheduling
  subgraph (v1.13 added `retires`; the mint-time gloss predated it).
  <!-- vocab: {"action":"amend","code":"E004","at":"v2.6","by":"pc-439d"} -->
- **E011** — an `evidence` or `context` reference naming a scheme this
  repo's `resolvers:` does not declare (v1.7 for evidence, extended to
  `context` at v2.5).
  <!-- vocab: {"action":"amend","code":"E011","at":"v2.6","by":"pc-439d"} -->

<a id="e013-s-truncation-claim-narrowed-the-snapshot-head-is-the-witness"></a>

_v2.6 amendments, source line 818 — marks `E013`, `E015`._

(defects `pc-905e`, `pc-ed3e`)

§8 said E013 "detects truncation, splicing, and any out-of-band edit of the
log." That overstated it in exactly one direction: a **suffix** truncation
leaves an internally valid prefix, and a valid prefix is a valid chain, so
E013 structurally cannot fire on it. The claim is narrowed rather than the
evidence stretched: E013 detects **interior** damage — a broken `prev`, a
`seq` out of order, a splice, an edit. Custody against suffix truncation
comes from two witnesses outside the chain: locally, `.pecia/snapshot.head`
(a snapshot derived from entries the log no longer carries is a fork by
E015's own test); at the published layer, §5's prefix guard. A local store
whose log, snapshot and head are all regenerated consistently is
indistinguishable from an earlier state of the timeline **by construction**
— local custody cannot exceed that, and the published ref is the anchor.
  <!-- vocab: {"action":"amend","code":"E013","at":"v2.6","by":"pc-905e"} -->

Because the head file is that witness, E015 stops treating its absence or
blankness as an off switch (round 1 demonstrated both: a blank head made
arbitrary forged snapshot content certify clean, and deleting the head made
a truncated log invisible):

- A **blank** head records derivation from the **empty prefix** — the state
  `write_snapshot` emits for an empty timeline — so the content check runs
  against that prefix: an empty snapshot is clean, content is E015.
- An **absent** head beside a snapshot **carrying content** is E015: the
  binding that lets stale, forked and truncated be told apart is gone, and
  the remedy is `pecia snapshot`. An absent head beside an empty projection
  binds nothing and stays clean.
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.6","by":"pc-ed3e"} -->

<a id="the-verdict-is-a-function-of-the-repository-never-of-the-invocation"></a>

_v2.6 amendments, source line 849 — allocates no code._

directory (defect `pc-3690`)

The timeline already resolved through the nearest `.git`; the config and
snapshot paths resolved through the bare cwd, so `check`'s verdict moved
with where you stood — from a repository subdirectory a declared `context`
drew a false E011 and a forked root snapshot passed clean. The root is now
resolved once, as the git toplevel when inside a work tree (falling back to
the cwd outside any — the state `--ledger` and `PECIA_LOG_DIR` serve), and
every repo-relative path derives from it. This is also the root rule any
port (the MCP surface included) inherits: resolve the repository once, then
derive.

<a id="e014-the-legacy-coarse-form-closes-and-extension-fields-join-the"></a>

_v2.6 amendments, source line 862 — marks `E014`._

derived `touched` set (defect `pc-c7fe`)

Two holes in §4.1's derived-not-authored guarantee, closed together.

**(a) There is no coarse acceptance.** Entries written before edge
subfields became separate conflict units (pc-4924) declared `edges` where the
recomputation yields `edges.blocks` etc. An exactly-coarse declaration is E014
like any other mismatch with the derivation: no write path produces one, so it
can only be authored. (This amendment originally grandfathered the eight such
entries in the reference log by the SHA-256 of each whole entry, which put
entry hashes into the implementation as identifiers and tied the code to the
byte format. On 2026-09-21 those eight were rewritten to the derived form and
the exemption deleted, `pc-c6a9`. Implementations carry no legacy path.)

**(b) The diff runs over the union of both revisions' top-level keys**, not
a fixed whitelist. The per-revision machinery — `rev`, `updated`, the
provenance/brand fields the substrate re-stamps (`anchor`, `anchor_dirty`,
`forced`), and `edges` itself (its subfields are the units) — is excepted;
everything else that differs, schema-permitted extension fields included,
is part of the derived set. Before this, an `x_*` change beside an empty
declaration was invisible to E014 and to §5's conflict test.
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.6","by":"pc-c7fe"} -->

Stating the malformation contract while this gate is open (`pc-2675`): a
`touched` value that is not a JSON array is an **E014 finding naming the
malformation**, at exit 1 — never a fatal E000. The checker is total: it
reports, it never crashes (the v1.5/F19 rule, applied to the envelope).
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.6","by":"pc-2675"} -->

<a id="the-formal-model-widens-to-the-invariants-and-conflict-units-the-code"></a>

_v2.6 amendments, source line 892 — allocates no code._

grew (defect `pc-af81`)

Round 1 measured the Alloy model's four field atoms against the code's 21
conflict units and none of the v2.4/v2.5 invariants — the formal gate could
not turn red when the omitted semantics regressed, and the round's own
escaped defect (`pc-76b5`, clearing `context` under a concurrent disjoint
edit) sat in an omitted field. Extended at this sitting:

- **Conflict units**: `retires` (v1.13's scheduling edge — it joins the
  dependency graph, E003, E017 and the blockers computation) and `context`
  (the optional field whose cleared state is absence) join the modelled
  set, in `diff`, the re-chain construction, and both companions.
- **Invariants**: `e012clean` (the expired retirement promise, anchored on
  the target), `e016clean` (closed-while-blocked, anchored on the blocked
  record) and `e017clean` (no reflexive edge) join `clean`, with the write
  gate's corresponding refusals in `writeOk` — including the drop-side E012
  guard the model itself demanded (PublishPreservesClean produced the
  counterexample when `clean` grew before `writeOk` did).
- **The escaped case is a theorem**: `RechainPreservesClearing` — clearing
  an optional field commutes with a concurrent disjoint edit and re-chains
  as absence — with a non-vacuity run beside it. The JSON null-vs-absent
  representation defect that actually escaped sits below this abstraction
  and stays pinned by the fixture tests.
- **The null arm grows**: trap 4 (`BrandSurvivesRechain`) pins the
  brand-dropping re-chain (`pc-f7fc`) as a permanent expected
  counterexample — the distinct obligation the dropped V10 (`pc-9a63`)
  claimed to carry.

What remains uncovered is stated in the model's own `Field` comment and in
`claims.yaml`'s formal-model notes, which carry the same list and move
together: the content scalars, the custody scalar edges, the ratification
pair, `no_edges`, and `anchor`/`anchor_dirty` (deliberately not conflict
units).

<a id="e007-s-argument-scan-scope-stated-joined-values-covered"></a>

_v2.6 amendments, source line 927 — marks `E007`._

(defect `pc-1871`)

v2.5's flat "argument tokens that are absolute-path-shaped … draw an E007
finding" overstated a heuristic: `--root=/Users/alice/private` passed
silently because only whole tokens were inspected. The scan now also
inspects the **value half of an `=`-joined token**, and its scope is stated
rather than implied: it is a heuristic over the string forms `TOKEN` and
`OPT=TOKEN` where TOKEN is absolute-path-shaped (`/…`, `~…`) or
parent-escaping (`../`, `/../`). Other encodings — quoted concatenations,
environment indirection, paths built at runtime — are out of scope, which
is one of the two reasons it stays a **warning**, never an error (the
other: the standing corpus carries such records as custody no write can
repair). The truth half stays with `audit`, per the v1.14 split.
  <!-- vocab: {"action":"amend","code":"E007","at":"v2.6","by":"pc-1871"} -->

<a id="one-timeline-per-store-the-publication-ref-is-what-serializes-stores"></a>

_v2.6 amendments, source line 943 — allocates no code._

(defect `pc-099b`)

Rule 5's "a project has exactly one work ledger" is made precise, because
it was violable as written under a documented override. **The unit the
local layer guarantees is the store**: one timeline per log directory,
append-only under every write path but reconciliation (§3.1, v2.14). The store is bound to a repository by residing under its
`--git-common-dir` — one store per clone, shared by its worktrees — and
`PECIA_LOG_DIR` **declares** a different store rather than discovering one
(§ "Still outstanding", pc-dd71). Declaring two stores for one repository
therefore creates two locally-clean timelines, and nothing at the store
layer can see that; **the publication layer is what serializes them**: both
race for one `refs/pecia/log`, the first publish wins, and the second is
refused as a fork with nothing merged silently. That refusal's prescribed
recovery is `pecia sync`, which now exists in the offline case too: with no
remote configured, `sync` reconciles against the **local** publication
register `refs/pecia/log` — the very ref the refused publish raced — using
the identical re-chain machinery. What is deliberately **not** claimed: a
store is not cryptographically bound to a repository identity, so a
deliberately mis-declared `PECIA_LOG_DIR` can still address the wrong
store's content at the working layer; the publication boundary remains the
line nothing crosses unserialized.

<a id="migrate-checks-what-it-writes-and-promotion-carries-the-brand"></a>

_v2.6 amendments, source line 966 — allocates no code._

(defects `pc-0fa7`, `pc-cce9`)

Two custody holes in the one command that rebuilds the timeline.

- **The output is checked before it is written** (`pc-0fa7`). `migrate` ran
  `cas_admissible` and a read-back over what it wrote but never the full
  checker, so a normal unforced bootstrap exited 0 while creating a
  canonical log the very next `check` rejects. The candidate timeline is
  now held to the same full pass `publish` and `sync` already use; on
  findings, nothing is written, the findings are the output, and the exit
  is 1.
- **Promotion carries the brand** (`pc-cce9`). When an existing canonical
  log is replaced (`--force`, with `--force-drop` accepting the drop of
  log-only entries), any record-revision entering the rebuild that the
  canonical log did not hold byte-for-byte is content becoming authority
  without ever passing the write gate — the same escape-hatch shape as
  `--force` on a write, so every such entry is branded `forced: true` and
  the count is reported (`promoted_branded`). The custody principle the V7
  and V10 theorems state for `publish` — escape-hatch uses are enumerable
  from the ledger alone — now covers the migrate path too. A plain
  bootstrap brands nothing: with no existing log there is no canonical
  content to promote over.

<a id="e001-refuses-no-edges-beside-a-real-edge-defect-pc-0c54"></a>

_v2.6 amendments, source line 990 — marks `E001`._

`no_edges` declares the **absence** of the other edge fields — the reading
v2's own sync conflict rule already enforces (`touched_conflicts`, corrected
at pc-7f4e) — yet an ordinary local `edit <id> --no-edges` on a record
holding a real edge produced a record asserting both, and `check` accepted
it. The record contract now states the semantic half: a record carrying
`edges.no_edges: true` beside any non-empty edge field violates E001. The
write gate is the checker (v1.4), so the same mint refuses the state at
`edit`, `close` and `check` in one move. History scanned at this amendment:
zero stored records carry the contradiction, so no cutover is needed.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.6","by":"pc-0c54"} -->

### v2.7 amendments (2026-09-11 — the round-3 fix sitting: closing the

<a id="e001-refuses-an-empty-edge-target-e003-s-scan-is-total-defect-pc-94d2"></a>

_v2.7 amendments, source line 1012 — marks `E001`, `E003`._

A scalar edge value of `""` was admitted by both gates at once: E001's
type check accepts every string, and E003's dangling-target scan skipped
falsy scalar values, so a reference that is not null, is not an id, and
names no existing or planned record passed `check` and the write gate
alike. The record contract now states what the help text always said — a
scalar edge is **null or names a record**; the empty string (or any
whitespace-only string) is an E001 violation, and a blank element of a
list edge is the same violation. E003's scalar scan now tests presence
(`is not None`) rather than truthiness, so the dangling scan stays total
even for values E001 has already refused. The write gate is the checker
(v1.4), so `edit <id> --caused-by ""` is refused at the write path in the
same move. `record.schema.json` adds `minLength: 1` to the scalar-edge and
list-element string types.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.7","by":"pc-94d2"} -->
  <!-- vocab: {"action":"amend","code":"E003","at":"v2.7","by":"pc-94d2"} -->

<a id="extension-changes-the-diff-could-not-see-dotted-names-refused-null"></a>

_v2.7 amendments, source line 1030 — marks `E001`, `E014`._

distinguished from absence (defect `pc-a437`)

Two holes in v2.6's union-of-top-level-keys rule (`pc-c7fe`), found
independently by two round-2 lanes. (a) The union **excluded any key
containing `.`** — `field_get` would have misread it as an `edges.*`-style
path — so a revision changing a dotted extension key passed `check` with
`touched: []`, and the correct declaration of that change was refused as
an E014 mismatch. (b) Both sides were read with `dict.get`, so **adding an
extension key with value `null` was indistinguishable from its absence** —
invisible to E014 and to `sync`'s conflict test, while its true
declaration was refused. Fixed in three moves, all one rule: a dotted
top-level key is now an **E001 violation** (the record contract names
none, and a name the diff machinery cannot address is not a legal field
name — `record.schema.json` states the same via `propertyNames`); the
union keys compare by **literal key and by presence**, so a present null
and an absent key are different states and even a forced or imported
dotted key cannot move silently; and `sync`'s re-chain applies extension
fields presence-aware, so replaying the *removal* of a key removes it
rather than writing `null`.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.7","by":"pc-a437"} -->
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.7","by":"pc-a437"} -->

<a id="the-no-op-skip-excludes-creations-a-divergent-rev-1-sharing-a"></a>

_v2.7 amendments, source line 1053 — allocates no code._

published id is a conflict (defect `pc-e499`)

§5.4's no-op rule said an empty `touched` set is already true remotely and
need not be re-chained. That reading is sound only where a local
predecessor exists to be unchanged FROM. A rev-1 **creation** derives
`touched: []` by construction, so two individually clean stores creating
the same valid id with different content composed as: the publish CAS
correctly refuses the second writer, and the prescribed recovery — `pecia
sync` — classified the divergent creation as a no-op, skipped it, reported
`synced: true`, and the losing intent was gone from the losing store's own
log. Amended: the skip applies only to revisions with a local predecessor;
a creation whose content equals the remote head byte-for-byte over the
conflict units is a true no-op and still skips, and a creation that
differs is surfaced as a conflict with the differing fields named, exit 1,
nothing written — the same contract as a same-field conflict.

<a id="migrate-damage-does-not-disable-the-orphan-guard-and-replacement"></a>

_v2.7 amendments, source line 1070 — allocates no code._

of an empty log still carries the brand (defects `pc-e520`, `pc-14b2`)

Two boundary failures of the same command, both against v2.6's own
promotion rules. **`pc-e520`:** the log-only-orphan guard (`pc-824a`) ran
only over a fully-read log, and a blank physical line is an E001 finding
that stops `read_log` at that line — so exactly the damaged state that
most needs the guard disabled it, and `migrate --force` erased every
log-only revision past the damage without `--force-drop`. Amended: a log
that exists but cannot be read cleanly refuses the rebuild outright —
entries past the damage cannot be verified as preserved — until the log
is fixed or `--force-drop` accepts the loss deliberately. **`pc-14b2`:**
the `forced: true` promotion brand (`pc-cce9`) was conditioned on the
replaced log holding content, so a `--force` replacement of an
initialized **empty** log — an existing log by migrate's own refusal —
promoted snapshot-born records to sole authority with no brand and
`promoted_branded: 0`. Amended: replacement is a fact about the file, not
its content; every record-revision the replaced log did not hold
byte-for-byte is branded, an empty log holding none of them.

<a id="repeat-init-does-not-launder-the-snapshot-witness-defect-pc-0ff7"></a>

_v2.7 amendments, source line 1090 — marks `E015`._

`init` regenerated the snapshot unconditionally from whatever the log
held, so a detected E015 — §8's designed witness of a suffix-truncated
log, the one loss E013 structurally cannot see — became a clean state
after an ordinary, documented, re-runnable command, and in an unpublished
store the removed revision had no remaining witness anywhere. Amended, in
three parts. **`init`** on an existing timeline refuses when the log does
not read back cleanly or the snapshot disagrees with the chain (E015):
the disagreement is evidence, and a bootstrap command repairs nothing. A
merely **stale** projection stays fair game — stale is clean (§6) and
repeat init still advances it. **`init`** with no log at all beside a
record-carrying snapshot — the ordinary fresh-clone shape, since the
snapshot is tracked and the log is never fetched (D009) — refuses too,
pointing at `pecia sync`, instead of burying the records under an empty
timeline. **`pecia snapshot`**, the deliberate remedy, refuses exactly
one state: the truncation signature (recorded head nowhere in the chain
while the log's records are a proper prefix of the snapshot's), where the
snapshot is the only witness of the missing suffix; every other E015
flavour keeps it as the remedy the diagnostic names.
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.7","by":"pc-0ff7"} -->

<a id="the-trap-companion-s-vocabulary-is-the-shipped-one-private-clean"></a>

_v2.7 amendments, source line 1112 — allocates no code._

predicates refused (defect `pc-5fba`)

`spec/pecia-v2-traps.als` kept private `e003cleanLines`/`e004cleanLines`/
`e006cleanLines`/`cleanLines` over line sets: E003/E004 there ignored
`retires`, and E005, E008, E012, E016 and E017 were absent entirely — so
trap 3 was judged against a definition every later invariant change
silently outgrew, and the alloy-gate companion check (pc-0c76's `enum
Field|fun diff[` pattern) could not reject it. Amended: the line-set
head/graph/invariant machinery (`headsL`, `headL`, `idsL`, `depGraphL`,
the `eNNNcleanL` family, `cleanL`) now lives in `peciaV2.als` as the one
shipped vocabulary, the Log forms are one-line wrappers over it, the
traps file imports rather than declares it, and `dev/alloy-gate.sh`
refuses any companion declaration stemming from the clean/head/ids/
depGraph family. Trap 3 now bites against the FULL invariant set, and the
gate's expected counts are 14/14 theorems and runs (grown by this same
sitting's pc-2caf planned-frontier extension); traps 0/4; seeds 0/3 —
realized locally 2026-09-11. **CORRECTED 2026-09-12 (pc-17dd):** this
line said "unchanged (13/13 theorems and runs)" while the shipped file
and `dev/alloy-gate.sh` both carried 14 — the executable counts agreed
with each other and only this prose lagged, by one, exactly the drift
class the counts-live-in-the-gate rule (pc-855f) exists to end.
`alloy-gate.sh --static` runs
the structural half without a JDK so the refusal is suite-testable.

<a id="an-invalid-published-blob-is-refused-never-normalized-in-transport"></a>

_v2.7 amendments, source line 1137 — allocates no code._

(defect `pc-13f6`)

Every consumer of a published `log.jsonl` blob — `sync`'s hydrate and
re-chain, `publish`'s prefix guard, `migrate`'s published-prefix check —
filtered blank lines away before use (and the shared git helper stripped
trailing ones besides), so an E001-invalid published artifact was
silently repaired in transport: `sync` exited 0 and hydrated a normalized
local log while the publication ref kept pointing at a physically
different, contract-violating blob, and the prefix guards compared
against a fiction — `pc-e7f0`'s shape, one layer down. Amended: the blob
is read unstripped, a blank line anywhere in it (or an unparseable line)
is a loud refusal naming the line, and nothing is hydrated, published
over, or rebuilt against it until the published timeline is repaired
(`migrate --force-drop` remains the deliberate rewrite).

<a id="a-pinned-store-is-self-contained-the-snapshot-follows-pecia-log-dir"></a>

_v2.7 amendments, source line 1153 — marks `E015`._

(defect `pc-74da`)

`PECIA_LOG_DIR` relocated the log and the lock while `.pecia/work.jsonl`
and `snapshot.head` stayed repository-global — so with two declared
stores in one repository, the documented two-timeline shape, whichever
store wrote last rewrote the SHARED snapshot, and the other store's very
next `check` turned E015 FORKED with its own log untouched: "locally
clean" was not simultaneously achievable for both. Amended: a pinned
store carries its own projection (`work.jsonl`) and head witness
(`snapshot.head`) beside its log and lock, under the pinned directory,
read and written by every path that touches the projection — `check`'s
E015, `snapshot`, `init`'s guards, `migrate`'s historical absorb,
`board`'s caption. The unpinned repository keeps `.pecia/` exactly as
before, and `doctor`'s custody checks stay deliberately repo-scoped: a
scratch store is not custody. E015 still fires within one pinned store
whose own projection forks — the gate keeps its denominator.
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.7","by":"pc-74da"} -->

<a id="the-formal-model-gains-the-planned-frontier-and-declares-the-list-edge"></a>

_v2.7 amendments, source line 1172 — allocates no code._

order exclusion (defects `pc-2caf`, `pc-a4df`)

**`pc-2caf`:** the CLI accepts an edge to an id declared `planned:` in
`.pecia/config.yaml` — a supported, write-gate-clean state — and the
model had no image of it: `e003clean` and `writeOk` required targets in
`idsOf[L]`, so the planned-frontier path sat outside every preservation
theorem. `peciaV2.als` now carries `one sig Config { planned: set Id }`,
`e003clean` and `writeOk` accept planned targets, and the
`SomePlannedFrontier` run keeps the coverage non-vacuous (expected count
14/14; the move is a deliberate edit in `dev/alloy-gate.sh`, per
`pc-855f`'s rule). The extension immediately earned its keep: V4's
counterexample exposed a missing `writeOk` clause — entry of a
non-terminal record at a planned id under an expired retirement promise,
which the code's write gate refuses by construction (the gate is the full
checker diff) — now stated in the model and bound to code by a fixture
test. **`pc-a4df`:** the ORDER and MULTIPLICITY of the list edges are a
DECLARED coverage exclusion, not silence: the code stores
`blocks`/`retires` as JSON arrays and derives `touched` for a pure
reordering — two revisions, two conflict units — while the model types
both edges `set Id` and diffs by set inequality, mapping every order and
repeat count of one member set to one atom. Stated in the model's Field
comment and in the `formal-model` claim; the reorder-is-a-revision
behaviour stays bound by fixture tests, where model-to-code binding
lives.

<a id="the-e013-base-bullet-is-regenerated-from-its-v2-6-narrowing"></a>

_v2.7 amendments, source line 1198 — **amends `E013` without marking it**._

(defect `pc-130e`)

The v2.6 amendment narrowed E013 to interior damage, and the generated
meanings registry kept glossing it "Detects truncation, splicing" with no
qualification — because the gloss derives from the base §8 bullet, the
amendment's own marker sits under prose that is not a code bullet, and
the base section was never regenerated (the `pc-ae3a` debt, propagating
into a machine-read artifact exactly as `pc-439d` did at round 1). The
base bullet now carries the narrowed claim with a pointer to the
amendment, `vocabulary.json` is regenerated from it, and a test pins the
shipped gloss against the superseded wording returning.

<a id="checker-totality-two-crash-classes-become-findings"></a>

_v2.7 amendments, source line 1211 — marks `E014`._

(defects `pc-3ef3`, `pc-55e1`)

The checker-totality contract — report, never crash — was broken for two
parseable timelines, both dying to the top-level handler as fatal E000:
fail-closed, but with the finding-shaped diagnostics lost and the
contract for the state unstated. **`pc-3ef3`:** a mixed-type `touched`
array (`["priority", 1]`) passed the v2.6 envelope guard (`pc-2675`
covered only the non-list case) and reached `sorted()`. The element
check joins the envelope check: `touched` must be a JSON array of
field-name STRINGS, and anything else is the E014 finding saying so.
**`pc-55e1`:** a revision following a record whose historical `rev` is
malformed (`"bad"`) reached integer arithmetic. The malformed rev keeps
its own E001, and the follower now draws an E014 stating that
contiguity cannot be verified past malformed history — said, not
skipped, and never a crash.
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.7","by":"pc-3ef3"} -->

<a id="e002-fires-on-a-repeated-id-rev-anywhere-the-sound-only"></a>

_v2.7 amendments, source line 1229 — marks `E002`._

narrowing removed (defect `pc-b5bb`)

v2.6 says a repeated `(id, rev)` **anywhere in the timeline** is E002,
and the implementation grouped over structurally sound records only —
the v1.5 phase rule ("a checker that crashes on garbage is not a
checker") applied wholesale, though this grouping indexes on exactly two
keys and needs no more soundness than their types. Removing the title
from one copy of a duplicate pair made E002 disappear, the verdict
staying red only through that copy's own E001. The code catches up to
the spec's wording rather than the wording narrowing to the code: the
grouping now runs over every record carrying a well-typed `(id, rev)`,
so an E001-unsound duplicate draws E002 beside its own E001, and the
sound-only rule keeps governing the phases that genuinely index on more.
  <!-- vocab: {"action":"amend","code":"E002","at":"v2.7","by":"pc-b5bb"} -->

### v2.8 amendments (2026-09-12 — the round-4 fix sitting: closing the

<a id="e001-findings-carry-revision-identity-defect-pc-3bdc"></a>

_v2.8 amendments, source line 1254 — marks `E001`._

The write gate is a set-diff over serialized findings (v1.4), and E001 is
the one `run_checks` code reported per REVISION rather than per head — so
a candidate revision REPEATING a historical malformation (claim 19's
`no_edges`-beside-a-real-edge case) serialized identically to the
historical finding, collided in the diff, and landed as a second
malformed revision at exit 0. Every E001 finding from the record contract
now carries its revision (`[rev N]` in the message): the repeat is a NEW
finding and is refused at `edit`, `close`, and every other writer, a
repairing revision produces no finding and lands, and `check` names each
revision's finding distinctly where it previously printed two identical
lines.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.8","by":"pc-3bdc"} -->

<a id="e001-refuses-an-empty-top-level-field-name-defect-pc-2d40"></a>

_v2.8 amendments, source line 1269 — marks `E001`._

`record.schema.json`'s `propertyNames` is `^[^.]+$` — non-empty and
dot-free — and the checker mirrored only the dot half (v2.7, pc-a437), so
a record carrying key `""` passed `check` across a whole history while
`dev/schema-check.py` refused every line: two shipped gates disagreeing
about the canonical record shape. Like the dotted case, the empty name is
refused rather than special-cased: no write path produces one, and a name
the diff machinery cannot address is not a legal field name.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.8","by":"pc-2d40"} -->

<a id="a-detected-e015-is-evidence-for-every-writer-defect-pc-c6b8"></a>

_v2.8 amendments, source line 1280 — marks `E015`._

v2.7 (pc-0ff7) taught `init` and `snapshot` to refuse over the
suffix-truncation signature — the recorded snapshot head nowhere in the
chain while the log's records are a proper prefix of the snapshot's — and
the ordinary writers kept regenerating: an `add` (and `edit`/`close`,
through the same append path) or a no-op offline `sync` erased the
missing record's last witness at exit 0. Every snapshot regeneration now
runs the same test first and refuses at exit 2 with the witness named.
The one deliberate exception is a sync whose RESULT contains the recorded
head: there the hydrate restores the truncated entries from the published
chain, the witness is satisfied rather than erased, and the operation is
the documented recovery. `migrate` remains the rebuild route by design —
it absorbs the snapshot's records as input, and `--force-drop` is the
deliberate-drop authorization.
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.8","by":"pc-c6b8"} -->

<a id="the-recovery-paths-stop-trusting-what-they-consume-defects"></a>

_v2.8 amendments, source line 1297 — allocates no code._

`pc-acd4`, `pc-4d57`, `pc-1362`)

Three seam fixes in §5's machinery, stated together because they share
one rule — nothing is consumed, written, or certified on the strength of
a writer's exit code or a chain check alone:

- **`sync` checks the LOCAL timeline in full before re-chaining it**
  (pc-acd4). `read_log` validates the chain; it says nothing about the
  CAS or the graph, so a forged rev-3-after-rev-1 suffix that `check`
  refuses (E008 + E014) was re-chained into a clean rev 2 keeping the
  forged mutation, exit 0. The local suffix now gets the same full-checker
  treatment the remote blob (pc-66b6) and the rebuilt result (trap 3)
  already had.
- **Every local `refs/pecia/log` write is read back** (pc-4d57). §5's
  sentence — "the implementation re-reads `refs/pecia/log` after every
  publish and asserts the expected head" — was true only of the remote
  push (ls-remote, VP20); the local `update-ref` was trusted by exit code
  on `publish` and on both of `sync`'s register writes. All three sites
  now read the ref back and refuse on mismatch, so the sentence is true
  of both halves.
- **An unparseable published line is NAMED** (pc-1362). The published-blob
  line gate detected only blankness against its stated E001-at-the-line-
  level contract, so `publish` called a damaged line a fork and `migrate`
  a divergent reconstruction — refusals that held while steering the
  operator to reconciliation instead of the one broken line. The gate now
  parses each line and requires the entry shape, naming the line in every
  consumer's refusal; a line that parses but is not an entry (previously
  a fatal E000 deep in sync's re-chain arithmetic) is refused at the same
  gate.

<a id="e018-a-projection-refusal-is-a-finding-never-a-crash-defect"></a>

_v2.8 amendments, source line 1328 — marks `E018`._

`pc-a5f8`)

`format-v1.md` v1.12 stands: `9999-99-99` passes E001, because whether a
date is REAL is rule-1 territory, not the checker's. But `gantt` must
CONSTRUCT a calendar day from the value, in both renderings — and it died
on the ASCII path with a fatal `E000 ValueError` at exit 2 while
`--mermaid` emitted the impossible value under `dateFormat YYYY-MM-DD`: a
crash and a lie, on state every gate certifies, reachable through the
ordinary write path. A projection that cannot interpret certified state
refuses structurally instead:

- **E018** — unprojectable value: a value the structural gates accept by
  shape that a projection cannot interpret — today, a date-shaped
  `created` or `target` on a milestone that is not a real calendar day,
  reaching `gantt`. Emitted by the projection commands as an error
  finding at exit 1, naming the record, the field, and the day part of
  the value, BEFORE either rendering — so no chart is half-drawn and no
  impossible value flows to a downstream renderer. NEVER emitted by
  `check`: the checker's shape-only stance is v1.12's, and stays.
  <!-- vocab: {"action":"mint","code":"E018","at":"v2.8","by":"pc-a5f8"} -->

<a id="the-doctor-s-d-codes-join-the-spec-s-vocabulary-defect-pc-085ad"></a>

_v2.8 amendments, source line 1350 — marks `D001`, `D002`, `D003`, `D004`, `D005`, `D006`, `D007`, `D008`, `D009`, `D010`, `D011`._

`spec/vocabulary.json` has always declared "the specs are canonical for
meaning" — and neither spec defined a single D-code, so the registry's
`d_codes` view was read from `pecia_cli.py`'s `D_CODES` dict and an
implementation-only change to a posture code's meaning regenerated
cleanly (pc-cbda recorded the absence; the executable half is this
defect). The D-codes are now MINTED here, one bullet per code, generated
from the table they must equal at the moment the spec becomes canonical;
from this amendment on, `dev/vocab-check.py` builds `d_codes` from these
bullets and refuses (V014) whenever the implementation table and the spec
disagree — in either direction, meaning included — and V006 refuses a D
bullet without a marker exactly as it does an E bullet. The doctor's
posture codes:

- **D001** — repo ships a pre-commit hook but core.hooksPath is unset — the gate has never run in this clone
  <!-- vocab: {"action":"mint","code":"D001","at":"v2.8","by":"pc-085ad"} -->

- **D002** — core.hooksPath is set but no pre-commit hook is there — commits run unguarded
  <!-- vocab: {"action":"mint","code":"D002","at":"v2.8","by":"pc-085ad"} -->

- **D003** — the pre-commit hook is not executable — git skips it silently, which reads like a passing gate
  <!-- vocab: {"action":"mint","code":"D003","at":"v2.8","by":"pc-085ad"} -->

- **D004** — a second pre-commit hook exists outside the configured hooks path — one of them is dead code
  <!-- vocab: {"action":"mint","code":"D004","at":"v2.8","by":"pc-085ad"} -->

- **D005** — .pecia/.lock is not git-ignored — permanent untracked noise trains readers to ignore noise
  <!-- vocab: {"action":"mint","code":"D005","at":"v2.8","by":"pc-085ad"} -->

- **D006** — the snapshot still declares merge=union, a v1 attribute the v2 projection never merges
  <!-- vocab: {"action":"mint","code":"D006","at":"v2.8","by":"pc-085ad"} -->

- **D007** — the ledger is not tracked by git — an uncommitted ledger is not custody
  <!-- vocab: {"action":"mint","code":"D007","at":"v2.8","by":"pc-085ad"} -->

- **D008** — core.hooksPath resolves outside this working tree — the hook that runs is another checkout's copy
  <!-- vocab: {"action":"mint","code":"D008","at":"v2.8","by":"pc-085ad"} -->

- **D009** — no local timeline and a remote is configured — the fresh-clone state, recoverable with sync
  <!-- vocab: {"action":"mint","code":"D009","at":"v2.8","by":"pc-085ad"} -->

- **D010** — a ledger exists and NO write gate is active in this clone — every other D-code assumes a gate to report on
  <!-- vocab: {"action":"mint","code":"D010","at":"v2.8","by":"pc-085ad"} -->

- **D011** — the config is not tracked by git — commit gates validate the staged ledger against the STAGED config, so the live and committed checks disagree
  <!-- vocab: {"action":"mint","code":"D011","at":"v2.8","by":"pc-085ad"} -->

<a id="the-registry-s-meanings-can-no-longer-silently-rot-defect-pc-c05a"></a>

_v2.8 amendments, source line 1398 — marks `E001`, `E002`, `E003`, `E007`, `E012`, `E013`, `E014`, `E015`._

v2.6 (pc-439d) made a gloss travel with amend events exactly as with
mints — and the parser records a meaning only when the marker is owned by
a same-code bullet, so combined amendment prose silently produced
meaning-less events: 18 mint/amend allocations carried no gloss and 8
live codes had a LATEST event with no gloss while `meanings` served an
older one, vocab-check green over all of it. pc-130e closed one instance
by regenerating one base bullet; this closes the channel.
`dev/vocab-check.py` now refuses (V013) any live code whose latest
mint/amend event carries no owned gloss, so an amendment that moves
semantics must restate the meaning where the machine reads it. The eight
stale codes are refreshed here, each gloss restating the CURRENT
semantics as amended through this sitting:

- **E001** — the full per-revision record contract: the line parses, fields and edges typed and shaped, extension names non-empty and dot-free; findings name their revision
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.8","by":"pc-c05a"} -->

- **E002** — a repeated (id, rev) anywhere in the timeline, grouped over every well-typed pair (v2.7), identical or divergent content alike — the log is corrupt; see E013
  <!-- vocab: {"action":"amend","code":"E002","at":"v2.8","by":"pc-c05a"} -->

- **E003** — edge targets must exist or be declared planned; checked on heads only, and the scan is total — an empty-string scalar is E001's finding, never silently skipped
  <!-- vocab: {"action":"amend","code":"E003","at":"v2.8","by":"pc-c05a"} -->

- **E007** — a done defect needs executable-shaped evidence or a declared reference; a warning-severity scan flags machine-local argument paths (v2.5; =-joined values v2.6)
  <!-- vocab: {"action":"amend","code":"E007","at":"v2.8","by":"pc-c05a"} -->

- **E012** — a retirement promise that expired unkept: the target is non-terminal and every record claiming to retire it is terminal; anchored on the target (v1.13)
  <!-- vocab: {"action":"amend","code":"E012","at":"v2.8","by":"pc-c05a"} -->

- **E013** — chain break: prev or seq wrong against the preceding entry — INTERIOR damage only (v2.6); a clean suffix truncation is E015's witness territory, not this
  <!-- vocab: {"action":"amend","code":"E013","at":"v2.8","by":"pc-c05a"} -->

- **E014** — CAS violation: rev is not head+1 at its log position, or touched differs from the recomputed diff; a malformed envelope is a finding, never a crash (v2.7)
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.8","by":"pc-c05a"} -->

- **E015** — snapshot forked, or the truncation witness: recorded head in the chain, content matching; the witness blocks regeneration by every writer (v2.8)
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.8","by":"pc-c05a"} -->

### v2.9 amendments (2026-09-12 — the round-5 fix sitting: closing the

<a id="the-schema-excludes-whitespace-only-edge-targets-defect-pc-f97f"></a>

_v2.9 amendments, source line 1445 — allocates no code._

`record.schema.json`'s `scalarEdge` used `minLength: 1`, which excludes
only the truly empty string — so `edges.caused_by: "   "` was refused by
`check` (E001: a scalar edge is null or names a record) while
`./dev/schema-check.py` certified the same record at exit 0. The
adapter-facing schema diverged from the checker exactly where claim 26
says they agree (the mirror of round 3's `pc-2d40` divergence, in the
other direction). `scalarEdge` now requires `pattern: \S`, the same form
`title` and `owner` already use. Siblings fixed in the same commit:
list-edge `items` (`blocks`, `retires`) and `ratified_by` carried the
identical `minLength: 1` where the checker refuses whitespace-only
values. The truly empty string stays refused by both instruments (the
`pc-94d2` rule is unchanged; the pattern subsumes it).

<a id="e006-and-e008-get-usable-registry-meanings-a-serving-gloss-may-not-be-a-"></a>

_v2.9 amendments, source line 1460 — marks `E006`, `E008`._

The latest-meaning replacement left two codes serving captured fragments
as their current meaning: E006's gloss began "/E007 are state checks on
heads…" (the tail of v1.4's combined E006/E007 bullet) and E008's began
"= contiguous from the minimum present rev.**" — neither states the
code's own condition, and V013 accepted both because the markers were
owned. Two moves: the meanings are restated below in bullets each code
owns, and `dev/vocab-check.py` gains **V015** — a live code's serving
gloss may not open with a joiner character (`/` or `=`), the splice
signature a captured fragment carries where the parser consumed the
token it hung from. A declared heuristic: it catches the splice
signature, not every conceivable fragment (a gloss legitimately opens
with a word, a path, or a quoted name). Historical fragments stay as
custody; only the serving gloss is held to this.

- **E006** — a head in a terminal status (done, dropped, superseded) without a non-empty disposition; a state check on heads (v1.4): a repaired head is legitimately clean, and custody of the violating revision stays with audit's historical-custody-violation
  <!-- vocab: {"action":"amend","code":"E006","at":"v2.9","by":"pc-fea8"} -->

- **E008** — revision gap(s): a record's revisions must be contiguous from the minimum present rev; a gap is an error, raised per record over well-typed (id, rev) pairs
  <!-- vocab: {"action":"amend","code":"E008","at":"v2.9","by":"pc-fea8"} -->

<a id="e014-s-two-comparisons-declared-precisely-canonical-order-and-the-presen"></a>

_v2.9 amendments, source line 1482 — marks `E014`._

Two imprecisions in what E014 compares, both found by the round-4 pass,
both resolved by DECLARATION — the checker's verdicts do not move.

**Canonical order (`pc-ebd2`).** §4.1 defines *conflict* by set
intersection and said nothing about serialization order, while the
checker compared `touched` as an ordered array — the same field-set
reordered was refused, checker-stricter-than-spec. Declared, not
loosened: `touched` is serialized in **lexicographic (sorted) order**,
which is what `diff_fields` has always emitted and what every write path
produces; a declaration carrying the correct field-set in any other
order is refused (E014), and the refusal now names the order as what it
is refusing. Only a hand-built or foreign log can produce the case, and
no compatibility is owed to any format outside this repository's own
timeline (`pc-fd65`). Loosening to set comparison was rejected because a
canonical serialized form is what keeps entry hashes, the findings diff,
and byte-prefix publication comparisons deterministic — and it would
have admitted duplicate elements.

**Presence scope (`pc-9997`).** The v2.7 (`pc-a437`) presence-aware
comparison is **extension-field territory** — the union keys outside the
core contract. Core fields compare by value at the diff, absence equated
with null. Declared as scope rather than extended, because the value
level already closes the seam: every optional core field refuses null
when present (E001's "when present, must be…" rules), so an absent→null
transition on a core field is always refused and can never move
silently, and the write path never materializes an absent optional core
field as null. The claim-3 sentence "E014 compares presence, not just
values" is narrowed to extension fields, where it was measured.

- **E014** — CAS violation: rev is not head+1 at its log position, or touched differs from the recomputed diff, compared as the canonically sorted array (v2.9), presence-aware on extension fields (v2.7); a malformed envelope is a finding, never a crash
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.9","by":"pc-ebd2"} -->

<a id="e017-s-head-state-scope-declared-defect-pc-c96a"></a>

_v2.9 amendments, source line 1516 — marks `E017`._

The v2.5 mint said "an edge field, list or scalar, that names its own
record" with no scope line, while the scan has always iterated **heads
only** — so a rev-1 self-edge cleared at rev 2 checks clean, and the
implementation comment said "on ANY edge field" directly above the
heads-only loop. The behavior is correct and is now declared: E017 is a
**state check on heads**, exactly like E003, E006, E007 and E012 — a
repaired head is legitimately clean, and permanent check-red for a
corrected history would make laundering the attractive repair. History
scanning was rejected for the same reason it was rejected at v1.4 for
E006/E007: custody of a violating revision is audit's job, not a
permanent error. The contradicting comment is corrected in the same
commit.

- **E017** — self-edge: an edge field, list or scalar, of a HEAD that names its own record; a state check on heads (v2.9) — a repaired head is legitimately clean
  <!-- vocab: {"action":"amend","code":"E017","at":"v2.9","by":"pc-c96a"} -->

<a id="doctor-tests-the-checker-resolution-the-gate-documents-defect-pc-87d8"></a>

_v2.9 amendments, source line 1534 — marks `D012`._

`doctor` tested hook existence and executability, never the template's
documented three-way checker resolution (`$PECIA_CLI`, `pecia` on PATH,
the repository-root `pecia_cli.py`) — so a repository whose hook could
resolve nothing read `ok: true, warnings: 0` while every ordinary commit
exited 1 at "no pecia CLI found". The hook fails closed, correctly;
doctor's posture answer was wrong in the direction automation trusts.
The `pc-aff4` precedent put structural unexecutability inside doctor's
remit; the resolution chain is the same class one layer up. Minted:

- **D012** — the active hook resolves no pecia checker — the gate fails closed, so every ordinary commit is refused while posture read clean
  <!-- vocab: {"action":"mint","code":"D012","at":"v2.9","by":"pc-87d8"} -->

Applied only to a hook honoring the documented chain (identified by its
`PECIA_CLI` reference), so a hand-rolled gate embedding its own path is
never falsely accused; doctor's environment stands in for the hook's,
which is what the operator can actually set.

<a id="doctor-fix-reports-only-what-happened-defect-pc-daec"></a>

_v2.9 amendments, source line 1553 — allocates no code._

`cmd_init`'s return value was discarded inside `--fix`, so over a
malformed timeline — where repeat `init` correctly REFUSES (the
`pc-0ff7` guard) — `--fix` appended "re-ran init" to `fixed`, dropped
the still-present D005 from `unfixed`, and exited 0: the very
accounting `pc-1121` added at v2.8 reported an action that did not
happen. Now: a fix enters `fixed` only when its command exited 0; a
refused fix is named in `refused` with its findings kept in `unfixed`;
and a `--fix` that could not do what it set out to do exits 1. The
`automated` set is credited per action rather than all-or-nothing, so a
hooksPath fix landing beside a refused init no longer absorbs D005/D006.

<a id="the-formal-model-sees-a-re-chain-revision-regression-defect-pc-fc4c"></a>

_v2.9 amendments, source line 1566 — allocates no code._

`pred rechained` transferred six field atoms between existing Line
atoms and constrained no `out.rev`, appended no Entry through the
revCas, and had no concurrent-creation branch — so Alloy stayed green
if `cmd_sync` mis-assigned the re-chained revision, mishandled a
multi-entry suffix, or regressed the identical-vs-divergent creation
partition, and the declared exclusion list did not state the gap.
Moved into the model: `rechained` now constrains `out.rev` to continue
from the landed head (the code's `heads[rid]["rev"] + 1`), **V12**
(`RechainedEntryPassesTheRevCas`) binds that arithmetic to the revCas
over an actual log, and `SomeRechainedCasStep` keeps it non-vacuous
against a multi-revision head; the expected counts move 14 → 16 by
deliberate edit in `dev/alloy-gate.sh` (the `pc-855f` rule), realized
16/16, traps 0/4, seeds 0/3, and the kill was demonstrated by weakening
the rule to `mine.rev + 1` (15/16, gate exit 1). Declared rather than
modeled, in the Field comment's exclusion list and the register's
formal-model entry (which move together): the multi-entry-suffix loop
(each step is V12's step; the loop is bound by `SyncComposition`, whose
fixtures since `pc-8b81` include a two-revision local-only suffix) and
the concurrent-creation partition (identical = no-op skip, a branded
creation re-chaining as custody per `pc-dacc`; divergent = surfaced
conflict regardless of the brand, `pc-e499`/`pc-23e6`; bound by
`SyncDivergentCreation`).

### v2.10 amendments (2026-09-12 — the round-6 fix sitting: closing the

<a id="e001-admits-strict-json-tokens-only-defect-pc-6af9"></a>

_v2.10 amendments, source line 1599 — marks `E001`._

"Every line parses" meant, in practice, "Python's `json.loads` accepts
it" — and Python's parser admits `NaN`, `Infinity` and `-Infinity`,
which are not JSON tokens (RFC 8259 §6). A log line carrying one in an
extension field checked clean at exit 0, and `canonical()` wrote the
token back, so the checker certified a line the one-JSON-object-per-line
contract refuses. Amended: **every timeline parse gate refuses the three
non-JSON tokens as an unparseable line** — the store log (E001 naming
the line), the explicitly named `--ledger` file (E001), the published
blob in every consumer (`publish`, `sync`, `migrate`), and the
migration/witness readers (where an unparseable line is already the
declared skip-as-garbage case). The write side is closed in the same
move: `canonical()` serializes with `allow_nan=False`, so nothing
non-JSON can enter a timeline through this tool. A finite extension
value is unaffected.

- **E001** — the full per-revision record contract: the line parses as strict JSON (`NaN`, `Infinity` and `-Infinity` are not tokens), fields and edges typed and shaped, extension names non-empty and dot-free; findings name their revision
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.10","by":"pc-6af9"} -->

<a id="a-malformed-snapshot-is-e015-never-a-crash-defect-pc-23a1"></a>

_v2.10 amendments, source line 1619 — marks `E015`._

The checker read both generated files — `.pecia/work.jsonl` and
`.pecia/snapshot.head` — with a strict `read_text()`, so invalid-UTF-8
bytes in either reached the top-level handler as **fatal E000
UnicodeDecodeError at exit 2**, where equivalent valid-text corruption
of the same file is a structured E015 at exit 1. E000's specified
meaning is inability to read a *timeline*; corruption of a generated
*projection* is E015's subject and shares its remedy (regenerate with
`pecia snapshot`). Amended: non-text bytes in the snapshot or its
witness are an **E015 finding naming the file and the malformation**,
exit 1. Siblings closed in the same commit: the truncation-witness
reader (which crashed every writer that asks the witness question —
`add`, `sync`, `snapshot`, `init`) and `migrate`'s snapshot absorb now
decode surrogateescape, landing garbage bytes in the already-declared
forged-case handling (claim 27's rule: the checker — and every consumer
of a generated file — reports, never crashes).

- **E015** — snapshot forked, corrupted (non-text bytes in the projection or its witness included, v2.10), or the truncation witness: recorded head in the chain, content matching; the witness blocks regeneration by every writer (v2.8)
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.10","by":"pc-23a1"} -->

<a id="migrate-reconstructs-in-witnessed-order-not-lexical-order-defect-pc-356c"></a>

_v2.10 amendments, source line 1640 — allocates no code._

`collect_historical_records` sorted the rebuild by `(rev, id)`,
discarding the order every source blob actually carries — and a
snapshot is written in **log order**, so over the same E015
suffix-truncation shape (log = the published one-entry prefix, snapshot
= the two-record witness) `migrate --force` accepted the recoverable
loss when the two rev-1 ids happened to sort in creation order and
refused "the reconstruction diverges" when they did not, steering the
operator to `--force-drop` — deliberate loss acceptance — although the
ordered witness suffices to reconstruct and verify the original chain.
Recoverability turned on the luck of the id draw. Amended: **each
source's observed sequence is a set of precedence constraints and the
reconstruction is their topological merge**, `(rev, id)` breaking ties
among unconstrained keys (the old order is the degenerate no-witness
case); when witnesses genuinely disagree on order the merge falls back
to `(rev, id)`, deterministically, disclosed in the output as
`order: "rev-id (witness orders conflict)"` — and the published-prefix
guard (`pc-2276`) still arbitrates whatever the reconstruction
produces. A witnessed order that violates the per-id rev sequence is
still refused by the CAS admission check, loudly.

<a id="doctor-fix-credits-a-fix-when-the-condition-cleared-and-every-write-is-r"></a>

_v2.10 amendments, source line 1662 — allocates no code._

Two failures of the v2.9 accounting rule ("reports only what happened",
`pc-daec`), one action over from its repair:

- **Credit followed the fixer's exit code, not the condition**
  (`pc-9663`). D006's detector tokenizes attributes
  (`.pecia/work.jsonl text merge=union` fires) while `init` removed only
  the exact legacy line — so `--fix` credited the removal because init
  exited 0, dropped D006 from `unfixed`, and the variant attribute
  stayed active; the next `doctor` fired D006 again. Amended twice over:
  remover and detector now share one parse (a line's `merge=union`
  token is stripped wherever the detector would fire, other attributes
  and unrelated comments preserved, the bare declaration still taking
  its explaining comment block per `pc-80f4`), and **every automated
  credit re-runs its finding's own detector after the fixer** — a
  condition still standing behind an exit-0 fixer is named in `refused`,
  its finding stays in `unfixed`, and the run exits 1.
- **A failed `core.hooksPath` write vanished from the accounting**
  (`pc-ef4c`). With `.git` read-only the underlying `git config` fails
  (could not lock config file), but the helper collapsed the failure to
  None — the attempted fix appeared in neither `fixed` nor `refused`,
  exit 0, D001 standing. The write now captures its error, **is read
  back** (VP20's shape: delivery confirmed by reading the target, never
  by the writer's exit code), and a refusal is named with the run
  exiting 1.

<a id="d012-tests-the-resolution-and-the-execution-the-hook-will-run-defects-pc"></a>

_v2.10 amendments, source line 1689 — **amends `D012` without marking it**._

Two ways doctor's stand-in for the hook diverged from the hook itself,
each reading `ok: true` over a gate refusing every ordinary commit —
the `pc-87d8` class one layer over (the chain was tested; against the
wrong directory, and short of execution):

- **The wrong directory** (`pc-2f19`). git runs hooks from the
  work-tree root, so a relative `$PECIA_CLI` is root-relative to the
  hook; doctor resolved it against its own invocation directory, so
  from a subdirectory `PECIA_CLI=../vendor/checker.py` read clean while
  the identical environment's commit died at "no pecia CLI found".
  A relative value now resolves from the repository root, where the
  hook resolves it.
- **The wrong test** (`pc-b418`). The template executes a non-`.py`
  checker directly (only `*.py` goes through `python3`), so an
  existing mode-644 file dies "Permission denied" into the
  staged-ledger refusal at every commit while D012's `is_file()` read
  clean. A resolved non-`.py` checker must now also be executable, or
  D012 fires naming the execution mode.

<a id="the-declared-synccomposition-bound-now-binds-the-loop-defect-pc-8b81"></a>

_v2.10 amendments, source line 1710 — allocates no code._

Claim 23's declared coverage exclusion said "the code's suffix loop is
bound by `SyncComposition`" — and every fixture in that class carried
at most **one** local-only suffix entry, so a mutant refusing the
second iteration of `for e in mine:` passed the class whole while
genuinely breaking a two-revision sync the unmodified CLI handles. A
coverage overstatement in the declared bound (the `pc-fc4c`/`pc-582a`
class one binding over), not an implementation failure: the full Alloy
gate held 16/16 at the tag. The lane's falsifier is now a fixture: a
`SyncComposition` case creating two local-only revisions, both shown
re-chaining with the winner's edit intact, demonstrated failing under
that exact mutant. The exclusion texts (this spec's `pc-fc4c` section,
the model's Field comment, the register's formal-model entry — which
move together) are refined in the same commit, and the
concurrent-creation gloss now states the brand ordering (`pc-dacc`
custody re-chain; conflict regardless of the brand, `pc-23e6`).

<a id="the-two-checker-routes-share-one-line-discipline-defect-pc-5127"></a>

_v2.10 amendments, source line 1728 — marks `E001`._

`check --ledger` — the explicitly named record-line route, and the one
the adopter pre-commit hook runs on the staged ledger — silently
normalized blank physical lines away (`load_raw`'s blank-line
`continue`), while the canonical store route refuses the same line with
E001 ("one entry per line, with no blank lines", `pc-e7f0`). The
divergence between the two public routes was undeclared scope: no
blank-line rule existed for the record-line format at all. Declared and
aligned in the strict direction: **the record-line format shares the
log's line discipline** — every physical line parses, blank lines are
an E001 finding — with one deliberate difference: bare records carry no
chain, so the `--ledger` route reports the blank line and continues
reading (the store route must stop, since every line after a break is
untrustworthy). No write path emits a blank line, so nothing legitimate
reddens.

- **E001** — the record contract on both routes (store and --ledger): every line parses as strict JSON, no blank lines, fields and edges typed and shaped
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.10","by":"pc-5127"} -->

<a id="sync-s-success-report-rechained-counts-writes-defect-pc-1d45"></a>

_v2.10 amendments, source line 1748 — marks `D012`._

The re-chain success emit reported `rechained: len(mine)` — the
**examined** local suffix — so one actual append beside one
identical-creation skip reported `rechained: 2, skipped_noops: 1`: the
success surface overstating the write next to the very field that says
part of it never happened. The ledger was correct; the label had no
contract anywhere (a shipped test pinned the overstatement). Declared:
**`rechained` counts the entries actually appended beyond the common
prefix**, `skipped_noops` counts examined no-ops, and the two sum to
the local suffix examined; `fast_forwarded` (the hydrate branch)
remains the count of adopted published entries.

- **D012** — the active hook resolves no pecia checker it can RUN (relative $PECIA_CLI from the repo root; a non-.py checker must be executable) — the gate fails closed
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.10","by":"pc-b418"} -->

### v2.11 amendments (2026-09-13 — the round-7 fix sitting: closing the

<a id="the-crash-class-is-closed-at-the-entry-points-not-per-site-defects-pc-34"></a>

_v2.11 amendments, source line 1772 — allocates no code._

Two new fatal-E000 sites opened behind the closed `pc-23a1`/`pc-6af9`
fixes, so this sitting inventoried every byte-to-JSON entry point and
every `canonical()` caller for BOTH malformations (non-UTF-8 bytes,
non-finite floats) instead of patching per site:

- **The `--ledger` route shares the store route's byte discipline**
  (`pc-34b5`). `load_raw`'s explicit-path route — the adopter pre-commit
  hook's staged-ledger gate — decoded with a strict `read_text()`, so a
  non-UTF-8 line died as fatal E000 UnicodeDecodeError at exit 2 where
  the store route names the same bytes as a structured E001 at exit 1.
  Amended: the route decodes with surrogateescape and refuses per line
  (E001 naming the line), and reading continues past the bad line — bare
  records carry no chain, the `pc-5127` shape.
- **A non-finite decoded value is refused where it enters** (`pc-d502`).
  `1e1000000` is valid JSON numeric syntax Python decodes to `inf`; the
  v2.10 token gate catches only the literal tokens, so `check --ledger`
  certified the value at exit 0 while `canonical(allow_nan=False)`
  killed store `check` and `migrate` as fatal E000 ValueError. Amended:
  **`strict_json_loads` refuses a float literal whose decoded value is
  non-finite** — an unparseable line (E001) at every timeline parse gate
  at once, since no strict serializer could ever write the line back. A
  finite value of any magnitude is unaffected.
- **`migrate` refuses what it cannot read, loudly** (`pc-d502`, the
  recovery half). The reconstruction collector skips garbage by
  declaration but counted nothing, so a content-carrying line the strict
  parse refuses would have been DROPPED from a rebuild at exit 0.
  Amended: the collector counts `unreadable_lines`, and `migrate`
  refuses when the count is nonzero without `--force-drop` — the same
  ladder as the damaged-log guard, the count disclosed either way. Blank
  lines carry no content and stay uncounted (the `pc-e7f0` distinction).
- **Sibling sweep, same commit**: `load_config` decoded the
  adopter-owned `config.yaml` strictly (one bad byte killed every
  command); the audit imperative scan read tracked test files strictly
  inside an OSError-only net (one non-UTF-8 test file crashed `audit`
  and `board`). Both decode with surrogateescape now — the flat config
  parser and the substring scan each have declared garbage handling.

<a id="the-doctor-detectors-ask-git-never-line-tokens-defects-pc-4c7d-pc-dc10"></a>

_v2.11 amendments, source line 1811 — allocates no code._

The v2.10 rule "credited when the condition cleared" (`pc-9663`) held,
and the round-6 pass opened its sibling one level deeper: the DETECTORS
themselves parsed line tokens where git's effective answer differs.
D005's exact-line test read `.pecia/.lock` present while a later
`!.pecia/.lock` negation made git report the lock NOT ignored; D006's
two-literal-pattern parse read nothing while `.pecia/** merge=union`
activated the forbidden driver on the snapshot per `git check-attr`.
Amended: **D005 asks `git check-ignore` and D006 asks `git check-attr`**
— the answers compose negations, wildcards, and every source file the
way git's own status and merge machinery do; the tokenized parses remain
only as the degraded fallback when git itself is unavailable. `init`'s
ignore-rule appender asks the detector's question too, appending at the
end so re-running `init` wins git's last-match rule and remains the
remedy D005 names. `init`'s attribute REMOVER deliberately keeps its
tokenized scope over the two literal snapshot patterns: stripping a
wildcard declaration would edit an adopter's intent for other files, and
the `pc-9663` accounting already names what the remover cannot clear
(D006 stands, in `refused`).

<a id="d012-identifies-the-chain-on-code-not-comments-defect-pc-709d"></a>

_v2.11 amendments, source line 1832 — **amends `D012` without marking it**._

The v2.9 scope sentence — "applied only to a hook honoring the
documented chain (identified by its `PECIA_CLI` reference), so a
hand-rolled gate embedding its own path is never falsely accused" — was
implemented as a substring test over the whole hook text, comments
included. A hand-rolled hook whose only `PECIA_CLI` occurrence is a
comment stating it does NOT use that resolution was accused: doctor
reported every ordinary commit refused (`ok: false`, exit 1) while the
hook's embedded checker ran and commits succeeded. Amended: **the
identification strips shell comments first** (a `#` opening the line or
preceded by whitespace, to end of line); a `$PECIA_CLI` expansion,
assignment, or string in live code still identifies the chain.

<a id="e004-anchors-only-on-records-a-cycle-passes-through-defect-pc-86f9"></a>

_v2.11 amendments, source line 1846 — **amends `E004` without marking it**._

`cycles_in` returned on the first cycle found, leaving every node of the
abandoned DFS path marked "visiting" — so the next root's traversal read
the stale mark as a back edge, and `pc-a` self-cycling beside a one-way
`pc-b -> pc-a` emitted a false second E004 anchored on `pc-b`, a record
no cycle passes through, misdirecting remediation while the overall
refusal stayed correct. Amended: **a found cycle is recorded and the
walk continues**, restoring the DFS invariant that a visiting-marked
node is always on the current path; the not-on-path fallback that
manufactured the false finding has no remaining input and is gone. A
second genuine cycle reachable from the same traversal is now also
reported rather than shadowed by the first. NARROWED at v3.4 (`pc-c69d`):
one traversal cannot keep that promise, since a cycle through a node the
walk has already finished is never walked; each E004 now names its whole
cyclic component instead, and the gate reads the component.

<a id="e008-groups-over-every-well-typed-id-rev-defect-pc-8291"></a>

_v2.11 amendments, source line 1863 — **amends `E008` without marking it**._

E008's contiguity scan grouped over `record_is_sound` records while its
gloss says gaps are raised over well-typed `(id, rev)` pairs — the exact
widening E002 received at v2.7 (`pc-b5bb`), for the same reason: the
grouping indexes on two keys and needs no more soundness than their
types. A rev-3 copy that lost its title vanished from the scan and the
`[2]` gap went unreported, the verdict red only through the copy's own
E001. Amended: **E008 shares E002's well-typed grouping**; an unsound
copy at a contiguous rev still draws no gap.

<a id="the-closed-declaration-set-reads-declarations-the-way-the-solver-does-de"></a>

_v2.11 amendments, source line 1874 — allocates no code._

The `pc-219e` enumeration extracted companion declarations with a
single-line pattern — keyword and identifier on one physical line —
while Alloy permits arbitrary whitespace between them. `pred` alone on
one line with `privateCleanLines[...]` on the next evaded `--static`
AND the full gate: the round-6 scorer ran the pinned solver over the
bypass fixture and the counts stayed green, proving the solver parses
the split form the scan could not see; the identical one-line form was
refused. Amended: **one comment-stripped, newline-spanning extraction
feeds every structural refusal** in `dev/alloy-gate.sh` — the closed
declaration set, the `clean/head/graph` family blacklist, and the
`Field`/`diff`/`diffPartial` re-declaration checks — so no sibling
check keeps the single-line hole. Comment stripping is what the line
anchor used to approximate: without it a comment merely mentioning
`pred foo` would be refused as a declaration (the false-accusation
shape, inverted); a control pins that both ways. `--static` fails
closed when its extractor (`python3`) is unavailable — an empty
declaration list refuses nothing.

<a id="the-registry-serves-whole-glosses-defect-pc-8704"></a>

_v2.11 amendments, source line 1894 — allocates no code._

`vocabulary.json`'s `meanings` view assigned `gloss[:160]` — a mid-word
byte prefix nothing declared — so E014's served meaning ended
"presence-awa" and E015's "content mat", the v2.9 canonical-order and
v2.10 non-text qualifications truncated out of the machine-readable
register that carries the latest gloss per live code. Seven live codes
were being cut. Amended: **the serving gloss is the owner bullet's
normalized text, entire** — the spec is the register's source, and the
register serves what the spec states, with no undeclared bound. The
`pc-3f87`/`pc-0a9a` register-accuracy class, in the generated
vocabulary itself.

<a id="e015-s-non-text-diagnosis-is-independent-of-the-witness-s-state-defect-p"></a>

_v2.11 amendments, source line 1907 — **amends `E015` without marking it**._

The v2.10 non-text clause (`pc-23a1`) promised non-text bytes in the
snapshot are named as such — and the naming lived inside the
valid-witness branch, so both compound states dodged it: a FORKED
witness left the comparison prefix unset and the projection was never
decoded, and a MISSING witness read the bytes without decoding,
describing garbage as "content". The verdict stayed red with the right
remedy on both paths; the promised naming of the file and the
malformation was absent (the round's one cross-lane duplicate).
Amended: **the projection is decoded before the witness dispatch**, so
the non-text finding appears beside the fork or missing-witness finding
in every compound state; ordinary text in the same states draws no
non-text finding (the paired control).

<a id="e015-findings-name-the-file-wherever-the-store-is-defect-pc-f61a"></a>

_v2.11 amendments, source line 1922 — **amends `E015` without marking it**._

Under `PECIA_LOG_DIR` the checker read the correct pinned files
(claim 29 held behaviorally) and the E015 message literals still said
`.pecia/work.jsonl` and `.pecia/snapshot.head` — paths absent from the
fixture, where claim 6's v2.10 clause promises the finding names THE
file. Amended: the snapshot findings (and `init`'s fresh-clone refusal,
the same shape) resolve their file names through one display rule —
repo-relative for the ordinary store, the pinned path under a pin — the
resolution the board caption has used since `pc-74da`.

<a id="sync-s-conflict-report-landed-rev-names-the-revision-that-conflicted-def"></a>

_v2.11 amendments, source line 1933 — allocates no code._

The same-field conflict object carried a field no contract defined —
the `pc-1d45` shape, one surface over — and filled it with
`landed[-1]`: the latest landed revision of the id, whatever it
touched. With landed rev 2 touching `title` and rev 3 touching
`owner`, a losing concurrent title edit reported `landed_rev: 3`,
pointing the writer at a revision that never touched the field. The
refusal and no-write behavior were correct throughout. Declared:
**`landed_rev` is the latest landed revision whose touched set
conflicts with yours** — the revision whose edit you must read to
reconcile; `your_rev` remains your losing revision and `fields` the
conflicting units. The divergent-creation conflict keeps reporting the
remote head it diverged from, which is the revision its `fields` are
computed against.

<a id="reference-not-attempted-names-the-field-it-read-defect-pc-5893"></a>

_v2.11 amendments, source line 1949 — allocates no code._

One message template served both audit-surface loops — evidence and
context — and hardcoded "evidence reference", so a record carrying only
a `--context` reference was described on the board as an evidence
reference. The finding kind and the never-resolve rule (claim 15) were
correct; the explanatory text named the wrong field. Amended: the
finding carries a `field` value (`evidence` or `context`) and the note
renders it, so the surface names the reference it actually read.

### v2.12 amendments (2026-09-14 — the round-8 fix sitting: closing the

<a id="e001-s-id-and-date-anchors-are-full-matches-defect-pc-9726"></a>

_v2.12 amendments, source line 1967 — **amends `E001` without marking it**._

`ID_RE.match()` and `DATE_RE.match()` ended in `$`, which under
`match()` also matches immediately before a final newline — so an id,
`created`, milestone `target`, or decision `ratified` value ending in
`"\n"` was certified at exit 0 where the trailing-space form is a
structured E001, and `add --target '2026-09-14\n'` landed at exit 0
with the newline stored by the write gate itself. No grammar change:
the stated shapes (`^pc-…$`, `YYYY-MM-DD`) always excluded the newline;
the implementation's anchor discipline was weaker than its own
neighbours (the anchor and foreign-reference validators in the same
file already used full matches). Amended: **the four ID_RE/DATE_RE
sites use `fullmatch`**, and the write gate refuses the newline forms
it used to store. No write path emits them, and this repository's own
history stays green — no cutover needed.

<a id="the-line-unit-is-the-lf-delimited-physical-line-defect-pc-d96d"></a>

_v2.12 amendments, source line 1983 — marks `E001`._

Every reader of the log and record-line formats split lines with
Python's `splitlines()`, which also breaks on U+2028, U+2029, U+0085,
`\v`, `\f` and the information separators — so a ONE-physical-line
record (exactly one LF byte in the file) whose JSON string carried a
literal U+2028 was sheared before the parser could see it and falsely
refused E001 on both checker routes, while the `\u2028`-escaped
control was certified; the same shear misread published blobs and the
publish prefix comparison. JSON's grammar admits an unescaped U+2028
inside a string (RFC 8259 escapes only `"`, `\`, and the C0
controls). Decided: **the stored formats' line separator is LF (0x0A)
and nothing else.** A physical line is a maximal LF-free byte run;
U+2028 and its Unicode line-boundary siblings inside a string are
CONTENT; a raw LF inside a string remains two physical lines and both
halves are E001. One shared splitter (`source_lines`) feeds the store
route, the `--ledger` route, the published-blob reader, the publish
prefix comparison, `migrate`'s collector, and the snapshot witness, so
no reader keeps a private line notion; `dev/schema-check.py` and
`dev/report.py` follow as consumers of the same format. This tool's
own serializer (`canonical`) escapes non-ASCII and never emits the
literal forms, so nothing this tool wrote changes meaning; the
acceptance widens to what the grammar always admitted, and nothing
reddens.

- **E001** — the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON, no blank lines, fields and edges typed and shaped; Unicode line-boundary characters inside strings are content, never separators
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.12","by":"pc-d96d"} -->

<a id="the-format-declares-a-nesting-bound-defect-pc-2e2f"></a>

_v2.12 amendments, source line 2011 — marks `E001`._

The format declared no nesting limit, so a balanced JSON array at the
interpreter's recursion boundary — 1500 deep on the lane sandbox's
Python 3.9.6, near 10000 on CPython 3.12 — was VALID input that
reached the top-level handler as fatal E000 RecursionError at exit 2:
the checker's totality contract broken by a per-value RESOURCE, an
axis the v2.11 byte/float entry-point inventory structurally could not
cover (its third recurrence, and the sealed round-7 frame's stated
threshold for demanding a structural guard rather than a third
sweep). Decided: **a stored line nests at most 100 levels, and a
record at most 99** (one below, because the log's entry envelope
wraps it). The bound is checked ITERATIVELY — a string-aware bracket
scan at the parse gate (`strict_json_loads`) before any recursive
parse, and an iterative walk in the record contract for candidates
built in memory that never re-parse — so no conforming value can
drive the parser, the serializer, the diff machinery, or a projection
anywhere near an interpreter boundary on any supported Python, and
the guard itself cannot be crashed by what it guards against. An
over-deep line is a structured E001 at exit 1 naming the depth and
the bound. Two orders of magnitude above any shipped record's depth;
nothing in this repository's history reddens — no cutover needed. The
beads import adapter gains the sibling handling at its own boundary
(a foreign line past the interpreter's limit lands in its declared
malformed-line report, not a crash), stated without citing this bound
because beads owns its format.

- **E001** — the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON within the declared nesting bound (100 for a line, 99 for a record), no blank lines, fields and edges typed and shaped
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.12","by":"pc-2e2f"} -->

<a id="a-record-contract-finding-is-identified-by-its-revision-or-failing-that-"></a>

_v2.12 amendments, source line 2041 — marks `E001`._

The v2.8 universal — "every record-contract E001 finding carries its
revision (`[rev N]`)" — cannot literally hold for a record MISSING
`rev`: two such records on the `--ledger` route produced two
byte-identical "missing fields: rev" findings with neither revision
nor line identity, indistinguishable to a reader and COLLIDING in the
write gate's serialized-finding diff (the `pc-3bdc` hole one level
over — the gate is the checker, so identical findings are exactly what
the diff cannot refuse twice). Amended: **where the revision cannot
identify the condemned line, the source line does.** A sound rev keeps
the unchanged `[rev N]`; a malformed rev keeps its repr'd tag and
gains the line (`[rev 'bad' — malformed, line N]`); an absent rev is
tagged `[rev absent — line N]`. The line is the record's one-based
physical source line — the file line on the `--ledger` route (skipped
garbage consumes numbers), the log line on the store route, the
timeline position at the write gate, where both sides of the diff now
carry lines so history stays self-identical and only a genuinely new
malformation reads as new. A caller with no line notion (the board's
posture pass) omits lines and the tags fall back to the pre-v2.12
forms. Refusals were correct throughout; identity is what this adds.

- **E001** — the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON within the declared nesting bound, no blank lines, fields and edges typed and shaped; every finding carries its revision, or its source line where the revision cannot identify it
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.12","by":"pc-b60a"} -->

<a id="a-capability-detector-asks-the-question-the-runtime-asks-defect-pc-acaf"></a>

_v2.12 amendments, source line 2066 — marks `D003`, `D012`._

D012 tested `is_file()` for a `.py` checker although the hook runs one
as `python3 "$cli"`, which must OPEN it — so a checker at mode 000 read
`ok: true`, errors 0 while every ledger-bearing commit died "can't open
file … Permission denied", one mode bit away from the missing-checker
arm that fires correctly. This is the `pc-9663`/round-6 detector rule
one level deeper: that rule fixed WHICH ORACLE a detector consults (ask
git, not a line token); this fixes WHICH QUESTION it asks. Declared: **a
posture detector asks the question the guarded runtime asks, in the
runtime's own terms** — for a file the runtime reads, an open, not a
mode bit (`os.access` answers for the real uid and ignores ACLs, so it
can disagree with the very call whose outcome is being reported).

The sibling sweep over doctor's other capability predicates found two
more, fixed in the same commit, and both are UNDECIDABLE where the
first is not — git and the template `exec` these directly, and an
interpreted `#!` file at mode 111 dies "Permission denied" at every
commit (measured) while a self-contained binary runs, which doctor
cannot tell apart without the read permission it was refused:

- **An unreadable hook**. D003's `X_OK` passed and D012's read of the
  hook text swallowed the `PermissionError` into `""`, skipping the
  resolution branch entirely — a fully green posture over a repository
  where git refuses every commit. Now a D003 **warning**.
- **An unreadable non-`.py` checker**. `X_OK` passed, same shape. Now a
  D012 **warning**.

Warnings, not errors, deliberately: the finding states the condition
doctor verified (executable, not readable) and not an outcome it did
not observe, so a mode-111 binary is not falsely accused — the
false-red half of the `pc-709d` lesson. Posture stays `ok: true`, and
the operator is told the answer is undecidable and why.

- **D003** — the pre-commit hook is not executable — git skips it silently, which reads like a passing gate; also warns when it is executable but unreadable, where an interpreted hook dies at every commit and doctor cannot tell
  <!-- vocab: {"action":"amend","code":"D003","at":"v2.12","by":"pc-acaf"} -->
- **D012** — the active hook resolves no pecia checker it can RUN (relative $PECIA_CLI from the repo root; a .py checker must be openable by python3, a non-.py checker executable) — the gate fails closed
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.12","by":"pc-acaf"} -->

<a id="a-refused-write-never-happened-validate-then-rename-defect-pc-d373"></a>

_v2.12 amendments, source line 2105 — allocates no code._

Three writers shared a **write-then-validate** ordering — serialize onto
the live log, read it back, refuse. `sync`'s re-chain branch had no
rollback at all. It validated the published timeline with
`timeline_errors`, which checks the records and the graph and says
nothing about `seq` or `prev`, and nothing else on that branch looked at
the published chain — so a published line declaring `seq: 9` passed, was
re-chained, **written**, and only then refused by `read_log`'s E013. A
previously clean local store was left at seq `[9, 2]`, red under E013 and
E015, by a command that exited 2 saying it had refused. The hydrate
branch and `migrate` did not have that hole, but they closed it by
RESTORING the old bytes afterwards — a rollback that has to run and be
right, on a path that has already been shown to be reachable with the
validation wrong.

Declared: **a recovery path that refuses leaves the store byte-identical,
because it never opened it.** The bytes are staged beside the target (the
same directory, so the rename is atomic wherever the store is), read back
through `read_log` for the chain and through the caller's own further
check — for the hydrate branch, the full checker, per `pc-66b6` — and
only then moved into place with one `os.replace`. The staging file is
removed however the attempt ends, so a crash leaves a dot-file rather
than a damaged log. `sync`'s hydrate and re-chain branches and
`migrate`'s reconstruction all write this way; restoring-after-refusal is
gone from all three.

And the published chain is now validated **by name, before the re-chain
arithmetic touches it**: the refusal says the published timeline's chain
is not valid, rather than blaming the local re-chain for damage it
inherited. That is what `pc-c6b8`'s "an invalid published timeline is
named and refused before hydration or re-chain" asserted, made true on
the branch where it was not.

<a id="record-text-may-not-become-chart-grammar-defect-pc-87e0"></a>

_v2.12 amendments, source line 2139 — allocates no code._

`pc-8f0a` closed the mermaid output boundary at the CHARACTER level:
`mermaid_label()` replaces every character mermaid reads as syntax, and
`graph` additionally emits its labels quoted (`["<id>: <title>"]`). The
gantt projection emits its task labels UNQUOTED, and gantt has a second
hazard no character class can see — **its statements are recognized by a
keyword at the start of a line**. A milestone titled `title ATTACK` was
therefore emitted as

    title ATTACK (pc-…) :crit, pc_…, 2026-01-01, 2097-12-31

which a renderer reads as the chart's `title` directive, and one titled
`section ATTACK` as a section break. Record text altering the chart
grammar: the same class one level up, on the one mermaid surface that
does not quote. The records were structurally valid throughout — `check`
is clean over both — which is the point: this is an output-boundary
failure, not a record-contract one.

Amended: **a gantt task label whose first word is a gantt statement
keyword is emitted quoted.** The keyword set is a deliberate superset of
the grammar's own (`gantt`, `title`, `section`, `dateFormat`,
`axisFormat`, `tickInterval`, `includes`, `excludes`, `todayMarker`,
`inclusiveEndDates`, `topAxis`, `displayMode`, `weekday`, `accTitle`,
`accDescr`, `click`): a word wrongly included costs one escaped label, a
word wrongly left out costs the boundary. Quoting is safe to STATE rather
than hope, because `mermaid_label()` has already replaced every `"` with
a space — the two quotes are the only ones on the line, and record text
cannot close them early.

SCOPE, narrower than it may read: this guarantees the line is not read as
a directive. Whether a particular renderer displays the quotes inside the
task name is that renderer's business, and a visibly escaped label is the
honest outcome either way — it says a transform happened. Every other
label is byte-identical to before, so no existing chart changes and
v2.2's "`--mermaid` is byte-for-byte the prior default" holds for every
chart that was not exploitable.

`dev/report.py` carries the same escape **in its own right**, not by
inheritance: its `sanitize_mermaid()` rebuilds each row through a strip
that removes `"` and `()`, so it would delete the CLI's quotes and its id
parenthetical and put the keyword back at the start of the line. It is a
consumer of this format rather than an importer of the CLI, so it keeps
its own copy of the keyword set and a test asserts the two agree.

### v2.13 amendments (2026-09-14 — the round-9 fix sitting: closing the

<a id="the-refusal-contract-spans-the-command-not-only-the-write-primitive-defe"></a>

_v2.13 amendments, source line 2192 — allocates no code._

v2.12 declared that **a recovery path that refuses leaves the store
byte-identical, because it never opened it**, and made that true of the
write primitive. It was not true of `sync`. Both branches ordered the
work `write_log_validated` (the `os.replace`) → `write_snapshot` → the
local register CAS, so a concurrent publication landing in that window
refused at exit 2 with `log.jsonl` **and** `work.jsonl` already changed.
The control was on the same command: a refusal ordered BEFORE the rename
left both files byte-identical, so the discipline existed and this
refusal escaped it — the declaration held for everything the command did
up to the rename and for nothing it did after.

Amended: **the declaration is a property of the COMMAND.** Every exit of
a recovery path, not only the exits ordered before its write, leaves the
store byte-identical. The mechanism is that the last-moment external work
a write must not outlive — for `sync`, taking the publication register —
runs from inside the staged write, after validation and before the
rename, and its refusal is a refusal of the whole write. `sync`'s hydrate
and re-chain branches share one register helper, so the CAS, the VP20
read-back and the rewind guard below are ordered the same way on both.

SCOPE: this is an ordering guarantee about refusals, not a transaction. A
failure of the rename ITSELF after the register has moved leaves the
register ahead of the store — which is the state a fresh clone is already
in, and which `sync` is the recovery for. The direction is chosen
deliberately: a register ahead of a store is recoverable by the command
that just refused, and a store ahead of its register is what `pc-b4b3`
and `pc-4d57` exist to prevent.

<a id="sync-may-not-unpublish-a-landed-revision-defect-pc-1bb6"></a>

_v2.13 amendments, source line 2222 — allocates no code._

The register CAS refusal names its own remedy: the concurrent publication
"stays the winner (first publish wins, format-v2.md 5) … run `pecia sync`
again to reconcile against the new publication". That remedy rewound the
winner. `sync` resolved `remote_commit` from the fetched remote ref and
moved the local register to it under a CAS against the value it READ,
with no test of what that value published — so when a publish took the
local register and its remote half failed (`refs/pecia/log` ahead of
origin), the prescribed retry exited 0 `synced: true` while moving the
register back to origin's older commit, and the next publish wrote an
origin from which the winner's record was absent. The record stayed
recoverable from the winning store; what was lost was its place in the
serializing register, by the remedy the refusal itself named.

Amended: **`sync` refuses to move the local register to a timeline that
drops a record the register publishes, or that rewinds one's revision.**
The test is CONTENT, not ancestry. The ordinary recovery IS a divergence:
`publish` moves the local register before it pushes, so a push refused by
an advanced remote leaves the register on a commit the remote does not
contain, and that is exactly the state `sync` exists to reconcile — an
ancestry test would refuse the flow it lives inside. What may never
happen is loss, so the comparison is between the head revision per record
id that the register currently publishes and the timeline this `sync` is
about to write, which is what the next `publish` will deliver. A
divergent register whose content survives the re-chain passes; a register
holding a record, or a revision, the new timeline drops is refused at
exit 2 with the delivering remedy (`pecia publish`) rather than the
reconciling one.

A refusal's named remedy is part of its contract and is tested as such:
the closing control runs the remedy and asserts it resolves.

The guard reads the register's own blob, so the `pc-39f6` rule applies to
it: a blob that cannot be read is an UNKNOWN register, never an empty
one, and an unanswerable guard refuses rather than waving the move
through. A blob that is E001-invalid at the line level is named and
refused, never normalized in comparison (`pc-13f6`).

<a id="a-red-case-arm-is-executed-not-asserted-defects-pc-ba95-pc-eb40"></a>

_v2.13 amendments, source line 2261 — allocates no code._

Two findings, one shape: **the registry certified its own evidence without
running the discriminating case.**

`dev/gates.py --audit` checked that each inline gate's registered MARKERS
were textually present in `dev/hooks/pre-commit`. Markers are the refusal's
WORDS, and a gate can be turned off without touching one of them: changing
`hook-review-isolation`'s condition to `if false` left every marker in
place, so the audit exited 0, reported `registry audit: ok`, and went on
counting that gate among the "13/16 gates proved able to turn RED" — while
all three of the kills its entry names failed, which is the gate being
dead. Deleting the block outright WAS refused, so the audit worked where it
was built to; presence was checked and reachability was not.

Separately, the `query-prose-containment` entry is tested against the
universal that every ledger- or config-derived emitted string is bounded.
Removing only the APPLICATION of `OWNER_CAP` in the `owner` projector — the
constant and the sanitisation both left in place — emitted a 2000-character
owner against a declared cap of 1024, and the exact evidence command the
entry registers passed, as did the separately cited cap class, which tests
the cap VALUES (headroom, finiteness, worst-case volume) and never that
they are applied anywhere.

Declared: **a registered red-case arm must be accompanied by the edit that
disables the gate, and that edit is applied and the arm shown failing.**
`dev/gates.json` gains a `redcase_mutation` per gate — `{path, from, to,
why}` — and `dev/gates.py --prove` copies the tracked tree, applies the
edit, and requires the named arm to FAIL there against the control of the
same arm passing with nothing mutated. It refuses an arm that stays green
under its own disabling edit, an arm already red on the live tree (the
control that has to hold first), an edit that does not apply exactly once,
and an empty denominator. `--run` calls it, so it is not a route nobody
runs. Every one of the seven inline gates carries one.

Amended alongside it: **the audit says what the audit did.** Its summary
line now reads "red-case arms REGISTERED", names `--prove` as the route
that executes them, and reports arms registered without an executable
disabling edit as a declared gap — the same way an unproven gate has always
been reported. Registration is not proof, and the two are named apart. As a
byproduct the registered edit anchors each inline block's GUARD as well as
its words, so a gate disabled as unreachable code reddens the audit at
commit time and not only under `--prove`.

And for an evidence gap of the same shape, the fix is the arm the universal
needs, not another example: the boundedness arms are exhaustive in BOTH
directions — every field projector is declared with the cap it must apply
or declared as emitting no ledger-derived string, so one added later fails
until it is classified — and they carry the discriminating control that a
value AT the cap passes through byte-identical. They live inside the
registered evidence, because evidence that is not registered is not
evidence for the claim.

SCOPE: `--prove` proves that a gate's arm is SENSITIVE to that gate being
disabled. It does not prove the gate is correct — rule 1 still holds, and
correctness stays with the kills.

<a id="the-doctor-surface-and-what-a-d-code-asserts-defects-pc-cbda-pc-af3d"></a>

_v2.13 amendments, source line 2318 — marks `D012`._

`doctor` and its D-codes were an entire diagnostic surface the spec did not
mention (`pc-cbda`): findings built by the same helper, carrying the same
`{severity, code, id, message}` shape the Diagnostics contract defines, over
exit codes that overlap the 0/1/2 contract — a second code namespace sharing
the specified output contract while being entirely unspecified. Silence read
as an oversight rather than as a boundary. It is a boundary, and this states
it.

**The D namespace is an implementation diagnostic namespace, not part of the
record-format contract.** `doctor` answers one question — *is the enforcement
ACTIVE in THIS clone* — which is posture, and rule 1 already says posture is
never correctness. No adapter, sibling tool or external consumer may depend
on a D-code's meaning or its number; pecia's own tests may and do, which is
why the narrower "nothing depends on them" would be false. The namespace has
exactly one authored home, `D_CODES` in `pecia_cli.py`, and `dev/vocab-check`
refuses a disagreement between that table and the call sites in either
direction.

**What a D-code asserts.** Every D-code asserts that a named condition holds
or fails *for the runtime that will actually perform the guarded operation* —
not for a lookalike, and not for the file alone. Three rules follow, and all
three were learned one defect at a time on the same detector:

1. **The oracle rule** (`pc-9663`, `pc-4c7d`): a detector asks the authority
   that decides — git for what git will do — rather than a proxy it can read
   more easily, such as line tokens in a file.
2. **The question rule** (`pc-acaf`): a detector asks the question the guarded
   runtime asks. `is_file()` is not `python3 can open this`, and
   `os.access()` answers for the real uid rather than for the runtime.
3. **The environment rule** (`pc-af3d`, NEW at v2.13): a detector asks that
   question *of the environment the runtime will run in*. Every program name
   the guarded invocation depends on must resolve on the PATH the hook
   inherits — a missing interpreter is not a property of any file, so a
   detector that stops at the filesystem cannot see it.

Derived, rather than added as a fresh detector: **D012 asks whether the
active hook can RUN its resolved checker, in the environment it will run in.**
The template invokes a `.py` checker as `python3 "$cli"`, so D012 requires the
resolved checker to exist, to be openable by python3, *and* `python3` to
resolve on PATH; a non-`.py` checker is executed directly, so it requires
executability instead, and reports the undecidable executable-but-unreadable
case at warning severity rather than asserting an outcome it did not observe.

SCOPE, stated because it is an approximation and should read as one: doctor's
own environment stands in for the hook's. That is what an operator can
actually set and inspect, and it is exactly right for the common case; it is
wrong for a hook invoked by a GUI client with a different PATH, which doctor
cannot see from inside the repository. A D-code is a posture report about the
environment doctor was run in, and running `doctor` from the same shell that
commits is what makes it answer about that shell.

- **D012** — the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repo root; a .py checker must be openable AND python3 must be on PATH; a non-.py checker executable) — the gate fails closed
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.13","by":"pc-af3d"} -->

<a id="a-refusal-hands-the-adopter-a-command-that-resolves-defect-pc-7ab5"></a>

_v2.13 amendments, source line 2374 — allocates no code._

`templates/pre-commit` resolves its checker three ways precisely because an
adopting repository has no `pecia_cli.py` at its root (`pc-d182`). Its
staged-ledger refusal then ran that checker with `>/dev/null` and printed a
FIXED recovery line naming `python3 pecia_cli.py check …`. In a PATH-only
adoption — the case the resolution order exists for — the refusal therefore
threw away the E001 it had just computed and handed the adopter a command
that exits 2, "No such file or directory". The verdict was right and only the
diagnostic was wrong, which is the `pc-91a0` shape at the other branch of the
same hook.

Amended: **a refusal reports what the checker said, and any command it
prescribes is one this hook could run.** The findings are captured and printed
under the refusal — the treatment the projection-binding branch already gave
them — and every prescribed command names the checker THIS hook resolved,
spelled as a human would type it. Both branches share that one spelling, so
the two cannot drift.

<a id="a-refusal-s-words-are-true-of-the-value-it-refused-defect-pc-3942"></a>

_v2.13 amendments, source line 2393 — marks `E018`._

E018 exists for a target that IS date-shaped and names no calendar day
(`9999-99-99`): `check` passes it at exit 0 by design, which is what makes
the projection the only place it can be caught. A value that is not
date-shaped at all is a DIFFERENT state — `check` refuses it with E001 — and
both reached the same sentence, whose text states as fact that the value is
"date-shaped but not a calendar day" and that "the structural gates accept
it (E001 is shape-only, v1.12)". Both halves are false of the second state,
and the second half tells the reader the checker accepted something the
checker rejects. The verdict was right throughout — exit 1, no partial
chart — and so was the remedy.

Amended: **E018's message says which of the two states it found.** A
shape-valid value keeps the v2.8 wording, which is true of it. A value that
is not date-shaped is named as such, with the fact that `check` refuses it
too and that it reached the projection through a write that bypassed the
gate or an import that never ran one — and its value is emitted through the
full output bound rather than through the day-part slice, which would show a
misleading ten characters of something that is not a date.

E018 stays one code for one refusal — a value a projection cannot place —
rather than splitting, because the projection's decision is the same in both
states; what differs is what the operator should do next, which is what a
message is for. `check` still never emits E018.

- **E018** — unprojectable value: a value a projection cannot place as the calendar day it needs. Two states, distinguished in the message: a date-shaped `created` or `target` on a milestone that is not a real calendar day, which the structural gates accept (E001 is shape-only); and a value that is not date-shaped at all, which `check` refuses with E001 and which reaches a projection only through a bypassed or unrun gate. Emitted by the projection commands as an error finding at exit 1, naming the record and the field, BEFORE either rendering. NEVER emitted by `check`.
  <!-- vocab: {"action":"amend","code":"E018","at":"v2.13","by":"pc-3942"} -->

<a id="the-formal-layer-pins-correspondence-not-only-shape-defects-pc-d0dd-pc-8"></a>

_v2.13 amendments, source line 2422 — allocates no code._

Two findings, one gap: the gate and the model pinned STRUCTURE while
leaving the mapping to the code unpinned.

`pc-7d3f` moved the Field-transfer check from occurrence to transfer — each
enum member must carry its own `rechainedBy` clause transferring its own
distinct `Line` field. That is a BIJECTION between members and fields, and a
bijection survives a swap: a traps file with the `FStatus` and `FBlocks`
transfers exchanged keeps six members mapped to six distinct fields, keeps
every correspondence wrong, and passed. The pinned jar returned the same
counts at exit 0 over the swapped fixture as over the shipped companion, so
no layer of the gate — static or full — refused traps that transfer the
wrong fields. A deleted clause was still refused by name, so the check
worked where it was built to.

Amended: **the member-to-field mapping is the MODEL's, and each transfer
must match it.** `fun diff` says which `Line` comparison derives each enum
member; `dev/alloy-gate.sh` reads that mapping (parameter names taken from
the signature, not assumed) and requires each `rechainedBy` clause to
transfer exactly that field. It refuses a `diff` it cannot read rather than
falling back to distinctness, which a swap satisfies (VP4); it refuses an
enum member `diff` derives from no comparison, since nothing then says what
the member stands for; and distinctness is KEPT beside correspondence rather
than folded into it, because it is what catches a `diff` whose own mapping
collapses two members onto one field — which correspondence alone would then
certify.

The same gap in the model itself: **V12's non-vacuity witness is the code's
append.** `SomeRechainedCasStep` was satisfiable with `mine` on a different
record (`rechained` constrains `out.lid` and never `mine.lid`) and with `e`
outside the log's entry set, so the run did not pin the same-record append
`sync` performs, while the coverage sentence said it did. The witness now
carries `mine.lid = base.lid`, `e not in L.es` and `chainCas[L, e]`, and the
gate's 16/16 is what says it still has an instance — the strengthening was
available all along, which is what made this a gap rather than a modelling
limit. No theorem was weakened and no CLI behaviour was wrong: the prose
read stronger than the model, and the model has been brought up to it rather
than the prose brought down.

SCOPE, unchanged: the tier stays `machine-checked(scope)` — exhaustive up to
the scopes in the `.als` files and silent beyond them. Correspondence
between the model and the Python is still fixture-bound, not proved.

### v2.14 amendments (2026-09-17 — the round-10 fix sitting: closing the

<a id="the-projection-is-part-of-the-commit-point-defect-pc-29ee"></a>

_v2.14 amendments, source line 2475 — allocates no code._

v2.13 made the refusal contract a property of the COMMAND by taking the
register from inside the staged write. It left one write outside: the
projection. `sync` with an unwritable `.pecia/` renamed the log, advanced
`refs/pecia/log`, and then died on `write_snapshot` — exit 2 with both
digests changed, the register moved, and `check` red under E015 at the
head the witness still recorded. A step worse than the state v2.13 fixed,
whose refusal at least left the store clean.

Amended: **the projection is written at the same commit point as the log.**
`.pecia/work.jsonl` and `.pecia/snapshot.head` are staged in their own
directory before any external register is taken, and committed by rename
immediately after the log's rename. So a projection that cannot be written
is a refusal with the store byte-identical and the register unmoved, and
the two projection files can no longer move apart — content regenerated
under the old recorded head is precisely the fork E015 reports.

The ordinary write path is held to the same rule: `add`, `edit` and
`close` stage the projection BEFORE appending to the log, so an
unwritable projection leaves the log at its old length rather than one
entry ahead of its witness. `migrate` projects through the same commit
point. `publish` never opens the store; `snapshot` never touches the log.

SCOPE: this is an ordering guarantee about refusals, not a transaction.
The residual window is the two renames within one directory after a
staged write has already succeeded — one syscall each, both over files
that exist, in the directory the staging just proved writable.

<a id="a-register-publishes-revisions-not-revision-numbers-defect-pc-9f60"></a>

_v2.14 amendments, source line 2504 — allocates no code._

v2.13 stated the rewind guard's comparison as "the head revision per
record id that the register currently publishes and the timeline this
`sync` is about to write". That is the number, and two stores can write
different content at the same number: store A won the local register with
`pc-X` rev 2 and failed its remote half, store B wrote its own rev 2, and
B's `sync` exited 0 having rewound the register past A's publication —
which then sat on no register and no remote, with the two rev 2s drawing
E002 wherever they met. The claim states the universal as LOSS; a number
cannot express it.

Amended: **the comparison is `(id, rev) -> canonical content`.** A
revision the register publishes passes if the timeline this `sync` would
write carries it, OR if the LOCAL timeline the re-chain derives from
does. The second clause is not a weakening, it is what keeps the ordinary
recovery legal: a re-chain RENUMBERS the local suffix, so the register's
own rev 2 legitimately becomes rev 3 and its content is not at that
number any more — a content test without it would refuse the flow the
guard lives inside. What the clause does not cover is content this store
has never held, which is the other store's publication above.

<a id="a-refusal-s-remedy-terminates-and-sync-gained-the-command-it-needs-defec"></a>

_v2.14 amendments, source line 2526 — allocates no code._

The same-field conflict refusal prescribed "re-read the record and
re-apply your intent", and v2.13 declared that a refusal's named remedy
is part of its contract. Followed verbatim, that remedy does not resolve:
the re-applied edit is a NEW local revision on top of the losing one,
both touch the field, and the next `sync` refuses naming both. There was
no in-place resolution to have missed — the missing step was discarding
the losing local-only revisions, and no command could do it.

Amended: **`pecia sync --take-landed ID` resolves a same-field conflict
by taking the landed revision.** Every local-only revision of ID that
conflicts is dropped from the re-chain and NAMED in the result — id, your
rev, the landed rev, and the FIELD NAMES, never a field's value, so the
output-withholding contract (§v2.4, claim 25) is untouched. The rest of
the local suffix re-chains normally. The writer then re-applies the
intent on top of the landed state with `pecia edit`, and that sequence
terminates from the first refusal and from the two-conflict state the old
wording produced. The refusal's note says all of this, including what
re-applying alone does.

The flag discards revisions, so it is refused rather than assumed: an ID
with no same-field conflict in this `sync` is a cannot-run, and so is the
flag on the hydrate branch, where there are no local-only revisions to
discard. `discarded` joins `rechained` and `skipped_noops` in the success
report, and the three sum to the suffix examined (§v2.10).

<a id="an-explicit-remote-is-honoured-or-refused-never-discarded-defect-pc-719e"></a>

_v2.14 amendments, source line 2553 — allocates no code._

`publish` and `sync` took their no-remote branch BEFORE reading
`args.remote`, so an explicitly named remote vanished: `sync --remote
NAME` exited 0 `synced: true` against the local register and `publish
--remote NAME` exited 0 reporting `remote: none — …`, with the name
nowhere in either output, while `--remote`'s own help presents it as the
selected remote.

Amended: **a `--remote` naming no configured remote is refused and
named**, on both commands and both branches — with no remotes configured
the refusal says so and states that the local register is the whole
timeline here; with remotes configured it lists them. The declared v2.6
no-remote fallback is unchanged when no name is given. `sync` refuses at
exit 2, having fetched and written nothing; `publish` refuses at exit 1
with `published_local` true, because the local half did land and it is
delivery that did not happen. This also removes a false diagnosis the
reproduction found beside the defect: a push to a nonexistent remote used
to report "the remote advanced — run `pecia sync`".

<a id="append-only-is-scoped-where-the-store-is-described-defect-pc-00b0"></a>

_v2.14 amendments, source line 2573 — allocates no code._

§3.1, the v2.6 store paragraph and the `v2-storage` claim described the
STORE as append-only with no scope line, and an ordinary unforced `sync`
shrinks a clean local store: a no-op revision (`edit --priority 2` on a
record already at priority 2) leaves a clean two-entry log that `check`
passes, and the next offline `sync` returns it to one line with
`skipped_noops: 1`. That is claim 35's declared skip and the behaviour is
correct; the unqualified word is what does not hold.

Amended: **the local store is append-only under every write path but
reconciliation.** `add`, `edit` and `close` only append. `sync` re-chains,
which rewrites the local log by construction: the local-only suffix is
renumbered onto the published head, a revision that changes nothing is
skipped, and `--take-landed` discards the revisions the writer names.
What is append-only with no qualification is the PUBLISHED timeline —
`refs/pecia/log` only ever gains entries, which is what the
published-prefix fork refusal and the rewind guard enforce.

<a id="d012-derives-its-program-names-from-the-files-that-get-executed-defect-p"></a>

_v2.14 amendments, source line 2592 — marks `D012`._

v2.13 stated the environment rule as a universal — a D-code asserts its
condition "for the runtime that will actually perform the guarded
operation", and "every program name the guarded invocation depends on
must resolve on the PATH the hook inherits". The implementation
enumerated ONE name, `python3`, for ONE checker kind. Two environment
failures were therefore invisible, each measured on a PATH carrying every
other utility the hook uses: a resolved extensionless checker whose own
shebang reads `#!/usr/bin/env -S uv run --script` with `uv` absent, and
the hook's own `#!/usr/bin/env bash` with `bash` absent. Both read
`ok: true`, 0 errors, 0 warnings while every ordinary commit died, and
both cleared by adding ONLY the missing program to the same PATH. The one
pair v2.13 did implement (`.py` + `python3`) drew D012 correctly.

This is the doctor detector class's fifth level and the first that is a
SPEC/IMPLEMENTATION DISAGREEMENT rather than a further under-specification:
the spec stated the universal correctly and the code enumerated one case
of it. What bounds the class is therefore not a sixth detector.

Amended: **the program-name set is DERIVED from the files that get
executed, not listed.** D012 reads the interpreter chain out of the hook
git will exec and out of the invocation the hook performs — `python3` for
a `.py` checker, the checker's own shebang chain when the hook execs it
directly. The two kinds of requirement are distinguished, because they are
different questions: `#!/usr/bin/env foo` is a PATH lookup performed by
`env` (the error is `env: foo: No such file or directory`), while
`#!/bin/sh` is an absolute path the kernel execs and PATH says nothing
about it. Asking the PATH question of an absolute shebang would be a false
red of the kind `pc-709d` names.

SCOPE, stated so the next level is not mistaken for this one: what is
derived is the INTERPRETER CHAIN of the two files that get executed. The
POSIX utilities the hook body calls (`git`, `mktemp`, `sed`) are not
enumerated — a missing `git` is not a state a git hook can be reported in,
and guessing command words out of shell text is a false-red risk with no
demonstrated failure behind it.

- **D012** — the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repo root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs — the hook's own shebang, and `python3` or the checker's shebang — resolving, derived from those files rather than listed) — the gate fails closed
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.14","by":"pc-a2da"} -->

<a id="a-prescribed-command-survives-the-path-it-names-defect-pc-c59a"></a>

_v2.14 amendments, source line 2633 — allocates no code._

v2.13 made a refusal's prescribed command part of its contract: it names
the checker THIS hook resolved, spelled as a human would type it. The
template built that spelling by concatenation (`cli_cmd="python3 $cli"`)
and echoed it unquoted, so a checker at a path containing a space — the
measured fixture was `vendor space/pecia cli.py` — was printed as a
command that splits at the space and exits 2, `python3: can't open file
'…/vendor'`, when retyped. `run_cli` quotes `"$cli"` and was unaffected,
so the gate's verdict and its captured findings were correct throughout:
the remedy named the checker and then unnamed it at the first space.

Amended: **a command a refusal prescribes is quoted for the shell that
will retype it.** The template emits the checker through `printf %q`, and
`doctor`'s remedies — which interpolate paths the same way (`chmod +r
<checker>`, `git config core.hooksPath <dir>`) — quote them through
`shlex.quote`. An ordinary path is unchanged by either, so only the paths
that needed quoting acquire any.

<a id="the-formal-gate-reads-the-predicate-not-the-file-defect-pc-d405"></a>

_v2.14 amendments, source line 2652 — allocates no code._

v2.13 moved the Field-transfer check from occurrence to correspondence and
left its SCOPE unexamined: the clauses were matched over the whole
comment-stripped traps module, so transfer-shaped text anywhere in the file
counted as coverage for a member. Delete the `FStatus` clause from `pred
rechainedBy` and append a separate, unchecked `assert
UncheckedDecoyTransfer` carrying the same text, and `--static` exited 0 with
the predicate mentioning `FStatus` nowhere — and the full pinned-jar run over
that fixture returned 16/16, 0/4, 0/3, "all expectations met", identical to
the shipped companions. No layer, static or solver, refused a companion that
had stopped transferring a modeled conflict unit.

Amended: **the transfer clauses are read from `pred rechainedBy`'s own
brace-balanced body.** A check that cannot find that predicate refuses rather
than falling back to the file, because falling back is precisely the
behaviour that made the decoy work (VP4).

This is the third instance of one shape — a COMMENT naming a member
(`pc-7d3f`), two transfers SWAPPED (`pc-d0dd`), and now transfer-shaped text
OUTSIDE the predicate — and the three together say what the class is: each
time, the check asked a question of the FILE when the property belongs to one
declaration. The general form of the fix is the one used here and at
`pc-cb505`: parse to the declaration the property is about, then ask.

<a id="a-repair-that-narrows-a-grouped-finding-is-not-a-new-error-defect-pc-54c"></a>

_v2.14 amendments, source line 2677 — allocates no code._

The write gate refuses a write iff it introduces NEW error findings, and
`pc-b60a` gave a record-contract finding its revision (or failing that its
line) as identity. A GROUPED finding — E016 naming every open blocker, E012
naming every terminal claimant, E009 naming every active head — has no such
identity: its identity was the whole serialized text, so dropping one of two
blockers rewrote the message and the improvement read as a newly introduced
error, refused at exit 1 quoting the NARROWED finding. The same edit where
there was only one blocker landed at exit 0, so the gate was not refusing
edits to blockers; it was refusing the repair that leaves a smaller version of
the same finding standing. E009's three-head case moved its anchor as well, so
the serialized finding differed in `id` too. Ordinary recovery of a
multi-participant finding therefore required `--force`, which brands a repair
as an escape-hatch use — and E016's own printed alternative, "reopen this
record", is refused by E005, so neither branch of its advice ran unforced.

Amended: **a finding that groups participants declares them, and a write is
not refused for leaving a strictly smaller version of an existing finding
standing.** The participants are the checker's to declare — never the gate's
to parse back out of prose — and they travel beside the finding rather than
inside it, so the emitted shape stays `{severity, code, id, message}`.

The comparison is by (code, SUBJECT, participants), and the subject is what
keeps the rule honest: E016 and E012 anchor on the record the finding is
about, while E009 anchors on a mere representative (the lowest active head,
which moves when that head is superseded). A second record going terminal
under one of the same blockers is a different subject and stays refused even
though its participants are a subset; a write that ADDS a participant makes
the set larger and stays refused too.

E016's printed alternative is amended with it, per the v2.13 rule that a
refusal's named remedy is part of its contract: closing the blocker is the
other repair that clears the finding, and the message says plainly that the
terminal record cannot be reopened.

### v2.15 amendments (2026-09-18 — the round-11 fix sitting: closing the

<a id="an-amendment-that-moves-a-meaning-restates-it-where-the-machine-reads-it"></a>

_v2.15 amendments, source line 2722 — allocates no code._

v2.14's `pc-54c7` moved E016's semantics: its printed alternative "reopen
this record" was replaced, BECAUSE E005 refuses it, by "drop the `blocks`
claim that no longer holds, or close the blocker(s); this record cannot be
reopened (E005: terminal is final)". The implementation carries the new
message and the narrative above states the rule — and the §v2.4 owner
bullet that `spec/vocabulary.json`'s `meanings` view is generated from was
not amended, so its only vocab event remained the v2.4 mint. The registry
therefore served the superseded gloss as current, and the remedy it served
is one the object itself refuses: performing it (`edit <id> --status open`
on the terminal record) exits 1 naming E005. `dev/vocab-check.py` was clean
at exit 0 with 89 markers throughout, because V013 sees the DECLARED case —
a live code whose latest mint or amend event carries no gloss — and nothing
told it an amendment elsewhere in the document had obliged one.

Amended: **a code's owner bullet is amended in the same sitting as the
semantics, and carries its own vocab event.** E016's bullet now states the
v2.14 remedy and carries an `amend` marker at this level, so the served
gloss and the emitted message say the same thing. What is NOT claimed: no
gate derives the obligation. V013 refuses a marker with no gloss; it cannot
see that a paragraph elsewhere moved a meaning, and reading English for
that is the false-red risk `pc-709d` names. What bounds the class is the
practice — the amendment and the bullet move together — and the check that
exists is claim 11's, over what the registry serves.

Beside it, in the generator rather than the spec: the `meanings` view kept
the closing `**` of a bullet whose bold span covers the code AND its name
(`- **E016 closed-while-blocked** — …`), because the extractor stripped the
code and left the rest of the span. Two of nineteen bullets are written that
way, E000 and E016, and both served a stray `**` inside the gloss. The
extractor now takes the leading bold span whole and keeps the name it
carries.

<a id="refs-pecia-log-names-two-refs-and-only-one-of-them-is-append-only-defect"></a>

_v2.15 amendments, source line 2756 — allocates no code._

v2.14 scoped "append-only" off the local store and onto the published
timeline (`pc-00b0`), naming the published timeline by a ref name — and
`refs/pecia/log` is the name of TWO refs. The one delivered to the remote
only ever gains entries. The CLONE-LOCAL ref of that name, which `publish`
writes and reports as `published_local: true` even when delivery fails, is
replaced by an ordinary `sync` in a losing clone: measured, the losing
clone's ref holds `['base', 'A local-only']`, and after `sync` it holds
`['base', 'B landed']` — a different blob of the same length, not a prefix,
not an ancestor. Nothing is lost (the reconciled store keeps all three
records and `check` is clean) and the behaviour is the reconciliation
design. The scoping moved the imprecision rather than closing it: from
WHICH TIMELINE is append-only to WHICH REF the word names.

Amended: **what is append-only with no qualification is the timeline as
DELIVERED — the ref on the remote that `publish` advances by
compare-and-swap.** The clone-local ref of the same name is this clone's
record of what it has published or fetched; a losing publish leaves it
ahead of the remote, and the `sync` that reconciles replaces it. The word
now names the thing the fork refusal and the rewind guard actually enforce,
which is a ref one clone cannot rewrite, rather than a name two refs share.

<a id="d012-reads-an-env-s-string-as-env-reads-it-defect-pc-fc1e"></a>

_v2.15 amendments, source line 2779 — marks `D012`._

v2.14 (`pc-a2da`) derived D012's program names from the interpreter chain
of the files that get executed. The derivation split a `#!` line on
whitespace and took the first non-flag token verbatim — but under `-S` the
remainder of the line is ONE argument that env splits itself, and there
quotes are syntax. A checker whose shebang reads `#!/usr/bin/env -S 'sh'`
runs `sh` while the reader looked for a program literally named `'sh'`,
found none, and reported that every ordinary commit dies. Every ordinary
commit succeeded — the false red the v2.14 amendment names as the hazard it
was built to avoid, arriving through quoting instead of through
absoluteness.

Amended: **D012 reads the interpreter chain with the lexical rules of
whatever will read it.** Under `-S`: quote grouping of both kinds,
backslash escapes, `${VAR}` substitution, and a `#` that starts a token
beginning a comment. Outside `-S` the argument env receives is the literal
text of the line, and it stays literal — a shebang that says `env 'sh'`
really does ask for a program of that name, and stripping quotes everywhere
would silence a true finding to cure a false one.

**A name the line COMPUTES is a posture doctor cannot read**, and is
reported as such: a `${VAR}` that doctor's own environment does not carry
draws a WARNING naming the variable, never an error naming a program. This
extends the v2.13 scope line — doctor's environment stands in for the
hook's — to say what happens when that stand-in cannot answer. It is the
same rule as the executable-but-unreadable hook: report the condition
verified, not the outcome not observed.

- **D012** — the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repo root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs — the hook's own shebang, and `python3` or the checker's shebang — resolving, derived from those files rather than listed and read with the lexical rules of whatever reads them, so an `env -S` string's quotes, escapes and `${VAR}` are env's syntax and a name computed from a variable this environment does not carry is reported undecided rather than missing) — the gate fails closed
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.15","by":"pc-fc1e"} -->

<a id="the-checker-value-is-one-expression-tested-and-executed-defect-pc-cb43"></a>

_v2.15 amendments, source line 2811 — allocates no code._

The adopter template resolved `cli="$PECIA_CLI"`, tested it with
`[ -f "$cli" ]` — path semantics — and then executed a non-`.py` checker as
`"$cli" "$@"`, which for a value containing no slash is a PATH LOOKUP. A
checker at the work-tree root under a bare relative name therefore passed
the hook's own existence test and died at execution with `command not
found`, while doctor — resolving the same value as a path, which is what
v2.10 (`pc-2f19`) settled — reported `ok: true`, 0 errors. This is the
sixth level of the doctor detector class and the first that is not about
which QUESTION the detector asks: the hook's own two references to the
value disagreed, so no stand-in resolving it once could match both.

Amended: **`$PECIA_CLI` is a PATH, and a relative one resolves from the
work-tree root.** The template makes the value absolute at resolution,
before anything reads it, so the value tested, the value executed and the
value printed in a refusal's prescribed command are one expression — and it
is the reading doctor already used, which is what gives the detector a
single subject again. The rule is stated here rather than left to the two
implementations because it is the disagreement, not either half, that was
the defect.

<a id="the-register-is-one-document-to-every-reader-defect-pc-4b3c"></a>

_v2.15 amendments, source line 2833 — allocates no code._

`claims.yaml` has three readers: a raw line scan (`dev/claims_ids.py`, used
by the gate join and the foreign-reference resolver, deliberately without a
YAML parser so it can run inside a hook), a PyYAML parse
(`dev/claims-check.py`, `dev/prose-check.py`), and a round-trip editor
(`dev/claims-edit.py`). A file with TWO top-level `claims:` keys made the
scan read the UNION of both blocks while PyYAML gave the LATER key
authority, so `dev/gates.py --audit` accepted a gate naming a claim the
checker never saw, both at exit 0. That is v2.14's `pc-3472` reached by a
route it did not consider: it bounded WHICH lines carry an id and not HOW
MANY `claims:` keys there are.

Amended: **a repeated key in the register is a malformation, and every
reader refuses it by name.** The divergence is last-key-wins versus union
and there is no reading that makes both right, so no reader picks a winner.
Each refuses in the terms it can see: the parserless scan refuses a second
top-level `claims:` key (column 0 is the only structure it can trust —
YAML cannot put an unindented line inside a block scalar), and the parsing
readers refuse a repeated key at any depth through one shared strict
loader. The editor already refused, by raising; it now refuses in its own
cannot-run vocabulary with nothing written.

<a id="the-formal-gate-reads-alloy-s-lexical-structure-defect-pc-6196"></a>

_v2.15 amendments, source line 2856 — allocates no code._

v2.14's `pc-d405` moved the transfer check from the whole traps module to
`pred rechainedBy`'s own brace-balanced body, under the general form "parse
to the declaration the property is about and then ask" — and implemented it
with a brace counter that does not know what a string literal is. `some
"{"` inside the predicate raised the depth and `some "}"` inside a later,
unchecked `assert` returned it to zero there, so the extracted body ran
past the predicate's real closing brace and swallowed a decoy: the FStatus
transfer counted as covered while the predicate constrains `out.status`
nowhere, with the pinned jar reporting 16/16, 0/4, 0/3 at exit 0. Fourth
instance of one shape, and the first inside the fix meant to close it.

Amended: **the lexical question is asked once for the whole file and every
check reads that answer.** Comment text and string-literal contents are
blanked (length and line breaks preserved, so offsets and line numbers
stand), and the declaration scan, the `enum Field` and `fun diff` body
extractions and the predicate extractor all read the blanked text. This
closes the direction that LOSES refusals as well as the one that invents
them: a `--` or `*/` inside a literal used to blank live code from every
check that reads the stripped source, and a `pred foo` inside one was
extracted as a declaration.

SCOPE: this is a lexer, not a parser. It knows comments, string literals
and `'` as an identifier character; it does not know Alloy's grammar, and a
check that needs the grammar still has to say so.

### v2.16 amendments (2026-09-18 — the round-12 fix sitting: closing the

<a id="a-diagnostic-s-contract-is-not-at-one-end-of-it-defects-pc-eed1-pc-6b00-"></a>

_v2.16 amendments, source line 2891 — marks `E001`, `E014`, `E016`._

Every E-code message is built the same way — `<the condemned value> <what
is wrong> <what to do>` — and `finding()` passed the ASSEMBLED string
through `safe_text(message, MESSAGE_CAP)`, which truncates from the END.
So whenever an authored value was large enough to reach the cap, what the
cap ate was exactly what the message promised at its tail. Three
registered universals failed on that one mechanism, none of them about a
missed refusal:

- E001's identity. A `type` of 9,000 characters produced a finding of
  exactly 8,192 characters containing no `[rev` at all, against a
  universal that had been stated twice (v2.8 `pc-3bdc`, v2.12 `pc-b60a`)
  and strengthened once precisely so it could not fail.
- E014's diagnosis. With 1,202 derived fields the printed array consumed
  the message and "non-canonical order" — the thing v2.9 says this
  finding NAMES as its refusal — never appeared.
- E016's remedy. Under 1,000 open blockers the finding named 817 of them
  and then stopped mid-list, taking with it "close the blocker(s)", the
  alternative v2.14 added under the v2.13 rule that a refusal's named
  remedy is part of its contract. A reader could neither reach 183 of the
  records the finding names nor see what to do with the ones it does.

Amended: **the variable part of a diagnostic is bounded at composition,
never the assembled whole.** One authored value and one participant list
each get a stated budget (512 characters, elements 128), so the fixed
tail — the identity tag, the diagnosis, the remedy — is never what the
cap sees. `MESSAGE_CAP` is unchanged and stays what it was: the ceiling
on an emitted message, not the instrument that decides what survives.

**A bounded list says how much it did not say.** For E016 and E012 the
list IS part of the remedy, so a truncated one carries a count and an
explicit notice — `… and 953 more of 1000 (…7cd4791e)` — rather than the
bare digest a truncated scalar carries. This NARROWS v2.14's "the finding
declares its participants — every open blocker": the emitted message
names as many as its budget allows and states the total; the finding's
own `group`, which is what the write gate's narrowing rule reasons about,
still carries every participant and is unaffected. Participants travel
beside the finding, not inside it, which is why the narrowing is a
statement about output volume and about nothing else.

The notice carries a digest for the reason `safe_text`'s does: a grouped
finding's identity in the write gate is its serialized text, so two
different participant sets sharing a prefix and a cardinality must not
render as one string.

SCOPE: the siblings share the emitter and are fixed with it — E012's
claimant list, E009's active heads, E004's cycle, E008's gap list, E003's
edge target, E005's two statuses, E011's scheme, E013's declared `seq`,
and the write gate's own `write refused: … (--force to override)`
wrapper, whose tail could be eaten by an inner message already at the
cap. None of those was probed by the pass; all are the same call site's
problem. What is NOT claimed: no gate derives this obligation. A future
E-code that assembles a message by hand can still put its contract behind
an unbounded value, and what bounds the class is the budgeted composition
helpers plus the arms in `DiagnosticContractSurvivesTheCap`.

- **E001** — the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON within the declared nesting bound, no blank lines, fields and edges typed and shaped; every finding carries its revision, or its source line where the revision cannot identify it, and the condemned value is bounded so the identity tag cannot be truncated away
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.16","by":"pc-eed1"} -->
- **E014** — CAS violation: rev is not head+1 at its log position, or touched differs from the recomputed diff, compared as the canonically sorted array (v2.9), presence-aware on extension fields (v2.7); a malformed envelope is a finding, never a crash; the printed arrays are bounded so the finding still names non-canonical order as what it refuses
  <!-- vocab: {"action":"amend","code":"E014","at":"v2.16","by":"pc-6b00"} -->
- **E016 closed-while-blocked** — a record in a terminal status while some non-terminal record holds a `blocks` edge to it. The message names as many blockers as its budget allows and states how many there are in all; the finding's participant set carries every one. Resolve it the way the edge is wrong or the closure was early: drop the `blocks` claim that no longer holds, or close the blocker(s); the terminal record cannot be reopened (E005: terminal is final).
  <!-- vocab: {"action":"amend","code":"E016","at":"v2.16","by":"pc-1130"} -->

<a id="the-read-back-decides-delivery-in-both-directions-defects-pc-bc87-pc-239"></a>

_v2.16 amendments, source line 2955 — allocates no code._

§5 states the rule with no branch — "Delivery is confirmed by reading the
ref back, never by `git push`'s exit code" — and v2.8 (`pc-4d57`) extended
it to the local half, "so the sentence is true of both halves". Every
read-back in the publication path was nonetheless placed AFTER the
nonzero-status return, so each could only ever confirm a success the
writer had already reported. A writer that performs the write and THEN
exits nonzero was believed on its exit code alone — the one direction the
guard was never asked about, because the hazard it was built for was a
writer lying about success.

Both commands then made a false statement about a state they had produced
themselves. With a `git` whose `push` really pushes and then exits 1,
`publish` reported `published_remote: false` at exit 1, with the remedy
"the remote advanced — run `pecia sync`, then publish again", while the
remote ref equalled the local ref and the remote blob carried the new
record: a remedy for a state that did not exist. With a `git` whose
`update-ref` writes and then exits 1, `sync` refused with "Nothing was
rewound and nothing was written" and blamed a concurrent publisher, while
the register had moved under its own hand and no competitor existed.

Amended: **the ref decides delivery on every branch, including the one
where the writer claims failure.** `publish` reads `ls-remote` before
choosing what to report; `advance_register` and `publish`'s local
`update-ref` read the ref back before choosing between their refusals.

What each now says, because a refusal's words must be true of the value it
refused (v2.13, `pc-3942`):

- the ref carries the new commit and the writer reported failure — the
  delivery LANDED. `published_remote: true`, `read_back_matches: true`,
  `writer_reported_failure` naming the status and the writer's own words,
  and a `next` that says there is nothing to re-run and nothing for `sync`
  to reconcile. Exit 1: the delivery is real and the writer misreporting is
  an error the operator has to see.
- the ref does not carry it — a refusal, whose `next` names what the remote
  ACTUALLY holds: no ref at all, the parent this publish built on, or a
  timeline that has advanced (the one case where `pecia sync` is the
  remedy).
- the read-back itself cannot run — `published_remote: "unknown"`. An
  unreadable target is unknown, not undelivered, which is the `pc-39f6`
  rule applied to the delivery question rather than to the prefix guard.
- `sync`'s register write, on the lied-about-failure branch — the refusal
  says the register DID move and that this command moved it, that the log
  was not written because the refusal is ordered before the rename, and
  that the resulting register-ahead-of-store state is the one `sync` itself
  recovers (v2.12, `pc-d373`). The genuine CAS loss keeps its own message
  and now distinguishes a competing writer from a CAS that failed for a
  reason of its own, by comparing what the ref reads against what this sync
  read.

SCOPE: this changes what is REPORTED, never what is written. No new write
happens on any of these branches, and the store's byte-identity under a
refusal (`pc-d373`, `pc-b652`) is untouched.

NOTED, because it is evidence about the review protocol rather than about
the object: neither command changed between the `v2-pass-r10` and
`v2-pass-r11` tags, and the round-11 frame predicted zero findings in that
lane for exactly that reason. Two defects in the delivery discipline
survived ten rounds of review of unchanged code.

<a id="a-trusted-prefix-is-not-the-chain-defect-pc-1667"></a>

_v2.16 amendments, source line 3017 — marks `E015`._

`read_log` returns at its FIRST error, because a broken chain makes every
later line untrustworthy — so after a parse or chain break the entries a
caller holds are a trusted PREFIX and not the timeline. E015 decides by
locating the recorded head IN those entries, and nothing told it the
difference. Against a two-entry log damaged at its second line, `check`
reported the log error correctly and then emitted E015 against the witness
and projection it had written itself and never touched: "the snapshot is
FORKED, not merely stale (a stale snapshot's head is an ancestor and is
clean)" — a message whose whole content is a discrimination between two
states, neither of which was the case. Measured on two independent damages
(an interior blank line, E001; a broken `seq`, E013), and repairing ONLY
the log returned the store to clean with `work.jsonl` and `snapshot.head`
byte-identical throughout, which is what says the accusation was about the
read.

The remedy compounded it: the finding prescribed `pecia snapshot`, which
reads the same log and exits 2 on the same error, re-emitting the log's own
finding as though a regeneration had been attempted and failed.

Amended: **E015's comparison runs only over a log that read whole.** Where
the read stopped early the comparison is skipped and what is emitted says
so — a warning naming the projection, stating that it and its witness are
untouched and UNJUDGED, pointing at the log's own finding for the line, and
saying explicitly that `pecia snapshot` is not the remedy here. The
verdict is unaffected: the log's error already makes it nonzero, and
nothing is certified clean by this.

`pecia snapshot` says the same thing from its own side: over a log that
does not read whole it refuses naming that as the reason, says the
projection is derived from the log so the log is what to repair, and
states that nothing was written. Every other E015 flavour keeps this
command as its documented remedy (`pc-223c`, `pc-0ff7`).

SCOPE, stated because the suppression direction is the risk: a GENUINE
fork over an intact log still fires E015 at error severity and exit 1, and
that arm is a control here rather than an assumption. What is given up is
a fork signal in the compound state where the log is ALSO damaged — and
there `check` is already nonzero, already tells the operator to repair the
log, and says that the projection was not judged, so nothing is certified
that was not certified before.

- **E015** — snapshot forked, corrupted (non-text bytes in the projection or its witness included, v2.10), or the truncation witness: recorded head in the chain, content matching; the witness blocks regeneration by every writer (v2.8). The comparison runs only over a log that read WHOLE (v2.16): against the trusted prefix a stopped read returns, the question cannot be answered, and what is emitted says the projection was not compared rather than asserting a fork.
  <!-- vocab: {"action":"amend","code":"E015","at":"v2.16","by":"pc-1667"} -->

<a id="d012-resolves-every-relative-path-against-one-base-defect-pc-2745"></a>

_v2.16 amendments, source line 3063 — marks `D012`._

The seventh level of the doctor detector class, and the second instance of
the resolution-disagreement shape after `pc-cb43`. Claim 33's whole subject
is that a D-code "asserts that a named condition holds for THE RUNTIME THAT
WILL ACTUALLY PERFORM the guarded operation — asking the authority that
decides, asking the question that runtime asks, and asking it of the
ENVIRONMENT that runtime will run in". git runs a hook from the work-tree
root, so a relative entry in the PATH the hook inherits names a directory
under THAT root; `shutil.which` resolves it against the calling process's
own directory. One environment string, two resolutions.

Measured with `PATH=badpath:/usr/bin:/bin` and a `badpath/pecia` whose
shebang names a missing interpreter: from the repository root doctor emits
D012 and exits 1; from `deep/nested` the SAME environment gives `errors 0,
ok true`, no findings, exit 0 — and the very next ordinary commit from that
directory dies in the hook at `env: definitely-missing-interpreter: No such
file or directory`.

The class question the round-11 frame demanded an answer to, answered here
rather than deferred. v2.10's `pc-2f19` made a relative `$PECIA_CLI`
root-relative; v2.15's `pc-cb43` made the checker VALUE single. Neither
reached the PATH, and normalizing the value did nothing for this level
because the disagreement is about the BASE.

Amended: **every relative path D012 reasons about resolves against the
work-tree root — one base, not one value.** Program lookups go through
`which_from_root`, which rebases each relative PATH entry (and an empty
entry, which POSIX reads as the current directory) onto the work-tree root
before looking, and resolves a name carrying a directory component the same
way. The pinned-value cases (`$PECIA_CLI`, the hook path) already resolved
from the root and are unchanged.

SCOPE, unchanged and restated because this does not close the class: doctor
still models the hook's environment WITH ITS OWN, which the register
declares as an approximation. What is now single is the base. A variable the
hook would inherit but doctor does not carry is still `computed` and
reported undecided (`pc-fc1e`), and a difference in the PATH VALUE itself —
a hook run by a daemon with a different environment — is outside what any
in-process detector can see.

- **D012** — the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repository root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs — the hook's own shebang, and `python3` or the checker's shebang — resolving, derived from those files rather than listed and read with the lexical rules of whatever reads them, so an `env -S` string's quotes, escapes and `${VAR}` are env's syntax and a name computed from a variable this environment does not carry is reported undecided rather than missing). Every relative path this code reasons about resolves against the work-tree root, the way git will — the PATH's own relative entries included (v2.16) — so the answer does not depend on the directory doctor was invoked from. The gate fails closed.
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.16","by":"pc-2745"} -->

<a id="the-posture-answer-is-a-field-and-the-exit-code-is-a-different-question-"></a>

_v2.16 amendments, source line 3107 — allocates no code._

On a repository with a ledger and no gate, `doctor` printed D010 — "this
repo has a pecia ledger and no write gate is active in this clone, so
nothing checks a record before it is committed. Exit 0 from `check` is
something you ran, not something the repo enforces" — and summarised
`{errors: 0, ok: true, warnings: 1}` in the same output. `ok` meant "no
error-severity D-code", which nothing narrows it to, while claim 33 says
this command reports whether the gates are ACTIVE. The machine-readable
field a caller reads contradicted the finding printed above it.

Amended: **`ok` answers doctor's own question.** It is true when there are
no error-severity findings AND the write gate is active wherever there is
a ledger to guard, with `gate_active` beside it so the reason is read
rather than inferred. A repository that has not adopted pecia has nothing
to guard and stays `ok: true`, or the field becomes noise everywhere it is
about nothing.

**The exit code does not move with it.** D010's warning severity is a
decision, not an oversight (`pc-1eba`, `pc-1121`): an unguarded ledger is
also a legitimate first minute of adoption, and an error would make `init`
be followed immediately by a red `doctor`. So an unguarded ledger is now
`ok: false` at exit 0, which is rule 1's own shape — exit 0 means the
command ran and reported, never that what it reports is good.

The `--fix` half is the same seam and is resolved the other way round.
Claim 34 said "a `--fix` that could not do what it set out to do exits 1",
and the code answered something narrower: a fixer that ATTEMPTED an action
and failed. On the adopter's case `--fix` reports `fixed: []`,
`unfixed: ["D010"]`, no `refused`, and exits 0, because it attempted
nothing and nothing it attempted failed.

Amended: **the claim is narrowed to that, and the surface gains the answer
it lacked.** The exit code of `--fix` says whether what it ATTEMPTED took;
`posture_clean` says whether anything is left, computed after the fixes
from `unfixed` and `refused` — and since v2.10 (`pc-9663`) every automated
credit re-runs its own detector, so an empty `unfixed` means the condition
cleared rather than that the fixer exited 0. `posture_clean` is the field
automation should read, and it is the same repair as `ok`'s on the plain
surface.

WHY THE CLAIM AND NOT THE CODE, written down so the call can be reviewed.
Widening the exit code would overturn `pc-1121`'s deliberate design, whose
own conclusion was that the missing thing on that surface was the ACCOUNT
of what `--fix` did and did not do rather than a red exit. A claim may not
outrun its evidence, and narrowing a claim to what the code does is always
available; the hazard the record is really about — a caller reading a
green — is closed by giving that caller a field that is not green.

<a id="a-refusal-says-where-its-refusal-is-and-is-right-about-it-defect-pc-8fca"></a>

_v2.16 amendments, source line 3156 — allocates no code._

Over a malformed timeline `doctor --fix` exits 1 with
`refused: ["init refused (its refusal is on stderr) — nothing about the
ignore rule or merge attribute changed"]` while stderr is EMPTY and both
refusal objects — init's E001 finding and its `initialized: false` note —
are on stdout. A caller that followed the sentence read an empty stream.

The accounting around it is correct: `core.hooksPath=dev/hooks` is credited
and the config really landed, the init refusal is named rather than
dropped, and the run exits 1. This is the output contract only.

Amended: **v2.13's rule that a refusal's named remedy is part of its
contract covers where a refusal says to look.** The sentence names stdout,
which is where the objects are.

The discrimination that makes this a property of the path rather than of
the capture: the same CLI DOES emit a fatal refusal on stderr from
`snapshot` in the same repository, and that is asserted beside the fix.

<a id="a-stamp-may-not-predate-the-entry-s-own-dated-text-defect-pc-d4c3"></a>

_v2.16 amendments, source line 3176 — allocates no code._

`claims.yaml` records corrections as dated sentences inside the entry they
correct — "CORRECTED 2026-09-14 (`pc-87e0`, spec v2.12)" — and `verified` is
a separate field that nothing compared with them.
`reference-implementation` carried `verified: '2026-09-13'` beside two
corrections its own notes date 2026-09-14. The corrections' own tests pass,
so this is provenance chronology rather than an implementation failure, and
that is what makes it the register-accuracy class rather than a defect in
the object: `claims-check`, `vocab-check` and `gates.py --audit` were all
clean over it, because nothing read dates against dates.

Amended: **a `verified` stamp is refused when it is older than the latest
date written in the entry's own claim, kill or notes.** Equality passes: a
same-day correction is the ordinary case in this register, and a rule that
refused it would demand a re-stamp for every sitting.

WHAT THIS RULE IS ENTITLED TO ASSERT, stated because it is a text
comparison and rule 1 applies to it as much as to anything else: it is an
ORDERING, not a recomputation. It cannot tell whether the evidence was
re-run — nothing in a register can — and it refuses only the state that is
provably wrong, a stamp that cannot cover what it postdates.

A DECLARED HEURISTIC, in the same family as the `pc-c1f3` stamp-count
convention: any `YYYY-MM-DD` in the entry's prose is taken as a dated
statement about that entry, and the pattern is anchored on the century so a
version number, a record id or a bare number cannot be read as one. The
false-red direction carries three of the four controls.

<a id="a-count-beside-machinery-is-stale-the-moment-the-machinery-moves-defects"></a>

_v2.16 amendments, source line 3205 — allocates no code._

Two instances of one shape, and the shape is the round's own lesson. The
`vocab-check-wired` entry's stamp comment read "registry regenerated, 90
markers" while `dev/vocab-check.py` printed `census.markers: 91` at exit 0
and `spec/vocabulary.json` carried 91 allocations — the count moved in the
sitting that wrote the comment (E016 gained its v2.15 marker under
`pc-d935`) and the comment did not. `reference-implementation`'s notes
called `EvidenceShapeHermeticity` "the twelve-test HERMETICITY ARM"; the
class has fifteen test methods, and "five of its cases" had drifted with
it.

The `pc-c1f3` rule already forbade a count in a verified-stamp comment,
after a test count there had rotted twice. It caught `N tests` and nothing
else, so the same defect one noun over passed a gate written to stop it.

Amended: **a verified-stamp comment carries no count at all.** The rule is
widened from test counts to any count, with the lookbehind that keeps a
date's `18` and a version's `16` out of it, and the message points at the
gate or runner that prints the number instead. Two entries were repaired by
it on the real register — the record's own subject and a stale "24 records"
nobody had filed.

For a count in an entry's PROSE the repair is the same and the gate is
deliberately not extended there, which is worth saying plainly: this
register is mostly dated history, and the historical sentences are FULL of
counts that were true when written ("344 tests OK, up from 333"). A
heuristic over them would redden the record of what happened, which is the
false-red risk `pc-709d` names. So `pc-5d02`'s sentence names the arm
instead of counting it — the number is available from running the arm,
which is the only place a count stays true — and what bounds the class in
prose is the practice, not a gate.

<a id="the-unpublish-prohibition-names-its-one-authorized-route-defect-pc-79b8"></a>

_v2.16 amendments, source line 3238 — allocates no code._

§v2.13's heading — "`sync` may not unpublish a landed revision" — and the
register's restatement of it are absolute, and the register's sentence sat
sixteen lines below "`--take-landed` discards the local-only revisions the
writer names" in the SAME entry. Both are true of the code, and the second
is a case of the first: B's register publishes `pc-XXXX` rev 2, `sync
--take-landed <id>` exits 0, reports the discard with id, your rev, landed
rev and field names, moves the register, and B's register then publishes
the landed revision in place of its own. The guard permits it through the
v2.14 second clause — a revision passes if the ARRIVING timeline carries it
or the local timeline the re-chain derives from does — so the implemented
guard is narrower than the sentence.

Amended: **the prohibition is on unpublishing a landed revision WITHOUT THE
WRITER NAMING IT.** `sync --take-landed ID` is the one authorized route
past it, and it is a case of the rule rather than an exception to it: the
discard is explicit, writer-named and reported, and the flag is refused
where there is nothing to discard (`pc-ef5b`). The register may stop
publishing a revision it published — by a command that says so, and only
then.

Nothing in the implementation changes. This is the register and this
document catching up with what the code has said since v2.14, and it is not
a blinding cost: claim 47 quoting `--take-landed`'s discard was in the
reviewing lane's own brief, and the lane filed with it in view.

### v2.17 amendments (2026-09-19 — the round-13 fix sitting: closing the

<a id="a-projection-s-guard-discarded-the-states-its-own-refusal-was-written-fo"></a>

_v2.17 amendments, source line 3273 — marks `E018`._

E018's scan over a milestone's `created` and `target` opened `if not value
or not isinstance(value, str): continue`, which is a TRUTHINESS test where
a PRESENCE test belongs, and the two states it discarded are exactly the
two the branch below it was written for.

An **empty-string** `target` is falsy, so both renderers charted it as "no
target date" at exit 0 — a stored bad value read as an absent one — while
`check` calls it PRESENT and invalid in so many words ("target, when
present, must be a YYYY-MM-DD string"). A **non-string** `target` is
truthy, so it flowed past the guard into `date.fromisoformat` and into `<`
against a date string, and both renderers died with a fatal E000 at exit 2.
The v2.13 sentence already covers both — each is "a value that is not
date-shaped at all" — so the code, not the rule, was what had not arrived.

A cleared optional field is ABSENCE, never `null` and never `""` (the
v2.6 presence rule, `pc-76b5`), which is what makes presence the honest
test: nothing a write path produces can be distinguished from absence by
its truthiness.

Amended: **E018 tests PRESENCE, and every present value that is not a
placeable calendar day is refused.** The empty string and the non-string
join the not-date-shaped state rather than escaping the scan, and because
`''` and `42` are indistinguishable once stringified, the message carries
the value's TYPE beside it.

**A third state, and it is an absence rather than a value.** `created` is
required — E001 refuses a record without it — and the ASCII renderer
indexes it directly, so a `created` that is absent was a `KeyError` E000
rather than a chart. That is E018's own case: a projection that cannot
place a day refuses with a finding. `target`'s absence is untouched and
stays the undated milestone both renderers already chart as such, which is
the discriminating control on this repair.

- **E018** — unprojectable value: a value a projection cannot place as the calendar day it needs. Three states, distinguished in the message: a date-shaped `created` or `target` on a milestone that is not a real calendar day, which the structural gates accept (E001 is shape-only); a value that is not date-shaped at all — including the empty string and any non-string, whose type the message names — which `check` refuses with E001 and which reaches a projection only through a bypassed or unrun gate; and a `created` that is ABSENT, which E001 also refuses and which the renderers read directly. Tested by PRESENCE, never truthiness: an absent `target` is the undated milestone, not a refusal. Emitted by the projection commands as an error finding at exit 1, naming the record and the field, BEFORE either rendering. NEVER emitted by `check`.
  <!-- vocab: {"action":"amend","code":"E018","at":"v2.17","by":"pc-5706"} -->

**The neighbourhood, fixed in the same sitting rather than filed.** `audit`
and `board` consume the same milestone `target` through the shared
`milestone_is_overdue` predicate with no E018 scan in front of them, and
both died on the same `TypeError` at exit 2. Overdue is DERIVED (rule 2,
v2.1), and a value that is not a date derives nothing, so the predicate
now declines rather than raises. That is not the malformation going
unreported: `check` refuses the record with E001 at exit 1, `board` runs
those checks itself and exits 1 on them, and `gantt` refuses with E018
before either renderer runs. The structural verdict stays with the
structural gate, which is rule 1's own division of labour.

<a id="the-hook-asks-for-the-file-it-can-run-not-for-what-the-shell-would-run-d"></a>

_v2.17 amendments, source line 3322 — marks `D012`._

The eighth level of the doctor detector class, and the round-12 report
diagnosed the class rather than counting it again: it is not about WHICH
value doctor resolves (`pc-cb43`), nor WHICH question it asks of that value
(`pc-acaf`), nor WHICH base it resolves against (`pc-2745`). It is about
doctor REIMPLEMENTING the hook's resolution in a different language instead
of resolving what the hook resolves. So the class is unbounded in the
simulation's fidelity, and a ninth level is what modelling the shell more
finely would buy.

`templates/pre-commit` asked `command -v pecia`. That is a shell builtin
whose answer includes functions, aliases and builtins, and which prints a
matched FUNCTION's BARE NAME — so with an exported `pecia()` in the
environment the hook set `cli=pecia`, its own slashless rebase turned that
into `$top/pecia`, a path that does not exist, and the gate refused every
ordinary commit at "no pecia CLI found". `doctor` resolved the same
environment through a PATH search, which can never see a shell function,
predicted the repository-root fallback, and reported `ok: true` one command
earlier. Measured, with the same repository and the same commit in the same
bash as its control.

**The repair is at the source, and it is neither of the two the record
named.** Executing the hook's own resolution would make a diagnostic run
adopter-authored shell out of the file it is diagnosing, which is a worse
posture than the gap it closes. Widening D012's registered text alone would
leave an adopter whose every commit dies reading a green doctor. What was
actually wrong is narrower than either: `command -v` answers "what would
this shell run", and a shell function is an answer THE HOOK CANNOT USE — it
needs a file path, which is why it immediately rebased the bare name into a
bogus one. The hook was asking a question whose answer it could not accept,
and whose one usable form is the PATH search doctor was already modelling.

Amended: **the shipped hook resolves `pecia` with `type -P`** — a PATH
search for an executable FILE, ignoring functions, aliases and builtins.
That is what the hook's own comment has promised since `pc-d182` ("`pecia`
on PATH"), it is the only thing the hook can exec, and it is exactly what
`which_from_root` models. A `pecia` shell function now falls through to the
repository-local checker instead of killing the gate. The class closes here
because the two resolutions are the same question, not because one models
the other better.

**And where doctor cannot model, it asks.** A hook copied before this
change still carries `command -v`, and doctor must not certify a posture on
a model it knows is lossy there. For that one question it stops simulating
and asks `bash -c 'type -t'` — one fixed command, in the same
non-interactive mode git runs the hook in, never text read out of the
adopter's hook. It reports D012 only when the divergence is LIVE, i.e. when
such a name actually exists in the environment; with no `pecia` function,
alias or builtin the old resolver and the model agree, doctor's answer is
right, and a finding would be lint about the hook's spelling rather than a
statement about posture — the false-red risk `pc-709d` names.

- **D012** — the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repository root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs — the hook's own shebang, and `python3` or the checker's shebang — resolving, derived from those files rather than listed and read with the lexical rules of whatever reads them, so an `env -S` string's quotes, escapes and `${VAR}` are env's syntax and a name computed from a variable this environment does not carry is reported undecided rather than missing). Every relative path this code reasons about resolves against the work-tree root, the way git will — the PATH's own relative entries included (v2.16) — so the answer does not depend on the directory doctor was invoked from. The PATH resolution it models is the one the shipped hook performs (`type -P`: an executable file, never a shell function, alias or builtin — v2.17); where a hook carries the pre-v2.17 `command -v` resolver instead, doctor ASKS the shell whether such a name exists and reports D012 when that divergence is live, rather than certifying on a model it cannot apply there. The gate fails closed.
  <!-- vocab: {"action":"amend","code":"D012","at":"v2.17","by":"pc-7acd"} -->

**The scope sentence in claim `doctor-posture` is unchanged and still
true**: doctor's own environment stands in for the hook's. What v2.17
removes is not that approximation but a second one nested inside it — that
doctor's model of the hook's RESOLVER was a different resolver.

<a id="e011-says-what-it-has-always-done-a-state-check-on-heads-defect-pc-95d0"></a>

_v2.17 amendments, source line 3383 — marks `E011`._

E011's canonical bullet carried NO scope clause — "an `evidence` or
`context` reference naming a scheme this repo's `resolvers:` does not
declare" — while the implementation has always iterated heads. Measured,
for both fields E011 governs: rev 1 carrying `jira:ABC-1` is E011 at exit
1; rev 1 carrying it with rev 2 clearing the field is clean at exit 0, and
that ledger is indistinguishable to `check` from one that never carried
the reference at all.

The asymmetry is with the DOCUMENT, not with the code, and the five
sibling codes are what settle it: E003, E006, E007, E012 and E017 are all
heads-only **by explicit declaration in their own normative text**, and a
repaired head being legitimately clean is this checker's design throughout.
E011 alone being total would be the one place the six split five-one. What
E011 asks is whether THIS repo can resolve what its CURRENT records point
at, and a scheme named only by a superseded revision is not something
anything will resolve — resolution itself is `audit`'s, over heads.

Amended: **E011 is a state check on heads**, stated in its bullet beside
its siblings. The implementation is unchanged. The comment beside the loop
said "an undeclared scheme is never a silent pass", which is false of a
superseded revision; it now says what the loop does and why.

- **E011** — an `evidence` or `context` reference naming a scheme this repo's `resolvers:` does not declare (v1.7 for evidence, extended to `context` at v2.5); a state check on heads (v2.17) — a repaired head is legitimately clean, exactly as for E003, E006, E007, E012 and E017, and the scheme a superseded revision named is one nothing will resolve.
  <!-- vocab: {"action":"amend","code":"E011","at":"v2.17","by":"pc-95d0"} -->

**What this gives up, stated rather than absorbed.** An undeclared scheme
in a superseded revision stays a silent pass for `check`. That is the same
thing the other five give up and it is bounded the same way: custody of a
violating revision belongs to `audit`'s historical surface, not to a
structural code whose subject is the current state.

<a id="a-refused-sync-reports-what-it-did-and-its-remedy-converges-defect-pc-42"></a>

_v2.17 amendments, source line 3416 — allocates no code._

Two defects in one finding, both in the refusal path and both about
REPORTING rather than about storage.

**The refusal reported a discard it did not perform.** With two records in
conflict, `sync --take-landed ONE` refuses — the other conflict remains,
so the write is atomic and nothing happens — and it named ONE under
`discarded`, a field whose whole meaning is that the revision is gone. The
log was byte-identical before and after, which is what refutes it. §v2.14
already says `discarded` belongs to the SUCCESS report; the implementation
emitted it on the refusal branch too, so this is the code catching up with
the rule rather than the rule moving. It is the same shape as the round-11
pair `pc-bc87`/`pc-2390`: a report describing the branch the writer WANTED
rather than the branch the code TOOK.

Amended: **on a refusal the named revisions are reported under
`would_discard`** — what would have been dropped had the sync completed,
and is still present — and never under `discarded`. They are not dropped
from the report: a writer who named an id is owed an answer about it, and
the answer says the branch was not taken.

**And the remedy did not converge.** §v2.14 states that the prescribed
sequence "terminates from the first refusal and from the two-conflict
state the old wording produced", and both of those are ONE record's
revisions. The note said "Resolve each id with `pecia sync --take-landed
<id>`", and across TWO conflicting records following that literally never
terminates: each invocation refuses again on the id left out.

Amended: **the refusal prints the single invocation that names EVERY
conflicting id.** Resolving them one at a time has no fixed point, and the
note says so. The printed command is bounded like every other participant
list (§v2.16): it names as many ids as its budget allows and states how
many it did not, so a long conflict set cannot consume the sentence after
it. §v2.13's rule that a refusal's named remedy is part of its contract is
what makes this a defect rather than a wording preference, and the arm
that closes it EXECUTES the command the note prints rather than reading
it.

### v3.0 amendments (2026-09-22 — the canonical form is written down)

<a id="e001-and-the-canonical-form-rfc-8785-over-a-narrowed-domain-defects-pc-d"></a>

_v3.0 amendments, source line 3460 — marks `E001`._

Until this section the canonical form was defined only by §3.2's parenthetical
"(`sort_keys`)", which says nothing about separators, escaping, number formatting or
integer range, each of which changes the hash. The form was whatever CPython's
`json.dumps` defaults produced, so no second implementation could be written from
the text.

**The canonical form of an entry, and of a record, is its RFC 8785 (JSON
Canonicalization Scheme) serialization, encoded as UTF-8.** `prev` is the
lowercase-hex SHA-256 of the preceding entry's canonical form. Writers store every
line of the log and the projection in canonical form, so for a conforming log the
hash input is the stored line's bytes.

**The domain is narrowed so that RFC 8785's two hard rules cannot arise:**

- **Numbers are integers** in [−(2⁵³−1), 2⁵³−1]: never `-0`, never written with a
  fraction or exponent. RFC 8785 formats numbers by ECMAScript's IEEE-754 algorithm;
  for these integers that is plain decimal digits. A non-integer value belongs in a
  string. The range is also what a JavaScript client survives, which matters for the
  MCP interface.
- **Object keys are ASCII**, so RFC 8785's UTF-16 code-unit key order equals
  code-point order.
- **Object keys are unique**, as RFC 8785 requires of its input.
- **Strings are valid Unicode**: no lone surrogate, since UTF-8 cannot carry one.

Within that domain the same bytes are also Matrix's canonical JSON, so two
independent specifications are available as test oracles. Escaping is RFC 8785's:
`"` and `\` escaped, U+0000–U+001F as `\b \t \n \f \r` or lowercase `\u00hh`,
everything else as raw UTF-8, **U+2028 and U+2029 included**. A reader MUST
therefore split on LF only (v2.12, `pc-d96d`): under v3 a line separator inside a
string is stored literally, where the v2 form happened to escape it.

The hash algorithm is not tagged in the data. Changing it is a format boundary with
a migration, as this one is.

- **E001** — the record contract on both routes (store and --ledger): every LF-delimited physical line parses as strict JSON within the declared nesting bound and the canonical domain (integer numbers within ±(2^53−1) and never -0, ASCII and unique object keys, valid Unicode), no blank lines, fields and edges typed and shaped; every finding carries its revision, or its source line where the revision cannot identify it, and the condemned value is bounded so the identity tag cannot be truncated away
  <!-- vocab: {"action":"amend","code":"E001","at":"v3.0","by":"pc-ddd9"} -->

<a id="ids-are-minted-twelve-hex-wide-and-a-collision-is-its-own-condition-defe"></a>

_v3.0 amendments, source line 3499 — allocates no code._

An id is `pc-` plus the leading hex of a hash over (type, title, created, nonce),
widened while the candidate is already taken. The width it STARTS at was four, and
the taken-set is only what the minting clone knows — so two clones creating records
before either publishes draw from a 65,536-wide space with no shared allocator. At
100 ids in that window the birthday probability is 7.3%, and at 1,000 it is a
certainty. **The floor is twelve** (1.8e-11 in the same window, 1.8e-5 over a
lifetime of 100,000 records). Existing shorter ids remain valid and are not
re-minted: they are unique within their ledger, and re-minting would rewrite every
citation of them.

A prefix per checkout was considered and rejected: it is unique only probabilistically
too, so it moves the same birthday problem up a level while adding per-clone state a
clone must not copy, and it puts the originating checkout in every id.

**A divergent creation sharing a published id is an ID COLLISION, not a same-field
conflict.** Two clones minted one id for unrelated records; there is no field to
reconcile and no losing revision to discard. `sync` names it as its own kind and
REFUSES `--take-landed` for it, because that flag discards a losing revision and
honouring it here drops a distinct record — the remedy is to re-create the local
record, which mints a fresh id.

**Migration.** The reference log was re-serialized in the v3.0 form and re-chained
on 2026-09-22; every entry's content was verified identical apart from `prev`, and
nothing was refused, because the log held no float, no integer beyond 2⁵³, no
non-ASCII or duplicate key and no lone surrogate. Its published ref was replaced
rather than advanced.

### v3.1 amendments (2026-09-25 — the Rust port's `doctor`)

<a id="an-undecidable-gate-is-not-a-green-one-defect-pc-937e"></a>

_v3.1 amendments, source line 3530 — allocates no code._

A hook that is executable but unreadable runs if it is a self-contained binary
and dies "Permission denied" at every commit if it is a script, and doctor
cannot read it to tell which. D003 said exactly that — as a WARNING — while
`gate_active` was computed from `is_file` and `X_OK` alone, so the summary read
`ok: true, gate_active: true` one command before a commit the hook killed. The
fields v2.16 made the posture ANSWER were green over the one state the claim
says must be "reported as undecidable rather than green".

Amended: **`gate_active` is `true`, `false`, or `null`**, and `null` means the
hook is present and executable but cannot be read, so whether the gate runs
cannot be decided here. **`ok` is true only over a gate that is ACTIVE** (or
where there is no ledger to guard), so an undecided gate is `ok: false`. D010
("no write gate is active") does not fire on `null`: undecided is not absent.
The exit code does not move — D003's unreadable arm stays a warning, for the
reason D010 is one.

### v3.2 amendments (2026-09-25 — the query index)

<a id="a-query-may-stand-on-a-verified-read-until-the-log-changes-decided-with-"></a>

_v3.2 amendments, source line 3550 — allocates no code._

Every command read and verified the whole chain from its first byte, on every
run. The log is append-only and pecia is its only writer, yet nothing
remembered what had already been verified. The cost was the entire history:
at a million entries (2.6 GB), `next` took 9.5 s and 8 GB to report what the
3% of entries that are open heads determine.

Amended: **an implementation MAY keep a query index**, a derived file beside
the log (in the git common dir, or in the pinned store). It records each
head's **query view**: the eight fields the queries read (`id`, `type`,
`status`, `title`, `priority`, `created`, `target`, `edges`), which are none
of the prose and about a tenth of a record's bytes. That is the graph, kept
apart from the prose. It also records the log file's **identity** at the
moment the index was made: device, inode, size, modification time and
status-change time. It is made only from a read that verified the whole
chain and that a query would accept (no read finding, every record sound, no
revision duplicated). It is also re-made after the implementation's own
append or rewrite, if the file is exactly the size that write produced.

- **`ready`, `next`, `blocked`, `graph` and `gantt` MAY answer from the
  index, and only while the log's identity equals the recorded one.** On any
  difference they read and verify the whole log as before, refusing at the
  first break, and remake the index if the read is clean. Either way a query
  is handed the query views of the heads its answer depends on: non-terminal
  heads for `ready`, `next` and `blocked`, milestones for `gantt`, every head
  for `graph`. The two paths therefore agree by construction. Non-terminal
  heads lead the index in a section of their own, so the commonest queries
  read that section and nothing else. Each section carries a digest (the
  SHA-256 of the SHA-256s of its successive 1 MiB chunks), and a view that
  does not name the head it is filed under counts as a damaged index. The
  digests catch damage, not forgery: they are unkeyed, so an index
  rewritten with fresh digests is believed until the log changes. That
  grants a writer nothing `--force` does not, and `check` never reads the
  index.
- **A write of one revision MAY stand on it too — `add`, and `edit` or
  `close` without `--also-closes` — on a stricter condition.** The index
  must also record the identities of the projection and its witness as the
  implementation itself last wrote them, taken from its own handles on the
  files it renamed into place, and both must still be equal. Then the write
  reads every head's query view from the index and, for a revision, its own
  head's line from the log at the offset the index records, accepted only if
  that line holds exactly the view it is filed under. It gates the candidate
  with the write gate restricted to what one appended revision can change:
  the candidate's own record findings; the one new transition pair; the
  candidate's per-head findings before and after; and the four graph
  findings (E004, E009, E012, E016) over every head before and after. Over a
  timeline the index certifies (every record sound, no revision duplicated,
  each revision one past its head), that equals the full gate, finding for
  finding and in order. The write then links its entry to the last entry's
  hash, which the index records; extends the projection by the one record
  rather than regenerating it; moves the witness; and re-records the index.
  The entry, projection, witness, output and exit code are those of a write
  that read the log in full. On any difference it reads the log in full, as
  before. Measured at a million entries: `add` goes from 19.3 s and 9.9 GB
  to 0.43 s and 1.2 GB.
- **`check` never uses it.** It verifies the whole chain on every run,
  whatever the index says. So do `audit` and `board`, which read history, and
  the writes that rewrite or retire more than one record.
- **The index is disposable.** It is never published, committed, chained or
  hashed. Deleting it is always safe. A damaged or unreadable one is treated
  as absent.

**The trust this adds, stated.** A change to the log that preserves all five
identity components is not seen by a query until the identity next changes
or `check` runs. The status-change time moves on every write to the file and
cannot be set by an ordinary program, so that change requires root or control
of the system clock. The same actor could equally edit the index or the
binary. The chain's guarantee is unchanged: `check` still establishes it from
nothing. What changes is when a query re-establishes it: only after the file
changes, rather than on every run. Measured at a million entries (2.6 GB):
`next` goes from 9.5 s and 8 GB to 30 ms. A write that stands on the index
takes the same trust and no more. If the log changed with its identity
preserved, the write's entry links to the hash of the entry that was
verified, not to whatever is there now. The next `check` then reports a
break at exactly that point, and the write has not absorbed the change.

### v3.3 amendments (2026-09-25 — nothing useful is withheld)

<a id="prose-is-emitted-not-withheld-decision-pc-ded91385e31a-decided-by-noah"></a>

_v3.3 amendments, source line 3629 — allocates no code._

v1.10 (defect `pc-cdb8`) withheld `body`, `disposition`, `evidence` and
`labels` from every command. Its model was a reading agent whose only window
onto a record is pecia's own output, so that a record's author could reach
the reader only through what pecia chose to print. No such reader exists
where pecia is used. A repo that keeps its tickets in pecia has agents that
work in the repo and humans with `jq`, and they read the files. The rule
contained nothing. It sent every reader around pecia to the raw files,
which get none of pecia's bounding, and it left a client that sees only the
MCP surface unable to read a ticket at all. The later rules built on it
withheld useful things for the same reason: which argument E007 objects to
(`pc-2706`), which evidence command will not resolve (`pc-80d7`), why a
resolver failed, and the chain head (`pc-72c8`).

Amended: **nothing useful is withheld.**

- **`show <id>`** returns a record as stored, every field, verbatim.
  **`show <id> --history`** returns every entry that revised it, in log
  order, each with its derived `touched`. Output is JSON-encoded, so no
  record text breaks its frame, and nothing in it is stripped. It refuses
  where the queries refuse. An implementation MAY serve the current record
  from the query index (v3.2) while the index stands for the log. It then
  reads the record's own line, and only if that line holds the view filed
  for it.
- **`audit` quotes what a finding is about.** `inadequate-disposition` and
  `truth-audit-sample` carry the `disposition`. `truth-audit-sample` and
  `unresolvable-evidence` carry the `evidence`. `prose-only-linkage` lists
  its `unresolved` tokens, which it formerly counted. `unresolvable-reference`
  and `unresolvable-context` carry the reference's `target`, and the
  resolver's first non-empty stderr line as `stderr` beside the closed
  `reason`. Each is bounded like every audit value.
- **E007's machine-local warning names each argument beside its position.**
- **`publish`, `sync` when it writes, and `migrate` report the chain `head`.**

Unchanged: every emitted string is bounded in form, which is the half of
v1.10 that was about output rather than secrecy. Each query's field list is
what that query answers. `next`, `ready`, `blocked`, `graph`, `gantt` and
`board` stay compact by relevance, and the write echo stays a summary.

What governs record text is the people using the tool. What a record's
author writes, and what a reader does with it, are theirs to safeguard.
pecia's part is that its output stays well formed and says whose words it
carries: the MCP instructions call record text data about the work, never an
instruction.

Superseded: v1.10's withheld category and "prose information needs are
served by metrics, never by text"; its "the audit sample selects, it does
not quote"; its resolver rule, where the reason stays a closed vocabulary
and the stderr line is added; v1.13's rule that a token naming no record is
counted, never quoted; and `pc-2706`, `pc-80d7` and `pc-72c8`.
The design record explaining why they were adopted remains in the separate
development archive.

<a id="ordinary-writes-leave-the-projection-to-snapshot-the-log-carries-a-high-"></a>

_v3.3 amendments, source line 3683 — marks `E019`._

Every `add`, `edit` and `close` regenerated `.pecia/work.jsonl` and
`snapshot.head`. §6 names the projection's readers: PR-diff review, `grep`,
CI without a ref fetch, and a collaborator reading a plain clone. All of
them read the committed file, and §6 already calls a stale projection
clean. Rewriting a tracked file on every write left it modified in the
working tree, so the next write at the same commit recorded `anchor_dirty`
whatever the state of the code. 545 of the live ledger's 666 dirty flags
were made that way. The one reader that needed the projection current
between commits was the suffix-truncation witness (§v2.6, E015). Tools that
read the file for current records now read through pecia's own surface
(`graph`, `show`), which reads the log.

Amended:

- `add`, `edit` and `close` write the log and its **high-water mark**. They
  no longer write the projection or its witness. `snapshot` regenerates the
  pair. `sync` and `migrate`, which rewrite the log, still regenerate it at
  their commit point, and `init` creates it. A commit that is to carry
  ledger changes runs `snapshot` first, and the template pre-commit hook
  says so when the staged projection is behind the log.
- The mark is `log.mark`, beside the log. For the default store that is in
  the git common dir, where it is never tracked, and a pinned store keeps
  it beside its own log. It is one line: the newest entry's `seq` and hash.
  Every command that writes the log stages the mark before the log moves
  and renames it into place after it, the order the projection kept at
  v2.13.
- A missing mark makes no claim. That is a store written before v3.3, or a
  fresh clone.
- **E019** — the log ends before its high-water mark: the mark records a
  `seq` the log does not reach, or the log's entry at that `seq` has
  another hash, or the mark does not parse. `check` reports it over a log
  that reads whole. `add`, `edit`, `close` and `init` refuse over it.
  `sync` refuses unless the timeline it adopts holds the marked entry,
  which is the recovery. `migrate` refuses unless its rebuild holds the
  marked entry, or `--force-drop` accepts the loss.
  <!-- vocab: {"action":"mint","code":"E019","at":"v3.3","by":"pc-25cca4980c47"} -->
- The v3.2 write lane no longer records the projection's identities, since
  a write no longer extends the projection. It requires the mark to name
  the entry the index records as the last.

What is traded away, stated. Entries written since the last `snapshot` and
never published are still detected when lost, as E019, but no local copy is
left to recover them from. Recovery is `sync` from the published timeline.
Removing the mark is the deliberate act of accepting a loss, as
`--force-drop` is for `migrate`.

### v3.4 amendments (2026-09-25 — the round-13 findings, closed)

<a id="e004-names-each-cycle-s-whole-component-and-a-write-that-grows-none-adds"></a>

_v3.4 amendments, source line 3733 — marks `E004`._

v2.11 promised that a second genuine cycle reachable from the same traversal
is reported rather than shadowed. One depth-first pass cannot keep that
promise. It records one cycle per back edge, and once a node has finished on
one cycle, a second cycle through it is never walked. Round 13 measured
`a -> b -> d -> a` beside `a -> c -> d -> a`: one E004, with `c` on a cycle
and named by no finding. From there the write that removed the reported
cycle was refused, because the gate read the shadowed survivor as new.

Amended: **each E004 names its cycle's whole cyclic component**, the
strongly connected component the cycle lies in. Every record on any cycle is
in one. The finding still reports the cycle its traversal found, and adds
`; the same cyclic component also holds …` for any member that cycle misses.
Cycles are not enumerated, because their number can be exponential in the
graph. **The write gate reads the component**: an E004 whose component is the
same as, or inside, a component the ledger already carries is not a new
error. This is v2.13's narrowing rule (`pc-54c7`) with equality admitted for
E004 alone, because a cycle has no subject and its reported witness can move
while the damage does not grow. A write that makes a cyclic component, or
grows one, is refused exactly as before.

- **E004** — a cycle in the `blocks` ∪ `parent` ∪ `retires` scheduling subgraph, reported with its whole cyclic component (v3.4): a write that leaves every cyclic component the same or smaller introduces no new E004.
  <!-- vocab: {"action":"amend","code":"E004","at":"v3.4","by":"pc-c69d"} -->

### v3.5 amendments (2026-09-26 — the CLI prints for people)

<a id="the-cli-prints-for-people-and-the-mcp-server-for-models-decision-pc-f590"></a>

_v3.5 amendments, source line 3760 — allocates no code._

The CLI is read by people, and the MCP server (v3.1) by models. The output
contract was the reverse. Every query (`show`, `next`, `ready`, `blocked`,
`check`, `audit`, `doctor`, `graph`) and every write printed JSON for the
person at the terminal, and `--json` was accepted and did nothing. `board` and
`gantt` were the only views, and they had no machine form.

Amended: **the CLI prints for people by default, and `--json` prints the
JSON.**

- **`--json` prints exactly the pre-v3.5 output**: one JSON document per
  line, findings one per line. Every command that printed JSON takes it.
  Scripts, hooks, gates and CI that read output pass it. `graph --format
  json` is the same as `graph --json`, and an explicit `--format` still wins.
- **Without `--json`, each value is rendered for a person**:
  - a finding is one line, `<severity> <code>[ <id>]: <message>`;
  - `add`, `edit` and `close` echo the record on one line,
    `added <id> rev <n> · <type> · p<priority> · <status> · <title>`;
  - `next` and `ready` print a table of id, priority, type and title;
  - `blocked` names each record's blockers and the questions it awaits;
  - `show` prints the record as it reads: a header of id, type, priority,
    status and revision, the title, the other non-empty fields aligned (each
    edge kind on its own line), then the body as written;
  - `show --history` prints each revision with what it changed and the new
    values;
  - `graph` lists each record with outgoing edges, and its titles;
  - `audit` groups its findings by kind;
  - every other result is its fields, aligned;
  - a cannot-run prints `pecia: <message>` on stderr.
  The rendering is a function of the same values `--json` prints, so the two
  forms cannot disagree about what a command found.
- **The MCP server returns the JSON form**, as it always has
  (`structuredContent` and its text). `board` and `gantt` are the exception:
  they keep their text views, for a session that wants to show a person the
  board. Neither has a JSON form, in the CLI or over MCP.
- **Unchanged:** exit codes, rule 1, and the output bounding. Every rendered
  string is bounded by the same transform as its JSON value. The one
  exception is `show`'s record text: it keeps its lines and its own fences,
  and control and format characters are removed. v3.3's "nothing in it is
  stripped" now holds of `show --json` and the MCP result, where JSON
  encoding does that work.
- **A reader that closes the pipe** (`pecia ready | head -1`) ends the
  writing. The command still finishes and exits with its own code, with
  nothing on stderr. A terminal that cannot encode a character gets a
  replacement, never a traceback, as `board` always did.

Chosen over detecting a terminal (human output when stdout is a TTY, JSON
when piped): output that changes with how a command is run surprises its
reader, and an agent's shell is usually piped. Traded away: every machine
reader must now say `--json`, a one-time change; and both implementations
render the human form, held equal by the suite.

### v3.6 amendments (2026-09-26 — a committed snapshot is a verified copy)

<a id="a-snapshot-that-rebuilds-to-its-recorded-head-is-a-verified-copy-of-the-"></a>

_v3.6 amendments, source line 3815 — allocates no code._

Publishing is explicit, so any commit that carries records nobody has
published has a snapshot ahead of `refs/pecia/log`. A fresh clone of such a
commit had no timeline it could adopt. `sync` refused (E015): adopting the
published timeline would regenerate the snapshot, the only copy of the
unpublished entries. `migrate` rebuilt from history and refused wherever the
rebuild diverged from the published timeline, which it does in any repository
whose committed snapshots were ever written in conflicting orders. Since
2ba91b6 (`pc-7e110b7cf434`) the refusals said so, and the only exit they could
name was for the writer to publish.

A copy already existed. The committed snapshot is `canonical(rec)` of every
entry in log order, which E015 checks against the head. Every other field of
an entry is determined: `seq` by position, `prev` by the entry before, and
`touched` by the diff from the id's head, which no writer may author (§4.1,
enforced by E014). Measured on this repository's snapshot at 2ba91b6: chained
alone, its 1274 records rebuild the live log byte for byte, and the last
entry's hash is `snapshot.head`.

Amended:

- **A verified copy.** A snapshot's records, chained in order with the
  writer's own entry construction, form a verified copy of the timeline
  through the snapshot's recorded head when the last entry's hash is that
  head. A snapshot that was edited, replaced or truncated does not rebuild to
  its head, and is never trusted.
- **`sync` adopts it where the timeline stops short of it.** When the local
  log and the published timeline are both prefixes of the verified copy, and
  the copy is longer than either, `sync` writes the copy. The published
  timeline must be a prefix, so published history is never rewritten; the
  adopted timeline passes the full checker like any adoption; and the local
  register advances to the published commit as on every sync. The result
  reports `from_snapshot`, the entries taken from the snapshot beyond both.
  The same rule restores a log that lost entries from its end, where the
  snapshot still holds them. A `--take-landed` given to such a sync is
  refused, since there is nothing to discard. The E019 rule is unchanged:
  over a violated mark, the copy is adopted only if it holds the marked
  entry.
- **What still refuses, and why.** A snapshot that does not rebuild to its
  head is refused as before. So is one the published timeline parts from, and
  the refusal says at which entry. The snapshot's later entries must be
  re-chained before they can be published, so that commit's snapshot can
  never be adopted. Its writer has to sync, publish and commit the snapshot
  that produces, and a clone checks out that commit and syncs.
- **A revision that landed as it stands is not a conflict.** In a re-chain,
  a local-only revision of a record this log already held, whose record is
  identical (canonical form: id, rev and content) to one that landed after
  the common prefix, is already true remotely, and is skipped. A creation
  keeps its own rule (`pc-e499`): identical, it is a no-op; different, an id
  collision. Before this, the conflict test saw the same field
  on both sides and refused, and its remedy told the writer to discard the
  revision and re-apply an intent that had already landed. That was measured
  before this amendment landed: a clone holding a writer's unpublished edits
  verbatim met them again after the writer re-chained and published, and was
  refused on both. The result reports `already_landed` when it is nonzero.
  `rechained`, `skipped_noops`, `already_landed` and `discarded` together
  account for the local-only suffix examined.

Traded away: the snapshot becomes a recovery source as well as a witness, but
only where it verifies. A clone that adopts a copy holds its writer's
unpublished entries verbatim, and a clone that then publishes them publishes
the writer's own entries, with the writer's hashes. Not decided here: whether
`migrate` should also start from a verified copy. It reads unmerged branches
too, so the copy would give its order and history would add only the
revisions the copy lacks.

### v3.7 amendments (2026-09-26 — migrate starts from a verified snapshot)

<a id="migrate-takes-a-verified-snapshot-as-its-rebuild-s-order-and-first-entri"></a>

_v3.7 amendments, source line 3884 — allocates no code._

`migrate` rebuilds a timeline from every committed snapshot blob and the
working tree, ordering revisions by a topological merge of the order each
source witnessed (v2.10, `pc-356c`), with a (rev, id) sort where the
witnesses conflict. This repository's committed snapshots conflict: those
written before v2 are in id order. Measured before this amendment, across 627
blobs, history held exactly the current snapshot's 1284 revisions, nothing
more and nothing divergent. `migrate` still rebuilt a different timeline,
diverging at entry 2, whose hashes matched nothing any writer held. v3.6 left
`migrate` as the route for a fresh clone of a never-published repository,
for recovery with no remote, and for CI's rebuild fallback. In each it
produced an approximation.

Amended: **when the working tree's snapshot is a verified copy (v3.6), it is
`migrate`'s order and its first entries.** The copy qualifies when its
records chain to its recorded head, each has a string id and an integer rev,
and no (id, rev) appears twice. The rebuild then takes the copy's records, in
order and as written. History adds only the (id, rev) revisions the copy
lacks, such as an unmerged branch's, after it. Those are ordered by the same
witnessed merge restricted to them, or by (rev, id) where their witnesses
conflict. `order` reports `snapshot`, `snapshot, then witnessed` or `snapshot,
then rev-id (witness orders conflict)`.

Unchanged: divergent content for an (id, rev) is refused as before, as are
unreadable source lines and log-only orphans. The published-prefix guard
still decides. A snapshot that does not verify leaves `migrate` exactly as it
was. For a repository whose committed snapshots always agreed in order, the
witnessed merge already produced this order; the change is visible where they
did not.

### v3.8 amendments (2026-09-26 — a write that changes nothing writes nothing)

<a id="a-revision-that-changes-nothing-is-not-written-defect-pc-9e70b7933815"></a>

_v3.8 amendments, source line 3917 — allocates no code._

`edit` with a value equal to the current one, or with no change at all,
appended a revision whose derived `touched` set was empty, at exit 0, and
`check` passed it. v2.14 (`pc-00b0`) described exactly this case and called
it correct, because `sync` skips such a revision (claim 35). That skip reaches
only the unpublished suffix. Once published, a revision that asserts nothing
is permanent in every clone, and it moves `rev` and `updated` as though the
record changed. This repository carries one.

Amended: **`edit` and `close` write no revision whose derived `touched` set
is empty.** The command exits 0 and reports the record as it stands, the same
echo a write gives, with `unchanged: true`. In the person's form it reads
`unchanged <id> rev <n> · …`. In an `--also-closes` batch, such an item is
listed where it stands in the plan, marked unchanged, and nothing is written
for it; the rest of the batch is written as before. A **forced** revision is
still written even when it changes nothing, because its brand records a use
of the escape hatch, which audit enumerates (`pc-dacc`). `sync`'s claim-35
skip is unchanged, for no-op revisions already in a log.

---

## Appendix — what this document does not carry

- The source's own history beyond the amendment text: the ledger records each
  sitting cites are in `refs/pecia/log`, not here.
- `spec/format-v1.md`, which base sections 7 and 9 defer to and which two codes
  (E005, E009) still depend on entirely (`pc-0aaa`).
- Any claim that the implementation does what this says. That is the kill
  matrix's job, and `dev/spec-agree.py` reports where the two derivations part.
