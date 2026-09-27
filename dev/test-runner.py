#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""test-runner — the unittest suite across N processes, one report.

The suite spawns the CLI as a subprocess in nearly every test and grew past
nine minutes serial; on this machine most of that is waiting on process
start-up, so it parallelises almost perfectly. This runner keeps the suite
exactly as `python3 -m unittest discover -s tests` sees it and only changes
who runs which test:

  parent   discovers every test id, deals them round-robin to N workers,
           runs the workers concurrently, and prints one unittest-shaped
           report: the same `FAIL: name (module.Class.name)` headers, the same
           `Ran N tests in Xs` line, the same OK / FAILED (…) verdict, so
           anything that greps a unittest log (the expected-failures list,
           the gates runner) keeps working unchanged.
           A failure raised inside `subTest()` is reported the way plain
           unittest reports it — one header per failing subtest, unittest's
           own ` (i=1)` suffix after the grepped prefix (pc-c8ca).
  worker   `--worker IDS OUT`: loads the ids, runs them with a timing result,
           writes JSON. Each worker is a separate process with the repository
           root as cwd, so a test that changes cwd or environment cannot leak
           into another worker (and the suite already has none that share a
           fixed path).

Usage:
  dev/test-runner.py [-j N] [-s tests] [-k SUBSTRING] [--slowest N] [--serial] [--list]

