#!/usr/bin/env python3
"""The write gate pecia ships to OTHER projects — templates/pre-commit.

WHY THIS FILE EXISTS. pecia's headline claim is that the write gate refuses a
malformed record at commit time, at tier `tested`. Until pc-8e3f that was true
of pecia's own repo and unreachable for anyone else: dev/hooks/pre-commit
hard-requires pecia's gate registry, claims ledger, README markers and spec, so
copying it into a fresh project fails on the first commit. The template is the
part that generalises.

Every test here drives the REAL template in a REAL throwaway git repo that
contains nothing of pecia but pecia_cli.py — no claims.yaml, no spec/, no
dev/gates.*. If any of those turn out to be needed, these fail, which is the
point: a template that only works inside pecia is the defect it was written to
fix.

Shipping an untested template would be the same fault one level out — an
artifact asserted rather than demonstrated.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "pre-commit"
CLI = ROOT / "pecia_cli.py"


class AdopterTemplate(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "myapp"
        (self.repo / "dev" / "hooks").mkdir(parents=True)
        shutil.copy(CLI, self.repo / "pecia_cli.py")
        (self.repo / "pecia_cli.py").chmod(0o755)
        shutil.copy(TEMPLATE, self.repo / "dev" / "hooks" / "pre-commit")
        (self.repo / "dev" / "hooks" / "pre-commit").chmod(0o755)
        (self.repo / "app.py").write_text("print('hi')\n")
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "core.hooksPath", "dev/hooks")

    # THE HOOK MUST RUN THE CHECKER UNDER TEST. The template resolves
    # $PECIA_CLI, then `pecia` on PATH, then ./pecia_cli.py — so a developer's
    # installed `pecia` won the second rung and every commit here was gated by
    # THAT copy, not the one copied into the fixture. It went unseen while the
    # two agreed and surfaced when v3.0 changed the canonical form (pc-ddd9):
    # the installed copy, a symlink into another checkout, hashed the old way
    # and refused a legitimate commit as a forked projection. The pinning the
    # repo-root arm below already did locally is now the harness default;
    # arms that test a PATH adoption prepend their own bindir, which still wins.
    @property
    def env(self) -> dict[str, str]:
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
                if d and not (Path(d) / "pecia").exists()]
        env = {**os.environ, "PATH": os.pathsep.join(dirs)}
        env.pop("PECIA_CLI", None)
        return env

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=str(self.repo),
                              text=True, capture_output=True, check=False,
                              env=self.env)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        # python3, not the shebang: the template promises a working gate on a
        # machine with no uv, and this asserts that promise rather than
        # inheriting whatever the developer happens to have installed.
        return subprocess.run(["python3", "pecia_cli.py", *args],
                              cwd=str(self.repo), text=True, capture_output=True,
                              env=self.env)

    def commit(self, msg: str = "c") -> subprocess.CompletedProcess[str]:
        return self.git("commit", "-m", msg)

    def adopt(self) -> None:
        self.assertEqual(self.cli("init").returncode, 0)
        self.git("add", "-A")
        self.assertEqual(self.commit("adopt pecia").returncode, 0)

    # -- the gate does its job ------------------------------------------------

    def test_control_a_repo_with_no_ledger_commits_freely(self):
        """Step 2 of the template: no ledger, nothing to check. A gate that
        blocked commits in a project that has not adopted pecia would be
        uninstalled within the hour."""
        self.git("add", "-A")
        self.assertEqual(self.commit("initial").returncode, 0, self.commit().stderr)

    def test_control_adoption_itself_commits(self):
        self.adopt()

    def test_control_an_ordinary_change_commits(self):
        self.adopt()
        (self.repo / "app.py").write_text("print('bye')\n")
        self.git("add", "app.py")
        r = self.commit("ordinary")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kill_a_malformed_staged_ledger_is_refused(self):
        """The claim, in the only form that matters to an adopter."""
        self.adopt()
        p = self.repo / ".pecia" / "work.jsonl"
        p.write_text(p.read_text() + '{"not a record"\n')
        self.git("add", ".pecia/work.jsonl")
        r = self.commit("bad ledger")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("STAGED ledger", r.stderr)

    def test_kill_a_structurally_invalid_record_is_refused(self):
        """Not just parse errors: the cross-record invariants too. `blocked` is
        derived state and may never be stored (E001)."""
        self.adopt()
        p = self.repo / ".pecia" / "work.jsonl"
        rec = json.loads(p.read_text().splitlines()[0]) if p.read_text().strip() else {}
        rec.update({"id": "pc-0001", "type": "task", "title": "t", "status": "blocked",
                    "rev": 1, "created": "2026-01-01", "updated": "2026-01-01",
                    "priority": 2, "body": "", "labels": [], "owner": "t",
                    "edges": {}, "evidence": None, "disposition": None})
        p.write_text(p.read_text() + json.dumps(rec) + "\n")
        self.git("add", ".pecia/work.jsonl")
        self.assertNotEqual(self.commit("stored derived state").returncode, 0)

    # -- the disciplines it inherits -----------------------------------------

    def test_it_reads_the_index_not_the_working_tree(self):
        """A defect STAGED and then repaired in the working tree must still be
        caught — git commits the index. This is the failure class pecia itself
        shipped twice; a template that got it wrong would teach it onward."""
        self.adopt()
        p = self.repo / ".pecia" / "work.jsonl"
        good = p.read_text()
        p.write_text(good + '{"not a record"\n')
        self.git("add", ".pecia/work.jsonl")
        p.write_text(good)                      # working tree repaired, index still bad
        r = self.commit("staged bad, worktree good")
        self.assertNotEqual(r.returncode, 0, "the gate read the working tree")

    def test_a_working_tree_only_defect_does_not_block_a_clean_commit(self):
        """The other direction, and the reason the first is not simply
        paranoia: unstaged mess must not block a clean staged commit."""
        self.adopt()
        (self.repo / "app.py").write_text("print('x')\n")
        self.git("add", "app.py")
        p = self.repo / ".pecia" / "work.jsonl"
        p.write_text(p.read_text() + '{"not a record"\n')   # never staged
        r = self.commit("clean staged change")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kill_staged_deletion_of_the_ledger_is_refused(self):
        """`git rm --cached` leaves the working copy in place, so nothing looks
        missing — and it removes the very file the gate reads, which would
        disable the gate rather than fail it."""
        self.adopt()
        self.git("rm", "--cached", "-q", ".pecia/work.jsonl")
        r = self.commit("de-adopt by stealth")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing staged deletion", r.stderr)

    def test_it_needs_nothing_of_pecia_but_the_cli(self):
        """The whole point. If this repo ever needs claims.yaml or spec/ or
        dev/gates.json, the template has stopped being adoptable."""
        present = {p.name for p in self.repo.iterdir()}
        self.assertNotIn("claims.yaml", present)
        self.assertNotIn("spec", present)
        self.assertFalse((self.repo / "dev" / "gates.json").exists())
        self.adopt()

    # -- where the checker comes from (pc-d182) -------------------------------

    def relocate_cli(self, subdir: str) -> Path:
        """Move the repo-local CLI out of the repo, so only the resolution
        path under test can find it."""
        dest = Path(self.tmp.name) / subdir / "pecia_cli.py"
        dest.parent.mkdir()
        shutil.move(str(self.repo / "pecia_cli.py"), str(dest))
        return dest

    def commit_env(self, env: dict, msg: str = "c") -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "commit", "-m", msg], cwd=str(self.repo),
                              text=True, capture_output=True, env=env)

    def test_kill_no_checker_anywhere_refuses_rather_than_skips(self):
        """A gate that cannot find its checker must fail, never pass the
        commit through unexamined. PATH is pinned so a `pecia` installed on
        the developing machine cannot leak in and green this vacuously."""
        self.adopt()
        self.relocate_cli("nowhere")
        (self.repo / "app.py").write_text("print('z')\n")
        self.git("add", "app.py")
        r = self.commit_env({**os.environ, "PATH": "/usr/bin:/bin"})
        self.assertNotEqual(r.returncode, 0, "the gate skipped instead of failing")
        self.assertIn("no pecia CLI found", r.stderr)

    def test_pecia_cli_env_var_wins(self):
        """The first rung: an adopter points PECIA_CLI at wherever pecia
        actually lives, and the repo needs no copy of its own."""
        self.adopt()
        vendored = self.relocate_cli("vendor")
        (self.repo / "app.py").write_text("print('z')\n")
        self.git("add", "app.py")
        r = self.commit_env({**os.environ, "PECIA_CLI": str(vendored)})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_pecia_on_path_is_found_and_executed_directly(self):
        """The second rung, and the shape the future binary takes: `pecia` on
        PATH is executed as itself, not through python3."""
        self.adopt()
        real = self.relocate_cli("vendor2")
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        wrapper = bindir / "pecia"
        wrapper.write_text(f'#!/bin/sh\nexec python3 "{real}" "$@"\n')
        wrapper.chmod(0o755)
        (self.repo / "app.py").write_text("print('z')\n")
        self.git("add", "app.py")
        env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
        r = self.commit_env(env)
        self.assertEqual(r.returncode, 0, r.stderr)

    # -- the staged projection binds to the timeline (pc-421d) ----------------

    def second_adopter(self, name: str) -> Path:
        """Another independent adopter store, for forging against."""
        other = Path(self.tmp.name) / name
        (other / "dev" / "hooks").mkdir(parents=True)
        shutil.copy(CLI, other / "pecia_cli.py")
        shutil.copy(TEMPLATE, other / "dev" / "hooks" / "pre-commit")
        subprocess.run(["git", "init", "-q", "-b", "main", "."],
                       cwd=str(other), capture_output=True, check=True)
        subprocess.run(["python3", "pecia_cli.py", "init"], cwd=str(other),
                       capture_output=True, check=True)
        subprocess.run(["python3", "pecia_cli.py", "add", "--type", "task",
                        "--title", "forged-projection-title", "--owner", "x"],
                       cwd=str(other), capture_output=True, check=True)
        subprocess.run(["python3", "pecia_cli.py", "snapshot"], cwd=str(other),
                       capture_output=True, check=True)
        return other

    def test_a_staged_projection_behind_the_log_commits_and_says_so(self):
        """v3.3 (pc-25cca4980c47): an ordinary write leaves the projection to
        `snapshot`, so a commit can carry a projection behind the log. That
        is clean — a branch legitimately lags — and it commits, with a note
        naming the gap and the command that closes it; after `snapshot` the
        note is gone."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "unsnapshotted", "--owner", "t")
        (self.repo / "app.py").write_text("print('later')\n")
        self.git("add", "app.py")
        r = self.commit("a change beside a lagging projection")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("holds 0 of the log's 1 entries", r.stderr)
        self.assertIn("snapshot", r.stderr)
        self.cli("snapshot")
        self.git("add", ".pecia")
        r = self.commit("the ledger")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("projection holds", r.stderr)

    def test_kill_a_forged_staged_projection_is_refused(self):
        """pc-421d (round-5 lane D-F1): `check --ledger` on the staged
        work.jsonl runs shape checks only — never the timeline or witness
        checks — so a projection copied from another store committed at
        exit 0 while ordinary `check` refused the working tree with E015
        immediately after. The staged projection now binds to this clone's
        timeline."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "authoritative-title",
                 "--owner", "t")
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.assertEqual(self.commit("real record").returncode, 0)
        other = self.second_adopter("other")
        shutil.copy(other / ".pecia" / "work.jsonl",
                    self.repo / ".pecia" / "work.jsonl")
        self.git("add", ".pecia/work.jsonl")
        r = self.commit("forged projection")
        self.assertNotEqual(r.returncode, 0,
                            msg="a detached projection must not commit")
        self.assertIn("does not bind", r.stderr)
        check = self.cli("check")
        self.assertEqual(check.returncode, 1,
                         msg="the premise: ordinary check refuses the same "
                             "state")
        self.assertIn("E015", check.stdout)

    def test_kill_staged_deletion_of_the_witness_is_refused(self):
        """The second half of pc-421d: deleting .pecia/snapshot.head — the
        stale/fork/truncation discriminator — committed at exit 0."""
        self.adopt()
        self.git("rm", "--cached", "-q", ".pecia/snapshot.head")
        r = self.commit("drop the witness")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing staged deletion", r.stderr)

    def test_control_a_stale_staged_projection_still_commits(self):
        """The discriminating control (VP4(b)): the timeline advancing past
        the staged pair is the ordinary state of a commit that does not
        touch .pecia/ — stale is clean, and a gate refusing it would block
        every commit."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "advances the log",
                 "--owner", "t")
        (self.repo / "app.py").write_text("print('later')\n")
        self.git("add", "app.py")
        r = self.commit("ordinary change beside a stale staged pair")
        self.assertEqual(r.returncode, 0, r.stderr)

    # -- the binding check reads the same staged pair (pc-91a0) ---------------

    def enable_custom_type(self, staged: bool) -> None:
        """Turn on `extra_types: [custom]`, staged or working-tree-only."""
        config = self.repo / ".pecia" / "config.yaml"
        config.write_text(config.read_text().replace("# extra_types: []",
                                                     "extra_types: [custom]"))
        if staged:
            self.git("add", ".pecia/config.yaml")

    def disable_custom_type(self) -> None:
        config = self.repo / ".pecia" / "config.yaml"
        config.write_text(config.read_text().replace("extra_types: [custom]",
                                                     "# extra_types: []"))

    def test_kill_an_unstaged_config_edit_does_not_refuse_a_clean_staged_pair(self):
        """pc-91a0 (round-7 lane E1-F1): step 3 validates the staged
        ledger/config PAIR with --config; step 4 ran the store route
        WITHOUT it, so the checker fell back to the working tree. A staged
        pair that step 3 had just passed at exit 0 was then refused because
        of an UNSTAGED config edit — the index-not-worktree discipline
        holding in one of two checks that share one contract."""
        self.adopt()
        self.enable_custom_type(staged=True)
        add = self.cli("add", "--type", "custom", "--title", "custom record",
                       "--owner", "t")
        self.assertEqual(add.returncode, 0, add.stdout + add.stderr)
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.disable_custom_type()          # the working tree, never staged
        working = self.cli("check")
        self.assertEqual(working.returncode, 1,
                         msg="the premise: the WORKING pair really is invalid")
        self.assertIn("E001", working.stdout)
        r = self.commit("clean staged pair, unstaged config edit")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kill_a_staged_config_that_invalidates_the_staged_ledger_is_refused(self):
        """The discriminating control, and the direction that matters: the
        pair is what is checked, so revoking the type IN THE INDEX refuses
        the very commit the test above lets through. Reading the staged
        config must not mean vouching for the record."""
        self.adopt()
        self.enable_custom_type(staged=True)
        self.assertEqual(self.cli("add", "--type", "custom", "--title",
                                  "custom record", "--owner", "t").returncode, 0)
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.disable_custom_type()
        self.git("add", ".pecia/config.yaml")   # the revocation IS staged
        r = self.commit("staged config revokes the staged record's type")
        self.assertNotEqual(r.returncode, 0,
                            msg="the staged pair is invalid and must be refused")
        self.assertIn("STAGED ledger", r.stderr)

    def test_kill_the_binding_refusal_reports_the_checker_not_a_guess(self):
        """The second half of pc-91a0: the message asserted E015 and
        prescribed `pecia snapshot`, so an E001 condition arrived wearing
        E015's language and a remedy that does not apply to it. The
        findings are now printed — here, over a genuinely forked
        projection, the message carries the checker's own E015."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "authoritative-title",
                 "--owner", "t")
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.assertEqual(self.commit("real record").returncode, 0)
        other = self.second_adopter("other-reporting")
        shutil.copy(other / ".pecia" / "work.jsonl",
                    self.repo / ".pecia" / "work.jsonl")
        self.git("add", ".pecia/work.jsonl")
        r = self.commit("forged projection")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not bind", r.stderr)
        self.assertIn("E015", r.stderr,
                      msg="the refusal must carry what the checker said")

    def test_control_a_fresh_clone_keeps_the_shape_check_alone(self):
        """A plain clone never fetches refs/pecia/log, so there is no local
        timeline to bind against; the hook must not brick every commit in
        the documented fresh-clone state."""
        self.adopt()
        clone = Path(self.tmp.name) / "clone"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(clone)],
                       capture_output=True, check=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                     ("core.hooksPath", "dev/hooks")):
            subprocess.run(["git", "-C", str(clone), "config", k, v],
                           check=True)
        (clone / "app.py").write_text("print('cloned')\n")
        subprocess.run(["git", "-C", str(clone), "add", "app.py"], check=True)
        r = subprocess.run(["git", "-C", str(clone), "commit", "-m", "in clone"],
                           text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()


class DoctorSeesTheUnguardedLedger(unittest.TestCase):
    """pc-1eba: D010.

    Every other D-code reports on a gate that EXISTS — D001 needs a shipped
    hook, D002/D003 need core.hooksPath set. A repo that ran `pecia init` and
    wired nothing satisfied none of them, so doctor answered its own question
    ("is the enforcement ACTIVE?") with `ok: true, warnings: 0` over a ledger
    with no enforcement at all.

    It could not be observed inside pecia, whose clone always has hooks. These
    arms are written from the adopter's side for that reason.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "myapp"
        self.repo.mkdir()
        shutil.copy(CLI, self.repo / "pecia_cli.py")
        (self.repo / "pecia_cli.py").chmod(0o755)
        subprocess.run(["git", "init", "-q", "-b", "main", "."],
                       cwd=str(self.repo), check=True)

    def codes(self) -> set[str]:
        r = subprocess.run(["python3", "pecia_cli.py", "doctor", "--json"],
                           cwd=str(self.repo), text=True, capture_output=True)
        out = set()
        for line in r.stdout.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("code"):
                out.add(d["code"])
        return out

    def init(self) -> None:
        subprocess.run(["python3", "pecia_cli.py", "init"],
                       cwd=str(self.repo), capture_output=True, check=True)

    def test_kill_a_ledger_with_no_gate_is_reported(self):
        self.init()
        self.assertIn("D010", self.codes())

    def test_control_wiring_the_template_clears_it(self):
        """The green control, and the one that keeps D010 honest: if it fired
        regardless of the gate, it would be noise rather than posture."""
        self.init()
        hooks = self.repo / "dev" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(TEMPLATE, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        subprocess.run(["git", "config", "core.hooksPath", "dev/hooks"],
                       cwd=str(self.repo), check=True)
        self.assertNotIn("D010", self.codes())

    def test_control_no_ledger_no_finding(self):
        """A repo that has not adopted pecia has nothing to guard, and being
        told so on every run is how a warning gets trained out."""
        self.assertNotIn("D010", self.codes())

    def test_control_a_non_executable_hook_is_D003_not_D010(self):
        """The gate exists and git skips it silently — that is D003's case,
        and D010 must not shadow it with a vaguer message."""
        self.init()
        hooks = self.repo / "dev" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(TEMPLATE, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o644)
        subprocess.run(["git", "config", "core.hooksPath", "dev/hooks"],
                       cwd=str(self.repo), check=True)
        codes = self.codes()
        self.assertIn("D003", codes)
        self.assertNotIn("D010", codes)

    # -- v2.16: the summary answers doctor's own question -----------------

    def summary(self, *args: str) -> tuple[int, dict]:
        r = subprocess.run(["python3", "pecia_cli.py", "doctor", "--json", *args],
                           cwd=str(self.repo), text=True, capture_output=True)
        objs = [json.loads(l) for l in r.stdout.splitlines()
                if l.strip().startswith("{")]
        return r.returncode, (objs[-1] if objs else {})

    def wire(self) -> None:
        hooks = self.repo / "dev" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(TEMPLATE, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        subprocess.run(["git", "config", "core.hooksPath", "dev/hooks"],
                       cwd=str(self.repo), check=True)

    def track_the_ledger(self) -> None:
        """D007 and D011 are about custody, not about the gate, and they
        stand in a freshly `init`ed directory. Staging clears both — the
        detectors read the index — so the arms below are about the code they
        name and not about an unrelated pair."""
        subprocess.run(["git", "add", ".pecia", ".gitignore"],
                       cwd=str(self.repo), check=True, capture_output=True)

    def test_kill_ok_is_false_where_the_repo_says_no_gate_is_active(self) -> None:
        """pc-8411 (round-11 lane E1-F1): D010 printed "nothing checks a
        record before it is committed" and the machine-readable field beside
        it said `ok: true` — so `ok` meant "no error-severity D-code", which
        claim 33 narrows nowhere. It answers doctor's own question now."""
        self.init()
        self.track_the_ledger()
        rc, summary = self.summary()
        self.assertIn("D010", self.codes(), msg="the fixture's premise")
        self.assertIs(summary["ok"], False)
        self.assertIs(summary["gate_active"], False)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(rc, 0,
                         msg="the EXIT CODE is deliberately unchanged — a "
                             "fresh adoption is not an error")

    def test_control_a_wired_repo_is_ok_true(self) -> None:
        self.init()
        self.track_the_ledger()
        self.wire()
        rc, summary = self.summary()
        self.assertIs(summary["ok"], True)
        self.assertIs(summary["gate_active"], True)
        self.assertEqual(rc, 0)

    def test_control_a_repo_with_no_ledger_is_ok_true(self) -> None:
        """Nothing to guard is not an unguarded something: a repository that
        has not adopted pecia must not read `ok: false`, or the field becomes
        noise everywhere it is not about anything."""
        rc, summary = self.summary()
        self.assertIs(summary["ok"], True)
        self.assertEqual(rc, 0)

    def test_kill_fix_says_the_posture_is_not_clean_when_it_fixed_nothing(self) -> None:
        """pc-b8e2 (round-11 lane E1-F2). Claim 34 said "a --fix that could
        not do what it set out to do exits 1"; the code answered something
        narrower — a fixer that ATTEMPTED an action and failed — and on the
        adopter's case --fix reported `fixed: []`, `unfixed: ["D010"]` and
        exited 0 with the posture unchanged.

        The CLAIM is narrowed to what the code does and the surface gains
        the answer it lacked. Exit 0 here means "it ran and reported", which
        is rule 1's own sentence, and D010's warning severity is a
        deliberate decision (pc-1121, pc-1eba) that an error code would
        overturn — `init` would be followed by a red doctor. What was
        missing is the machine-readable posture answer `ok` already carries
        on the plain surface, and that is what `posture_clean` is."""
        self.init()
        self.track_the_ledger()
        rc, summary = self.summary("--fix")
        self.assertEqual(summary["fixed"], [])
        self.assertEqual(summary["unfixed"], ["D010"])
        self.assertIs(summary["posture_clean"], False)
        self.assertEqual(rc, 0,
                         msg="the exit code is deliberately unchanged; "
                             "`posture_clean` is the field to read")
        self.assertIn("D010", self.codes(),
                      msg="and the posture really is unchanged")

    def test_control_posture_clean_is_true_when_nothing_is_left(self) -> None:
        """The arm that keeps the new field from being a constant."""
        self.init()
        self.track_the_ledger()
        self.wire()
        rc, summary = self.summary("--fix")
        self.assertEqual(summary["unfixed"], [])
        self.assertIs(summary["posture_clean"], True)
        self.assertEqual(rc, 0, msg=json.dumps(summary))

    def test_control_a_fix_that_repairs_what_it_found_is_clean(self) -> None:
        """A --fix with real work to do: the hook is shipped and unwired, so
        D001 stands, --fix wires it, and nothing is left — `posture_clean`
        must follow the repair rather than the attempt."""
        self.init()
        self.track_the_ledger()
        hooks = self.repo / "dev" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(TEMPLATE, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        rc, summary = self.summary("--fix")
        self.assertIn("core.hooksPath=dev/hooks", summary["fixed"])
        self.assertEqual(summary["unfixed"], [], msg=json.dumps(summary))
        self.assertIs(summary["posture_clean"], True)
        self.assertEqual(rc, 0)


class RefusalIsActionableInEveryAdoption(AdopterTemplate):
    """pc-7ab5 (round-8 lane E1-F3): the template resolves its checker three
    ways precisely because an adopting repository has no `pecia_cli.py` at
    its root (pc-d182) — and then the staged-shape refusal ran that checker
    with `>/dev/null` and printed a FIXED recovery line naming `python3
    pecia_cli.py check …`. In a PATH-only adoption, the case the resolution
    order exists for, the refusal therefore threw away the E001 it had just
    computed and handed the adopter a command that exits 2, "No such file or
    directory". The verdict was right; only the diagnostic was wrong, which
    is the pc-91a0 shape at the other branch of the same hook.

    Inherits AdopterTemplate's fixture so these run against the same real
    template in the same throwaway repo."""

    def on_path_only(self) -> Path:
        """A PATH-only adoption: the CLI lives outside the repo and is
        reachable only as `pecia` on PATH — the second rung."""
        real = self.relocate_cli("vendor-path")
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        wrapper = bindir / "pecia"
        wrapper.write_text(f'#!/bin/sh\nexec python3 "{real}" "$@"\n')
        wrapper.chmod(0o755)
        return wrapper

    def path_env(self, wrapper: Path) -> dict:
        return {**os.environ,
                "PATH": f"{wrapper.parent}:{os.environ.get('PATH', '')}"}

    def stage_a_malformed_ledger(self) -> None:
        p = self.repo / ".pecia" / "work.jsonl"
        p.write_text(p.read_text() + '{"not a record"\n')
        self.git("add", ".pecia/work.jsonl")

    def test_kill_the_refusal_carries_the_finding_it_computed(self) -> None:
        self.adopt()
        wrapper = self.on_path_only()
        self.stage_a_malformed_ledger()
        r = self.commit_env(self.path_env(wrapper), "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("STAGED ledger", r.stderr)
        self.assertIn("E001", r.stderr,
                      msg="the refusal discarded the checker's own finding")

    def test_kill_the_prescribed_command_is_one_this_hook_could_run(self) -> None:
        """The command the refusal prints must name the checker the hook
        RESOLVED. `python3 pecia_cli.py` is the one invocation the resolution
        order exists because an adopter does not have."""
        self.adopt()
        wrapper = self.on_path_only()
        self.stage_a_malformed_ledger()
        r = self.commit_env(self.path_env(wrapper), "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(wrapper), r.stderr)
        self.assertNotIn("python3 pecia_cli.py", r.stderr)

    def test_control_the_discarded_output_is_what_the_adopter_needs(self) -> None:
        """The record's own control: the resolved checker, run by hand on the
        same staged pair, reports the E001 at exit 1 — so what the refusal
        used to throw away is exactly the information being restored."""
        self.adopt()
        wrapper = self.on_path_only()
        self.stage_a_malformed_ledger()
        staged = Path(self.tmp.name) / "staged"
        staged.mkdir()
        for name in ("work.jsonl", "config.yaml"):
            blob = self.git("show", f":.pecia/{name}")
            (staged / name).write_text(blob.stdout)
        by_hand = subprocess.run(
            [str(wrapper), "check", "--ledger", str(staged / "work.jsonl"),
             "--config", str(staged / "config.yaml")],
            cwd=str(self.repo), text=True, capture_output=True)
        self.assertEqual(by_hand.returncode, 1,
                         msg=by_hand.stdout + by_hand.stderr)
        self.assertIn("E001", by_hand.stdout + by_hand.stderr)

    def test_control_a_repo_root_adoption_names_its_own_checker(self) -> None:
        """The other resolution rung, so the remedy is shown TRACKING the
        resolution rather than being one more hard-coded string that happens
        to be right here. PATH is pinned the way the sibling arms in
        test_pecia pin it: a `pecia` installed on the developing machine
        would otherwise win the second rung and green this vacuously."""
        self.adopt()
        self.stage_a_malformed_ledger()
        dirs = [d for d in os.environ["PATH"].split(os.pathsep)
                if d and not (Path(d) / "pecia").exists()]
        env = {**os.environ, "PATH": os.pathsep.join(dirs)}
        env.pop("PECIA_CLI", None)
        r = self.commit_env(env, "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        # .resolve(): the hook spells the path as `git rev-parse
        # --show-toplevel` gives it, and on macOS the temp root is a symlink.
        self.assertIn(f"python3 {self.repo.resolve() / 'pecia_cli.py'}",
                      r.stderr)

    def in_a_path_with_a_space(self) -> Path:
        """The CLI under a directory AND a filename containing a space — the
        record's fixture, `vendor space/pecia cli.py`."""
        dest = Path(self.tmp.name) / "vendor space" / "pecia cli.py"
        dest.parent.mkdir()
        shutil.move(str(self.repo / "pecia_cli.py"), str(dest))
        return dest

    def retype(self, printed: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        """Run the prescribed command the way an adopter would: by retyping
        it into a shell, which is where the quoting has to be right."""
        return subprocess.run(["bash", "-c", printed], cwd=str(cwd),
                              text=True, capture_output=True)

    def prescribed(self, stderr: str, marker: str) -> str:
        """The command line a refusal printed under `marker`, joined across
        its continuation."""
        lines = stderr.splitlines()
        start = next(i for i, ln in enumerate(lines) if marker in ln) + 1
        out = []
        for ln in lines[start:]:
            out.append(ln.strip().rstrip("\\").strip())
            if not ln.rstrip().endswith("\\"):
                break
        return " ".join(out)

    def test_kill_the_prescribed_command_survives_a_space_in_the_checker_path(self) -> None:
        """pc-c59a (round-9 lane E1-F2): `cli_cmd` was built by concatenation
        and echoed unquoted, so a checker at a path containing a space was
        printed as a command that splits at the space — retyped, it exits 2
        with `python3: can't open file '…/vendor'`. run_cli quotes "$cli" and
        was unaffected, so the gate's verdict and findings were right and the
        remedy unnamed itself at the first space (claim 24, pc-7ab5)."""
        self.adopt()
        cli = self.in_a_path_with_a_space()
        env = {**os.environ, "PECIA_CLI": str(cli)}
        self.stage_a_malformed_ledger()
        r = self.commit_env(env, "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("E001", r.stderr)
        printed = self.prescribed(r.stderr, "Re-run it yourself with:")
        self.assertIn("vendor", printed)
        again = self.retype(printed, self.repo)
        self.assertEqual(again.returncode, 1,
                         msg="the prescribed command must RUN and reproduce "
                             "the hook's own finding:\n" + printed + "\n"
                             + again.stdout + again.stderr)
        self.assertIn("E001", again.stdout + again.stderr)

    def test_kill_the_binding_remedy_survives_a_space_too(self) -> None:
        """The other refusal on the same hook, one spelling over."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "authoritative-title",
                 "--owner", "t")
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.assertEqual(self.commit("real record").returncode, 0)
        other = self.second_adopter("other-space")
        shutil.copy(other / ".pecia" / "work.jsonl",
                    self.repo / ".pecia" / "work.jsonl")
        cli = self.in_a_path_with_a_space()
        self.git("add", ".pecia/work.jsonl")
        r = self.commit_env({**os.environ, "PECIA_CLI": str(cli)},
                            "forged projection")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not bind", r.stderr)
        # this refusal carries its command mid-sentence, across a wrap
        match = re.search(r"regenerated with:\s*(.+?),\s*then stage",
                          r.stderr, re.S)
        self.assertIsNotNone(match, msg=r.stderr)
        printed = " ".join(match.group(1).split())
        self.assertIn("vendor", printed)
        again = self.retype(printed, self.repo)
        self.assertEqual(again.returncode, 0,
                         msg="the prescribed regeneration must run:\n"
                             + printed + "\n" + again.stdout + again.stderr)

    def test_control_a_space_free_path_prints_the_same_runnable_command(self) -> None:
        """The discriminating control: the identical fixture under a path
        with no space printed a command that ran before this fix and runs
        now, so the quoting is what changed and not the command."""
        self.adopt()
        dest = Path(self.tmp.name) / "vendor-plain" / "pecia_cli.py"
        dest.parent.mkdir()
        shutil.move(str(self.repo / "pecia_cli.py"), str(dest))
        self.stage_a_malformed_ledger()
        r = self.commit_env({**os.environ, "PECIA_CLI": str(dest)}, "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        printed = self.prescribed(r.stderr, "Re-run it yourself with:")
        self.assertNotIn("\\", printed,
                         msg="an ordinary path must not acquire quoting noise")
        again = self.retype(printed, self.repo)
        self.assertEqual(again.returncode, 1, msg=printed + again.stderr)
        self.assertIn("E001", again.stdout + again.stderr)

    def test_kill_the_binding_refusals_remedy_resolves_too(self) -> None:
        """The sibling on the same hook: the projection-binding branch
        prescribed `python3 pecia_cli.py snapshot`, the same unrunnable
        spelling one refusal over. Both branches share one spelling now."""
        self.adopt()
        self.cli("add", "--type", "task", "--title", "authoritative-title",
                 "--owner", "t")
        self.cli("snapshot")  # v3.3: a write leaves the projection to snapshot
        self.git("add", ".pecia")
        self.assertEqual(self.commit("real record").returncode, 0)
        other = self.second_adopter("other-path")
        shutil.copy(other / ".pecia" / "work.jsonl",
                    self.repo / ".pecia" / "work.jsonl")
        wrapper = self.on_path_only()
        self.git("add", ".pecia/work.jsonl")
        r = self.commit_env(self.path_env(wrapper), "forged projection")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not bind", r.stderr)
        self.assertIn(f"{wrapper} snapshot", r.stderr)
        self.assertNotIn("python3 pecia_cli.py snapshot", r.stderr)


class OneSubjectForTheCheckerValue(unittest.TestCase):
    """pc-cb43 (round-10 lane E1-F1), the doctor detector class's SIXTH level
    and the first that is not about which QUESTION the detector asks.

    The template resolved `cli="$PECIA_CLI"` and tested it with `[ -f "$cli" ]`
    — path semantics, satisfied by a file at the work-tree root — while
    `run_cli` executed a non-.py checker as `"$cli" "$@"`, which for a value
    containing no slash is a PATH LOOKUP. A checker sitting at the repository
    root under a bare relative name therefore passed the hook's own existence
    test and died at execution, and doctor — resolving the same value as a
    path, which is what claim 33 says it does since pc-2f19 — reported
    `ok: true`, 0 errors. No stand-in resolving the value once could match
    both readings, because the hook's own two references disagreed.

    So the hook makes a relative value absolute against the work-tree root at
    resolution, once, and the thing tested, the thing executed and the thing
    printed to the adopter are one expression again.

    Standalone rather than a subclass of AdopterTemplate: these arms need that
    fixture, not those tests, and inheriting would re-run two dozen
    git-driving cases for nothing.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "myapp"
        (self.repo / "dev" / "hooks").mkdir(parents=True)
        self.vendored = Path(self.tmp.name) / "vendor" / "pecia_cli.py"
        self.vendored.parent.mkdir()
        shutil.copy(CLI, self.vendored)
        self.vendored.chmod(0o755)
        shutil.copy(TEMPLATE, self.repo / "dev" / "hooks" / "pre-commit")
        (self.repo / "dev" / "hooks" / "pre-commit").chmod(0o755)
        (self.repo / "app.py").write_text("print('hi')\n")
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "core.hooksPath", "dev/hooks")
        subprocess.run(["python3", str(self.vendored), "init"],
                       cwd=str(self.repo), capture_output=True, check=True)
        self.git("add", "-A")
        first = self.commit_with({**os.environ, "PECIA_CLI": str(self.vendored)},
                                 "adopt pecia")
        self.assertEqual(first.returncode, 0, first.stderr)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=str(self.repo),
                              text=True, capture_output=True, check=False)

    def commit_with(self, env: dict, msg: str = "c") -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "commit", "-m", msg], cwd=str(self.repo),
                              text=True, capture_output=True, env=env)

    def doctor(self, env: dict) -> tuple[int, set[str], object]:
        r = subprocess.run(["python3", str(self.vendored), "doctor", "--json"],
                           cwd=str(self.repo), text=True, capture_output=True,
                           env=env)
        codes, ok = set(), None
        for line in r.stdout.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("code"):
                codes.add(d["code"])
            if "ok" in d:
                ok = d["ok"]
        return r.returncode, codes, ok

    def wrapper(self, name: str) -> Path:
        """A non-.py checker the hook must EXEC, at the repository root."""
        path = self.repo / name
        path.write_text(f'#!/bin/sh\nexec python3 "{self.vendored}" "$@"\n')
        path.chmod(0o755)
        return path

    def staged_change(self) -> None:
        (self.repo / "app.py").write_text("print('z')\n")
        self.git("add", "app.py")

    def test_kill_a_bare_relative_checker_commits_as_doctor_says_it_will(self) -> None:
        """The record's own condition. `PECIA_CLI=checkwrap` with the file at
        the repository root: doctor read `ok: true`, 0 errors, and the
        identical environment's commit died at `checkwrap: command not
        found`, its prescribed retry repeating the unusable spelling."""
        self.wrapper("checkwrap")
        env = {**os.environ, "PECIA_CLI": "checkwrap"}
        _, codes, ok = self.doctor(env)
        self.assertTrue(ok, msg=f"doctor: {codes}")
        self.assertNotIn("D012", codes)
        self.staged_change()
        r = self.commit_with(env, "ordinary change")
        self.assertEqual(r.returncode, 0,
                         msg=f"doctor said the gate can run; the commit says: "
                             f"{r.stderr}")
        self.assertNotIn("command not found", r.stderr)

    def test_kill_the_gate_still_bites_through_the_bare_relative_checker(self) -> None:
        """Anti-vacuity for the arm above: the commit must succeed because the
        checker RAN, not because the gate quietly stopped running. The same
        environment, with a malformed staged ledger, is refused."""
        self.wrapper("checkwrap")
        ledger = self.repo / ".pecia" / "work.jsonl"
        ledger.write_text(ledger.read_text() + '{"not a record"\n')
        self.git("add", ".pecia/work.jsonl")
        r = self.commit_with({**os.environ, "PECIA_CLI": "checkwrap"},
                             "bad ledger")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("STAGED ledger", r.stderr)

    def test_control_the_same_file_spelled_with_a_slash_is_unchanged(self) -> None:
        """The record's own control: `./checkwrap` committed at exit 0 before
        this fix, so the file, its mode and its interpreter were never the
        difference — only the spelling was. Both spellings are one subject
        now."""
        self.wrapper("checkwrap")
        self.staged_change()
        r = self.commit_with({**os.environ, "PECIA_CLI": "./checkwrap"},
                             "ordinary change")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_control_a_truly_absent_checker_is_refused_by_both_readings(self) -> None:
        """The other end, which must stay accusing: a value naming no file is
        D012 at `ok: false` and a hard refusal at commit — the two readings
        agreeing about absence as they now agree about presence."""
        env = {**os.environ, "PECIA_CLI": "no-such-checker"}
        rc, codes, ok = self.doctor(env)
        self.assertIn("D012", codes)
        self.assertFalse(ok)
        self.assertEqual(rc, 1)
        self.staged_change()
        r = self.commit_with(env, "ordinary change")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no pecia CLI found", r.stderr)

    def test_control_a_relative_checker_resolves_from_the_root_not_the_cwd(self) -> None:
        """The rule the normalization states, measured where it bites: git
        runs hooks from the work-tree root and doctor resolves a relative
        value from there too (pc-2f19), so the same value names the same file
        when doctor is run from a subdirectory."""
        self.wrapper("checkwrap")
        sub = self.repo / "deep" / "nested"
        sub.mkdir(parents=True)
        r = subprocess.run(["python3", str(self.vendored), "doctor", "--json"],
                           cwd=str(sub), text=True, capture_output=True,
                           env={**os.environ, "PECIA_CLI": "checkwrap"})
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertNotIn("D012", r.stdout)


class DoctorReadsAQuotedShebangInAnAdoption(unittest.TestCase):
    """pc-fc1e (round-10 lane E1-F2) end to end, in the three fresh adopting
    repositories the record measured, differing only in the checker's
    shebang: the quoted spelling RUNS, so doctor may not say it dies.

    `shebang_requirements()` split the `#!` line on whitespace and took the
    first non-flag token verbatim, but under `-S` env does its own splitting,
    in which quotes are syntax — so `#!/usr/bin/env -S 'sh'` ran `sh` while
    the reader looked for a program literally named `'sh'` and reported that
    every ordinary commit dies. Every ordinary commit succeeded. The unit-level
    arms are tests.test_pecia.TheShebangIsReadAsEnvReadsIt."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "myapp"
        (self.repo / "dev" / "hooks").mkdir(parents=True)
        self.vendored = Path(self.tmp.name) / "vendor" / "pecia_cli.py"
        self.vendored.parent.mkdir()
        shutil.copy(CLI, self.vendored)
        shutil.copy(TEMPLATE, self.repo / "dev" / "hooks" / "pre-commit")
        (self.repo / "dev" / "hooks" / "pre-commit").chmod(0o755)
        (self.repo / "app.py").write_text("print('hi')\n")
        subprocess.run(["git", "init", "-q", "-b", "main", "."],
                       cwd=str(self.repo), capture_output=True, check=True)
        for key, value in (("user.email", "t@example.invalid"),
                           ("user.name", "t"),
                           ("core.hooksPath", "dev/hooks")):
            subprocess.run(["git", "config", key, value], cwd=str(self.repo),
                           capture_output=True, check=True)
        subprocess.run(["python3", str(self.vendored), "init"],
                       cwd=str(self.repo), capture_output=True, check=True)

    def checker(self, shebang: str) -> Path:
        path = self.repo / "checkwrap"
        path.write_text(f'{shebang}\nexec python3 "{self.vendored}" "$@"\n')
        path.chmod(0o755)
        return path

    def doctor(self, env: dict) -> tuple[int, str]:
        r = subprocess.run(["python3", str(self.vendored), "doctor", "--json"],
                           cwd=str(self.repo), text=True, capture_output=True,
                           env=env)
        return r.returncode, r.stdout + r.stderr

    def commit(self, env: dict) -> subprocess.CompletedProcess[str]:
        subprocess.run(["git", "add", "-A"], cwd=str(self.repo),
                       capture_output=True, check=True)
        return subprocess.run(["git", "commit", "-m", "adopt"],
                              cwd=str(self.repo), text=True,
                              capture_output=True, env=env)

    def test_kill_a_quoted_shebang_is_not_reported_as_a_missing_program(self) -> None:
        self.checker("#!/usr/bin/env -S 'sh'")
        env = {**os.environ, "PECIA_CLI": "./checkwrap"}
        rc, out = self.doctor(env)
        self.assertNotIn("D012", out)
        self.assertEqual(rc, 0, msg=out)
        r = self.commit(env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_control_the_unquoted_spelling_reads_the_same(self) -> None:
        self.checker("#!/usr/bin/env -S sh")
        rc, out = self.doctor({**os.environ, "PECIA_CLI": "./checkwrap"})
        self.assertNotIn("D012", out)
        self.assertEqual(rc, 0, msg=out)

    def test_control_a_genuinely_missing_interpreter_is_still_D012(self) -> None:
        """The detector is alive at both ends, which is what makes the arms
        above about quoting alone: a shebang naming a program that really is
        absent is still an error, and the commit really does die."""
        self.checker("#!/usr/bin/env -S definitely-missing-interp")
        env = {**os.environ, "PECIA_CLI": "./checkwrap"}
        rc, out = self.doctor(env)
        self.assertIn("D012", out)
        self.assertEqual(rc, 1)
        r = self.commit(env)
        self.assertNotEqual(r.returncode, 0)
