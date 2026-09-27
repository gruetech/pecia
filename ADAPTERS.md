# Writing a pecia adapter

An adapter converts another tracker's export — beads, Jira, Linear, GitHub
Issues, a markdown TODO pile — into pecia records. Adapters live outside
the core (decision pc-8a27): pecia ships the **affordance**, not the
connectors. This file is the contract, written to be handed to an agent
verbatim. The worked example is [`adapters/beads-import`](adapters/beads-import/).

Rewritten for the v2 storage model on 2026-09-07 (pc-4f02, pc-8a68): the
canonical ledger is an append-only, hash-chained log under the clone's
`.git`, written only through the CLI's compare-and-swap, and
`.pecia/work.jsonl` is a **generated snapshot** of it. Two consequences run
through everything below: you never append to a ledger file, and ongoing
reconciliation of a changing source is the format's native path, not a
forbidden second program.

## The contract (start here)

> Emit one JSON object per line, each matching
> [`spec/record.schema.json`](spec/record.schema.json). Validate with
> `python3 pecia_cli.py check --ledger out.jsonl`. Exit 0 means **well-formed —
> now read rule 1**: well-formed is never true. The checker certifies
> shape and consistency; truth stays with executable evidence and sampled
> audit. Nothing you emit becomes true by validating.

The checker IS the adapter validation harness. You do not need a test rig,
a mock, or pecia's internals — you need a loop:

```
write out.jsonl  →  python3 pecia_cli.py check --ledger out.jsonl  →  read
findings (one JSON object each, E-coded)  →  fix  →  repeat until exit 0
```

`dev/schema-check.py --ledger out.jsonl` gives finer-grained per-field
diagnostics for the single-record shape (it validates against the JSON
Schema); `check` is authoritative and also covers the cross-record
invariants the schema cannot see.

