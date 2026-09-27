#!/usr/bin/env python3
"""Per-test outcomes of the suite against a given CLI — the port's scoreboard.

The Rust port's acceptance test is the existing suite pointed at the binary
through the pc-5462 indirection (PECIA_TEST_CLI). A single pass count is not
enough to steer by: this repository has shipped gates green over an empty
denominator, and the first measurement of the port was that 472 of 1,325
tests PASS against a binary that implements nothing. So this records the
outcome of EVERY test, and can compare two runs — a stub, a partial port,
the Python — so a slice of Rust shows exactly which tests it turned green,
and a green that was green against the stub is never counted as progress.

Uses the test runner's own worker mode, so what is measured is what
`dev/test-runner.py` runs.

Usage:
  dev/port-scoreboard.py run  --cli PATH --out FILE [-j N]
  dev/port-scoreboard.py diff BASE.json NEW.json      # what changed
  dev/port-scoreboard.py show FILE [--file test_pecia]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "dev" / "test-runner.py"


WORKER = r"""
import json, os, sys, unittest, io
sys.path.insert(0, sys.argv[3]); sys.path.insert(0, sys.argv[4])
ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
records = []
class R(unittest.TestResult):
    def startTest(self, t):
        os.environ["PECIA_SCORE_TEST"] = t.id(); super().startTest(t)
    def _rec(self, t, kind, detail=""):
        parent = getattr(t, "test_case", None)
        records.append({"id": (parent or t).id(), "kind": kind, "detail": detail})
    def addSuccess(self, t): self._rec(t, "ok")
    def addFailure(self, t, e): self._rec(t, "FAIL", self._exc_info_to_string(e, t))
    def addError(self, t, e): self._rec(t, "ERROR", self._exc_info_to_string(e, t))
    def addSkip(self, t, r): self._rec(t, "skip", r)
    def addSubTest(self, t, st, o):
        if o is not None:
            k = "FAIL" if issubclass(o[0], t.failureException) else "ERROR"
            self._rec(t, k, self._exc_info_to_string(o, t))
