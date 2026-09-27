module peciaV2

/*
 * pecia v2 ledger algebra — bounded model-check (Alloy 6).
 *
 * Models spec/format-v2.md: ONE append-only hash-chained log per project,
 * compare-and-swap on (chain tip, per-id rev), and a tool-derived `touched`
 * field-set giving field-granularity conflict detection.
 *
 * Tier: machine-checked(scope), never "proved". Exhaustive up to the scopes
 * on each check command and silent beyond them.
 *
 * THIS IS THE ONLY MODEL. pecia.als ran alongside it from pc-3bbe until
 * pc-0033 — a model drifted from its implementation is one of VP4(a)'s three
 * vacuous greens, and until pc-0033 the Python still implemented v1 — then was
 * deleted in the commit that made its algebra unreachable. That deletion took
 * three theorems with it that had nothing to do with the merge algebra; they
 * were restored at pc-4a56 (see the Queries section).
 *
 * WHAT IS MODELED: the log as an explicit `prev` chain (NOT util/ordering —
 * see below), fork-freedom as a provable consequence of the publish rule,
 * CAS admissibility, the derived `touched` set, re-chaining a rejected
 * suffix, and the E-codes that survive v2 (E003/E004/E005/E006/E008).
 *
 * FORK-FREEDOM IS PROVED, NOT ASSUMED. It would have been far easier to
 * open util/ordering[Entry], which is total by construction — and that
 * would have made "there are no alternative timelines" true by fiat, which
 * checks nothing. The chain is therefore an explicit `prev` relation that
 * CAN fork, and V1 proves the publish rule is what prevents it. The seeded
 * kill SeedNoChainCas removes exactly that rule and must produce a fork.
 *
 * DELIBERATELY OUT OF MODEL: content truth (rule 1), hash collision (`prev`
 * is an abstract pointer, not a preimage-resistant digest — this model says
 * nothing about SHA-256), E007 evidence shape, E009 lineages, git's own
 * ref semantics, and the Python implementation itself.
 *
 * Companions: pecia-v2-traps.als (assertions EXPECTED to fail — real v2
 * hazards), pecia-v2-seeded-kills.als (deliberately weakened publish rules
 * the assertions must catch — this model's null arm).
 */

open util/integer

enum Status { Open, InProgress, Done, Dropped, Superseded }

