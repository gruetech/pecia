# pecia format — v1

**Status:** asserted spec (2026-07-25). Supersedes `format-v0.md`. Reconciled
from the Q1/Q2 conspectuses via `research/reconciliation-2026-07-25.md`.
**Outstanding before this may be called validated:** off-Claude replication of
the central finding (monoculture caveat), primary-source verification of the
reconciliation's starred figures, and the M2 checker enforcing every invariant
below with a demonstrated kill each.

## Foundational semantics (the four rules everything else serves)

1. **Exit-0 means "well-formed," never "true."** The checker certifies shape
   and consistency. Truth is established only by executable evidence and
   sampled audit. This sentence appears verbatim in checker `--help`.
2. **Derived state is computed, never stored.** `ready` and `blocked` are
   queries, not statuses. Anything recomputable from the graph must not be
   persisted, so it cannot rot.
3. **Capture is cheap; triage is audited.** Creating a record requires only
   `type` + `title`. Semantic completeness (edges, evidence, priority) is
   enforced by `audit` surfacing gaps — never by blocking creation. Rationale:
   required-at-creation fields measurably induce fabrication; unrecorded work
   is the best-replicated divergence channel. Both pressures point the same
   way: make writing effortless, make gaps visible.
4. **Absence is declared, not silent.** A semantic field may hold `"unknown"`;
   at audit level a gap is either declared frontier or a finding. Nothing is
   ambiently missing.

## Storage

- `.pecia/work.jsonl` — one record-revision per line (full JSON object).
  Updates **append** the complete revised record with `rev` incremented.
- **Resolution: highest `rev` per `id` wins** — merge-direction-independent
  (v0's last-occurrence-wins was shown to resolve by merge direction,
  silently; that defect is why `rev` exists). Two lines with the same
  (`id`, `rev`) but different content are a checker error (E010) requiring a
  manual merge disposition, never a silent pick.
- `.gitattributes`: `.pecia/work.jsonl merge=union`.
- `pecia compact` rewrites the file keeping resolved heads; run deliberately;
  git history retains everything else.
- `.pecia/config.yaml` — statuses/types/priorities are extensible per repo,
  but the core vocabulary below may not be redefined (workflow-fit gets an
  escape valve through *addition*, not mutation).

## Record

```json
{
  "id": "pc-a3f8",
  "rev": 1,
  "type": "defect",
  "title": "…",
  "status": "open",
  "priority": 2,
  "created": "2026-07-25",
  "updated": "2026-07-25",
  "edges": { "blocks": [], "parent": null, "duplicate_of": null,
             "discovered_from": null, "caused_by": null,
             "validates": null, "supersedes": null },
  "disposition": null,
  "evidence": "unknown",
  "owner": "nfeldman",
  "labels": [],
  "body": ""
}
```

### Field operational definitions

- **id** — minted once at creation: `pc-` + hash over (type, title, created,
  creation nonce), adaptive length per collision budget. The nonce exists
  because *uniqueness* is intended here, not idempotency; wall-clock time
  never enters identity. IDs are stable across edits. Never sequential.
- **rev** — positive integer, 1 at creation, +1 on every appended revision.
  A record *is* its highest-rev line. `rev` is the ordering relation; dates
  are data, not order.
- **type** —
  - `defect`: a deviation between what an artifact is recorded/expected to do
    and what it does. Closing as `done` requires executable evidence.
  - `task`: an intended change that is not a deviation.
  - `decision`: a choice among alternatives. Body carries MADR-style context /
    options / outcome; supersession runs through the `supersedes` edge; the
    checker enforces one active head per decision lineage.
  - `milestone`: a named aggregation point; children attach via `parent`;
    carries an optional `target` date; `done` when all children are terminal.
  - `question`: an unanswered information need. An open question blocks its
    dependents through the ordinary `blocks` edge. Exists because "blocked on
    an unanswered question" was the top undiscovered need in Q2 — the state
    was previously invisible inside `blocked`.
- **status** — stored states only; two competent readers must not diverge:
  - `open`: recorded; nobody is working on it.
  - `in-progress`: a named owner is actively working on it in the current
    session/day. (Staleness is an audit finding, not a status.)
  - `done`: the intent was carried out; disposition present; for defects,
    evidence is executable-**shaped** (E007) — **CORRECTED 2026-08-18
    (pc-a546): this op-def previously said evidence "is executable and
    exits 0," asserting a check rule 1 forbids. The checker never executes
    evidence; whether it actually runs and exits 0 is rule-1 territory,
    established only by the human or agent closing the record, never by
    `check` (v1.1's E007 operationalization: "the truth of evidence is
    rule-1 territory, its shape is structural").**
  - `dropped`: a deliberate decision not to do it; disposition present.
  - `superseded`: replaced by the record naming it in `supersedes`;
    disposition may point forward.
  - `blocked` and `ready` are **not statuses** (rule 2).
- **priority** — 0: blocks all other work in the repo today. 1: next up;
  ahead of anything new. 2: default; scheduled by `next`. 3: nice-to-have;
  only via explicit pull. 4: someday/idea; excluded from `next`.
- **edges** — targets are ids. Scheduling edges: **`blocks`** (target cannot
  be `done` while this record is non-terminal) and **`parent`** (containment;
  a parent cannot be `done` with non-terminal children). Custody edges (never
  affect scheduling): `duplicate_of` (same intent as target; resolve by
  closing one as `superseded`), `discovered_from` (found while working the
  target), `caused_by` (defect introduced by target), `validates` (this item
  is the check for target), `supersedes` (this record replaces target).
  `duplicate_of` is new in v1 — the best-attested issue relation in the
  literature, omitted by v0.
- **disposition** — required on every terminal transition: names what
  happened and why, sufficient for a reader with no session context. "Fixed"
  alone fails audit. (A stated disposition is not a correct one — Q1; hence
  sampled audit below.)
- **evidence** — one of: a **foreign reference**, shape `<scheme>:<id>`,
  whose scheme is declared in `.pecia/config.yaml`'s `resolvers:` (v1.7;
  `claims:<id>` is an ordinary instance of this, not a built-in special
  case); an **executable-shaped command** whose first token is path-shaped
  (contains `/`) or is declared in the evidence-command vocabulary — core
  defaults plus `extra_evidence_commands:` (v1.1, re-operationalized v1.14
  to stop reading the host: PATH lookups and filesystem existence checks
  made the identical ledger's verdict a fact about the machine running it,
  not the ledger); or `"unknown"` (non-terminal records only). Prose is not
  evidence. Shape only — whether a command's exit code or a reference's
  resolution would actually license the closure is rule-1 territory,
  established by `audit`, never by `check`. **CORRECTED 2026-08-18
  (pc-a546): this op-def previously described the v1.1 rule only, missing
  the v1.7 reference form and the v1.14 re-operationalization — both
  binding for several amendments before this section was re-read.**