**Why every command here says `python3 <file>` and not `./<file>`** (defect
`pc-805e`). Every `.py` in this repository carries
`#!/usr/bin/env -S uv run --script`, so the `./` form *requires uv* and
exits 127 — `env: uv: No such file or directory` — on a machine without it.
The CLI and the adapters are stdlib-only, which is the whole promise an
adopter is given ("there is nothing to install and no virtualenv to
build"), so `python3 <file>` runs them everywhere and the shebang route is
an optimisation for people who already have uv. This is the same choice the
shipped pre-commit template makes, and for the same stated reason: it runs
`.py` checkers "through python3 rather than the shebang precisely so a
machine without uv still gets a working gate."

**The one exception, and it is a real one.** `dev/schema-check.py` must be
run as a FILE — `dev/schema-check.py --ledger out.jsonl` — because its
PEP-723 header is what provisions `jsonschema`. It is the one documented
command here that genuinely needs uv, and handing its path to a bare
`python3` bypasses the provisioning and fails on the import instead.

`out.jsonl` is your **design artifact**: it is where the mapping is
developed, validated, and reviewed. It is not how records enter a live
ledger — that is the write path, below.

## The rules that bind an importer

1. **Exit 0 means "well-formed," never "true."** Everywhere. Design your
   claims accordingly: an importer can honestly assert *what the source
   recorded*, never that the recorded work is real.
2. **Derived state is computed, never stored.** `blocked` and `ready` are
   queries in pecia. If your source stores a blocked status, demote it:
   import the *dependency edges* (they carry the truth) and let pecia
   compute. Never write `status: "blocked"` — it is an E001 error.
3. **Absence is declared, not silent.** Every lossy step — a relation
   with no pecia equivalent, an edge target missing from the export,
   comments you drop — gets *declared*, in the record body or your run
   summary. A reader of the imported ledger must be able to see what the
   import could not carry.
4. **Determinism lives in the mapping; idempotence lives in the
   reconciler.** Same export, byte-identical `out.jsonl`: derive mapped
   content from source facts and never let wall-clock time or randomness
   into it. On the live write path, though, pecia mints record ids (with a
   nonce, so two writers cannot collide) and stamps dates at write time —
   so the join between source and ledger is **yours to carry**: put the
   source id in a label (`beads:bd-42`-style) and the source dates in the
   body. Idempotence is then a lookup, not a byte property: before
   writing, find each source id's label in the ledger; skip what is
   unchanged, revise what changed, add what is new. Re-running an import
   must never re-add.
5. **Never write to a ledger file.** Not `.pecia/work.jsonl` (a generated
   snapshot — writing to it as if it were authority is exactly what E015
   exists to catch), and not the log (out-of-band appends are E013 chain
   breaks). Records enter through `add`/`edit`/`close`, where the log
   takes each write under compare-and-swap; the snapshot regenerates
   itself on every write, and `python3 pecia_cli.py snapshot` rebuilds it on
   demand.

## Field-by-field guidance

The schema documents every field's shape inline; what follows is the
judgment layer.

- **id** — in `out.jsonl`, `pc-` + a deterministic function of the source
  id (the beads adapter sanitizes to the id charset); refuse to run if two
  source ids collide after mapping. On the live write path pecia mints the
  id and hands it back in `add --json`'s output — capture it, because edge
  targets need it. Either way, keep the original id as a label
  (`beads:bd-42`-style): the label is both provenance and your
  reconciler's join key.
- **rev** — 1 on every record in `out.jsonl`. On the write path `rev` is
  the compare-and-swap token and the CLI manages it: an entry appending
  rev N+1 lands only if the head for that id is rev N, and `touched` is
  derived by the tool (authored values are rejected — E014).
- **type** — map to the five core types (`defect`, `task`, `decision`,
  `milestone`, `question`); carry unmappable source types as `task` plus a
  label naming the original. Adding `extra_types` to the target repo's
  `.pecia/config.yaml` is also legal — that is a repo-owner decision, not
  an adapter default.
- **status** — map to stored states only. Anything computed (blocked,
  ready, stale) demotes to `open` + edges. Terminal source states need
  care — see the next two items.
- **disposition** (required on any terminal record, E006) — compose it
  from *source facts*: the source's close reason verbatim, the source id,
  the close date. If the source recorded no reason, say exactly that —
  "the source recorded no close reason" is a true disposition; a
  paraphrase you invented is not. Never fabricate a rationale the source
  does not contain.
- **evidence** (required executable-shaped on `defect` + `done`, E007) —
  an importer cannot re-verify closed work, and must not pretend to. The
  honest pattern is **provenance evidence**: a command that verifies what
  the *source* recorded, e.g.
  `python3 adapters/beads-import/beads2pecia.py --verify-closed imports/beads.jsonl bd-42`
  — exit 0 licenses "closed in source," which is the entire claim the
  import makes. This requires the export (and the verifier) to live in the
  repo; commit them. If the evidence command stops being runnable, `audit`
  surfaces it (`unresolvable-evidence`) — that is the system working, not a
  bug to suppress. Since v1.8, `check` reads a command's *shape* off the
  ledger and `.pecia/config.yaml` alone: the first token must contain `/`
  (a repo-relative path like the one above — the usual adapter case) or be
  declared in `extra_evidence_commands`. Emit path-shaped commands and you
  never touch the config; emit a bare `mytool …` and the target repo must
  declare it. This is deliberate — the previous rule asked the host PATH,
  so the same import checked green on the machine that ran it and red in a
  fresh clone.
  Since v1.7 there is a second honest shape: a **foreign reference**,
  `<scheme>:<id>`, naming a fact the source system owns rather than
  restating it. Declare the scheme in the target repo's
  `.pecia/config.yaml` (`resolvers: [beads=adapters/beads-import/has-id.py]`)
  — an undeclared scheme is E011. `check` verifies only that the scheme is
  declared; `audit` runs the verifier and reports what does not resolve.
  Prefer a reference over a copy whenever the source system remains the
  authority: duplicating its records into pecia creates a second authority
  for one fact, which is the drift this format exists to prevent.
- **edges** — mind the direction. pecia's `blocks` edge lives on the
  *blocker* ("target cannot be done while this record is non-terminal");
  many trackers store the dependency on the *dependent*. Invert where
  needed. Scalar edges (`parent`, `duplicate_of`, `discovered_from`,
  `caused_by`, `validates`, `supersedes`) hold one target: on collision,
  pick deterministically and declare the rest in the body. Relations with
  no exact pecia meaning go in the body as declarations — a wrong edge is
  worse than a declared absence (mapping a "conditional" or "related" link
  to `blocks` overclaims and corrupts scheduling).
- **`retires`** (list, v1.13) — for a source that says *"fixing this one
  resolves those"*: a release note's "closes #12, #14", a fix campaign's
  retirement set, an epic that subsumes its predecessors. It lives on the
  record doing the resolving and names what gets resolved, so closing the
  retirer closes them.
  - **Do not reach for it when the source only says a link exists.**
    `retires` asserts *closing Y closes X*. A partial contribution — Y is
    one of several things X needs — is `blocks`, which composes
    conjunctively and is what most trackers' "depends on" actually means.
    Same rule as everywhere else here: a wrong edge is worse than a
    declared absence, and this one corrupts scheduling *and* closure.
  - **Import the claim as the source states it, including the parts that do
    not resolve.** This is the one edge where an over-promise is the
    interesting output. The M3 arm-3 import kept a campaign's headline
    claim of 13 retired findings against 10 addressed items, and the three
    with no records became E003 dangling targets — an error-level finding
    on a two-month-old document. An importer that quietly dropped the three
    unmatched ids would have hidden exactly the defect worth reporting.
  - **E012** fires when a record is still non-terminal and every record
    retiring it has gone terminal. For an import of an already-finished
    campaign this usually means the source's own claim did not hold: either
    close the target from source facts, or drop the claim and declare it.
- **owner** — a stable identity string from the source (assignee, then
  owner), falling back to a named import identity like `import:beads`.
  Machine authors stay vendor-qualified.
- **priority** — map into 0–4 (2 is the default; 4 is excluded from
  `next`). A source's "someday/on ice" state maps well to priority 4 with
  the original priority declared.
- **body** — source description plus your declarations plus one
  provenance line (source id, source created/closed dates, export path,
  source status/type verbatim). The dates matter more under v2 than they
  did under v1: the record's own `created` is stamped at write time, so
  the body line is where the source chronology survives.

## Reading checker findings

| Code | To an importer it means |
|---|---|
| E000 | The checker could not read a timeline at all — wrong directory, or no `.pecia/`. Nothing about your records has been checked. |
| E001 | A record's shape is wrong — field missing, mistyped, unknown edge key, stored derived status. `dev/schema-check.py` pinpoints the field. |
| E002 | A repeated `(id, rev)`, identical or divergent — an **error** either way since v2 re-founded it as log corruption, emitted as its own finding beside the chain diagnostics (E013/E014). In `out.jsonl` it means your adapter emitted one id twice or is non-deterministic; in a live ledger it means the log is corrupt, because the compare-and-swap cannot admit the state. (E010, which used to own the divergent half, was deleted at v2 with the merge algebra — there is no merge for it to arbitrate.) |
| E003 | An edge targets an id that does not exist in the ledger. Drop the edge and declare it, import the missing record, or have the repo owner declare the id `planned` in config. On the write path this is why edges land in a second pass — see the merge sequence below. |
| E004 | Your import closed a dependency cycle. Real graphs from real trackers contain them; break the cycle and declare. |
| E005 | Illegal status transition across revisions — terminal states are final; reopening means a new record with `discovered_from` the old one. |
| E006 | Terminal record without a disposition. Compose one from source facts. |
| E007 | A done defect without executable-shaped evidence. Use provenance evidence (above): a path-shaped command head (containing `/`) needs no declaration, a bare one must be listed in `.pecia/config.yaml` as `extra_evidence_commands: [<name>]`. |
| E008 | Revision gap for an id. Emit rev 1, or contiguous revs. |
| E009 | Two active heads in a decision supersession lineage. |
| E011 | Evidence carries a `<scheme>:<id>` reference whose scheme the target repo does not declare. Add `resolvers: [<scheme>=<verifier>]` to `.pecia/config.yaml`, or emit a command instead. |
| E012 | A record is non-terminal and every record that `retires` it is terminal — the source claimed this would be resolved and it was not. Close it from source facts, or drop the claim and declare the drop. Do not silence it by inventing a close reason. |
| E013 | Chain break: the log was truncated, spliced, or edited out of band. An importer that appended to the log file directly produces exactly this — drive the CLI instead. |
| E014 | Compare-and-swap violation: a revision whose `rev` is not head+1, or an authored `touched` set. Someone (possibly you, concurrently with yourself) advanced the record since you read it — re-read the head and re-apply your change. |
| E015 | Snapshot forked: something wrote to `.pecia/work.jsonl` as if it were authority. That is the act the pre-v2 version of this file instructed, and it is an error now. Regenerate with `python3 pecia_cli.py snapshot`. |
| E016 | Your import closed a record while an open record still holds a `blocks` edge to it — the source's close claim and its dependency claim disagree. Import the blocker's closure too, or drop the stale edge from source facts and declare the drop. |
| E017 | An edge names its own record. No edge relation is reflexive; a source self-reference (an issue marked as its own duplicate) drops, with the drop declared in the body. |
| E018 | Unprojectable value, from a PROJECTION command — never from `check`. A date-shaped `created`/`target` that is not a real calendar day (`9999-99-99`) passes E001 by design (dates are shape-checked, v1.12) and `gantt` refuses to chart it at exit 1 with this finding. If your import fabricates placeholder dates, use real ones. |
| E019 | The log ends before its high-water mark (`log.mark`, beside the log): entries this store wrote are missing from the log's end, or the log was rewritten below the mark. An importer that truncated or rewrote the log file directly produces exactly this — drive the CLI instead. `pecia sync` restores entries that were published; removing the mark is the deliberate act of accepting the loss. |

(E013/E014/E015/E019 are properties of a live timeline; `check --ledger
out.jsonl` reads bare records, where they are meaningless by
construction. E018 is a projection refusal — `gantt`'s, not the
checker's.)

## Landing an import in a live ledger

1. **Validate the mapping**: emit to a standalone file and loop on
   `check --ledger out.jsonl` until exit 0.
2. **Write the records through the CLI**, one `add` per record — type,
   title, priority, owner, body, labels (the provenance label always) —
   capturing the minted id from each JSON reply into a source-id → pecia-id
   map. Close what the source closed (`close <id> --disposition …
   --evidence …`, `--status dropped|superseded` where mapped); move
   in-flight work with `edit <id> --status in-progress`.
3. **Edges land in a second pass**, via `edit`, translated through your id
   map — the write gate refuses a write that introduces new errors, and an
   edge to a record you have not added yet is a dangling E003. Add
   everything, then wire everything.
4. **Check and audit the result**: `python3 pecia_cli.py check` on the live
   timeline, then `python3 pecia_cli.py audit` — imported records with no edges
   draw `untriaged` findings until triaged or declared `no_edges`; that
   pressure is intended.

## Reconciling a changed source

This is the native path, not a different program. `rev` is a
compare-and-swap token, and repeated reconciliation of a changing source
is what that machinery is for.

- Re-run your mapping over the new export.
- Join against the ledger on your provenance labels (the snapshot,
  `.pecia/work.jsonl`, is the convenient read surface — read it freely;
  rule 5 is about writes).
- **Unchanged** source items: write nothing. **Changed**: `edit`/`close`
  the mapped record with the changed fields — if the CAS refuses because
  the record moved underneath you, re-read and re-apply. **New**: `add`,
  as above. **Gone from the source**: say so — close with a disposition
  stating the source dropped it, or declare it in the body; never delete.
- Field-level conflicts between your import and local edits are yours to
  resolve by policy, and the policy belongs in your adapter's
  documentation. The safe default: local wins, and the source's newer
  value is declared in the body.

## What not to do

- Do not fabricate evidence, dispositions, or dates the source does not
  contain. Required fields measurably induce fabrication (that is why
  pecia audits completeness instead of blocking on it) — an importer under
  schema pressure is exactly where fabrication happens. When the source is
  silent, declare the silence.
- Do not map lossy relations silently, in either direction.
- Do not write `blocked`/`ready` as statuses, ever (E001 enforces this).
- Do not treat exit 0 as success at anything beyond well-formedness.
- Do not write to `.pecia/work.jsonl` or to the log file, ever. The
  snapshot is a projection (E015 catches a fork); the log is
  CAS-guarded custody (E013 catches an out-of-band append). The write
  path is `add`/`edit`/`close`, and it is the whole write path.
- Do not re-add on re-import. Idempotence is your reconciler's lookup on
  the provenance label, not a property the storage grants you.