-- The CONFLICT UNITS the model carries (pc-af81 widened this from four).
-- Abstract but named for the code's own vocabulary: FRetires is v1.13's
-- scheduling edge (E012's subject), FContext the v2.5 optional field whose
-- CLEARED state is absence (pc-76b5's escaped case). What is deliberately
-- NOT here, stated so silence is not read as coverage: the content scalars
-- (title, priority, evidence, owner, labels, body, target), the custody
-- scalar edges (duplicate_of, discovered_from, caused_by, validates,
-- supersedes — E003/E017 cover them in code; here only blocks/parent/
-- retires enter the graph), the ratification pair, no_edges, and
-- anchor/anchor_dirty (excluded from conflict fields BY DESIGN in the
-- code's TOUCHED_FIELDS — per-revision provenance, not a conflict unit).
-- ALSO NOT HERE (v2.7, pc-a4df): the ORDER and MULTIPLICITY of the list
-- edges. The code stores blocks/retires as JSON arrays and derives
-- `touched` for a pure reordering — [pc-y, pc-z] then [pc-z, pc-y] are two
-- revisions and two conflict units — while this model types both edges
-- `set Id` and diffs by set inequality, mapping every order and repeat
-- count of one member set to one atom. Order-only changes, their
-- conflicts, and multiplicity are therefore structurally unrepresentable
-- here: a declared coverage exclusion of claim 23, not silence. The CLI's
-- own reorder-is-a-revision behaviour is bound by fixture tests, where
-- model-to-code binding lives for every theorem in this file.
-- ALSO NOT HERE (v2.8, pc-c2a7): the schema-permitted EXTENSION FIELDS'
-- conflict and replay semantics. The code treats them as first-class
-- conflict units — diff_fields and touched_conflicts both return
-- x_custom, sync surfaces an x_custom conflict and re-chains an
-- extension removal by presence (v2.6, pc-c7fe) — while this enum is
-- closed over six named fields and has no image of an open field family.
-- A regression of extension-field conflict or replay behaviour cannot
-- turn this model red; the binding is the ExtensionFieldDiff fixture
-- tests, like everything else on this list. It was excluded by silence —
-- the very thing this comment exists to prevent — from v2.6 until this
-- entry.
-- ALSO NOT HERE (v2.9, pc-fc4c): the re-chain's MULTI-ENTRY SUFFIX
-- mechanics and the CONCURRENT-CREATION partition. `rechained` now
-- constrains out.rev and V12 binds it to the revCas over a log, so a
-- re-chain revision regression IS visible — but the construction still
-- relates one entry at a time (the code's suffix loop is bound by
-- SyncComposition, whose fixtures since pc-8b81 include a two-revision
-- local-only suffix), and a divergent rev-1 creation sharing a
-- published id is never re-chained in code (identical = no-op skip,
-- a branded creation re-chaining as custody per pc-dacc; divergent =
-- surfaced conflict regardless of the brand, pc-e499/pc-23e6) — a
-- partition this model does not state and SyncDivergentCreation binds.
-- Excluded by silence from v2.5 until this entry.
-- ALSO NOT HERE (v2.16, pc-1206): the SERIALIZATION ORDER of the derived
-- `touched` array itself. The code derives it sorted and v2.9 makes a
-- correct field-set in any other order an E014 finding that NAMES the
-- order as what it refuses — while this model types the derived array as
-- a `set Field` over a closed enum, so the canonical, reordered and
-- repeated serializations of one field set are ONE value here and an E014
-- canonical-order regression cannot turn any theorem red. This is a
-- different object from the pc-a4df entry above, which is about the order
-- of elements INSIDE edges.blocks and edges.retires; this one is the
-- derived array's own serialization, and no exclusion named it. Binding is
-- the E014 order fixtures (TimelineGates and
-- DiagnosticContractSurvivesTheCap in tests/test_pecia.py), like
-- everything else on this list. Excluded by SILENCE — the very thing this
-- comment exists to prevent — from v2.9 until this entry, which is the
-- THIRD time that has happened and is why the pattern is recorded here
-- rather than only the fact.
-- claims.yaml's formal-model notes carry this same list; the two must
-- move together.
enum Field { FStatus, FBlocks, FParent, FDisposed, FRetires, FContext }

sig Id {}
sig Disposition {}
sig Force {}
sig Ctx {}

-- The declared planned frontier (v2.7, pc-2caf): .pecia/config.yaml's
-- `planned:` ids are legal edge targets the write gate and E003 accept
-- before any record exists — and the model had no image of them, so a
-- supported, write-gate-clean CLI state sat outside every preservation
-- theorem. One global atom, matching the code: config is repo-scoped and
-- shared by every log the model relates. A planned id never has a head, so
-- it cannot block, complete a cycle, or hold an E012/E016 obligation —
-- which is exactly the code's reading (compute_blockers and the head-scope
-- checks see only existing heads).
one sig Config { planned: set Id }

sig Line {
  lid: one Id,
  rev: one Int,
  status: one Status,
  blocks: set Id,
  parent: lone Id,
  disposed: lone Disposition,
  retires: set Id,
  context: lone Ctx,
  forced: lone Force
} { rev >= 1 }

-- One line of the log. `prev` abstracts the hash chain: entry n's prev
-- pointer names entry n-1. Two entries sharing a prev IS a fork.
--
-- STATED COVERAGE EXCLUSION (v2.17, pc-26a1, round-12 lane C-F2): THE
-- DECLARED SEQUENCE. E013 has two halves — the prev-hash chain, which
-- `prev` above abstracts and every chain theorem here reasons over, and a
-- `seq` field the checker requires to equal the entry's PHYSICAL LINE
-- NUMBER in the stored file (`entry["seq"] != n`, n the line index). This
-- signature has no `seq` and no notion of a file, a line, or a position:
-- `prev` is the CHAIN, not the LAYOUT. So a regression accepting a bad
-- seq, or accepting disagreement between physical line order and declared
-- sequence, turns no theorem in this file red.
--
-- The exclusion is taken rather than the modelling for the reason V11's
-- comment gives for the null-versus-absent case: the property sits BELOW
-- this abstraction, and reaching it would mean modelling the
-- serialization this model deliberately sits above. It is bound to code
-- by fixture tests, as every exclusion here is —
-- tests/test_pecia.py::test_e013_kill_seq_gap rewrites one entry's seq
-- and requires E013 — and it is declared in claims.yaml's formal-model
-- entry beside this comment, which move together.
sig Entry {
  prev: lone Entry,
  rec: one Line,
  touched: set Field
}

sig Log { es: set Entry }

-- ── The chain ────────────────────────────────────────────────────────────

pred acyclic[L: Log]      { no e: L.es | e in e.^prev }
pred closedPrev[L: Log]   { all e: L.es | e.prev in L.es }
pred oneRoot[L: Log]      { some L.es implies one e: L.es | no e.prev }

-- FORK-FREEDOM: no two entries share a predecessor. This is rule 5 made
-- structural — not "do not branch", but "no branch exists to take".
pred forkFree[L: Log] { all disj a, b: L.es | no (a.prev & b.prev) }

pred chained[L: Log] { acyclic[L] and closedPrev[L] and oneRoot[L] and forkFree[L] }

-- The tip: the entry nothing points back to.
fun tip[L: Log]: set Entry { { e: L.es | no f: L.es | f.prev = e } }

fun linesOf[L: Log]: set Line { L.es.rec }

-- ── Records and resolution ───────────────────────────────────────────────
--
-- NOTE what is absent: v1's `resolvable` predicate and e010clean. Under a
-- total order with CAS, two entries carrying the same (id, rev) are not a
-- merge needing a human — they are a corrupt log, and E012/E002 catch that
-- by chain integrity instead. The v1 predicates are not weakened here, they
-- are unreachable, which is why they are gone rather than green.
--
-- DEFINED OVER BARE LINE SETS, wrapped for Logs (v2.7, pc-5fba). The traps
-- companion poses its two-writer scenario over line sets, where the
-- Log-based forms do not apply — and it used to keep a PRIVATE, WEAKER copy
-- of this vocabulary (E003/E004 without retires; E005, E008, E012, E016 and
-- E017 absent entirely) that dev/alloy-gate.sh's redeclaration check could
-- not reject, so trap 3 was judged against a definition later invariant
-- changes silently outgrew. The `…L` forms below are the ONE shipped
-- vocabulary; the Log forms are one-line wrappers; and the gate now refuses
-- a companion that re-declares any of them (the pc-0c76 rule, widened).

fun headsL[L: set Line]: set Line {
  { l: L | no m: L | m.lid = l.lid and m.rev > l.rev }
}

fun headL[L: set Line, i: Id]: set Line { { l: headsL[L] | l.lid = i } }

fun idsL[L: set Line]: set Id { L.lid }

fun idsOf[L: Log]: set Id { idsL[linesOf[L]] }

fun headOf[L: Log, i: Id]: set Line { headL[linesOf[L], i] }

fun headsOf[L: Log]: set Line { headsL[linesOf[L]] }

pred terminal[s: Status] { s in Done + Dropped + Superseded }

-- ── Surviving E-codes ────────────────────────────────────────────────────

pred e003cleanL[L: set Line] {
  all h: headsL[L] | (h.blocks + h.parent + h.retires) in (idsL[L] + Config.planned)
}

-- The scheduling graph: blocks, parent, and (v1.13, pc-af81 here) retires —
-- the retirer blocks its targets, so mutual retirement is an E004 cycle.
fun depGraphL[L: set Line]: Id -> Id {
  { a: Id, b: Id | some h: headsL[L] | h.lid = a and b in (h.blocks + h.parent + h.retires) }
}

pred e004cleanL[L: set Line] { no (iden & ^(depGraphL[L])) }

pred e005cleanL[L: set Line] {
  all a, b: L |
    (a.lid = b.lid and b.rev = add[a.rev, 1] and terminal[a.status])
      implies b.status = a.status
}

pred e006cleanL[L: set Line] {
  all h: headsL[L] | terminal[h.status] implies some h.disposed
}

pred e008cleanL[L: set Line] {
  all i: idsL[L] | let revs = { r: Int | some l: L | l.lid = i and l.rev = r } |
    all r: Int | (r >= min[revs] and r <= max[revs]) implies r in revs
}

-- E012 (v1.13): a retirement promise that expired unkept — anchored on the
-- TARGET, exactly as the code anchors it: silent while any claimant is
-- live, firing when the last one goes terminal over an open target.
pred e012cleanL[L: set Line] {
  all i: idsL[L] |
    let claimants = { h: headsL[L] | i in h.retires } |
      (some claimants and (all c: claimants | terminal[c.status]))
        implies (all h: headL[L, i] | terminal[h.status])
}

-- E016 (v2.4): a record may not sit terminal while a non-terminal head
-- holds a `blocks` edge to it — anchored on the BLOCKED record.
pred e016cleanL[L: set Line] {
  all h: headsL[L] | terminal[h.status] implies
    no g: headsL[L] | not terminal[g.status] and h.lid in g.blocks
}

-- E017 (v2.5): no edge relation is reflexive, over every edge the model
-- carries. (The code's E017 also covers the custody scalars this model
-- deliberately omits — see the Field comment.)
pred e017cleanL[L: set Line] {
  all h: headsL[L] | h.lid not in (h.blocks + h.parent + h.retires)
}

pred cleanL[L: set Line] {
  e003cleanL[L] and e004cleanL[L] and e005cleanL[L] and e006cleanL[L]
  and e008cleanL[L] and e012cleanL[L] and e016cleanL[L] and e017cleanL[L]
}

pred e003clean[L: Log] { e003cleanL[linesOf[L]] }
fun depGraph[L: Log]: Id -> Id { depGraphL[linesOf[L]] }
pred e004clean[L: Log] { e004cleanL[linesOf[L]] }
pred e005clean[L: Log] { e005cleanL[linesOf[L]] }
pred e006clean[L: Log] { e006cleanL[linesOf[L]] }
pred e008clean[L: Log] { e008cleanL[linesOf[L]] }
pred e012clean[L: Log] { e012cleanL[linesOf[L]] }
pred e016clean[L: Log] { e016cleanL[linesOf[L]] }
pred e017clean[L: Log] { e017cleanL[linesOf[L]] }

pred clean[L: Log] { cleanL[linesOf[L]] }

-- ── Queries ──────────────────────────────────────────────────────────────
--
-- RESTORED at pc-4a56. These and the three theorems over them lived in
-- pecia.als and were deleted with that whole file at pc-0033, on the argument
-- that v2 removed an unreachable merge algebra. That argument covered eight
-- assertions and not these: readiness, deadlock-freedom and blame containment
-- are properties of the RECORD GRAPH, which the storage change did not touch.
-- Coverage narrowed silently under a change argued as narrowing nothing —
-- because the unit of retirement was the FILE rather than the assertion, the
-- same mistake the gate itself was making (pc-855f).

fun openIds[L: Log]: set Id {
  { i: Id | some h: headsOf[L] | h.lid = i and h.status = Open }
}

fun blockersOf[L: Log, i: Id]: set Id {
  { j: Id | some h: headsOf[L] |
      h.lid = j and not terminal[h.status] and i in (h.blocks + h.parent + h.retires) }
}

pred isReady[L: Log, i: Id] {
  i in openIds[L]
  no blockersOf[L, i]
}

-- ── The derived field-set ────────────────────────────────────────────────

fun diff[a, b: Line]: set Field {
    (a.status   != b.status   implies FStatus   else none)
  + (a.blocks   != b.blocks   implies FBlocks   else none)
  + (a.parent   != b.parent   implies FParent   else none)
  + (a.disposed != b.disposed implies FDisposed else none)
  + (a.retires  != b.retires  implies FRetires  else none)
  + (a.context  != b.context  implies FContext  else none)
}

-- `touched` is CORRECT iff it equals the tool's recomputation against the
-- head the writer read. spec/format-v2.md 4.1: derived, never authored.
-- E013's second half is exactly this predicate.
pred touchedDerived[L: Log, e: Entry] {
  (no headOf[L, e.rec.lid]) implies no e.touched
  else e.touched = diff[headOf[L, e.rec.lid], e.rec]
}

-- ── Publish: the compare-and-swap ────────────────────────────────────────

-- The per-id half of the CAS: rev N+1 only if the head is rev N.
pred revCas[L: Log, e: Entry] {
  (no headOf[L, e.rec.lid]) implies e.rec.rev = 1
  else e.rec.rev = add[headOf[L, e.rec.lid].rev, 1]
}

-- The chain half: e extends the current tip. A non-fast-forward push is
-- refused, which is what makes the ref a CAS register.
pred chainCas[L: Log, e: Entry] { e.prev = tip[L] }

-- Write-time discipline carried over from v1's reviseStrict, unchanged in
-- substance: legal transition, disposition on terminal, valid edge targets,
-- no self-edge, and the acyclicity guard v1's own first counterexample
-- forced. Storage changed; the write gate did not.
pred writeOk[L: Log, e: Entry] {
  let n = e.rec {
    no n.forced
    terminal[n.status] implies some n.disposed
    -- Config.planned joined at v2.7 (pc-2caf): the write gate accepts a
    -- declared planned target, and the model must cover that path.
    (n.blocks + n.parent + n.retires) in (idsOf[L] + n.lid + Config.planned)
    n.lid not in (n.blocks + n.parent + n.retires)
    all h: headOf[L, n.lid] | terminal[h.status] implies n.status = h.status
    -- E016's write-time half (v2.4, pc-af81): the gate is the checker, so a
    -- write may neither close a record an open head still blocks, nor point
    -- a live blocks edge at an already-terminal head.
    terminal[n.status] implies
      no g: headsOf[L] | g.lid != n.lid and not terminal[g.status] and n.lid in g.blocks
    not terminal[n.status] implies
      no t: n.blocks | some h: headsOf[L] | h.lid = t and terminal[h.status]
    -- E012's write-time half (v1.13, pc-af81): closing the last live
    -- claimant of an open target breaks the retirement promise unless
    -- another live claimant remains.
    terminal[n.status] implies
      all t: n.retires |
        (all th: headOf[L, t] | terminal[th.status])
        or (some g: headsOf[L] |
              g.lid != n.lid and not terminal[g.status] and t in g.retires)
    -- ... and so does DROPPING a retires edge when every remaining claimant
    -- is already terminal over a live target — the checker's diff-based
    -- gate refuses that write in code, and the model found the omission
    -- (PublishPreservesClean's counterexample) when clean grew e012clean.
    all t: (headOf[L, n.lid].retires - n.retires) |
      (all th: headOf[L, t] | terminal[th.status])
      or (some g: headsOf[L] |
            g.lid != n.lid and not terminal[g.status] and t in g.retires)
      or no { g: headsOf[L] | g.lid != n.lid and t in g.retires }
    -- E012's TARGET half at write time (v2.7, pc-2caf). A planned id may be
    -- the target of a retirement promise whose every claimant has already
    -- gone terminal — clean while the target has no record (E012 skips a
    -- headless target, in code and in e012cleanL alike), and dirty the
    -- moment a NON-TERMINAL record of that id enters. The code's write gate
    -- refuses that entry by construction (the gate is the full checker
    -- diff); this hand-written writeOk needed the case stated, and the
    -- planned-frontier extension is what made it reachable — V4's own
    -- counterexample found it.
    not terminal[n.status] implies
      (let claimants = { g: headsOf[L] | g.lid != n.lid and n.lid in g.retires } |
        some claimants implies some g: claimants | not terminal[g.status])
  }
}

pred publish[L, L2: Log, e: Entry] {
  e not in L.es
  chainCas[L, e]
  revCas[L, e]
  touchedDerived[L, e]
  writeOk[L, e]
  L2.es = L.es + e
  e004clean[L2]
}

-- The forced escape hatch: skips writeOk, never the chain or the CAS, and
-- must brand its line. Force bypasses the write gate, not the timeline.
pred publishForced[L, L2: Log, e: Entry] {
  e not in L.es
  chainCas[L, e]
  revCas[L, e]
  touchedDerived[L, e]
  some e.rec.forced
  L2.es = L.es + e
}

-- ── Re-chaining a refused push ───────────────────────────────────────────
--
-- A push is refused when the remote advanced. The loser re-chains its
-- unpublished entry onto the new tip. It may do so ONLY if no field it
-- touched was touched by what landed in between; otherwise the writer is
-- surfaced to (format-v2.md 4.1, 5.5). `rechainOk` is that test.

pred sameRecord[a, b: Entry] { a.rec.lid = b.rec.lid }

-- Re-chaining, stated as a construction rather than a test. `mine` was
-- authored against `base`; `landed` took the tip first. The re-chained
-- record takes MY value on the fields I touched and the LANDED value
-- everywhere else. This is the operation format-v2.md 5.4 describes, and
-- writing it as a construction is what makes V5 falsifiable — the earlier
-- draft stated re-chaining as a predicate whose hypothesis contained its
-- own conclusion, so it passed by tautology and could never have turned
-- red. VP4: a gate that cannot fail is not a gate.
--
-- COVERAGE BOUND, declared (v2.9, pc-fc4c — this construction used to
-- explain itself without declaring it): this pred relates LINE atoms.
-- Since v2.9 it also constrains `out.rev` (below) and V12 binds the
-- construction to the revCas over an actual log, so a re-chain revision
-- regression is visible to the model. What stays OUTSIDE it, bound by the
-- fixture tests like everything on the Field comment's list: the
-- multi-entry-suffix mechanics (the code re-chains a suffix one entry at
-- a time, each against the new head — V12 covers each step; the loop is
-- SyncComposition's, with a two-revision local-only suffix in its
-- fixtures since pc-8b81), and the concurrent-creation partition (a
-- rev-1 `mine` sharing a landed id: identical content is a no-op skip,
-- a branded creation re-chaining as custody per pc-dacc; divergent
-- content a surfaced conflict regardless of the brand — pc-e499 and
-- pc-23e6's code behavior, bound by SyncDivergentCreation; Alloy's
-- `lone Id` collapse of the empty edge is pc-798a's reason a
-- command-level test holds that).
-- THE BRAND-FREE CORE OF THE RE-CHAIN, FACTORED OUT (v2.17, pc-dad7).
-- This is not a refactor for tidiness. Trap 4 is supposed to pin the brand
-- clause below, and it asserted against a predicate declared inside the
-- trap file that rebuilt this transfer from scratch — so deleting
-- `out.forced = mine.forced` from this module left the trap failing for
-- its own reasons and the gate at 16/16, 0/4, 0/3, exit 0. Measured, with
-- the pinned jar. A companion that WEAKENS one clause must compose the
-- shipped construction and replace only that clause, or it is a private
-- copy with a different name, which is the pc-5fba/pc-219e class one layer
-- in: those closed a copy of the judged VOCABULARY, this is a copy of the
-- judged CONSTRUCTION.
pred rechainedFields[base, landed, mine, out: Line] {
  let t = diff[base, mine] {
    out.lid = landed.lid
    -- The re-chained revision CONTINUES FROM THE LANDED HEAD (v2.9,
    -- pc-fc4c): the code assigns revised["rev"] = heads[rid]["rev"] + 1,
    -- and this construction carried no rev constraint at all — Alloy
    -- stayed green if cmd_sync mis-assigned the revision. V12 is the
    -- theorem this line makes provable-and-falsifiable.
    out.rev = add[landed.rev, 1]
    (FStatus   in t implies out.status   = mine.status   else out.status   = landed.status)
    (FBlocks   in t implies out.blocks   = mine.blocks   else out.blocks   = landed.blocks)
    (FParent   in t implies out.parent   = mine.parent   else out.parent   = landed.parent)
    (FDisposed in t implies out.disposed = mine.disposed else out.disposed = landed.disposed)
    (FRetires  in t implies out.retires  = mine.retires  else out.retires  = landed.retires)
    (FContext  in t implies out.context  = mine.context  else out.context  = landed.context)
  }
}

pred rechained[base, landed, mine, out: Line] {
  rechainedFields[base, landed, mine, out]
  -- The brand is PER-REVISION CUSTODY and travels from the WRITER's own
  -- revision, never from the base being re-chained onto (pc-f7fc,
  -- pc-dacc). Trap 4 (peciaV2_traps.BrandSurvivesRechain) is the
  -- plausible-but-wrong construction that violates this, and V13 below is
  -- the theorem that turns RED when this line is deleted — which no
  -- assertion did until v2.17 (pc-dad7).
  out.forced = mine.forced
}

-- ── Theorems ─────────────────────────────────────────────────────────────

-- V1: publishing under CAS preserves fork-freedom. THE central property:
-- rule 5 is a consequence of the publish rule, not an assumption.
assert PublishPreservesForkFreedom {
  all L, L2: Log, e: Entry |
    chained[L] and (publish[L, L2, e] or publishForced[L, L2, e])
      implies chained[L2]
}
check PublishPreservesForkFreedom for 5 Entry, 5 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V2: a chained log has exactly one tip — there is one present, never two.
assert ChainedLogHasOneTip {
  all L: Log | chained[L] and some L.es implies one tip[L]
}
check ChainedLogHasOneTip for 6 Entry, 6 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V3: THE RETIRED SEED, INVERTED. In v1, last-occurrence-wins was the v0
-- defect and pecia-seeded-kills.als pinned it as a kill that must bite.
-- Under a total order with the rev CAS, the last entry for an id in chain
-- order IS its highest rev, so the two resolutions COINCIDE and the seed
-- can no longer fail. That is why LastWinsMatchesMaxRev is deleted rather
-- than kept green (VP4's destructive half) — and this theorem is the
-- positive statement that replaces it.
assert LastInChainCoincidesWithMaxRev {
  all L: Log | chained[L] and casRespected[L] implies
    all i: idsOf[L] |
      all e: L.es |
        (e.rec.lid = i and no f: L.es | f.rec.lid = i and e in f.^prev)
          implies e.rec = headOf[L, i]
}
check LastInChainCoincidesWithMaxRev for 6 Entry, 6 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- Every entry sits at rev = (rev of the previous entry for its id) + 1.
pred casRespected[L: Log] {
  all e: L.es | let earlier = { f: L.es | f.rec.lid = e.rec.lid and f in e.^prev } |
    (no earlier implies e.rec.rev = 1)
    and (some earlier implies
          e.rec.rev = add[(max[earlier.rec.rev]), 1])
}

-- V4: publishing preserves checker-cleanliness (v1's T1, re-proved over
-- the log rather than over a line set).
assert PublishPreservesClean {
  all L, L2: Log, e: Entry |
    chained[L] and clean[L] and publish[L, L2, e] implies clean[L2]
}
check PublishPreservesClean for 5 Entry, 5 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V5: THE PROPERTY FIELD-GRANULARITY BUYS, and the claim pc-316c rests on.
-- When my edit and the edit that beat me to the tip touched DISJOINT
-- fields, re-chaining mine onto theirs preserves BOTH: the re-chained
-- record differs from what landed on exactly the fields I touched, and on
-- nothing else — so their change survives and so does mine, with no human
-- involved. TrapBlindRechain is this same assertion over a re-chain that
-- takes `mine` wholesale, and it must fail.
assert RechainPreservesBothWhenDisjoint {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and no (diff[base, mine] & diff[base, landed])
    and rechained[base, landed, mine, out]
      implies diff[landed, out] = diff[base, mine]
}
check RechainPreservesBothWhenDisjoint for 6 Line, 6 Entry, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V11 (pc-af81, from round 1's escaped defect pc-76b5): CLEARING an
-- optional field commutes with a concurrent disjoint edit. `mine` clears
-- `context` (base carried one, mine carries none); `landed` touched
-- disjoint fields; the re-chained record must carry the ABSENCE — the
-- cleared state — and exactly the winner's edits otherwise. This is the
-- algebra of the case the implementation got wrong; the representation
-- half that actually escaped (JSON null resurrected where absence was
-- meant) sits BELOW this abstraction — Alloy's `lone` cannot spell the
-- difference between null and absent — and is pinned by the fixture tests
-- (SyncComposition's context-clearing kill), which is where model-to-code
-- binding lives for every theorem here.
assert RechainPreservesClearing {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and some base.context and no mine.context
    and no (diff[base, mine] & diff[base, landed])
    and rechained[base, landed, mine, out]
      implies (no out.context and diff[landed, out] = diff[base, mine])
}
check RechainPreservesClearing for 6 Line, 3 Id, 2 Disposition, 2 Ctx, 2 Log, 2 Entry, 4 Int, 2 Force

-- V12 (v2.9, pc-fc4c): THE RE-CHAINED REVISION PASSES THE CAS. The
-- construction above says out.rev continues from the landed head; the
-- revCas says an admissible entry's rev is head+1. This theorem pins the
-- two to EACH OTHER over an actual log — if either side's arithmetic
-- drifts (rechained taking mine.rev+1, or landed.rev itself, or the
-- revCas moving), the model turns red instead of staying green over a
-- re-chain revision regression. The premise names `landed` as the log's
-- head for the id, which is what the code's heads[] lookup selects.
assert RechainedEntryPassesTheRevCas {
  all L: Log, e: Entry, base, landed, mine, out: Line |
    chained[L] and headOf[L, base.lid] = landed
    and rechained[base, landed, mine, out]
    and e.rec = out
      implies revCas[L, e]
}
check RechainedEntryPassesTheRevCas for 6 Entry, 6 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V13 (v2.17, pc-dad7, round-12 lane C-F1): THE BRAND SURVIVES THE
-- RE-CHAIN, as a CORE assertion rather than only as a trap.
--
-- The brand rule has been in `rechained` since pc-af81 and NO theorem
-- observed it: with `out.forced = mine.forced` deleted, the gate stayed
-- 16/16, 0/4, 0/3 at exit 0 — measured by executing the pinned jar, not
-- inferred. The trap that is supposed to pin it asserted against a
-- predicate the trap file declared itself, so it failed for its own
-- reasons whether or not this module still carried the right rule; that
-- half is fixed by `rechainedFields` above, and this is the half that
-- makes the deletion RED. Why the trap could not do this job at all: a
-- trap states that a WRONG construction violates a property, so it keeps
-- biting no matter what the right construction says. Only a theorem over
-- the shipped construction can notice the shipped construction changing.
--
-- Audit enumerates escape-hatch uses from the ledger alone (pc-f7fc,
-- pc-dacc), so a brand dropped in re-chain erases the bypass event —
-- which is why this is custody rather than bookkeeping.
assert BrandCustodyThroughRechain {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and rechained[base, landed, mine, out]
      implies (some out.forced iff some mine.forced)
}
check BrandCustodyThroughRechain for 6 Line, 3 Id, 2 Disposition, 2 Log, 2 Entry, 4 Int, 2 Force, 2 Ctx

-- V13's NON-VACUITY, and it exhibits the exact scenario trap 4 poses: a
-- branded `mine` re-chained onto an UNBRANDED `landed`. Without this the
-- assertion above could hold over models in which nothing is ever branded,
-- which is the vacuous green VP4 names and the reason every theorem here
-- that constrains an optional feature carries a run beside it.
run SomeBrandedRechain {
  some base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and rechained[base, landed, mine, out]
    and some mine.forced and no landed.forced
} for 6 Line, 3 Id, 2 Disposition, 2 Log, 2 Entry, 4 Int, 2 Force, 2 Ctx

-- NOTE ON A DELETED ASSERTION. An earlier draft carried
-- DerivedTouchedNeverUnderReports, asserting that a derived `touched`
-- names every differing field. It is definitional — `touchedDerived` says
-- `touched = diff`, so the subset direction is an equality restated — and
-- a definitional assertion shipped as a theorem is the vacuous green VP4
-- names. The real content lives where it can fail: V5 above, and the
-- seeded kill SeedPartialDiff, which breaks `diff` itself and must bite.

-- V7: blame containment survives the storage change — from a clean chained
-- log, the only single step that can produce dirt is a forced one. And
-- because publish puts the branded entry IN the log it builds, the evidence
-- of that step is in the log as a corollary, not a second theorem.
--
-- CORRECTED at pc-9a63 (round-1 lane C-F7): a V10, ForcedStepsCarryTheMark,
-- stood beside this with identical quantifiers, premises and conclusion —
-- its comment claimed the evidence-in-the-log obligation, but after
-- expansion it was this check run twice, so 12/12 overstated the proof
-- breadth by one. It is DROPPED rather than reworded: the distinct
-- obligation its comment wanted — the brand SURVIVING a re-chain, pc-f7fc's
-- live violation — belongs over the re-chain construction, where it can
-- actually fail (the pc-af81 extension proves it there).
assert BlameContainment {
  all L, L2: Log, e: Entry |
    chained[L] and clean[L]
    and (publish[L, L2, e] or publishForced[L, L2, e])
    and not clean[L2]
      implies some e.rec.forced
}
check BlameContainment for 5 Entry, 5 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V8: no deadlock — in a clean log where every non-terminal head is open,
-- some open item is ready. Acyclicity guarantees a source; without this a
-- ledger could be clean, non-empty and unworkable.
assert NoDeadlockWhenAllOpen {
  all L: Log |
    chained[L] and clean[L]
    and (all h: headsOf[L] | not terminal[h.status] implies h.status = Open)
    and some openIds[L]
      implies some i: Id | isReady[L, i]
}
check NoDeadlockWhenAllOpen for 6 Entry, 6 Line, 5 Id, 3 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- V9: ready and blocked partition the open heads — the queries agree with the
-- graph they are computed from (rule 2: derived state is computed, never
-- stored, so the computation is the only thing that can be wrong).
assert ReadyBlockedPartition {
  all L: Log | chained[L] and clean[L] implies
    all i: openIds[L] | isReady[L, i] iff no blockersOf[L, i]
}
check ReadyBlockedPartition for 6 Entry, 6 Line, 4 Id, 3 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- ── Non-vacuity (AlloyCheck fails a run that finds no instance) ──────────

run SomeChainedLogWithHistory {
  some L: Log | {
    chained[L] and clean[L] and casRespected[L]
    #L.es >= 3
    some i: idsOf[L] | #{ l: linesOf[L] | l.lid = i } >= 2
  }
} for 6 Entry, 6 Line, 3 Id, 2 Disposition, 1 Log, 4 Int, 2 Force, 2 Ctx

run SomeDisjointTouchedPair {
  some L: Log, a, b: Entry | {
    chained[L] and a in L.es and b in L.es and a != b
    sameRecord[a, b]
    some a.touched and some b.touched
    no (a.touched & b.touched)
  }
} for 6 Entry, 6 Line, 2 Id, 2 Disposition, 1 Log, 4 Int, 2 Force, 2 Ctx

run SomePublishStep {
  some L, L2: Log, e: Entry |
    chained[L] and clean[L] and publish[L, L2, e] and #L.es >= 2
} for 6 Entry, 6 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- The planned-frontier coverage must not be vacuous (pc-2caf): an instance
-- where a CLEAN log's head names a target that exists only as a declared
-- planned id — the supported CLI state that previously had no image here.
run SomePlannedFrontier {
  some L: Log, h: headsL[linesOf[L]] | {
    chained[L] and clean[L]
    some (h.blocks & (Config.planned - idsOf[L]))
  }
} for 5 Entry, 5 Line, 3 Id, 2 Disposition, 1 Log, 4 Int, 2 Force, 2 Ctx

-- V12 must not hold by unsatisfiable premise (v2.9, pc-fc4c): an instance
-- where a re-chained entry really is appended against a multi-revision
-- head — landed.rev >= 2, so the arithmetic is exercised past the
-- creation case.
--
-- THE WITNESS IS THE CODE'S APPEND (v2.13, pc-8a93, round-8 lane C-F3).
-- `rechained` constrains out.lid and never mine.lid, and this run placed
-- no constraint on `e` beyond `e.rec = out` — so the witness was satisfied
-- by an instance with `mine` on a DIFFERENT record and `e` outside the
-- log's own entry set, which is not the same-record append cmd_sync
-- performs and is what claim 23's coverage sentence said it pinned. No
-- theorem was weakened and no CLI behaviour was wrong; the prose read
-- stronger than the model. The three constraints below are the code's
-- shape — the writer's revision is of the same record, the entry is new,
-- and it is appended at the tip — and the run is still satisfiable with
-- them, which is what makes this a gap rather than a modelling limit.
run SomeRechainedCasStep {
  some L: Log, e: Entry, base, landed, mine, out: Line | {
    chained[L]
    headOf[L, base.lid] = landed
    landed.rev >= 2
    mine.lid = base.lid
    e not in L.es
    chainCas[L, e]
    rechained[base, landed, mine, out]
    e.rec = out
    revCas[L, e]
  }
} for 6 Entry, 6 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- The clearing theorem must not hold by unsatisfiable premise: an instance
-- where a clearing genuinely meets a disjoint concurrent edit (pc-af81).
run SomeClearingRechain {
  some base, landed, mine, out: Line | {
    landed.lid = base.lid and mine.lid = base.lid
    some base.context and no mine.context
    some diff[base, landed]
    no (diff[base, mine] & diff[base, landed])
    rechained[base, landed, mine, out]
  }
} for 6 Line, 3 Id, 2 Disposition, 2 Ctx, 1 Log, 2 Entry, 4 Int, 2 Force
