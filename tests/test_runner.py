"""dev/test-runner.py — the parallel suite runner keeps unittest's report shape.

Downstream readers grep a unittest log: the protocol loop's expected-failures
list is `^(ERROR|FAIL): name (module.Class.name)` lines, and the gates runner
reads the exit code. So the runner is tested at that boundary, with a red
case: a fixture module carrying one failing, one erroring, one passing and
one skipped test must produce exactly those headers, the `Ran N tests`
line, a FAILED verdict and exit 1; a passing fixture must produce OK and
exit 0; `--list` must enumerate. Exit 0 from the runner means the tests
passed, never that the code is correct.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "dev" / "test-runner.py"

RED = '''
import unittest
class Shapes(unittest.TestCase):
    def test_passes(self): self.assertTrue(True)
    def test_fails(self): self.assertEqual(1, 2)
    def test_errors(self): raise RuntimeError("boom")
    @unittest.skip("deliberate")
    def test_skipped(self): pass
'''
GREEN = '''
import unittest
class Fine(unittest.TestCase):
    def test_a(self): self.assertTrue(True)
    def test_b(self): self.assertEqual(2, 2)
'''
# pc-c8ca: the two arms are the same three assertions. The subtest arm was
# reported `Ran 1 tests … OK` at exit 0 while plain unittest called it three
# failures; the bare arm (the control) was reported FAIL at exit 1 throughout,
# which is what says the runner is blind to addSubTest and not to failure.
SUBTEST = '''
import unittest
class Subs(unittest.TestCase):
    def test_only_subtest_failures(self):
        for i in (1, 2, 3):
            with self.subTest(i=i):
                self.assertEqual(i, 0)
'''
BARE = '''
import unittest
class Bare(unittest.TestCase):
    def test_same_assertions_without_subtest(self):
        for i in (1, 2, 3):
            self.assertEqual(i, 0)
'''
SUBTEST_MIXED = '''
import unittest
class Mixed(unittest.TestCase):
    def test_one_subtest_of_three_fails(self):
        for i in (1, 2, 3):
            with self.subTest(i=i):
                self.assertEqual(i % 2, 1)
    def test_every_subtest_passes(self):
        for i in (1, 2, 3):
            with self.subTest(i=i):
                self.assertEqual(i, i)
    def test_a_subtest_errors(self):
        with self.subTest(phase="boom"):
            raise RuntimeError("inside a subtest")
'''


def run(start: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(RUNNER), "-s", str(start), "-j", "2", "--slowest", "0", *extra],
                          cwd=ROOT, capture_output=True, text=True)


class RunnerReportShape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, name: str, source: str) -> Path:
        d = self.dir / name
        d.mkdir()
        (d / "test_fixture.py").write_text(source)
        return d

    def test_kill_a_failing_fixture_reports_unittest_headers_and_exits_1(self):
        r = run(self.fixture("red", RED))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL: test_fails (test_fixture.Shapes.test_fails)", r.stdout)
        self.assertIn("ERROR: test_errors (test_fixture.Shapes.test_errors)", r.stdout)
        self.assertIn("Ran 4 tests in", r.stdout)
        self.assertIn("FAILED (failures=1, errors=1, skipped=1)", r.stdout)
        self.assertIn("RuntimeError: boom", r.stdout)

    def test_control_a_passing_fixture_reports_ok_and_exits_0(self):
        r = run(self.fixture("green", GREEN))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 2 tests in", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("OK"), r.stdout)

    def test_list_enumerates_every_test_id(self):
        r = run(self.fixture("green", GREEN), "--list")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.split(), ["test_fixture.Fine.test_a", "test_fixture.Fine.test_b"])

    def test_kill_a_subtest_only_failure_is_reported_and_exits_1(self):
        r = run(self.fixture("sub", SUBTEST))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Ran 1 tests in", r.stdout)
        self.assertIn("FAILED (failures=3)", r.stdout)
        for i in (1, 2, 3):
            self.assertIn(
                "FAIL: test_only_subtest_failures "
                f"(test_fixture.Subs.test_only_subtest_failures) (i={i})", r.stdout)

    def test_control_the_same_assertions_outside_a_subtest_are_reported(self):
        r = run(self.fixture("bare", BARE))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL: test_same_assertions_without_subtest "
                      "(test_fixture.Bare.test_same_assertions_without_subtest)", r.stdout)

    def test_kill_the_runner_agrees_with_plain_unittest_on_a_subtest_module(self):
        d = self.fixture("mixed", SUBTEST_MIXED)
        r = run(d)
        plain = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(d), "-v"],
                               cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(plain.returncode, 1, plain.stderr)
        # one FAIL for the even subtest, one ERROR for the raising one, and the
        # all-passing test silent — the same counts plain unittest reports.
        self.assertIn("FAILED (failures=1, errors=1)", r.stdout)
        self.assertIn("FAILED (failures=1, errors=1)", plain.stderr)
        self.assertIn("FAIL: test_one_subtest_of_three_fails "
                      "(test_fixture.Mixed.test_one_subtest_of_three_fails) (i=2)", r.stdout)
        self.assertIn("ERROR: test_a_subtest_errors "
                      "(test_fixture.Mixed.test_a_subtest_errors) (phase='boom')", r.stdout)
        self.assertIn("Ran 3 tests in", r.stdout)

    def test_the_real_suite_is_discovered_and_split(self):
        r = subprocess.run([sys.executable, str(RUNNER), "--list"], cwd=ROOT, capture_output=True, text=True)
        ids = r.stdout.split()
        self.assertGreater(len(ids), 500)
        self.assertTrue(all("." in i for i in ids))


if __name__ == "__main__":
    unittest.main()
