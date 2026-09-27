module peciaV2_traps

/*
 * REAL v2 TRAPS — every assertion in this file is EXPECTED TO FAIL.
 *
 * Each counterexample is a genuine, reachable hazard in the v2 design as
 * specified, kept as a permanent formal record. dev/alloy-gate.sh FAILS if
 * any of these stops producing a counterexample without a corresponding fix
 * landing — a healed trap with no fix is model drift, not good news.
 *
 * These replace the two v1 traps that v2 makes unreachable
 * (BranchedCompactMergeIsClean, CrossBranchEdgeAdditionsMergeClean).
 * Retiring traps without replacing them would THIN THE NULL ARM, which
 * VP4(a) forbids: a formal gate ships with three expectations — theorems
 * pass, pinned counterexamples keep failing, seeded kills keep failing.
 *
 * Trap 3 is the direct descendant of v1's trap 3. The cross-branch cycle
 * does not disappear under a single timeline; it MOVES, from merge time to
 * re-chain time, and it is caught only if the re-chain path re-runs the
 * checker rather than only the field-intersection test. That is the
 * actionable instruction this trap exists to give pc-0033.
 *
 * THIS FILE OPENS THE THEOREM MODEL (pc-0c76; was a full copy). The
 * vocabulary the traps are judged against — Field, Line, Status, diff,
 * terminal, and (v2.7, pc-5fba) the line-set clean/head/graph machinery
 * headsL/idsL/cleanL — is the shipped one, imported, so a semantic
 * regression of the theorem model's definitions moves the traps too
 * instead of leaving them biting in a private copy. What stays local is
 * exactly what each trap WEAKENS or needs beyond the theorem model: the
 * blind re-chain, the authored-touched re-chain, the State sig trap 3
 * poses its two-writer scenario in. dev/alloy-gate.sh refuses this file
 * if it re-declares Field, diff, or any of the clean/head/graph
 * vocabulary — this file kept a private cleanLines (E003/E004 without
 * retires; E005/E008/E012/E016/E017 absent) that the pc-0c76 check could
 * not reject, and trap 3 was judged against a definition later invariant
 * changes silently outgrew.
 */

open peciaV2

sig State { ls: set Line }

-- Apply only the fields in `t` from `mine` onto `landed`.
pred rechainedBy[t: set Field, landed, mine, out: Line] {
  out.lid = landed.lid
  (FStatus   in t implies out.status   = mine.status   else out.status   = landed.status)
  (FBlocks   in t implies out.blocks   = mine.blocks   else out.blocks   = landed.blocks)
  (FParent   in t implies out.parent   = mine.parent   else out.parent   = landed.parent)
  (FDisposed in t implies out.disposed = mine.disposed else out.disposed = landed.disposed)
  (FRetires  in t implies out.retires  = mine.retires  else out.retires  = landed.retires)
  (FContext  in t implies out.context  = mine.context  else out.context  = landed.context)
}

-- ── TRAP 1: blind re-chaining loses the winner's change ──────────────────
--
-- The obvious implementation of "re-chain and retry" is to take the refused
-- entry as authored and re-point it at the new tip. That silently reverts
-- every field the winner changed, because the refused record still carries
-- the pre-winner values for fields its author never touched.
-- Disposition: format-v2.md 4.1 and 5.4 require the re-chain to apply only
-- the `touched` fields; peciaV2.RechainPreservesBothWhenDisjoint is the
-- positive statement. Expected counterexample: landed moves FStatus, mine
-- moves FBlocks, and out reverts FStatus.

pred rechainedBlind[landed, mine, out: Line] {
  out.lid = landed.lid
  out.status = mine.status
  out.blocks = mine.blocks
  out.parent = mine.parent
  out.disposed = mine.disposed
  out.retires = mine.retires
  out.context = mine.context
}

assert BlindRechainPreservesBoth {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and no (diff[base, mine] & diff[base, landed])
    and rechainedBlind[landed, mine, out]
      implies diff[landed, out] = diff[base, mine]
}
check BlindRechainPreservesBoth for 6 Line, 3 Id, 2 Disposition, 2 State, 4 Int, 2 Force, 0 Entry, 0 Log, 2 Ctx

-- ── TRAP 2: an AUTHORED `touched` loses the author's own update ──────────
--
-- If `touched` is accepted from the writer rather than derived by the tool,
-- it may under-report. The re-chain then applies only the declared fields
-- and silently drops the rest — the writer's own change is lost, and the
-- CAS reports success. This is GP8/BP4's scope warning stated formally:
-- over a GENERATIVE field the writer must author, a required-field guard
-- buys form, not truth. Disposition: format-v2.md 4.1 makes `touched`
-- tool-derived and rejected on input; E013 enforces it structurally.