- **owner** — stable identity string. Machine authors use a stable
  vendor-qualified identity (e.g. `claude:fable-5`, `copilot:gpt-x`), so
  provider drift is distinguishable from sampling variance later.

## Invariants (the checker contract)

**SUPERSESSION RULE, added 2026-08-18 (pc-a546).** This document had none —
twelve amendments were written and reviewed against the code of their day,
nobody re-read this section afterwards, and a reader had no textual basis
for treating it as stale even though it is the section consulted first. Now
binding: **a dated amendment below overrides this section wherever the two
conflict; this list is corrected to match, but the amendment is the primary
source and this list is projection.** The table below was re-verified
against the current implementation on the date of this correction, not
regenerated by tooling — GP28 agreement-checking (an automated check that
this section and the amendments cannot drift apart again) remains an open
question, not resolved by this correction. See pc-a546 for the four errors
this found and fixed, and two more in the Record field op-defs below it.

Each ships with a demonstrated kill at M2, or its claim stays aspirational.

- E001 every line parses; required fields present for the record's type
  <!-- vocab: {"action":"mint","code":"E001","at":"v1","by":"commit:15fd91c"} -->
- E002 duplicate (`id`, `rev`) with **identical** content — a warning, the
  normal `merge=union` artifact (v1.1; the base text here named this E010's
  case until this correction)
  <!-- vocab: {"action":"mint","code":"E002","at":"v1","by":"commit:15fd91c"} -->
- E003 edge target does not exist and is not declared in `planned`
  <!-- vocab: {"action":"mint","code":"E003","at":"v1","by":"commit:15fd91c"} -->
- E004 cycle in the `blocks`∪`parent` subgraph
  <!-- vocab: {"action":"mint","code":"E004","at":"v1","by":"commit:15fd91c"} -->
- E005 illegal status transition (legal: open→in-progress↔open,
  open/in-progress→done|dropped|superseded; terminal states are final —
  reopening means a new record with `discovered_from` the old one)
  <!-- vocab: {"action":"mint","code":"E005","at":"v1","by":"commit:15fd91c"} -->
- E006 terminal status without disposition
  <!-- vocab: {"action":"mint","code":"E006","at":"v1","by":"commit:15fd91c"} -->
- E007 `defect` + `done` with evidence that is neither executable-shaped nor
  a declared-scheme foreign reference (v1.1, re-operationalized v1.14 —
  first token path-shaped or in the declared evidence-command vocabulary;
  shape only, never executed)
  <!-- vocab: {"action":"mint","code":"E007","at":"v1","by":"commit:15fd91c"} -->
