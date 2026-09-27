module peciaV2_seeded_kills

/*
 * SEEDED KILLS — the v2 formal gate's null arm. Every assertion in this
 * file is EXPECTED TO FAIL, because each claims a deliberately weakened
 * publish rule or differ is adequate. If any assertion here ever PASSES,
 * the model has gone vacuous — wrong scopes, dead predicates, a drifted
 * abstraction — and dev/alloy-gate.sh fails the build. An instrument that
 * cannot fail is worse than none.
 *
 * These replace LastWinsMatchesMaxRev, which v2 makes unkillable. Under a
 * total order with the rev CAS, last-occurrence-wins and highest-rev-wins
 * COINCIDE, so the v0 defect it modelled is no longer expressible and the
 * seed would have healed on its own — a permanently-green null arm, which
 * is the exact failure VP4 names. It is deleted, and its positive form is
 * now a theorem (peciaV2.LastInChainCoincidesWithMaxRev). Seeds 1 and 2
 * below are what keep the CAS honest in its place.
 *
 * Each seed removes exactly ONE rule, so a passing assertion names the
 * rule that stopped mattering. Seed 3 is not synthetic: omitting a field
 * from a differ is an ordinary implementation slip, and under v2 it
 * silently discards the writer's own edit rather than raising anything.
 *
 * THIS FILE OPENS THE THEOREM MODEL (pc-0c76; was a full copy). The
 * earlier NOTE here argued a null arm must be an independent
 * implementation. Round 1 overturned that reading: the seeds' independence
 * lives in their WEAKENED RULES (publishNoChainCas, publishNoRevCas,
 * diffPartial — declared here and nowhere else), while the vocabulary they
 * are judged against (Field, Line, Entry, chained, diff) is the SUBJECT
 * under test and must be the shipped one. Re-declaring it meant a semantic
 * regression of the theorem model's own definitions left every seed biting
 * in its private copy while the counts stayed green — the exact drift this
 * arm exists to catch. Shared, that regression heals a seed and the gate
 * fires. dev/alloy-gate.sh refuses this file if it re-declares Field or
 * diff.
 */

open peciaV2

-- ── SEED 1: publish without the chain CAS ────────────────────────────────
--
-- Removes `e.prev = tip[L]` — the non-fast-forward rejection. This is what
-- a `push --force`, or a publish path that resolves the parent by anything
-- other than the current tip, amounts to. The claim under test is rule 5
-- itself: that a fork cannot arise. It must not survive.

pred publishNoChainCas[L, L2: Log, e: Entry] {
  e not in L.es
  e.prev in L.es          -- points SOMEWHERE in the log, not necessarily the tip
  L2.es = L.es + e
}

assert Seed1_WeakPublishPreservesForkFreedom {
  all L, L2: Log, e: Entry |
    chained[L] and some L.es and publishNoChainCas[L, L2, e]
      implies chained[L2]
}
check Seed1_WeakPublishPreservesForkFreedom for 5 Entry, 5 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- ── SEED 2: publish without the per-id rev CAS ───────────────────────────
--
-- Keeps the chain CAS, removes the requirement that a record's new rev be
-- exactly head+1. The chain stays linear and every entry is well-formed,
-- so nothing about the TIMELINE looks wrong; what breaks is that the
-- chain-latest entry for a record is no longer its highest rev, and a
-- stale writer can land an entry that resolution then ignores — a write
-- that reports success and is invisible to every reader.

pred lastInChainIsMaxRev[L: Log] {
  all e: L.es |
    (no f: L.es | f.rec.lid = e.rec.lid and e in f.^prev)
      implies (no g: L.es | g.rec.lid = e.rec.lid and g.rec.rev > e.rec.rev)
}

pred publishNoRevCas[L, L2: Log, e: Entry] {
  e not in L.es
  e.prev = tip[L]
  L2.es = L.es + e
}

assert Seed2_WeakPublishKeepsLastAtMaxRev {
  all L, L2: Log, e: Entry |
    chained[L] and some L.es and lastInChainIsMaxRev[L]
    and publishNoRevCas[L, L2, e]
      implies lastInChainIsMaxRev[L2]
}
check Seed2_WeakPublishKeepsLastAtMaxRev for 5 Entry, 5 Line, 3 Id, 2 Disposition, 2 Log, 4 Int, 2 Force, 2 Ctx

-- ── SEED 3: a differ that misses a field ─────────────────────────────────
--
-- `diffPartial` omits FBlocks — the ordinary slip of adding a field to the
-- record and not to the differ. Nothing raises: the conflict test sees two
-- disjoint field-sets, the re-chain applies only what was named, and the
-- writer's edge edit is discarded while the CAS reports success. Judged
-- against the TRUE diff — peciaV2's own, imported, so a regression of the
-- shipped differ moves this seed too — which is what makes this a kill
-- rather than a restatement of the weakened rule.

fun diffPartial[a, b: Line]: set Field {
    (a.status   != b.status   implies FStatus   else none)
  + (a.parent   != b.parent   implies FParent   else none)
  + (a.disposed != b.disposed implies FDisposed else none)
  + (a.retires  != b.retires  implies FRetires  else none)
  + (a.context  != b.context  implies FContext  else none)
}

pred rechainedByPartial[base, landed, mine, out: Line] {
  let t = diffPartial[base, mine] {
    out.lid = landed.lid
    (FStatus   in t implies out.status   = mine.status   else out.status   = landed.status)
    (FBlocks   in t implies out.blocks   = mine.blocks   else out.blocks   = landed.blocks)
    (FParent   in t implies out.parent   = mine.parent   else out.parent   = landed.parent)
    (FDisposed in t implies out.disposed = mine.disposed else out.disposed = landed.disposed)
    (FRetires  in t implies out.retires  = mine.retires  else out.retires  = landed.retires)
    (FContext  in t implies out.context  = mine.context  else out.context  = landed.context)
  }
}

assert Seed3_PartialDiffRechainPreservesBoth {
  all base, landed, mine, out: Line |
    landed.lid = base.lid and mine.lid = base.lid
    and no (diffPartial[base, mine] & diffPartial[base, landed])
    and rechainedByPartial[base, landed, mine, out]
      implies diff[landed, out] = diff[base, mine]
}
check Seed3_PartialDiffRechainPreservesBoth for 6 Line, 3 Id, 2 Disposition, 2 Log, 2 Entry, 4 Int, 2 Force, 2 Ctx