suite = unittest.defaultTestLoader.loadTestsFromNames(ids)
buf = io.StringIO(); old = sys.stdout, sys.stderr
sys.stdout = sys.stderr = buf
try: suite.run(R())
finally: sys.stdout, sys.stderr = old
json.dump({"records": records}, open(sys.argv[2], "w"))
"""

# Records each invocation's command before running the object under test.
# Paths are BAKED IN, not read from the environment: some tests run the CLI
# under a scrubbed environment, and a wrapper that needed its own variables
# failed there with 126 -- the tracer, not the binary. Only the test id comes
# from the environment, and it degrades to "?".
WRAPPER = """#!/bin/sh
printf '%s\\t%s\\n' "${{PECIA_SCORE_TEST:-?}}" "$1" >> '{trace}'
exec '{real}' "$@"
"""


def list_ids() -> list[str]:
    out = subprocess.run([sys.executable, str(RUNNER), "--list"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [l.strip() for l in out.split("\n") if l.strip().startswith("test_")]


def run(cli: str, out: Path, jobs: int) -> int:
    ids = list_ids()
    if not ids:
        print("no test ids discovered", file=sys.stderr)
        return 2
    chunks = [ids[i::jobs] for i in range(jobs)]
    with tempfile.TemporaryDirectory() as td:
        trace = Path(td) / "trace.tsv"
        trace.write_text("")
        wrapper = Path(td) / "pecia"
        real = Path(cli).resolve()
        wrapper.write_text(WRAPPER.format(trace=trace, real=real))
        wrapper.chmod(0o755)
        # A .py object must keep its suffix, or the suite would stop treating
        # it as Python and skip the tests that import it; only a compiled CLI
        # is wrapped for tracing.
        under_test = real if real.suffix == ".py" else wrapper
        env = {**os.environ, "PECIA_TEST_CLI": str(under_test),
               "PECIA_SCORE_TRACE": str(trace), "PECIA_SCORE_REAL": str(real)}

        def work(i: int) -> list[dict]:
            idf, outf = Path(td) / f"ids{i}", Path(td) / f"out{i}.json"
            idf.write_text("\n".join(chunks[i]) + "\n")
            subprocess.run([sys.executable, "-c", WORKER, str(idf), str(outf),
                            str(ROOT / "tests"), str(ROOT)],
                           cwd=ROOT, env=env, capture_output=True, text=True)
            if not outf.exists():
                return [{"id": t, "kind": "WORKER-DIED"} for t in chunks[i]]
            return json.loads(outf.read_text())["records"]
        with ThreadPoolExecutor(jobs) as pool:
            records = [r for chunk in pool.map(work, range(jobs)) for r in chunk]
        invoked: dict[str, set[str]] = {}
        for line in trace.read_text().split("\n"):
            if "\t" in line:
                tid, cmd = line.split("\t", 1)
                invoked.setdefault(tid, set()).add(cmd)
    outcome: dict[str, str] = {}
    detail: dict[str, str] = {}
    rank = {"ok": 0, "skip": 1, "expected-failure": 1, "unexpected-success": 2,
            "FAIL": 3, "ERROR": 3, "WORKER-DIED": 4}
    for r in records:
        prev = outcome.get(r["id"])
        if prev is None or rank.get(r["kind"], 3) > rank.get(prev, 3):
            outcome[r["id"]] = r["kind"]
            if r.get("detail"):
                detail[r["id"]] = str(r["detail"])[-1500:]
    for t in ids:
        outcome.setdefault(t, "NOT-RUN")
    out.write_text(json.dumps({"cli": str(cli), "outcomes": outcome, "details": detail,
                               "invoked": {k: sorted(v) for k, v in invoked.items()}},
                              indent=0, sort_keys=True))
    summary(outcome)
    return 0


def bucket(kind: str) -> str:
    return {"ok": "pass", "skip": "skip", "expected-failure": "skip"}.get(kind, "fail")


def summary(outcome: dict[str, str], file: str | None = None) -> None:
    by_file: dict[str, Counter] = {}
    for tid, kind in outcome.items():
        f = tid.split(".", 1)[0]
        if file and f != file:
            continue
        by_file.setdefault(f, Counter())[bucket(kind)] += 1
    total = Counter()
    for f in sorted(by_file):
        c = by_file[f]
        total.update(c)
        print(f"  {f:28s} pass {c['pass']:4d}  fail {c['fail']:4d}  skip {c['skip']:4d}")
    print(f"  {'TOTAL':28s} pass {total['pass']:4d}  fail {total['fail']:4d}  skip {total['skip']:4d}")


def diff(base: Path, new: Path) -> int:
    a = json.loads(base.read_text())["outcomes"]
    b = json.loads(new.read_text())["outcomes"]
    gained = sorted(t for t in b if bucket(b[t]) == "pass" and bucket(a.get(t, "")) != "pass")
    lost = sorted(t for t in b if bucket(b[t]) != "pass" and bucket(a.get(t, "")) == "pass")
    print(f"turned GREEN: {len(gained)}    turned RED: {len(lost)}")
    for t in lost:
        print(f"  RED   {t}  ({b[t]})")
    return 1 if lost else 0


def why(path: Path, ported: set[str]) -> int:
    """Split failures by the commands each test ACTUALLY INVOKED (traced), not
    by what its failure text happens to show: a test that asserts only an exit
    code never prints why. A failing test that invoked nothing unported is a
    defect in what has been ported."""
    data = json.loads(path.read_text())
    invoked = data.get("invoked", {})
    blocked: Counter = Counter()
    ours: list[str] = []
    for tid, kind in sorted(data["outcomes"].items()):
        if bucket(kind) != "fail":
            continue
        cmds = set(invoked.get(tid, []))
        missing = sorted(cmds - ported)
        if missing:
            blocked[" ".join(missing)] += 1
        else:
            ours.append(tid)
    print(f"blocked on an unported command: {sum(blocked.values())}")
    for cmds, n in blocked.most_common(15):
        print(f"  {n:4d}  {cmds}")
    print(f"failing with ONLY ported commands invoked: {len(ours)}")
    for tid in ours:
        print("  ", tid)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--cli", required=True)
    r.add_argument("--out", required=True); r.add_argument("-j", type=int, default=12)
    d = sub.add_parser("diff"); d.add_argument("base"); d.add_argument("new")
    s = sub.add_parser("show"); s.add_argument("file"); s.add_argument("--file", dest="only")
    w = sub.add_parser("why", help="failing tests: missing command vs everything else")
    w.add_argument("file")
    w.add_argument("--ported", default="check", help="comma-separated")
    args = ap.parse_args()
    if args.cmd == "run":
        return run(args.cli, Path(args.out), args.j)
    if args.cmd == "diff":
        return diff(Path(args.base), Path(args.new))
    if args.cmd == "why":
        return why(Path(args.file), set(args.ported.split(",")))
    summary(json.loads(Path(args.file).read_text())["outcomes"], args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