- E008 `rev` not **contiguous from the minimum present `rev` for that id**
  (v1.1; the base text here said "not monotonic (gap or regression)," a
  different property — a regression or a repeated rev is E002/E010's case,
  not E008's)
  <!-- vocab: {"action":"mint","code":"E008","at":"v1","by":"commit:15fd91c"} -->
- E009 decision lineage with more than one active head
  <!-- vocab: {"action":"mint","code":"E009","at":"v1","by":"commit:15fd91c"} -->
- E010 same (`id`,`rev`), **differing** content — an error, manual merge
  disposition required (v1.1; the base text here was identical to E002's,
  the exact ambiguity v1.1 existed to remove)
  <!-- vocab: {"action":"mint","code":"E010","at":"v1","by":"commit:15fd91c"} -->
- E011 evidence is a foreign reference (`<scheme>:<id>`) whose scheme is
  not declared in `.pecia/config.yaml`'s `resolvers:` (v1.7). Declaration is
  shape, checked here; resolution is truth, checked only by `audit`. Missing
  from this list entirely from v1.7 until this correction, despite being
  emitted and tested the whole time.
  <!-- vocab: {"action":"mint","code":"E011","at":"v1.7","by":"pc-4d1e"} -->
- Diagnostics: one JSON object per finding on stdout —
  `{severity, code, id, message}`; exit 0 clean / 1 findings / 2 cannot-run.

## Queries (deterministic)

- `ready` — open records whose `blocks`∪`parent` closure is all-terminal.
- `blocked` — open records with ≥1 non-terminal blocker, blockers listed;
  question-type blockers surfaced separately (they are answerable, not
  workable).
- `next` — `ready`, priorities 0–3, ordered by (priority, created, id).
  Total order; no randomness.
- `audit` — the anti-rot surface, all findings advisory (exit 0): untriaged
  records (empty edges, no `no_edges` declaration), stale `in-progress`,
  aging `"unknown"` evidence on defects, priority rot (p0/p1 open beyond a
  configured horizon), same-title near-duplicates, dispositions selected for
  spot-check. `audit --sample N` draws a deterministic seeded sample of
  closed records for human truth-audit — sampled oversight is the designed
  trust interface, because diff-review of machine-authored records
  measurably does not happen.
- `graph`, `gantt`, status tables — generated projections, never edited.

## Harness integration

- **On-demand over prime.** Agents query (`pecia next --json`) at need;
  session-start dumps are optional and small. (Primed constraints measurably
  decay across compaction; per-write cache invalidation makes big primes
  expensive.)
- **Pre-commit is the hard gate.** Harness-level hooks (e.g. task-creation
  vetoes) are convenience wiring — a shipped exit-2 regression class means
  they may not be the only enforcement.
- Interop: beads-JSONL import/export as the category's exit door; field
  alignment with the Anthropic task schema (`blocks`/`blockedBy` map to the
  `blocks` edge and its inverse computation).

## v1.1 amendments (2026-07-29 — driven by the M2 adversarial review)

The review (`research/M2-review-copilot.md`) surfaced ambiguities the v1 text
left underdetermined. Operationalizations, now binding:

- **E002 / E010 split.** The v1 table defined both as duplicate (`id`,`rev`)
  with differing content. Now: **E002** = identical duplicate (warning; the
  normal `merge=union` artifact; `compact` cleans it), **E010** = divergent
  duplicate (error; manual merge disposition required). Warnings alone exit
  0; any error exits 1.
  <!-- vocab: {"action":"amend","code":"E010","at":"v1.1"} -->
  <!-- vocab: {"action":"amend","code":"E002","at":"v1.1"} -->
- **E007 operationalized.** Evidence is executable-shaped iff its first
  shell token resolves on PATH or is an existing repo-relative path, or the
  string matches `claims:<id>` (validated against `claims.yaml` ids when
  that file exists). Prose fails; `true` passes — the truth of evidence is
  rule-1 territory, its *shape* is structural.
  <!-- vocab: {"action":"amend","code":"E007","at":"v1.1"} -->
- **E008 = contiguous from the minimum present rev.** Gap ⇒ error. Rationale:
  `compact` keeps heads at their original rev, so 1..max contiguity would
  false-positive every compacted ledger. Line *order* is not checked —
  union merges interleave legitimately; `rev` is the ordering relation.
  <!-- vocab: {"action":"amend","code":"E008","at":"v1.1"} -->
- **E003 is checked on heads only** — historical revisions may reference
  since-compacted ids.
  <!-- vocab: {"action":"amend","code":"E003","at":"v1.1"} -->
- **Queries refuse unresolvable ledgers.** `ready`/`blocked`/`next` exit 2
  (cannot-run) on unparseable lines or divergent same-(`id`,`rev`) content,
  directing to `check` — they never silently pick a side.
- **Closed-while-blocked is audit-surfaced, not an E-code.** The `blocks`
  op-def implies a record should not be `done` while a blocker is open, but
  across merged histories the retroactive legality of a past transition is
  genuinely ambiguous. `audit` reports it (`closed-while-blocked`);
  promotion to an error awaits dogfood evidence.
- **`target`** (YYYY-MM-DD) is an optional field on milestone records,
  consumed by `gantt`; E001 does not require it.

## v1.2 amendments (2026-07-29 — driven by review 2 and the formal model)

- **`priority` is required on every record** (E001). The CLI always writes
  it; a foreign writer omitting it is malformed, not defaulted. Queries may
  still read defensively, but persistence without the field fails check.
- **Inadequate-disposition audit, operationalized.** The Record op-def's
  '"Fixed" alone fails audit' becomes measurable: a terminal disposition
  under 20 characters (config `min_disposition_chars`) or under 3 words
  draws an `inadequate-disposition` audit finding. Advisory, never blocking
  — content quality is rule-1 territory; the audit surfaces it for the
  sampled human pass.
- **The hard gate checks the staged index, not the worktree.** Git commits
  the index; a hook that checks the worktree can be bypassed by staging a
  poisoned ledger and restoring the worktree (review 2, F7 — demonstrated).
  Pre-commit extracts `git show :.pecia/work.jsonl` and checks that.
- **Compaction discipline (trap 2 mitigation).** `compact` is sound only on
  a single lineage: run it on a synced mainline, never on a diverged
  branch. Two honestly-compacted divergent lineages union to a rev-gap
  E008 false positive (machine-found, spec/pecia-traps.als, pinned by
  test_trap2). A format-level fix (compaction markers) is deliberately
  deferred until dogfooding shows the rule is insufficient.
- **Branch-local clean is not merge-clearance (trap 3).** Cross-branch edge
  additions can union into an E004 cycle invisible to every branch-local
  check. The post-merge check is load-bearing: CI must run `check` on the
  merge result (PR merge refs), not only branch tips.

## v1.3 amendments (2026-07-30 — the write gate and the branded escape hatch)

Trap 1's disposition (pc-4e3b, decided yes). Formal contract:
`spec/pecia.als` T9 (BlameContainment) + T10 (ForcedStepsCarryTheMark);
seed 3 pins checker force-blindness adversarially.

- **The write gate.** `add`/`edit`/`close` validate structurally *before*
  appending: edge targets exist or are declared `planned`; no self-edges;
  legal transitions (terminal is final); disposition on terminal;
  executable-shaped evidence on defect closure; and the write may not close
  a cycle in the lineage-local blocks∪parent graph. A refused write prints
  its findings (E-coded, `write refused:` prefix) and exits 1 — **nothing
  is written**. Semantic completeness stays audit-only (rule 3 untouched).
- **`--force` bypasses the write gate, and only the write gate.** It brands
  the revision it writes with `"forced": true`. The brand is per-revision
  and never inherited; `forced`, when present, must be exactly `true`
  (E001). The brand is *content*: same-rev lines differing only in the
  brand are E010-divergent.
- **The checker is force-blind.** No E-code consults the brand; findings
  are identical with brands stripped. Force can never launder a violation
  past `check`.
- **Audit enumerates the hatch.** `audit` reports a `forced-write` finding
  for every branded revision in *history* (not just heads) — escape-hatch
  usage is recoverable from the ledger alone.

## v1.4 amendments (2026-07-30 — driven by review 3, F10–F16)

- **The full E001 field contract.** Every field is *typed*, not merely
  present: `rev` and `priority` are integers with booleans excluded; `id`
  is a `pc-`-prefixed token (hash-derivation is mint-time discipline, not
  post-hoc checkable); `created`/`updated`/`target` are `YYYY-MM-DD`
  strings; `title`/`owner` non-empty strings; `labels` a list of strings;
  `body` a string; `disposition`/`evidence` null or string; edge scalars
  null or id strings. Core types are exactly the five specced —
  `note` (a code-only stowaway) is removed.
- **E006/E007 are state checks on heads — declared, with the custody half
  preserved.** A repaired head is legitimately clean; permanent check-red
  for a corrected history would make `compact` a laundering incentive,
  which is worse. The violating revision is instead **audit-enumerated
  forever** (`historical-custody-violation`), exactly like `forced-write`:
  state repairs, custody remembers.
  <!-- vocab: {"action":"amend","code":"E007","at":"v1.4"} -->
  <!-- vocab: {"action":"amend","code":"E006","at":"v1.4"} -->
- **The write gate is the checker.** A write is refused iff it introduces
  new error findings into the ledger (check-diff). The gate and the
  checker cannot drift because they are the same function; every current
  and future E-code is automatically write-gated. (Replaces v1.3's
  enumerated gate list, which review 3 caught missing E001-vocabulary and
  E009-lineage — F12.)
- **Queries refuse divergence anywhere in history.** v1.1 as written; the
  implementation had invented a head-only exception (F15). No semantic
  change — an implementation correction.
- **The hard gate checks staged state completely.** Staged `claims.yaml`
  (F13) and the staged ledger/config *pair* (F14) — a worktree-only
  `planned:` entry must never vouch for a staged edge. Stated boundary:
  evidence *shape* resolution (PATH lookup, repo-file existence,
  claims-ref ids) runs against the worktree, deliberately — evidence
  commands execute in worktrees, not indexes.

## v1.5 amendments (2026-07-30 — driven by review 4, F17–F19)

- **The write path is atomic.** Read→validate→append happens under an
  exclusive ledger lock (`.pecia/.lock`, flock). Without it,
  check-then-append is a TOCTOU race: review 4's 40-writer storm produced
  divergent same-rev appends carrying no force brand — unforced dirt,
  falsifying blame containment exactly where the formal model declared
  same-worktree races out of scope. Same-worktree writers now serialize;
  cross-clone concurrency remains merge territory (union + rev resolution
  + E010) by design.
- **The checker is total.** Malformed field shapes (unhashable types,
  nested lists, object-valued edges) yield E001 findings — never a crash.
  Structurally unsound records are excluded from graph/transition/head
  phases (their E001 findings are the diagnosis); queries refuse malformed
  ledgers with a clean exit 2.
- **Canonical files cannot be silently deleted.** A staged deletion of
  `claims.yaml`, `.pecia/work.jsonl`, or `.pecia/config.yaml` (present in
  HEAD, absent from the index) fails pre-commit — a file missing from the
  index skips every content gate, so absence itself is now gated.
  De-adopting pecia is deliberate: `--no-verify`.

## v1.6 amendments (2026-07-31 — driven by deriving the adapter schema)

- **The record format has a frozen, versioned JSON Schema** —
  `spec/record.schema.json`, the adapter-facing contract for the
  single-record shape (E001 territory). Cross-record invariants
  (E002–E010) remain checker-only, so schema-valid never implies
  check-clean, and neither implies true (rule 1). Where a repo's config
  widens the vocabulary (`extra_types`/`extra_statuses`), the checker is
  authoritative and the schema documents core. A divergence between
  schema and checker on a core shape is a defect, kept dead by the
  differential kill matrix (`tests/test_pecia.py::RecordSchema`) and
  `dev/schema-check.py`, which validates a ledger against the schema and
  the schema against its 2020-12 metaschema. The schema's `version`
  tracks the amendment level of this spec.
- **`edges.no_edges`, when present, must be exactly `true`** (E001) — the
  same rule as the `forced` brand: a declaration is presence, not a
  toggle. Found deriving the schema: the checker accepted any value here
  while audit's truthiness read counted `"yes"` as declared and `false`
  as undeclared — two competent readers diverging on a stored value fails
  the status op-def's own standard. (pc-0d8f)

## v1.7 amendments (2026-08-01 — the cross-ledger boundary, decision pc-4d1e)

- **A foreign reference names a fact this ledger does not own.** Shape:
  `<scheme>:<id>`, scheme `[a-z][a-z0-9_-]*`, id `[A-Za-z0-9._/-]+`, no
  whitespace anywhere — which is exactly what distinguishes a reference from
  an evidence command (`true`, `./x.py --flag`). Legal wherever `evidence` is.
  `claims:<id>` becomes an ordinary instance of the general form rather than a
  built-in.
- **Schemes are declared in config, never hardcoded in the core.**
  `.pecia/config.yaml` carries `resolvers: [scheme=verifier command, …]`;
  adding a sibling system is a config edit. `DEFAULT_RESOLVERS` supplies
  `claims` so existing ledgers keep working; a config entry for the same
  scheme overrides it. **Caveat inherited from the flat-YAML subset:** a
  verifier command may contain neither a comma (the list separator) nor a `#`
  (the comment marker).
- **Declaration is shape; resolution is truth.** `check` enforces only that a
  reference's scheme is declared — **E011**, an error. It never executes a
  resolver, so `check` stays a pure function of the ledger (design target 1)
  and an uninstalled sibling system cannot redden a pre-commit gate on a
  ledger that is fine. `audit` runs each declared verifier and reports
  `unresolvable-reference` advisorily. Exit 0 from a verifier means
  **resolvable**, never true — rule 1 is unchanged, and the sentence a
  resolved reference licenses is exactly "the foreign system carries this id."
- **The ownership boundary this serves:** pecia owns repo-scoped work
  *intent*; a sibling system owns what it owns (for chorusmith: run-scoped
  execution and artifact lineage). Anything crossing is a reference, never a
  copy — rule 4 applied across systems, and the standing answer to
  second-ledger drift. Duplication was rejected because an importer is not a
  sync (E010 is the tripwire); a storage abstraction was rejected as
  speculative generality against design target 5.
- **Two defects closed by relocation** (`dev/claims-ref.py`): the core no
  longer hardcodes a scheme's regex, path, and parser (an OCP violation that
  made every new scheme a core edit), and the claims resolver no longer
  returns valid when `claims.yaml` is absent — a gate that could not turn red
  in precisely the repos most likely to hit it (VP4). A missing claims ledger
  is now a non-zero exit, i.e. an audit finding, not silence. (pc-4719)