Exit: 0 all passed / 1 failures or errors / 2 a worker died without a report.
Exit 0 means the tests passed, never that the code is correct (rule 1).
"""
from __future__ import annotations
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEP_EQ = "=" * 70
SEP_DASH = "-" * 70


def flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flatten(t)
        else:
            yield t


def discover_ids(start: str, pattern: str = "test*.py") -> list[str]:
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir=start, pattern=pattern)
    ids = []
    for t in flatten(suite):
        tid = t.id()
        if tid.startswith("unittest.loader._FailedTest"):
            # an import failure; surface it as a test the worker will re-hit
            ids.append(tid)
        else:
            ids.append(tid)
    return sorted(set(ids))


def sub_description(subtest) -> str:
    """unittest's own suffix for a subtest — `(i=1)`, `[msg] (i=1)`, `(<subtest>)`.

    `str(subtest)` is `str(subtest.test_case) + " " + that suffix`, so taking the
    difference keeps the header byte-identical to plain unittest's without
    reaching for the private `_subDescription`.
    """
    whole, parent = str(subtest), str(getattr(subtest, "test_case", ""))
    return whole[len(parent):].strip() if parent and whole.startswith(parent) else whole


class TimingResult(unittest.TestResult):
    """A TestResult that records per-test wall time and formatted tracebacks."""

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._t0 = 0.0

    def startTest(self, test):
        super().startTest(test)
        self._t0 = time.perf_counter()

    def _record(self, test, kind, detail="", sub=""):
        # addSkip is the sibling hook unittest also hands a _SubTest (a SkipTest
        # raised inside a subTest block); keep every record keyed by the real
        # test id with the subtest as a suffix, never folded into the id.
        parent = getattr(test, "test_case", None)
        if parent is not None and not sub:
            sub, test = sub_description(test), parent
        self.records.append({"id": test.id(), "kind": kind, "seconds": time.perf_counter() - self._t0,
                             "detail": detail, "sub": sub})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skip", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "expected-failure", self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "unexpected-success")

    def addSubTest(self, test, subtest, outcome):
        # pc-c8ca: a failure raised inside `with self.subTest(...)` arrives ONLY
        # here — unittest never calls addFailure or addSuccess for that test —
        # so a result that does not override this one records nothing, and the
        # run reports `Ran 1 tests … OK` over a red test. 43 shipped methods use
        # subTest. One record per failing subtest, as plain unittest counts them.
        super().addSubTest(test, subtest, outcome)
        if outcome is None:
            return  # a subtest that passed; the test's own outcome still reports
        kind = "FAIL" if issubclass(outcome[0], test.failureException) else "ERROR"
        self._record(test, kind, self._exc_info_to_string(outcome, test), sub=sub_description(subtest))


def run_worker(ids_path: str, out_path: str, start: str) -> int:
    # `python -m unittest discover -s tests` puts the cwd (the repository root)
    # and the start dir on sys.path; a script under dev/ gets dev/ instead, so
    # `from pecia_cli import …` in a test would not resolve. Match unittest.
    sys.path.insert(0, str(ROOT / start))
    sys.path.insert(0, str(ROOT))
    ids = [line.strip() for line in Path(ids_path).read_text().splitlines() if line.strip()]
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromNames(ids)
    result = TimingResult()
    t0 = time.perf_counter()
    # Silence the tests' own stdout/stderr chatter into a buffer; failures carry their own text.
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = buf, buf
        suite.run(result)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    Path(out_path).write_text(json.dumps({
        "testsRun": result.testsRun, "seconds": time.perf_counter() - t0,
        "records": result.records, "output": buf.getvalue()[-20000:],
    }))
    return 0


def short_name(tid: str) -> str:
    return tid.rsplit(".", 1)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(prog="test-runner")
    ap.add_argument("-j", "--jobs", type=int, default=min(12, os.cpu_count() or 2))
    ap.add_argument("-s", "--start", default="tests")
    ap.add_argument("-p", "--pattern", default="test*.py")
    ap.add_argument("-k", "--filter", default=None, help="only test ids containing this substring")
    ap.add_argument("--slowest", type=int, default=10)
    ap.add_argument("--serial", action="store_true", help="one worker, in this process")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--worker", nargs=2, metavar=("IDS", "OUT"), help=argparse.SUPPRESS)
    args = ap.parse_args()

    os.chdir(ROOT)
    if args.worker:
        return run_worker(args.worker[0], args.worker[1], args.start)

    ids = discover_ids(args.start, args.pattern)
    if args.filter:
        ids = [i for i in ids if args.filter in i]
    if args.list:
        print("\n".join(ids))
        return 0
    if not ids:
        print("no tests discovered", file=sys.stderr)
        return 2

    jobs = 1 if args.serial else max(1, min(args.jobs, len(ids)))
    bins: list[list[str]] = [[] for _ in range(jobs)]
    for i, tid in enumerate(ids):
        bins[i % jobs].append(tid)

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="test-runner-") as tmp:
        procs = []
        for w, chunk in enumerate(bins):
            ids_path = Path(tmp) / f"w{w}.ids"
            out_path = Path(tmp) / f"w{w}.json"
            ids_path.write_text("\n".join(chunk) + "\n")
            cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(ids_path), str(out_path),
                   "-s", args.start]
            procs.append((w, out_path, subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                                                        stderr=subprocess.PIPE, text=True)))
        records: list[dict] = []
        tests_run = 0
        dead = []
        for w, out_path, p in procs:
            _, err = p.communicate()
            if not out_path.exists():
                dead.append((w, p.returncode, err[-2000:]))
                continue
            data = json.loads(out_path.read_text())
            tests_run += data["testsRun"]
            records.extend(data["records"])
    wall = time.perf_counter() - t0

    failures = [r for r in records if r["kind"] == "FAIL"]
    errors = [r for r in records if r["kind"] == "ERROR"]
    skipped = [r for r in records if r["kind"] == "skip"]
    expected = [r for r in records if r["kind"] == "expected-failure"]
    unexpected = [r for r in records if r["kind"] == "unexpected-success"]

    out = sys.stdout
    for r in sorted(failures + errors, key=lambda r: (r["id"], r.get("sub", ""))):
        # A subtest failure keeps the `KIND: name (module.Class.name)` prefix every
        # downstream reader greps and appends unittest's own ` (i=1)` suffix (pc-c8ca).
        suffix = f" {r['sub']}" if r.get("sub") else ""
        out.write(f"{SEP_EQ}\n{r['kind']}: {short_name(r['id'])} ({r['id']}){suffix}\n{SEP_DASH}\n{r['detail']}\n")
    for w, rc, err in dead:
        out.write(f"{SEP_EQ}\nWORKER {w} DIED (rc={rc}) — its tests were not run\n{SEP_DASH}\n{err}\n")
    if args.slowest and records:
        out.write(f"{SEP_DASH}\nslowest {min(args.slowest, len(records))} tests:\n")
        for r in sorted(records, key=lambda r: -r["seconds"])[:args.slowest]:
            out.write(f"  {r['seconds']:6.2f}s  {r['id']}\n")
    out.write(f"{SEP_DASH}\nRan {tests_run} tests in {wall:.3f}s ({jobs} workers)\n\n")
    parts = []
    if failures:
        parts.append(f"failures={len(failures)}")
    if errors:
        parts.append(f"errors={len(errors)}")
    if skipped:
        parts.append(f"skipped={len(skipped)}")
    if expected:
        parts.append(f"expected failures={len(expected)}")
    if unexpected:
        parts.append(f"unexpected successes={len(unexpected)}")
    if dead:
        out.write(f"FAILED (workers died={len(dead)})\n")
        return 2
    if failures or errors or unexpected:
        out.write("FAILED (" + ", ".join(parts) + ")\n")
        return 1
    out.write("OK" + (" (" + ", ".join(parts) + ")" if parts else "") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