assert AuthoredTouchedNeverLosesAnUpdate {
  all base, landed, mine, out: Line, declared: set Field |
    landed.lid = base.lid and mine.lid = base.lid
    and declared in diff[base, mine]
    and no (declared & diff[base, landed])
    and rechainedBy[declared, landed, mine, out]
      implies diff[landed, out] = diff[base, mine]
}
check AuthoredTouchedNeverLosesAnUpdate for 6 Line, 3 Id, 2 Disposition, 2 State, 4 Int, 2 Force, 0 Entry, 0 Log, 2 Ctx

-- ── TRAP 3: v1's trap 3, moved rather than removed ───────────────────────
--
-- Two writers revise DIFFERENT records, each adding one blocking edge. No
-- field-intersection test can see a problem: the records are disjoint, so
-- the `touched` sets never even meet. Each revision is locally clean. The
-- re-chained result contains a cycle.
--
-- A single timeline does NOT dissolve this hazard — it relocates it from
-- merge time to re-chain time, where for the first time ONE writer is
-- positioned to see it. That is a real improvement and it is not automatic:
-- it holds only if the re-chain path re-runs the graph invariants. A
-- re-chain validated by field-disjointness alone reproduces v1's trap 3
-- exactly. Disposition: peciaV2.publish requires e004clean on the resulting
-- log, and pc-0033 must route the re-chain through the same predicate, not
-- through the conflict test alone.

pred reviseOn[L, L2: set Line, n: Line] {
  n not in L
  some h: headsL[L] | h.lid = n.lid and n.rev = add[h.rev, 1]
  (n.blocks + n.parent + n.retires) in idsL[L]
  n.lid not in (n.blocks + n.parent + n.retires)
  L2 = L + n
  cleanL[L2]
}

assert RechainByFieldDisjointnessAlonePreservesClean {
  all s, sa, sb: State, na, nb: Line |
    cleanL[s.ls]
    and reviseOn[s.ls, sa.ls, na]
    and reviseOn[s.ls, sb.ls, nb]
    and na.lid != nb.lid
      implies cleanL[sa.ls + nb]
}
check RechainByFieldDisjointnessAlonePreservesClean for 7 Line, 4 Id, 3 Disposition, 3 State, 4 Int, 2 Force, 0 Entry, 0 Log, 2 Ctx

-- ── TRAP 4: the re-chain that loses the brand (pc-af81, from pc-f7fc) ────
--
-- The plausible construction: apply the touched fields correctly — exactly
-- what peciaV2.rechained does — but let the force brand come from what
-- LANDED rather than from the writer's own refused revision. Audit
-- enumerates escape-hatch uses from the ledger alone, so a brand dropped
-- in re-chain erases the bypass event (pc-f7fc was this, live, in sync;
-- pc-dacc was its no-op-skip variant). The correct rule is in
-- peciaV2.rechained: `out.forced = mine.forced`, per-revision custody.
-- Expected counterexample: mine branded, landed not, out unbranded.

-- COMPOSED FROM THE SHIPPED CONSTRUCTION, NOT REBUILT BESIDE IT (v2.17,
-- pc-dad7, round-12 lane C-F1). This read `rechainedBy[diff[base, mine],
-- …]` — the trap file's OWN field transfer — so every clause it asserted
-- against was local, and deleting `out.forced = mine.forced` from
-- peciaV2.rechained changed nothing: the trap kept biting for its own
-- reasons, 16/16, 0/4, 0/3, exit 0, measured with the pinned jar. It now
-- composes peciaV2.rechainedFields, the brand-free core of the shipped
-- construction, and replaces EXACTLY the one clause it weakens. A drift in
-- the field transfer now moves this trap instead of leaving it biting in a
-- private copy — the pc-5fba/pc-219e rule (no private copy of the judged
-- vocabulary) applied to the judged CONSTRUCTION.
--
-- What this trap still cannot do, stated because it is what the record
-- turned on: a trap says a WRONG construction violates a property, so it
-- keeps biting whatever the right construction says. Noticing the right
-- rule being deleted is a THEOREM's job, and peciaV2.BrandCustodyThroughRechain
-- (V13) is that theorem. The two are not substitutes.
pred rechainedBrandFromLanded[base, landed, mine, out: Line] {
  rechainedFields[base, landed, mine, out]
  out.forced = landed.forced
}

assert BrandSurvivesRechain {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and no (diff[base, mine] & diff[base, landed])
    and rechainedBrandFromLanded[base, landed, mine, out]
      implies (some out.forced iff some mine.forced)
}
check BrandSurvivesRechain for 6 Line, 3 Id, 2 Disposition, 2 State, 4 Int, 2 Force, 0 Entry, 0 Log, 2 Ctx