- **Not in scope:** foreign references in edge targets. Edges drive
  scheduling, and pecia cannot compute a foreign record's terminality; E003
  still requires every edge target to exist in the ledger or be declared
  `planned`.

## v1.8 amendments (2026-08-04 — evidence portability, defect pc-f44a)

> **SUPERSEDED by v1.14 (2026-08-12).** The rule below — a path is evidence iff
> git tracks it — was itself a host read, narrower than the one it replaced but
> still not a function of the ledger alone. v1.14 removes it. Retained here
> because pc-f44a's defect and its reasoning are the record of how the boundary
> moved, and because this document has no supersession convention (pc-a546).

- **A path is evidence iff git tracks it.** E007's operationalization (v1.1)
  accepted a first token that "resolves on PATH **or is an existing
  repo-relative path**". Existence is a property of one machine. The rule is
  now split by shape, and the second half is custody rather than presence:
  - a **bare name** (no `/`) must resolve on PATH — the same lookup on every
    machine that has the tool, which is what CI reproduces;
  - a **path** (any `/`) must name a git-tracked repo-relative file, read
    from the **index**, so a helper staged in the same commit as the record
    citing it counts. Absolute paths and paths escaping the root are refused
    as machine-local by construction.
- **Why it changed.** A `done` defect carried
  `.venv/bin/python -m pytest …`, which passed on the author's machine
  because an untracked `.venv` existed there and failed everywhere else; the
  identical ledger produced opposite verdicts, and main's next push would
  have failed CI at the substrate job. `shutil.which()` could not have caught
  it: given a name containing a separator it checks that exact path, so the
  PATH branch admitted the machine-local interpreter too.
