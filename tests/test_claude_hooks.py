"""Contract tests for the Claude Code harness hooks (M4, pc-24fa).

These test the SCRIPTS' own stdin/stdout/exit-code contract in isolation —
never Claude Code's own enforcement of it, which is outside this repo's
control (CLAUDE.md: "treat TaskCreated/TaskCompleted hook wiring as
convenience, not enforcement"). Green here means well-formed, never that
Claude Code will honor it — rule 1, extended to a third-party harness."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "pecia_cli.py"
SESSION_START = ROOT / "dev" / "hooks" / "claude" / "session-start.py"
TASK_VETO = ROOT / "dev" / "hooks" / "claude" / "task-created-veto.py"


class TaskCreatedVeto(unittest.TestCase):
    def run_hook(self, payload) -> subprocess.CompletedProcess[str]:
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, str(TASK_VETO)], input=stdin,
                              text=True, capture_output=True, check=False)

    def test_kill_no_id_anywhere_is_blocked(self) -> None:
        result = self.run_hook({"task_id": "1", "task_subject": "run the tests",
                                "task_description": "just run pytest"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("pc-xxxx", out["reason"])

    def test_control_id_in_subject_passes(self) -> None:
        result = self.run_hook({"task_id": "1", "task_subject": "fix pc-abcd",
                                "task_description": "the thing"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_control_id_in_description_only_passes(self) -> None:
        result = self.run_hook({"task_id": "1", "task_subject": "run the tests",
                                "task_description": "closes pc-abcd"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_declared_absence_marker_passes(self) -> None:
        result = self.run_hook({"task_id": "1", "task_subject": "no-pecia-id: cleanup",
                                "task_description": "scratch work, off-ledger"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_missing_description_field_does_not_crash(self) -> None:
        # The docs say task_description "may be absent" — a real payload
        # shape, not a malformed one.
        result = self.run_hook({"task_id": "1", "task_subject": "run the tests"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], "block")

    def test_malformed_stdin_fails_open_not_crash(self) -> None:
        result = self.run_hook("not json at all {{{")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "",
                         msg="malformed input from the harness is not this hook's call")

    def test_non_dict_json_fails_open(self) -> None:
        result = self.run_hook("[1, 2, 3]")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")


class SessionStartPrime(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)

    def run_hook(self, env: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SESSION_START)], env=env,
                              text=True, capture_output=True, check=False)

    def test_no_pecia_cli_vendored_is_silent(self) -> None:
        # pecia_cli.py deliberately absent from this temp repo.
        result = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo), "PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_uninitialized_pecia_repo_is_silent_not_an_error(self) -> None:
        (self.repo / "pecia_cli.py").symlink_to(CLI)
        result = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "",
                         msg="next's own E000 exit is the silence signal")

    def test_ready_work_produces_additional_context(self) -> None:
        (self.repo / "pecia_cli.py").symlink_to(CLI)
        init = subprocess.run([sys.executable, str(CLI), "init"], cwd=str(self.repo),
                              capture_output=True, text=True, check=False)
        self.assertEqual(init.returncode, 0, msg=init.stderr)
        added = subprocess.run([sys.executable, str(CLI), "add", "--type", "task",
                                "--title", "primed work", "--owner", "t", "--json"],
                               cwd=str(self.repo), capture_output=True, text=True,
                               check=False)
        self.assertEqual(added.returncode, 0, msg=added.stderr)
        rid = json.loads(added.stdout)["id"]

        result = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        out = json.loads(result.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn(rid, ctx)
        self.assertIn("pecia_cli.py next", ctx)

    def _seed(self, n: int) -> list[str]:
        """Init a repo and add `n` ready tasks. Returns their ids, in add order."""
        (self.repo / "pecia_cli.py").symlink_to(CLI)
        init = subprocess.run([sys.executable, str(CLI), "init"], cwd=str(self.repo),
                              capture_output=True, text=True, check=False)
        self.assertEqual(init.returncode, 0, msg=init.stderr)
        ids = []
        for i in range(n):
            added = subprocess.run([sys.executable, str(CLI), "add", "--type", "task",
                                    "--title", f"ready work {i}", "--owner", "t",
                                    "--json"],
                                   cwd=str(self.repo), capture_output=True, text=True,
                                   check=False)
            self.assertEqual(added.returncode, 0, msg=added.stderr)
            ids.append(json.loads(added.stdout)["id"])
        return ids

    def test_kill_prime_states_no_ready_total_when_queue_exceeds_the_limit(self) -> None:
        r"""The pc-3373 kill.

        The prime used to print `len(items)` from an ALREADY-TRUNCATED fetch,
        so its "count" was its own --limit: it said "5 ready item(s)" against a
        true 39. The fix (decision: option (c)) was to state no total at all,
        so this pins the ABSENCE of a census rather than the correctness of
        one — a corrected count would still be a primed count, and CLAUDE.md
        routes that question to the live query.

        ANTI-VACUITY (VP4 — every gate must be able to turn RED): a fixture
        with <= TOP_N ready items cannot distinguish a truncated count from an
        honest one, so the precondition is asserted, not assumed. Against the
        pre-fix hook this test goes red on the `\d+ ready` match."""
        total = 7
        ids = self._seed(total)

        # PRECONDITION: the queue must genuinely exceed what the prime prints,
        # or the absence proved below is proved against nothing.
        ready = subprocess.run([sys.executable, str(CLI), "ready", "--json"],
                               cwd=str(self.repo), capture_output=True, text=True,
                               check=False)
        self.assertEqual(ready.returncode, 0, msg=ready.stderr)
        self.assertEqual(len(json.loads(ready.stdout)), total,
                         msg="fixture did not produce the ready queue it claims")
        self.assertGreater(total, 3,
                           msg="fixture must exceed the prime's display limit "
                               "or this test cannot fail")

        result = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        ctx = out_ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

        # 1. It truncates — only the top few ids are primed.
        id_lines = [ln for ln in ctx.splitlines() if ln.startswith("  ")]
        self.assertEqual(len(id_lines), 3)
        self.assertTrue(set(ids) & {ln.split()[0] for ln in id_lines})

        # 2. THE KILL: no census claim anywhere in the primed text.
        self.assertIsNone(re.search(r"\d+\s+ready", ctx),
                          msg=f"prime states a ready total: {out_ctx!r}")
        self.assertNotIn("item(s)", ctx)
        self.assertNotIn(str(total), ctx.split("\n")[0],
                         msg="header must not carry the true total either")

        # 3. The pointer half of option (c): it says where the total lives.
        self.assertIn("pecia_cli.py next", ctx)
        self.assertIn("TOTAL", ctx)

    def test_kill_primed_header_is_insensitive_to_queue_growth(self) -> None:
        """Companion to the pc-3373 kill, from the other side.

        A prime that carries no census must read IDENTICALLY as the queue
        grows behind it, provided the top slice is unchanged. It pins that the
        fix did not reintroduce a count that merely looks right.

        Verified RED against the pre-fix hook (4 -> 5), which is worth stating
        precisely because the naive expectation is wrong: the old bug is
        usually described as "the number never moves", and a number that never
        moved would PASS this test. It moves here only because this fixture
        straddles the old --limit 5 — 4 ready reads as "4", 9 ready reads as
        "5". Above that limit the old prime is frozen and this arm alone would
        be vacuous, which is why the absence assertion, not this one, is the
        load-bearing kill."""
        self._seed(4)
        first = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                               "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        ctx_before = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]

        for i in range(5):
            subprocess.run([sys.executable, str(CLI), "add", "--type", "task",
                            "--priority", "3", "--title", f"later work {i}",
                            "--owner", "t"], cwd=str(self.repo),
                           capture_output=True, check=False)

        second = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        ctx_after = json.loads(second.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(ctx_before, ctx_after,
                         msg="prime moved when only unshown ready work changed")

    def test_output_is_valid_sessionstart_hookspecificoutput_shape(self) -> None:
        (self.repo / "pecia_cli.py").symlink_to(CLI)
        subprocess.run([sys.executable, str(CLI), "init"], cwd=str(self.repo),
                       capture_output=True, check=False)
        subprocess.run([sys.executable, str(CLI), "add", "--type", "task",
                        "--title", "t", "--owner", "t"], cwd=str(self.repo),
                       capture_output=True, check=False)
        result = self.run_hook({"CLAUDE_PROJECT_DIR": str(self.repo),
                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        out = json.loads(result.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIsInstance(out["hookSpecificOutput"]["additionalContext"], str)


if __name__ == "__main__":
    unittest.main()
