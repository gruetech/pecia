"""The graph checks, the object under test against the reference.

E004 (cycles), E009 (decision lineages), E012 (retirement claims) and E016
(blocked terminal records) are computed over every head. The live ledger
carries few of them, so agreement there says little: this builds ledgers
dense with all four from a fixed synthetic record set, with edges
added at random from fixed seeds, and requires the object under test to
say exactly what the reference says about each — same bytes, same exit.

Which cycles E004 reports, and in what words, depends on where the search
enters each cyclic component, so the dense ledgers (hundreds of cycles
tangled into large components) are the ones that separate an
implementation that walks the graph the reference's way from one that
merely finds the same cycles.

Against the reference itself the comparison is vacuous, and says so by
skipping.
"""
from __future__ import annotations

import json
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_pecia import CLI, PECIA_TEST_CLI, argv_for, record

# (seed, how many edges each head may gain): sparse, then dense.
LEDGERS = [(1, (0, 0, 1, 2, 3)), (2, (0, 0, 1, 2, 3)), (3, (0, 0, 1, 2, 3)), (7, (2, 3, 4, 6)), (8, (2, 3, 4, 6))]


def tangled(records: list[dict], seed: int, spread: tuple[int, ...]) -> list[dict]:
    """The records with random blocks, retires and parent edges added to each
    id's last revision, some supersedes among decisions, and some statuses
    moved, all drawn from `seed`."""
    rng = random.Random(seed)
    recs = json.loads(json.dumps(records))
    last = {r["id"]: i for i, r in enumerate(recs)}
    ids = list(last)
    decisions = [i for i in ids if recs[last[i]].get("type") == "decision"]
    for i in ids:
        r = recs[last[i]]
        edges = r.setdefault("edges", {})
        for _ in range(rng.choice(spread)):
            kind = rng.choice(["blocks", "blocks", "retires", "parent"])
            target = rng.choice(ids + ["pc-ghost"])
            if kind == "parent":
                edges["parent"] = target
            else:
                edges.setdefault(kind, []).append(target)
        if r.get("type") == "decision" and decisions and rng.random() < 0.5:
            edges["supersedes"] = rng.choice(decisions)
        if rng.random() < 0.1:
            r["status"] = rng.choice(["open", "done", "superseded", "dropped", "in-progress"])
    return recs


class GraphChecksAgreeWithTheReference(unittest.TestCase):
    def setUp(self) -> None:
        if PECIA_TEST_CLI.resolve() == CLI.resolve():
            self.skipTest("the object under test is the reference: a differential against itself is vacuous")
        # A public repo begins with a small ledger. The differential corpus
        # must not shrink with the author's task list, so synthesize a fixed
        # set of valid heads before planting dense graph edges.
        self.records = [record(id=f"pc-{i:04x}",
                               type="decision" if i % 7 == 0 else "task",
                               title=f"Graph node {i}") for i in range(96)]
        self.dir = Path(tempfile.mkdtemp(prefix="pecia-graph-diff-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.dir)], check=False))
        (self.dir / "config.yaml").write_text("", encoding="utf-8")

    def check(self, target: Path, ledger: Path) -> tuple[str, str, int]:
        argv = argv_for(target, ["check", "--ledger", str(ledger), "--config", str(self.dir / "config.yaml"), "--json"])
        out = subprocess.run(argv, cwd=str(self.dir), capture_output=True, text=True, check=False)
        return out.stdout, out.stderr, out.returncode

    def test_every_graph_finding_is_the_references(self) -> None:
        codes: dict[str, int] = {}
        for seed, spread in LEDGERS:
            ledger = self.dir / f"tangled-{seed}.jsonl"
            with ledger.open("w", encoding="utf-8") as f:
                for r in tangled(self.records, seed, spread):
                    f.write(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
            reference = self.check(CLI, ledger)
            self.assertEqual(self.check(PECIA_TEST_CLI, ledger), reference, f"seed {seed}")
            for code in ("E004", "E009", "E012", "E016"):
                codes[code] = codes.get(code, 0) + reference[0].count(f'"code": "{code}"')
        # Not vacuous: every graph check fired, and the dense ledgers tangle
        # the cycles into components large enough for search order to show.
        self.assertTrue(all(n > 0 for n in codes.values()), codes)
        self.assertGreater(codes["E004"], 200, codes)


if __name__ == "__main__":
    unittest.main()