- **The rule is uniform, with no second branch.** Outside a git repo nothing
  is tracked, so path evidence fails there. An earlier cut of this amendment
  accepted path evidence on shape when no repo was present; that made
  `./missing.py` yield E007 inside a checkout and nothing on an extracted
  tree — the same per-environment verdict this amendment removes, merely
  relocated. pecia's custody claim is a git claim: where there is no git
  there is no custody. Evidence that must hold anywhere names a PATH command.
- **`check` is not thereby pure.** It reads the git index, which is external
  state, and design target 1 asks for a pure function of the ledger. This is
  a deliberate trade recorded rather than hidden: the branch it replaces read
  the filesystem too, and read it *per-machine*. The index is shared by every
  clone and by CI, so the verdict now varies with the commit instead of with
  whose laptop is running it. Resolved once per process, never per record.
- **Consequence for adapters.** An adapter writing evidence that names itself
  repo-root-relative (beads-import's `--evidence-prefix` default) requires
  that adapter to be tracked in the repo being imported into — which the flag
  already documented, and which is what an import target actually is.

## v1.9 amendments (2026-08-04 — what the schema's `version` means, defect pc-ae48)

- **`spec/record.schema.json`'s `version` is the spec amendment level it was
  last synchronised to, and it must equal the highest amendment level in this
  file.** v1.6 said it "tracks the amendment level of this spec"; the schema's
  own `$comment` said it tracks "the amendment level the schema was derived
  from." Those are different rules with different answers, so no reader could
  tell whether `1.7` beside a v1.8 spec was correct or stale. Two competent
  readers diverging on a stored value is the same standard v1.6 itself applied
  when it typed `no_edges`. The first reading wins, and it is now gated
  (`tests/test_pecia.py::SchemaSpecSync`).
- **Why the strict-equality reading, given it bumps the version for amendments
  that change nothing in the schema.** The bump is one character; the review it
  forces is the point. v1.8 changed what E007 accepts and the schema's
  `evidence` description still described the v1.1 rule — an adapter author
  reading the adapter-facing contract would have written path evidence that the
  checker refuses. A version that only moves when someone *notices* is a
  version that certifies nothing; a gate that turns red on every amendment
  until the schema is re-read is the cheap way to make noticing structural.
  (Practice catalog VP4: a gate that cannot turn red is not a gate.)
- **The `evidence` description now carries the v1.8 rule** — a bare name
  resolves on PATH, a path must be git-tracked and is read from the index.
  Corrected as part of this amendment, and the reason the gate exists.
- **Not thereby a claim about the schema's constraints.** Version equality says
  the schema was reviewed at that amendment level, never that its constraints
  changed. Schema-valid still does not imply check-clean, and neither implies
  true (rule 1).

## Changes from v0 (all trace to the reconciliation)

| Change | Driver |
|---|---|
| Exit-0 semantics stated as rule 1 | checker-certified fabrication (Q1) |
| Creation minimal; audit-not-block for completeness | PhantomFill / Constraint Tax (Q2) + information islands (Q1) |
| `rev` counter; highest-rev resolution | merge-direction defect, reproduced (Q2/B1); ordering gap (B6) |
| `ready`/`blocked` demoted from statuses to queries | derived-state-never-stored |
| `duplicate_of` edge added | best-attested relation, v0 omission (Q1) |
| `question` record type added | top undiscovered need (Q2) |
| Evidence executable-or-claims-ref; sampled truth audit | A3/B7 Goodhart bounds |
| Sampled audit as the oversight interface | custody ≠ oversight (Q2/B2) |
| On-demand harness access; pre-commit primacy | B4, B5 |

## v1.10 amendments (2026-08-04 — the output contract, defect pc-cdb8)

Records are written by strangers: `ADAPTERS.md` invites imports from beads /
Jira / GitHub Issues, and `merge=union` pulls records across branches. Agents
read this ledger's output. pecia's own stdout is therefore a channel from a
third-party author into a reading agent's context, and until now it was an
open one.

- **Three field categories, and the mechanism follows the category.**
  - **Withheld** — `body`, `disposition`, `evidence`, `labels` never leave any
    command as text. Enforced *subtractively*: output is built up from a
    declared per-command allow-list, never a record with fields removed, so a
    field nobody enumerated is absent by default. This is the mechanism
    because it is the one that was measured to hold: catalog v2.1's GP8/BP4
    scope qualifier, whose supporting comparison was run on a field
    physically stripped before serialization. A *"the following is data, not
    instruction"* preamble is the prose half of that comparison, and is
    deliberately not what this rests on. The figures are deliberately not
    reproduced: the rubric is private and unpublished, so this document
    cites the allocation and leaves its evidence where it lives.
  - **Bounded** — every other emitted string, including `title`, `owner`,
    every checker `message`, every finding `id`, and exit-2 diagnostics.
    Control characters, bidi overrides and zero-width marks are stripped;
    whitespace including newlines collapses; fence openers collapse; length
    is capped.
  - There is **no trusted category.** An invariant the checker enforces is not
    an invariant the query path may assume: queries gate on structural
    soundness, not on E001, so an out-of-vocabulary `type` reached `ready`
    verbatim; `ID_RE` accepts `pc-IGNORE-PREVIOUS-INSTRUCTIONS`; and `--force`
    bypasses E001 by design.
- **The bound is part of the contract.** The transform buys **form, not
  truth**. A `title` may still be a one-line instruction and will reach every
  reading agent — `title` is what `next` exists to emit. What it can no longer
  do is break its frame. Nothing here makes a record unable to instruct an
  agent, and no projection of this spec may say otherwise.
- **Prose information needs are served by metrics, never by text.**
  `body_chars`, `disposition_chars`, `disposition_words`, and a structural
  `evidence_kind` (`absent` / `unknown` / `command` / `prose` /
  `reference:<scheme>` / `malformed`). These are a **declared** content-derived
  side channel, and `audit` additionally discloses whether a foreign reference
  resolves. `evidence` is withheld more firmly than the rest: its semantic type
  is *a command to run*, and E007 admits `echo <anything>`.
- **The audit sample selects; it does not quote.** `audit --sample N` names
  `id` + `rev`, which pin the exact revision to open. It previously reproduced
  the disposition and the evidence command under the word "verify:".
- **Resolver output is a closed vocabulary** — `not-found` / `timeout` /
  `nonzero-exit` / `unparseable-command` / `os-error` / `empty-command`, plus
  the exit code. A declared verifier's stderr was previously spliced into an
  audit note; the verifier's *argument* is ledger content, so any resolver
  echoing it laundered ledger text back out.
- **Write echoes are projections too.** `edit` and `close` deep-copy the prior
  head, so they echoed a `body` the current caller never authored — an agent
  triaging an imported record took the importing stranger's prose into its
  context. `add` echoes only its own argv and is projected for uniformity,
  because the rule being exception-free is what makes its differential test
  exception-free. Presence-semantics fields keep presence semantics: an absent
  `forced` brand is an absent key, never `false`.
- **`.pecia/config.yaml` is declared trusted, not validated.** It is not
  `merge=union`, and anyone who can write it can write the CLI. Its values are
  still bounded in form.
- **Out of scope, permanently:** direct reads of `.pecia/work.jsonl`
  (`Read`, `git show`, `git diff`) expose everything. Only pecia's own stdout
  and stderr are covered — plus M4 harness wiring, whose SessionStart prime is
  pecia's own output and inherits this. An agent's ad-hoc read never will.
- **No content filtering on write.** A heuristic "does this look like an
  instruction" check over a field the writer controls is BP4's inverse,
  satisfied or evaded to order, and a halting version would refuse legitimate
  bodies. Rule 3 is untouched: capture stays cheap.

**`spec/record.schema.json` is bumped to `version: "1.10"`.** This amendment
changes the *output* contract and nothing in the stored record shape, so the
schema's constraints are unchanged. It is bumped anyway, because v1.9 settled
what that field means: strict equality with the highest amendment level, so
the number asserts *review at that level*, never that the constraints moved.
The review was done and is recorded here — the schema describes stored shape
only; `disposition`'s "\"Fixed\" alone fails audit" and `evidence`'s v1.7/v1.8
rules both remain accurate, since v1.10 changes what is *emitted*, not what is
*stored* or what E007 accepts.

(An earlier draft of this amendment argued the opposite — that bumping would
"assert a derivation that did not happen" — and was numbered v1.8. v1.9 landed
first on main, considered that exact argument, and rejected it: a version that
moves only when someone notices certifies nothing. Renumbered and conformed on
rebase, 2026-08-06.)

Design and the off-vendor challenge that corrected it:
`research/prose-containment-design.md`, `research/pc-cdb8-codex-1-design.md`.

## v1.11 amendments (2026-08-07 — how a cap is sized, defect pc-88c6)

v1.10 said emitted strings are "capped" and left the sizing unstated. The
implementation sized each cap to observed data plus a small margin, which is
the draconian reading, and `DATE_CAP` proved it: at **10** — the exact width
of the only legal value — it had zero headroom, so an ISO 8601 timestamp from
an importer rendered as `2…97af6a1b` instead of the value an operator needs
in order to fix it. Now binding:

- **A cap bounds worst-case VOLUME so output can be reasoned about. That is
  its entire purpose.** `next --limit N` costs at most N times the per-record
  ceiling. A cap is not a filter, makes no judgement about content, and is
  never what stops an instruction — there is ample room to write one inside
  any of them. Anti-flood, not anti-instruction, and no projection of this
  spec may imply otherwise.
- **Each cap is an *unrealistic* ceiling that is nonetheless cheap**, at
  least 4x the largest value any legitimate source produces. A cap sized near
  real data eventually truncates real data, and truncated real data is a bug
  that reads like an attack. Both directions are gated by
  `tests/test_pecia.py::OutputCaps`: headroom over a documented legitimate
  maximum, and a stateable per-record ceiling, so neither re-tightening nor
  unbounded drift passes silently.
- **The limit remains required.** A bound you cannot compute with is not a
  bound; the point is that worst-case output is a number, not that it is a
  small one.
- **`spec/record.schema.json` -> `version: "1.11"`** per v1.9. Re-read on
  amendment, as that rule intends: this changes what is emitted, not what is
  stored, and no constraint in the schema is stale against it. Note for
  whoever widens `isoDate` — the caps no longer stand in the way, but E001's
  `YYYY-MM-DD` still does, and that is a separate decision.

## v1.12 amendments (2026-08-07 — timestamp precision, decision pc-75b1)

`created`, `updated` and `target` required exactly `YYYY-MM-DD`. Every source
an adapter reads — Jira, GitHub Issues, beads — carries a timestamp, so every
importer truncated to the day and declared the loss under ADAPTERS.md rule 3.
The loss bought nothing: `rev` is the ordering relation, so time precision was
never load-bearing. Now binding:

- **A date, required and always leading, optionally carrying more precision.**
  `YYYY-MM-DD`, then optionally `T` + `HH:MM`, then `:SS`, then `.` and 1–9
  fractional digits, then `Z` or `±HH:MM`. Offsets only appear alongside a
  time. Legal wherever a date is.
- **Why this shape and not "any ISO 8601".** Ordinal dates (`2026-219`), week
  dates (`2026-W32-5`) and bare times are all ISO 8601 and none of them sorts
  lexicographically against `YYYY-MM-DD`. Because the date leads and is
  mandatory, a date-only string sorts as the *start of its day* — which is
  exactly what lets `audit` keep comparing these as plain strings against a
  date-only cutoff, because a date-only cutoff is always a strict prefix of
  every timestamp on that day. Pinned by
  `tests/test_pecia.py::TimestampPrecision::test_audit_cutoffs_stay_correct_across_mixed_precision`.
  **CORRECTED 2026-08-18 (pc-749d): "lexicographic order remains
  chronological order" was claimed as a general property. It is false at an
  otherwise-equal prefix once precision varies — `.` (0x2E) sorts below any
  digit and below `Z` (0x5A), so `…12:00:00.5Z` (500ms into the second)
  sorted *before* `…12:00:00Z` as a plain string, and the same shape recurs
  one level up for a missing `:SS`. Only the narrower, still-true claim above
  (date-only sorts as start-of-day, because it is a strict prefix) survives.
  `next`/`ready`/`board`'s sort no longer compares `created` as a raw string
  for this reason — see `chronological_key()` in `pecia_cli.py`, pinned by
  `tests/test_pecia.py::ChronologicalOrdering`. `audit`'s date-only cutoffs
  were never affected: the prefix property they rely on was always true.
- **`T` is the only separator.** ISO 8601 permits a space by agreement;
  refused here, because a space makes the value ambiguous with prose in a
  field this project bounds and transforms.
- **Shape only, unchanged.** `9999-99-99` still passes E001. Whether a date is
  *real* is rule-1 territory, not the checker's.
- **Strictly widening.** Every existing ledger stays valid, and pecia's own
  writes are unchanged — `today()` still emits date-only, so no record
  acquires precision it did not have.
- **`gantt` truncates to the day, and says so in the chart.** Its `dateFormat`
  is `YYYY-MM-DD`, its row syntax is colon- and comma-delimited and a
  timestamp carries both, and `dev/report.py` parses those rows expecting a
  bare date. This is the one declared-lossy projection; every other emit path
  carries the full value. Declared in the output, not left to be discovered as
  a broken bar.
- **Known imprecision, stated rather than discovered:** a UTC-offset timestamp
  can fall on the far side of a date-only cutoff by up to a day
  (`2026-08-01T00:00:00+05:30` is `2026-07-31T18:30Z`). Within tolerance for an
  advisory staleness audit; a cutoff that must be exact needs a real time
  comparison, not a string one.
- **`spec/record.schema.json` → `version: "1.12"`** per v1.9. Re-read on
  amendment: `$defs/isoDate` is the one constraint this touches, and it is
  widened in step. `dev/claims-check.py`'s date rule governs `claims.yaml`'s
  `verified:` field, is a separate contract, and is deliberately unchanged.

## v1.13 amendments (2026-08-11 — the `retires` relation, question pc-4d19)

One commit routinely resolves several records, and until now the linkage was
prose: 12 records across 4 groups in this repo were closed off shared work,
each hand-dispositioned, with nothing computing the relation between them
(measured, pc-4d19). The cost is not tidiness. M3 arm 3 imported a chorusmith
campaign whose headline asserted it retired **13** findings while its own
items addressed **10**; that over-promise had survived six weeks, and pecia
caught it only because the adapter *chose* to model retirement as `blocks`.
A pecia-native ledger could not have expressed the defect at all — the
project's strongest dogfood receipt was a bug its own format could not state.

- **`edges.retires` — a list of ids, held by the record that resolves them.**
  `Y.retires: [X]` asserts *closing Y closes X*. New core edge; absent is
  read as empty, so every existing ledger stays valid.

- **The direction is forced by the failure mode, not chosen for taste.** It
  must be a list on the RETIRER and never a scalar `retired_by` on the
  retired. chorusmith's catch was an **E003 dangling target** — three of the
  13 claimed findings had no records at all. A scalar written onto the
  retired record can only be attached to records that exist, so it cannot
  express an over-promise and therefore cannot catch one. E003 now covers
  `retires` targets, which makes that catch native rather than an adapter's
  reading.

- **It is a SCHEDULING edge, settled by this spec's own operational test.**
  Scheduling edges are exactly those that (a) constrain terminality, (b)
  enter E004's cycle subgraph, and (c) feed the `ready`/`blocked`/`next`
  closure. `retires` constrains terminality, so it qualifies, and it takes
  all three. Half-membership — the invariant without graph membership — was
  considered and rejected: mutual retirement would deadlock with no named
  cycle, when E004 exists precisely to name that.

- **The scheduling arc runs RETIRER → TARGET, the same orientation as
  `blocks` and `parent`.** An arc `u → v` means *u must reach a terminal
  status before v can*; that is `blocks` read forwards, `parent` read from
  the child, and `retires` read from the retirer, since X's terminality is
  produced BY Y's closure. So an open retirer holds its target out of `ready`
  and `next` — which is the forward signal pc-4d19 named as its first gap:
  `next` no longer offers work whose resolution is already somebody else's
  scheduled job.

  **This corrects pc-4d19 rev 2**, which read the arc the other way ("Y
  cannot be done while the X in Y.retires is non-terminal"). That is right as
  a *consistency invariant* — it is what E012 enforces below — and wrong as a
  *scheduling arc*: with several records retiring one target it deadlocks
  every one of them behind a target that only their own closure can resolve.
  The two directions are not in competition; they are different questions,
  and the analysis had merged them.

- **E012 — a retirement promise that expired unkept. Error, a state check on
  heads.** Fires when a record is non-terminal **and every record naming it
  in `retires` is terminal**.
  <!-- vocab: {"action":"mint","code":"E012","at":"v1.13","by":"pc-4d19"} -->

  It is anchored on the RETIRED record, not on each retirer, and that is the
  whole of the design. The obvious formulation — "Y may not be terminal while
  anything in `Y.retires` is non-terminal" — is wrong wherever one item is
  resolved by several pieces of work: Y1, Y2 and Y3 each name X, Y1 lands
  first, and the obvious rule reddens Y1 for doing its share. Read from X
  instead, the invariant is silent while any retirer is live and fires
  exactly when the last one goes terminal and X is still open. Where there is
  one retirer the two formulations coincide, so nothing is given up.

  Consequences, stated rather than left to be discovered:
  - **Terminality is `TERMINAL_STATUSES` throughout**, as everywhere else in
    this spec. A *dropped* retirer counts as gone, and the finding then says
    the promise died with it. A `done`-only reading would be the one place in
    the checker where the five statuses split three/two, with nothing under
    it but this E-code's convenience.
  - **A target that does not exist is E003's finding, not E012's.** Reporting
    both would double-count the chorusmith case.
  - **A head-state check, per v1.4.** A repaired head is legitimately clean;
    the alternative makes `compact` a laundering incentive. Unlike E006/E007
    this one is *not* additionally enumerated in `historical-custody-violation`,
    because "was this violated when it was written" needs the target's status
    at that point in history, and the ledger does not linearize across
    lineages. Declared as a known gap rather than approximated.
  - **Partial retirement is not a `retires` edge.** If Y's work only
    contributes to X, the true edge is `blocks` — "X cannot be done while Y
    is non-terminal" — which already exists and already composes
    conjunctively. Writing `retires` for a partial contribution is the wrong
    edge, and ADAPTERS.md's rule holds: a wrong edge is worse than a declared
    absence. Discovering *after the fact* that a claim was partial is
    repairable, because terminality is final for **status** and not for
    edges: `edit` the terminal retirer, drop the target, and E012 clears.

- **`--also-closes` on `close` and `edit` — the population mechanism.** A
  vocabulary nobody populates buys nothing (PLAN.md's measured
  unpopulated-edge problem), so the edge is written because it is the
  cheapest way to close a group, not because a rule tells an agent to —
  substrate-not-prompt applied to an edge rather than to a gate. It adds the
  `retires` edge AND closes the named targets, giving each the retirer's own
  disposition, prefixed `Retired by <id>: `.
  - **Atomic and ordered.** The write gate refuses a write introducing a new
    error, so a revision making the retirer terminal while a target is open
    is refused by E012 — correctly. The targets therefore close FIRST, inside
    one ledger lock, each gated against a ledger already carrying the
    previous ones; if any is refused, nothing is appended at all.
  - **It works on ALREADY-TERMINAL retirers, and that is the common case.**
    Discovering after Y is done that Y's work retired X is what actually
    happens, and a plain `edit Y --retires X` is refused by E012 while X is
    open. `edit --also-closes` is the retroactive path; it requires the
    retirer to be terminal already, because a non-terminal retirer needs no
    sugar (`--retires` alone is legal and silent).
  - Evidence fills a gap, never overwrites custody: a target already carrying
    its own structural evidence keeps it.
  - A batch emits a **JSON list**, in write order.

- **`audit` gains `prose-only-linkage`** — a disposition naming record ids
  the record has no edge to. This is the third gap pc-4d19 named, and it is
  what catches whatever bypasses both the edge and the sugar.
  - **Disclosure rule, and it is why reading a withheld field here is
    permissible at all:** a token is emitted only when it is a **key of the
    ledger** — a value `ready`, `blocked` and `graph` already emit — and
    every other `pc-`-shaped token is **counted, never quoted**. The alphabet
    `ID_RE` accepts is wide enough to spell an instruction, which v1.10 says
    in terms; this rule means the finding cannot become a channel for prose
    that merely matches the pattern. The disposition text itself stays
    withheld, exactly as `inadequate-disposition` leaves it.
  - **Linkage is read UNDIRECTED.** The relation is recorded once, on one
    end, and which end is a representation choice this finding has no
    business caring about. Read directionally it fires on every record
    `--also-closes` writes — the target's disposition says "Retired by
    `<retirer>`" while the `retires` edge lives on the retirer — so the sugar
    would manufacture the finding it exists to prevent. Found by this
    amendment's own discriminating control test, not by review.
  - The finding does not assert *which* edge is missing. It fires on absent
    linkage; `retires` is one repair and `discovered_from` / `supersedes` /
    `blocks` are others, and choosing is the record's own question.

- **What `retires` deliberately cannot express, declared under rule 4.** Its
  targets are record ids, so it cannot name a **retirer that is not a
  record**. Found while retrofitting this repo's own ledger: in pc-4d19's
  measured sample the thing that retired a record is most often a *commit* or
  a rebuild (`pc-4502`: "Fixed in the 2026-07-29 rebuild (commit 29cf564)"),
  and there is no record to hang the edge on. Minting retirer records to
  satisfy the edge would fabricate work items, which rule 3 exists to
  prevent. v1.7 already ruled foreign references out of edge targets, on the
  grounds that pecia cannot compute a foreign record's terminality; a commit
  has the same problem. Recorded as an open question, not closed by this
  amendment.

- **`spec/record.schema.json` → `version: "1.13"`** per v1.9. Unlike the last
  three amendments this one *does* change the stored record shape:
  `edges.retires` is a new array property, and `additionalProperties: false`
  on `edges` would otherwise reject every record carrying it.

## v1.14 amendments (2026-08-01, landed 2026-08-12 — E007 stops reading the host, defect pc-2251)

**Renumbered from v1.8.** This amendment was authored on a branch off v1.7 and
carried the number v1.8; main independently minted v1.8 for `pc-f44a` while the
branch sat unmerged, so two amendments claimed one number. This one moves, since
pc-f44a's is on main. Same collision class as the E012 one this merge also
resolved — a hand-assigned sequential namespace with no allocation discipline
(`pc-e9cf`). **It SUPERSEDES v1.8 above**, which is retained as history: the
git-tracked-path rule pc-f44a introduced is no longer what the checker does.

v1.7 restored ledger-purity for *references* and left it broken for
*commands*, which nobody noticed because the v1.4 text had licensed the
breakage in as many words. Both halves are settled here, the same way.

- **The v1.4 worktree carve-out is withdrawn.** v1.4 stated, as a deliberate
  boundary, that "evidence *shape* resolution (PATH lookup, repo-file
  existence, claims-ref ids) runs against the worktree." It cannot be
  deliberate and also leave `check` a pure function of the ledger; the
  contradiction was live in one function's docstring. **Shape is now read off
  the ledger and its config, and off nothing else.**
- **What it cost, concretely.** At dfc87de the identical ledger exited 0 in a
  clone carrying an untracked `.venv/` and exited 1 with E007 in a fresh
  worktree of the same commit — because one record's evidence began
  `.venv/bin/python` and `.venv/` is untracked. Pre-commit, the *hard* gate,
  inherited it: commits were blocked in every worktree and every fresh clone
  until someone happened to create a virtualenv. A gate whose colour is a fact
  about the machine is the VP4 class, and pc-4719 (the claims resolver that
  returned valid when `claims.yaml` was absent) was the same class one
  amendment earlier.
- **E007 re-operationalized** (superseding the v1.1 wording). Evidence is
  executable-shaped iff it parses as a shell command and its **first token**
  either **contains `/`** — path-shaped, so `.venv/bin/python`,
  `dev/alloy-gate.sh`, `./x.py` — or is **declared** in the repo's
  evidence-command vocabulary: `DEFAULT_EVIDENCE_COMMANDS` in the core,
  extended per-repo by `extra_evidence_commands: [...]` in
  `.pecia/config.yaml`. Prose fails; `true` passes, as before.
  <!-- vocab: {"action":"amend","code":"E007","at":"v1.14","by":"pc-2251"} -->
- **Why not simply drop the host reads.** Shape-checking that merely
  shlex-parses to a non-empty token list accepts `not-a-command` and
  `Fixed by rewriting the resolver`, and refusing prose is the entire reason
  E007 exists. `true` and `not-a-command` are syntactically indistinguishable,
  so the discrimination needs *some* vocabulary; the choice is only whether it
  is the machine's or the repo's. **PATH is itself an allowlist — an
  undeclared one, sampled from whatever happens to be installed.** Declaring
  it in config does not add arbitrariness; it moves an existing arbitrary set
  out of the environment and into a versioned, reviewable, staged-and-checked
  file. Adding `Fixed` to that list is then a visible act, not an accident.
- **Resolution moves to `audit`**, exactly as for references: a head token
  that is neither on PATH nor a path in this tree draws
  `unresolvable-evidence`, advisory. Every host read that used to sit in
  `check` now sits there and nowhere else. A path-shaped head is therefore
  trusted on shape and reported when it does not resolve — the same bargain a
  declared-but-unresolvable reference already gets.
- **Rule 1 is untouched.** A declared command name is not a true one; the
  vocabulary bounds *shape*, and truth remains executable evidence plus
  sampled audit. `claims.yaml` records the demonstrated kill: the null arm
  (prose, sentence prose, undeclared bare command, `"unknown"`, absent) turns
  E007 red, and the hermeticity arm asserts byte-identical `check` output for
  one ledger with and without a `.venv/`, and under an emptied `PATH`.
