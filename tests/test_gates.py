#!/usr/bin/env python3
"""Red-case arms for the GATE REGISTRY — every registered commit gate, proved
able to turn RED *through the runner*, plus the runner's own kills.

WHY THROUGH THE RUNNER. Each of these checkers already has tests that drive it
directly. Those prove the checker fires; they do not prove the REGISTRY fires
it. The failure this file exists to catch is the one BP23 names: an entry that
reads as enforcement while running nothing — a wrong path, a placeholder that
never expands, a trigger list that excludes the input the gate needs. So every
arm below goes through `dev/gates.py --precommit`, in a real temporary git
repository, against a real INDEX.

VP4 DISCIPLINE: every red arm is paired with a GREEN control on the same
fixture apparatus. A red-only arm cannot distinguish "the gate fired" from
"the runner is broken and everything fails".

VP21: the audit arms at the bottom are the meta-gate's own red cases. The
meta-gate ships green, so its ability to fail is proved in the same commit —
including the phantom-red-case arm, which is this file asserting that a
registry claiming THIS file's tests would be caught if they were deleted.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "dev" / "gates.py"

# The register's id scan both readers share (pc-3472), imported directly so
# its structure arms do not have to go through a subprocess to be read.
sys.path.insert(0, str(ROOT / "dev"))
import claims_ids as CLAIM_IDS  # noqa: E402

#: The floor the hook enforces. Duplicated here DELIBERATELY and asserted equal
#: to the hook's copy by test_control_the_real_registry_satisfies_the_real_floor
#: — an independent observation that must agree, checked, not deduplicated.
REQUIRED = ["claims-check", "ledger-check", "vocab-check", "prose-check"]


def _gates_module():
    """dev/gates.py loaded as a module, so the fixtures below are built from
    the SAME pin the audit enforces. The arms then measure the rule's
    behaviour; the separate control below measures the pin's content against
    the shipped registry, which is the part that can silently drift."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pecia_gates", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PIN = _gates_module().REQUIRED_INPUTS


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


class Fixture:
    """A throwaway git repo with a staged tree, and a registry pointing at the
    REAL checkers by absolute path (so `{top}` resolving to the temp repo does
    not silently make every gate a no-op)."""

    def __init__(self, tmp: Path):
        self.dir = tmp / "repo"
        self.dir.mkdir()
        git(["init", "-q"], self.dir)
        git(["config", "user.email", "t@t"], self.dir)
        git(["config", "user.name", "t"], self.dir)
        self.staged_root = tmp / "staged"
        self.staged_root.mkdir()

    def put(self, rel: str, content: str | bytes):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)

    def copy_real(self, rel: str):
        self.put(rel, (ROOT / rel).read_bytes())

    def stage_all(self):
        git(["add", "-A"], self.dir)

    def run(self, registry: dict) -> subprocess.CompletedProcess:
        rp = self.dir.parent / "registry.json"
        rp.write_text(json.dumps(registry))
        return subprocess.run(
            [sys.executable, str(RUNNER), "--precommit",
             "--staged-root", str(self.staged_root), "--registry", str(rp)],
            cwd=self.dir, capture_output=True, text=True)


def entry(gid, argv, staged_argv, inputs, triggers=None, require_cached=None):
    e = {"id": gid, "why": "fixture", "argv": argv, "staged_argv": staged_argv,
         "inputs": inputs, "triggers": triggers, "precommit": True,
         "on_fail": [], "redcase": None}
    if require_cached:
        e["require_cached"] = require_cached
    return {"discovery": {"globs": []}, "gates": [e]}


CLAIMS_OK = """\
schema_version: 1
audit: 2026-07-25
claims:
- id: only-claim
  claim: The write gate refuses a malformed record at commit time.
  tier: tested
  evidence: python3 -m unittest
  verified: 2026-08-31
  kill: fixture kill — a malformed record fails the gate
  notes: fixture
"""

# An `aspirational` claim carrying evidence and a verified date — the honesty
# rule claims-check exists to enforce, not merely a parse error, so the arm
# measures the gate's judgement rather than the YAML parser's.
CLAIMS_BAD = """\
schema_version: 1
audit: 2026-07-25
claims:
- id: only-claim
  claim: The write gate refuses a malformed record at commit time.
  tier: aspirational
  evidence: python3 -m unittest
  verified: 2026-08-31
  kill: fixture kill — a malformed record fails the gate
  notes: fixture
"""


class RedCase(unittest.TestCase):
    """One pair per registered commit gate: it fires on a real defect, and it
    does NOT fire on the same apparatus holding a clean tree."""

    def _pair(self, reg, build_ok, build_bad, gid):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(Path(td))
            build_ok(f)
            f.stage_all()
            green = f.run(reg)
        self.assertEqual(green.returncode, 0,
                         f"GREEN control for {gid} failed — the apparatus is broken, "
                         f"so the red arm below proves nothing.\n{green.stderr}")
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(Path(td))
            build_bad(f)
            f.stage_all()
            red = f.run(reg)
        self.assertEqual(red.returncode, 1, f"{gid} did not fire on a real defect")
        self.assertIn(gid, red.stderr)

    def test_claims_check_fires(self):
        reg = entry("claims-check",
                    [str(ROOT / "dev/claims-check.py")],
                    [str(ROOT / "dev/claims-check.py"), "{staged}/claims.yaml"],
                    ["claims.yaml"])
        self._pair(reg,
                   lambda f: f.put("claims.yaml", CLAIMS_OK),
                   lambda f: f.put("claims.yaml", CLAIMS_BAD),
                   "claims-check")

    def test_ledger_check_fires(self):
        reg = entry("ledger-check",
                    [str(ROOT / "pecia_cli.py"), "check"],
                    [str(ROOT / "pecia_cli.py"), "check",
                     "--ledger", "{staged}/.pecia/work.jsonl",
                     "--config", "{staged}/.pecia/config.yaml"],
                    [".pecia/work.jsonl", ".pecia/config.yaml"],
                    require_cached=".pecia/work.jsonl")

        def ok(f):
            f.copy_real(".pecia/work.jsonl")
            f.copy_real(".pecia/config.yaml")

        def bad(f):
            ok(f)
            p = f.dir / ".pecia/work.jsonl"
            p.write_text(p.read_text() + '{"this is not a record"\n')

        self._pair(reg, ok, bad, "ledger-check")

    def test_vocab_check_fires(self):
        reg = entry("vocab-check",
                    [str(ROOT / "dev/vocab-check.py")],
                    [str(ROOT / "dev/vocab-check.py"),
                     "--spec", "{staged}/spec/format-v1.md",
                     "--spec", "{staged}/spec/format-v2.md",
                     "--source", "{staged}/pecia_cli.py",
                     "--registry", "{staged}/spec/vocabulary.json"],
                    ["spec/format-v1.md", "spec/format-v2.md",
                     "spec/vocabulary.json", "pecia_cli.py"],
                    triggers=["pecia_cli.py"])

        def ok(f):
            for rel in ("spec/format-v1.md", "spec/format-v2.md",
                        "spec/vocabulary.json", "pecia_cli.py"):
                f.copy_real(rel)

        def bad(f):
            ok(f)
            # A STALE generated registry — the drift case, not a syntax error.
            reg_path = f.dir / "spec/vocabulary.json"
            d = json.loads(reg_path.read_text())
            for key in ("allocations", "codes", "entries"):
                if isinstance(d.get(key), list) and d[key]:
                    d[key] = d[key][:-1]
                    break
                if isinstance(d.get(key), dict) and d[key]:
                    d[key].pop(next(iter(d[key])))
                    break
            reg_path.write_text(json.dumps(d, indent=2))

        self._pair(reg, ok, bad, "vocab-check")

    def test_prose_check_fires(self):
        reg = entry("prose-check",
                    [str(ROOT / "dev/prose-check.py")],
                    [str(ROOT / "dev/prose-check.py"), "--root", "{staged}"],
                    ["README.md", "claims.yaml", "PLAN.md", "spec/format-v1.md"],
                    triggers=["README.md"])

        def ok(f):
            for rel in ("README.md", "claims.yaml", "PLAN.md", "spec/format-v1.md"):
                f.copy_real(rel)

        def bad(f):
            ok(f)
            p = f.dir / "README.md"
            # A referral naming a subject with no address for it — one of the two
            # real defects this gate was built from (pc-73f5).
            p.write_text(p.read_text() +
                         '\n<!-- referral: PLAN.md "a subject PLAN.md does not carry" -->\n'
                         'See PLAN.md for a subject PLAN.md does not carry.\n')

        self._pair(reg, ok, bad, "prose-check")

    def test_schema_check_fires(self):
        reg = entry("schema-check",
                    [str(ROOT / "dev/schema-check.py")],
                    [str(ROOT / "dev/schema-check.py"),
                     "--ledger", "{staged}/.pecia/work.jsonl"],
                    [".pecia/work.jsonl"],
                    triggers=[".pecia/work.jsonl"],
                    require_cached=".pecia/work.jsonl")

        def ok(f):
            f.copy_real(".pecia/work.jsonl")

        def bad(f):
            ok(f)
            p = f.dir / ".pecia/work.jsonl"
            p.write_text(p.read_text() + json.dumps({"id": "nope"}) + "\n")

        self._pair(reg, ok, bad, "schema-check")


class Audit(unittest.TestCase):
    """The meta-gate's own red cases (VP21: it ships green, so prove it can
    fail in the same commit)."""

    def _audit(self, reg: dict, cwd: Path,
               require: list[str] | None = None) -> subprocess.CompletedProcess:
        """cwd must be the real repo — discovery resolves the gate set from
        THIS repository's index — but the fixture registry is written OUTSIDE
        it. A test that drops files into the tree it is auditing changes the
        object it measures, and would have shown up as untracked debris in the
        very commit that introduced these tests."""
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "registry.json"
            rp.write_text(json.dumps(reg))
            cmd = [sys.executable, str(RUNNER), "--audit", "--registry", str(rp)]
            if require:
                cmd += ["--require", " ".join(require)]
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

    def _base(self):
        return {"discovery": {"globs": ["dev/*-check.py", "dev/*-gate.sh"]},
                "gates": []}

    def test_real_registry_is_clean(self):
        """The green control for every arm below."""
        r = subprocess.run([sys.executable, str(RUNNER), "--audit"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unregistered_gate_on_disk_is_caught(self):
        reg = self._base()
        reg["gates"] = [{"id": "none", "why": "x", "argv": None,
                         "staged_argv": None, "inputs": [], "precommit": False,
                         "precommit_why_not": ["x"], "redcase": None}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNREGISTERED GATE", r.stderr)

    def test_dangling_entry_is_caught(self):
        reg = self._base()
        reg["discovery"]["globs"] = []
        reg["gates"] = [{"id": "ghost", "why": "x",
                         "argv": ["{top}/dev/does-not-exist-check.py"],
                         "staged_argv": None, "inputs": [], "precommit": False,
                         "precommit_why_not": ["x"], "redcase": None}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DANGLING ENTRY", r.stderr)

    def test_phantom_redcase_is_caught(self):
        """The arm that guards THIS file: delete these tests and the registry
        stops being able to claim them."""
        reg = self._base()
        reg["discovery"]["globs"] = []
        reg["gates"] = [{"id": "liar", "why": "x", "argv": None,
                         "staged_argv": None, "inputs": [], "precommit": False,
                         "precommit_why_not": ["x"],
                         "redcase": "tests.test_gates.RedCase.test_no_such_arm"}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PHANTOM RED-CASE", r.stderr)

    def test_a_ships_false_entry_with_absent_files_is_withheld_not_broken(self):
        """D4 (pc-8105): the publisher is registered but withheld from
        publication, so in a public clone both its file and its red-case arm
        are absent BY DECLARATION. The audit says withheld and stays green —
        'missing' would teach a public reader to delete the entry, the exact
        shortening a fail-open list invites. The paired controls are
        test_dangling_entry_is_caught and test_phantom_redcase_is_caught:
        the same absences without `ships: false` stay findings."""
        reg = self._base()
        # A glob that matches nothing, rather than []: EMPTY DISCOVERY is its
        # own finding and this arm needs a clean exit to prove the withheld
        # state is not one.
        reg["discovery"]["globs"] = ["dev/*-nonesuch-gate.py"]
        reg["gates"] = [{"id": "private-gate", "why": "x",
                         "argv": ["{top}/dev/withheld-and-absent.py"],
                         "staged_argv": None, "inputs": [], "precommit": False,
                         "precommit_why_not": ["x"], "ships": False,
                         "claim_why_not": ["fixture: asserts nothing"],
                         "redcase": "tests.test_gates.NoSuchClass.test_withheld"}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("WITHHELD"), 2,
                         msg="both the gate file and its arm report withheld")
        self.assertNotIn("DANGLING ENTRY", r.stderr)
        self.assertNotIn("PHANTOM RED-CASE", r.stderr)

    def test_commit_gate_without_staged_argv_is_caught(self):
        reg = self._base()
        reg["discovery"]["globs"] = []
        reg["gates"] = [{"id": "unrunnable", "why": "x", "argv": ["{top}/pecia_cli.py"],
                         "staged_argv": None, "inputs": [], "precommit": True,
                         "redcase": None}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNRUNNABLE COMMIT GATE", r.stderr)

    def test_gutted_registry_is_caught_by_the_floor(self):
        """THE ARM THAT MATTERS. An empty registry audited CLEAN before the
        floor existed: nothing discovered, nothing to run, `registry audit:
        ok`. One file, one innocuous diff, every commit gate off."""
        reg = self._base()
        r = self._audit(reg, ROOT, require=REQUIRED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("REQUIRED GATE MISSING", r.stderr)

    def test_a_required_gate_flipped_to_false_is_caught(self):
        """The realistic version: the gate is still registered, still has a
        plausible `precommit_why_not` — 'temporarily disabled for a hotfix' —
        and prose is not something a checker reads."""
        reg = self._base()
        reg["gates"] = [{"id": g, "why": "x", "argv": ["{top}/pecia_cli.py"],
                         "staged_argv": ["{top}/pecia_cli.py"], "inputs": [],
                         "precommit": False,
                         "precommit_why_not": ["temporarily disabled for a hotfix"],
                         "redcase": None} for g in REQUIRED]
        r = self._audit(reg, ROOT, require=REQUIRED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("REQUIRED GATE DISABLED", r.stderr)

    def test_empty_discovery_globs_are_caught(self):
        """VP4: a discovery list that finds nothing cannot report anything
        unregistered, so the audit is vacuous by construction."""
        reg = self._base()
        reg["discovery"]["globs"] = []
        reg["gates"] = [{"id": g, "why": "x", "argv": None, "staged_argv": None,
                         "inputs": [], "precommit": True, "redcase": None}
                        for g in REQUIRED]
        r = self._audit(reg, ROOT, require=REQUIRED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("EMPTY DISCOVERY", r.stderr)

    def test_control_the_real_registry_satisfies_the_real_floor(self):
        """The green control for the three arms above — and the assertion that
        the hook's floor and the shipped registry actually agree TODAY."""
        hook = (ROOT / "dev" / "hooks" / "pre-commit").read_text()
        line = [l for l in hook.splitlines()
                if l.startswith("REQUIRED_COMMIT_GATES=")][0]
        floor = line.split("=", 1)[1].strip().strip('"').split()
        self.assertEqual(sorted(floor), sorted(REQUIRED),
                         "this test's copy of the floor has drifted from the hook's")
        r = subprocess.run(
            [sys.executable, str(RUNNER), "--audit", "--require", " ".join(floor)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _floor_case(self, mutate, tmp: Path, gid=None):
        """A SELF-CONTAINED repo satisfying the floor, with one field bent.

        It builds its own gate-shaped files rather than auditing the real repo
        with a synthetic gate list — which is what the green control for these
        arms first did, and it failed, because the real gate files on disk were
        then all unregistered. A fixture that cannot go green is not measuring
        the thing it names.
        """
        d = tmp / "repo"
        (d / "dev").mkdir(parents=True)
        gates = []
        # NOT `for gid in REQUIRED` — that shadowed this method's `gid`
        # parameter, so every targeted arm silently bent the LAST gate instead
        # of the named one, and two arms passed for the wrong reason until a
        # third failed and exposed it.
        for each in REQUIRED:
            (d / "dev" / f"{each}.py").write_text("#!/usr/bin/env python3\n")
            canonical = sorted(PIN[each])
            gates.append({
                "id": each, "why": "x",
                "argv": ["{top}/dev/" + each + ".py"],
                "staged_argv": ["{top}/dev/" + each + ".py"]
                               + ["{staged}/" + rel for rel in canonical],
                "inputs": canonical, "triggers": None, "precommit": True,
                "redcase": "tests.test_gates.Audit.test_gutted_registry_is_caught_by_the_floor",
                # The claim join (pc-fc37) is declared away rather than
                # satisfied: this fixture repo has no claims.yaml, and these
                # arms are about the floor's own fields. TheTwoRegistersAreJoined
                # is where the join itself is measured.
                "claim_why_not": ["fixture: a synthetic gate asserts nothing"],
            })
        (d / "tests").mkdir()
        (d / "tests" / "test_gates.py").write_text(
            "class Audit:\n    def test_gutted_registry_is_caught_by_the_floor(self): pass\n")
        git(["init", "-q"], d)
        git(["config", "user.email", "t@t"], d)
        git(["config", "user.name", "t"], d)
        git(["add", "-A"], d)
        reg = {"discovery": {"globs": ["dev/*-check.py", "dev/*-gate.sh"]},
               "gates": gates}
        target = next(g for g in reg["gates"] if g["id"] == gid) if gid \
            else reg["gates"][0]
        mutate(target)
        return reg, d

    def _floor(self, mutate, gid=None):
        """`gid` selects which gate the mutation lands on. It matters now that
        REQUIRED_INPUTS pins each required gate's canonical subject in code:
        an arm about decoy substitution has to bend the gate whose real inputs
        are pinned, not an arbitrary one."""
        with tempfile.TemporaryDirectory() as td:
            reg, d = self._floor_case(mutate, Path(td), gid=gid)
            return self._audit(reg, d, require=REQUIRED)

    def test_a_commit_gate_pointed_at_the_worktree_is_caught(self):
        """staged_argv's {staged} swapped for {top}: the gate then reads the
        worktree, so a staged defect hidden by an unstaged repair passes. The
        F7/F13 class, reintroduced through configuration."""
        def bend(g):
            # argv[0] left correct on purpose: this arm is about the INPUT
            # being redirected, not the executable being swapped, which
            # test_a_decoy_staged_token_with_a_swapped_executable_is_caught
            # covers separately. Keeping them apart means a single change
            # cannot make both arms pass for the same reason.
            g["staged_argv"] = g["staged_argv"] + ["{top}/claims.yaml"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("READS THE WORKTREE", r.stderr)

    def test_an_empty_trigger_list_is_caught(self):
        """`triggers: []` means should_run() never matches — the gate is on,
        listed, explained, and never runs."""
        r = self._floor(lambda g: g.__setitem__("triggers", []))
        self.assertEqual(r.returncode, 1)
        self.assertIn("NEVER TRIGGERS", r.stderr)

    def test_a_misspelled_require_cached_is_caught(self):
        """A typo makes the ls-files probe fail forever, so the gate skips
        forever. One transposed character, silently."""
        def bend(g):
            g["require_cached"] = "claims.yaLm"
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNCHECKABLE PRECONDITION", r.stderr)

    def test_a_commit_gate_with_no_redcase_is_caught(self):
        """VP21 at the floor: a gate that blocks commits with no proof it can
        turn RED is not known to be a gate."""
        r = self._floor(lambda g: g.__setitem__("redcase", None))
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNPROVEN COMMIT GATE", r.stderr)

    def test_a_decoy_staged_token_with_a_swapped_executable_is_caught(self):
        """THE ROUND-3 KILL. `["true", "{staged}/claims.yaml"]` satisfied the
        old "does some token contain {staged}?" test, audited clean, and landed
        a real claims.yaml honesty violation through a live commit. The gate
        ran. It examined nothing."""
        def bend(g):
            g["staged_argv"] = ["true", "{staged}/claims.yaml"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("RUNS A DIFFERENT PROGRAM", r.stderr)

    def test_a_bare_relative_input_path_is_caught(self):
        """The subtler round-3 kill: one input of a multi-input gate redirected
        to the worktree by spelling it as a bare path resolved against cwd.
        The old check only looked for the literal string '{top}/' + input."""
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["dev/claims-check.py"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("READS THE WORKTREE", r.stderr)

    def test_an_absolute_input_path_is_caught(self):
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["/etc/hosts"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ABSOLUTE PATH", r.stderr)

    def test_climbing_out_of_the_staged_tree_is_caught(self):
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["{staged}/../x.yaml"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ESCAPES THE STAGED TREE", r.stderr)

    def test_a_declared_input_that_is_never_passed_is_caught(self):
        """Materialised from the index and then never read is the same as not
        checked, and it reads as configured."""
        def bend(g):
            g["inputs"] = sorted(PIN["claims-check"] | {"PLAN.md"})
        r = self._floor(bend, gid="claims-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("INPUT NOT PASSED", r.stderr)

    def test_a_commit_gate_with_no_inputs_is_caught(self):
        def bend(g):
            g["inputs"] = []
            g["staged_argv"] = [g["staged_argv"][0], "{staged}"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("EXAMINES NOTHING", r.stderr)

    def test_a_decoy_input_substitution_is_caught(self):
        """THE ROUND-5 KILL, which landed real commits against two gates by
        repointing each at a small clean file the attacker supplied and
        DELETING the real path from `inputs`. Every rule from rounds 1-4 held
        and none fired: 'every declared input must be referenced' took its list
        of obligations from the same field being edited. It asked whether the
        registry agreed with itself, and it did."""
        def bend(g):
            g["staged_argv"] = [g["staged_argv"][0], "{staged}/dev/decoy.yaml"]
            g["inputs"] = ["dev/decoy.yaml"]
        r = self._floor(bend, gid="claims-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CANONICAL INPUT NOT READ", r.stderr)
        self.assertIn("DECOY INPUT", r.stderr)

    def test_adding_a_decoy_alongside_the_real_input_is_caught(self):
        """The subtler half: keep reading the real file, and ALSO read a decoy.
        The pin is exact in both directions precisely so this does not become
        the next round's bypass."""
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["{staged}/dev/decoy.yaml"]
            g["inputs"] = sorted(PIN["claims-check"] | {"dev/decoy.yaml"})
        r = self._floor(bend, gid="claims-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DECOY INPUT", r.stderr)

    def test_a_mutating_flag_on_the_gates_own_checker_is_caught(self):
        """THE ROUND-4 KILL, and the one that landed a real commit. `--write`
        is a legitimate flag of vocab-check's own: it turns "compare the
        generated registry byte for byte" into "regenerate it and call it
        clean". One token appended in gates.json. It is not a path, not
        absolute, has no `..`, no {top}, removes no input — every rule written
        in rounds 1-3 passed it, because they knew where arguments POINTED and
        nothing about what they AUTHORISED."""
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["--write"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNPERMITTED OPTION", r.stderr)

    def test_repointing_a_gate_at_another_subcommand_is_caught(self):
        """The bare-token half: pecia_cli's `check` is a subcommand, so bare
        words must be permitted — but only the ones the worktree invocation
        itself uses, or a gate could be re-pointed at `publish`."""
        def bend(g):
            g["staged_argv"] = g["staged_argv"] + ["publish"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNPERMITTED ARGUMENT", r.stderr)

    def test_the_read_only_option_vocabulary_is_allowed(self):
        """Green control: the options the real checkers actually use must pass,
        or the rule would have outlawed the gates it is protecting."""
        def bend(g):
            g["staged_argv"] = [g["staged_argv"][0], "--ledger"] + g["staged_argv"][1:]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_root_mode_gates_are_still_allowed(self):
        """prose-check's shape: `--root {staged}` covers every input without
        naming them. The rule above must not outlaw it."""
        def bend(g):
            g["staged_argv"] = [g["staged_argv"][0], "--root", "{staged}"]
        r = self._floor(bend)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_control_the_unbent_floor_registry_passes(self):
        """Green control for the four arms above — without it they only prove
        that this fixture fails for some reason."""
        r = self._floor(lambda g: None)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_non_ascii_gate_filename_is_still_discovered(self):
        """`git ls-files` octal-escapes and QUOTES any non-ASCII path by
        default, so a gate-shaped file with an accented name matched no glob
        and was invisible to discovery — a silent hole in the one check that
        makes registration mandatory."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "repo"
            (d / "dev").mkdir(parents=True)
            (d / "dev" / "bäd-check.py").write_text("#!/usr/bin/env python3\n")
            git(["init", "-q"], d)
            git(["config", "user.email", "t@t"], d)
            git(["config", "user.name", "t"], d)
            git(["add", "-A"], d)
            rp = Path(td) / "registry.json"
            rp.write_text(json.dumps(self._base()))
            r = subprocess.run(
                [sys.executable, str(RUNNER), "--audit", "--index",
                 "--registry", str(rp)], cwd=d, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, "a non-ASCII gate filename must still be seen")
        self.assertIn("UNREGISTERED GATE", r.stderr)

    def test_unexplained_exclusion_is_caught(self):
        reg = self._base()
        reg["discovery"]["globs"] = []
        reg["gates"] = [{"id": "silent", "why": "x", "argv": ["{top}/pecia_cli.py"],
                         "staged_argv": None, "inputs": [], "precommit": False,
                         "redcase": None}]
        r = self._audit(reg, ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNEXPLAINED EXCLUSION", r.stderr)


if __name__ == "__main__":
    unittest.main()


class InlineGateAudit(unittest.TestCase):
    """v2.7 (pc-fff0): the registry claimed 'adding or removing a gate
    requires editing this file, enforced at commit time', and it was
    silently false for the seven enforcement blocks living inline in
    dev/hooks/pre-commit — deleting one changed neither the audit nor any
    commit verdict. Inline gates are now registered with markers, and the
    audit refuses in both directions against the hook as the commit will
    contain it."""

    # The guard is separate from the refusal on purpose (pc-ba95): the
    # marker is the refusal's WORDS and the condition is what decides whether
    # they are ever printed, so the fixture has to be able to keep one while
    # losing the other.
    HOOK = ('#!/usr/bin/env bash\n'
            'if [ -n "$STAGED" ]; then\n'
            '  echo "✋ pre-commit: fixture refuses X" >&2\n'
            '  exit 1\n'
            'fi\n')
    GUARD = 'if [ -n "$STAGED" ]; then'

    def fixture(self, hook_text: str | None = None) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "repo"
        root.mkdir()
        git(["init", "-q"], root)
        (root / "dev" / "hooks").mkdir(parents=True)
        (root / "dev" / "hooks" / "pre-commit").write_text(
            self.HOOK if hook_text is None else hook_text)
        (root / "tests").mkdir()
        (root / "tests" / "test_pecia.py").write_text(
            "import unittest\n\n\n"
            "class FixtureGate(unittest.TestCase):\n"
            "    def test_kill_fires(self):\n"
            "        pass\n")
        return root

    def registry(self) -> dict:
        return {"discovery": {"globs": ["dev/*-check.py"]},
                "gates": [{"id": "fixture-inline",
                           "why": "fixture",
                           "inline": True,
                           "hook": "dev/hooks/pre-commit",
                           "markers": ["fixture refuses X"],
                           "precommit": True,
                           # Declared away, not satisfied: this fixture repo
                           # carries no claims.yaml and these arms measure the
                           # inline-marker rule (pc-fc37's join has its own).
                           "claim_why_not": ["fixture: asserts nothing"],
                           "redcase":
                               "tests.test_pecia.FixtureGate.test_kill_fires",
                           "redcase_mutation": {
                               "path": "dev/hooks/pre-commit",
                               "from": self.GUARD,
                               "to": "if false; then",
                               "why": "fixture: the refusal becomes "
                                      "unreachable with its marker intact"}}]}

    def audit(self, root: Path, index: bool = False) -> subprocess.CompletedProcess:
        rp = root.parent / "registry.json"
        rp.write_text(json.dumps(self.registry()))
        cmd = [sys.executable, str(RUNNER), "--audit", "--registry", str(rp)]
        if index:
            cmd.append("--index")
        return subprocess.run(cmd, cwd=root, capture_output=True, text=True)

    def test_control_a_registered_inline_gate_audits_clean(self) -> None:
        r = self.audit(self.fixture())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kill_removing_the_inline_block_turns_the_audit_red(self) -> None:
        """The record's own reproduction: delete the refusal block, keep the
        registry — the audit must now see the removal."""
        root = self.fixture(hook_text="#!/usr/bin/env bash\nexit 0\n")
        r = self.audit(root)
        self.assertEqual(r.returncode, 1,
                         msg="a deleted inline gate must redden the audit")
        self.assertIn("INLINE GATE REMOVED", r.stderr)

    def test_kill_an_unregistered_refusal_line_turns_the_audit_red(self) -> None:
        root = self.fixture(hook_text=self.HOOK +
                            'echo "✋ pre-commit: a NEW unregistered refusal" >&2\n')
        r = self.audit(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNREGISTERED INLINE GATE", r.stderr)

    def test_kill_index_mode_reads_the_staged_hook_not_the_worktree(self) -> None:
        """The F7/F13 staged-versus-worktree class, applied to the hook: a
        commit that deletes the block while the worktree keeps it must still
        redden --index."""
        root = self.fixture()
        git(["config", "user.email", "t@t"], root)
        git(["config", "user.name", "t"], root)
        gutted = root.parent / "gutted"
        gutted.write_text("#!/usr/bin/env bash\nexit 0\n")
        git(["add", "dev/hooks/pre-commit", "tests/test_pecia.py"], root)
        subprocess.run(["git", "update-index", "--cacheinfo", "100755",
                        subprocess.run(["git", "hash-object", "-w", str(gutted)],
                                       cwd=root, capture_output=True,
                                       text=True).stdout.strip(),
                        "dev/hooks/pre-commit"],
                       cwd=root, capture_output=True, check=True)
        r = self.audit(root, index=True)
        self.assertEqual(r.returncode, 1,
                         msg="the staged deletion is what the commit contains")
        self.assertIn("INLINE GATE REMOVED", r.stderr)

    def test_kill_a_markerless_inline_entry_is_refused(self) -> None:
        root = self.fixture()
        reg = self.registry()
        reg["gates"][0]["markers"] = []
        rp = root.parent / "registry.json"
        rp.write_text(json.dumps(reg))
        r = subprocess.run([sys.executable, str(RUNNER), "--audit",
                            "--registry", str(rp)],
                           cwd=root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("INLINE GATE UNANCHORED", r.stderr)

    def _audit_with(self, root: Path, reg: dict) -> subprocess.CompletedProcess:
        rp = root.parent / "registry.json"
        rp.write_text(json.dumps(reg))
        return subprocess.run([sys.executable, str(RUNNER), "--audit",
                               "--registry", str(rp)],
                              cwd=root, capture_output=True, text=True)

    def test_kill_a_gate_disabled_as_unreachable_code_is_refused(self) -> None:
        """pc-ba95 (round-8 lane D-F1), the record's own reproduction in
        miniature: the condition becomes `if false` and every registered
        marker stays where it was. The audit used to exit 0 over that."""
        root = self.fixture(
            hook_text=self.HOOK.replace(self.GUARD, "if false; then"))
        r = self.audit(root)
        self.assertEqual(r.returncode, 1,
                         msg="a gate disabled as unreachable code must "
                             "redden the audit")
        self.assertIn("INLINE GATE UNREACHABLE OR MOVED", r.stderr)
        self.assertNotIn("INLINE GATE REMOVED", r.stderr,
                         msg="the markers are all still present — that is "
                             "the whole point of this arm")

    def test_control_the_markers_alone_do_not_notice_the_disabling(self) -> None:
        """The discriminating control for the arm above: with the guard
        registered as the gate's disabling edit removed from the entry, the
        same disabled hook passes every marker check there is."""
        root = self.fixture(
            hook_text=self.HOOK.replace(self.GUARD, "if false; then"))
        reg = self.registry()
        del reg["gates"][0]["redcase_mutation"]
        r = self._audit_with(root, reg)
        self.assertIn("UNEXECUTED RED-CASE", r.stderr,
                      msg="an inline gate naming an arm with no disabling "
                          "edit is itself a finding")
        self.assertNotIn("INLINE GATE UNREACHABLE", r.stderr)
        self.assertNotIn("INLINE GATE REMOVED", r.stderr)

    def test_kill_an_inline_arm_with_no_disabling_edit_is_refused(self) -> None:
        root = self.fixture()
        reg = self.registry()
        del reg["gates"][0]["redcase_mutation"]
        r = self._audit_with(root, reg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNEXECUTED RED-CASE", r.stderr)

    def test_kill_a_disabling_edit_aimed_off_its_own_hook_is_refused(self) -> None:
        root = self.fixture()
        reg = self.registry()
        reg["gates"][0]["redcase_mutation"]["path"] = "dev/elsewhere.sh"
        r = self._audit_with(root, reg)
        self.assertEqual(r.returncode, 1)
        self.assertIn("MUTATION OFF ITS GATE", r.stderr)

    def test_the_audit_does_not_claim_a_proof_it_did_not_run(self) -> None:
        """The second half of pc-ba95: the audit counted gates it never
        executed among those 'proved able to turn RED'. It now says what it
        checked — registration — and names the route that executes."""
        r = self.audit(self.fixture())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("proved able to turn RED", r.stdout)
        self.assertIn("red-case arms REGISTERED", r.stdout)
        self.assertIn("--prove", r.stdout)


class RedCaseArmsAreExecuted(unittest.TestCase):
    """pc-ba95, the executing half: `dev/gates.py --prove` applies each
    gate's REGISTERED disabling edit in a private copy of the tree and
    requires the named arm to fail there, against the control of that arm
    passing on the tree as it stands. The audit checks registration; this
    checks the thing registration was standing in for.

    The arms below drive the route over a synthetic gate, so the mechanism
    is measured rather than one particular hook. The shipped gates' own
    proofs run from `dev/gates.py --prove` (all seven inline gates), which
    `--run` calls."""

    ARM = "tests.test_fixture_gate.FixtureGate.test_kill_fires"

    def tree(self, disabling: bool = True) -> tuple[Path, dict]:
        """A tiny git repo whose 'gate' is a marker line in a shell script
        and whose red-case arm asserts that line is reachable."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "repo"
        (root / "dev").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "dev" / "guard.sh").write_text(
            "#!/usr/bin/env bash\n"
            'if [ -n "$1" ]; then\n'
            '  echo "refused" >&2\n'
            "  exit 1\n"
            "fi\n")
        (root / "tests" / "test_fixture_gate.py").write_text(
            "import subprocess, unittest\n\n\n"
            "class FixtureGate(unittest.TestCase):\n"
            "    def test_kill_fires(self):\n"
            "        r = subprocess.run(['bash', 'dev/guard.sh', 'x'],\n"
            "                           capture_output=True, text=True)\n"
            "        assert r.returncode == 1, 'the guard did not refuse'\n")
        git(["init", "-q"], root)
        git(["add", "-A"], root)
        edit = ({"path": "dev/guard.sh", "from": 'if [ -n "$1" ]; then',
                 "to": "if false; then", "why": "fixture: the guard never runs"}
                if disabling else
                {"path": "dev/guard.sh", "from": "#!/usr/bin/env bash",
                 "to": "#!/usr/bin/env bash\n# a comment changes nothing",
                 "why": "fixture: an edit that does not disable anything"})
        reg = {"discovery": {"globs": ["dev/*-check.py"]},
               "gates": [{"id": "fixture-guard", "why": "fixture",
                          "precommit": False,
                          "precommit_why_not": ["fixture"],
                          "claim_why_not": ["fixture: asserts nothing"],
                          "redcase": self.ARM,
                          "redcase_mutation": edit}]}
        return root, reg

    def prove(self, root: Path, reg: dict) -> subprocess.CompletedProcess:
        rp = root.parent / "registry.json"
        rp.write_text(json.dumps(reg))
        return subprocess.run([sys.executable, str(RUNNER), "--prove",
                               "--registry", str(rp)],
                              cwd=root, capture_output=True, text=True)

    def test_control_a_real_gate_is_proved_by_its_own_disabling_edit(self) -> None:
        root, reg = self.tree(disabling=True)
        r = self.prove(root, reg)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("proved", r.stdout)
        self.assertIn("red-case arms EXECUTED: 1", r.stdout)

    def test_kill_an_arm_green_under_its_own_disabling_edit_is_refused(self) -> None:
        """The registry claiming a proof it does not have — the finding's
        own shape, made executable: the edit lands, the gate keeps working,
        and the arm passes, so the arm proves nothing about this gate."""
        root, reg = self.tree(disabling=False)
        r = self.prove(root, reg)
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("ARM GREEN UNDER ITS OWN DISABLING EDIT", r.stderr)

    def test_kill_an_arm_already_red_on_the_live_tree_is_refused(self) -> None:
        """The control has to hold before the kill means anything: an arm
        that fails with nothing mutated would 'prove' every gate."""
        root, reg = self.tree(disabling=True)
        (root / "dev" / "guard.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        r = self.prove(root, reg)
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("ARM RED ON THE LIVE TREE", r.stderr)

    def test_kill_an_edit_that_does_not_apply_is_refused(self) -> None:
        root, reg = self.tree(disabling=True)
        reg["gates"][0]["redcase_mutation"]["from"] = "a line that is not there"
        r = self.prove(root, reg)
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("MUTATION DID NOT APPLY", r.stderr)

    def test_kill_an_empty_denominator_is_refused(self) -> None:
        """VP4: a proof route over no gates must not report success."""
        root, reg = self.tree(disabling=True)
        del reg["gates"][0]["redcase_mutation"]
        r = self.prove(root, reg)
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("empty denominator", r.stderr)

    def test_every_shipped_inline_gate_registers_its_disabling_edit(self) -> None:
        """The shipped registry, not a fixture: an inline gate that names an
        arm must carry the edit that makes the arm fail, so `--prove` has
        something to run for every one of them."""
        reg = json.loads((ROOT / "dev" / "gates.json").read_text())
        inline = [g for g in reg["gates"] if g.get("inline")]
        self.assertTrue(inline)
        for g in inline:
            with self.subTest(gate=g["id"]):
                self.assertTrue(g.get("redcase"), "no red-case arm registered")
                edit = g.get("redcase_mutation")
                self.assertIsNotNone(edit, "no disabling edit registered")
                hook = (ROOT / edit["path"]).read_text()
                self.assertEqual(hook.count(edit["from"]), 1,
                                 f"the registered guard {edit['from']!r} must "
                                 f"appear exactly once in {edit['path']}")


class AlloyCompanionStatic(unittest.TestCase):
    """v2.7 (pc-5fba): the traps companion kept a private, WEAKER cleanLines
    (E003/E004 ignoring retires; E005/E008/E012/E016/E017 absent) that the
    pc-0c76 redeclaration check — `enum Field|fun diff[` only — could not
    reject, so trap 3 was judged against a definition later invariant
    changes silently outgrew. `alloy-gate.sh --static` runs the widened
    structural checks without a JDK, which is what makes this class runnable
    from the ordinary suite.

    v2.8 (pc-219e): the pc-5fba refusal was a name BLACKLIST, one rename
    away from silence — a companion regrowing the judged vocabulary under
    fresh identifiers passed it while the traps were judged against a
    private copy. The companion declaration set is now CLOSED: each file
    enumerates exactly its deliberate weakenings in dev/alloy-gate.sh, any
    other declaration is refused, and every Field member must appear in the
    traps file so the hand-repeated transfer clauses cannot silently lag
    the enum."""

    def scratch_gate(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "dev").mkdir()
        (root / "spec").mkdir()
        shutil.copy(ROOT / "dev" / "alloy-gate.sh", root / "dev" / "alloy-gate.sh")
        # v2.15 (pc-6196): the gate reads every .als through one lexical
        # answer, so the module that gives it travels with the gate.
        shutil.copy(ROOT / "dev" / "alloy_lex.py", root / "dev" / "alloy_lex.py")
        for f in ("peciaV2.als", "pecia-v2-traps.als", "pecia-v2-seeded-kills.als"):
            shutil.copy(ROOT / "spec" / f, root / "spec" / f)
        return root

    def run_static(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(root / "dev" / "alloy-gate.sh"),
                               "--static"],
                              capture_output=True, text=True, check=False)

    def test_control_the_shipped_files_pass_static(self) -> None:
        result = self.run_static(self.scratch_gate())
        self.assertEqual(result.returncode, 0,
                         msg=f"apparatus broken: {result.stdout}{result.stderr}")

    def test_kill_a_private_weaker_cleanlines_is_refused(self) -> None:
        """The record's own shape: a companion re-growing the pre-fix private
        vocabulary — E003/E004 over blocks+parent only, no retires."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "pred cleanLines[L: set Line] {\n"
                         + "  all h: headsL[L] | (h.blocks + h.parent) in idsL[L]\n"
                         + "}\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg="a private clean predicate must be refused")
        self.assertIn("pc-5fba", result.stderr)

    def test_kill_a_private_heads_function_is_refused(self) -> None:
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "fun headsOfLines[L: set Line]: set Line { headsL[L] }\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("pc-5fba", result.stderr)

    def test_kill_renamed_private_vocabulary_is_refused(self) -> None:
        """pc-219e's own shape: the pc-5fba class one rename away — the
        judged vocabulary regrown under fresh identifiers the family
        blacklist cannot see. The closed declaration set refuses it."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "fun privateMaxima[L: set Line]: set Line { headsL[L] }\n"
                         + "fun privateNames[L: set Line]: set Id { idsL[L] }\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg="a renamed private copy must be refused")
        self.assertIn("pc-219e", result.stderr)
        self.assertIn("privateMaxima", result.stderr)

    def test_kill_any_undeclared_companion_declaration_is_refused(self) -> None:
        """The declaration set is closed even for names that copy nothing:
        what makes the blacklist bypassable is that the gate cannot tell a
        harmless helper from a renamed copy, so nothing lands without a
        deliberate gate edit."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "pred scenarioLocalMachinery[n: Line] { some n.blocks }\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg="an undeclared companion declaration must be refused")
        self.assertIn("pc-219e", result.stderr)

    def test_kill_a_field_member_missing_from_the_traps_is_refused(self) -> None:
        """The growth surface lane C named: rechainedBy repeats one clause
        per Field member, so a member the enum gains and the traps never
        mention is silently unconstrained in every trap."""
        root = self.scratch_gate()
        model = root / "spec" / "peciaV2.als"
        model.write_text(model.read_text().replace(
            "enum Field { FStatus, FBlocks, FParent, FDisposed, FRetires, FContext }",
            "enum Field { FStatus, FBlocks, FParent, FDisposed, FRetires, FContext, FGrown }"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg="a Field member the traps never transfer must redden")
        self.assertIn("FGrown", result.stderr)

    GROWN_ENUM = ("enum Field { FStatus, FBlocks, FParent, FDisposed, "
                  "FRetires, FContext, FGrown }")
    SHIPPED_ENUM = ("enum Field { FStatus, FBlocks, FParent, FDisposed, "
                    "FRetires, FContext }")
    CONTEXT_CLAUSE = ("  (FContext  in t implies out.context  = mine.context  "
                      "else out.context  = landed.context)")

    CONTEXT_DERIVATION = "  + (a.context  != b.context  implies FContext  else none)"

    def grow_the_enum(self, root: Path) -> Path:
        """Grow the enum AND `fun diff` together. Since pc-d0dd the transfer
        check reads the member-to-field mapping out of `fun diff`, so an enum
        member the model derives from no comparison is itself a finding — and
        a fixture that grew only the enum would be testing that instead of
        the coverage rule these arms are about."""
        model = root / "spec" / "peciaV2.als"
        text = model.read_text()
        self.assertEqual(text.count(self.SHIPPED_ENUM), 1)
        self.assertEqual(text.count(self.CONTEXT_DERIVATION), 1)
        text = text.replace(self.SHIPPED_ENUM, self.GROWN_ENUM)
        text = text.replace(
            self.CONTEXT_DERIVATION,
            self.CONTEXT_DERIVATION
            + "\n  + (a.grown    != b.grown    implies FGrown    else none)")
        model.write_text(text)
        return root / "spec" / "pecia-v2-traps.als"

    def test_kill_a_comment_does_not_satisfy_the_field_coverage_check(self) -> None:
        """pc-7d3f (round-7 lane C-F1): the coverage check was `rg -q
        "\\bFX\\b"` over the RAW traps file — occurrence, not transfer — so a
        new conflict unit correctly refused for being transferred nowhere
        passed the moment a COMMENT mentioning it was appended, and the
        pinned-jar run over that same fixture was 16/16, 0/4, 0/3 at exit 0,
        so no layer of the gate refused. The pc-cb505 shape one check over:
        decl_pairs was comment-stripped, this was not."""
        for comment in ("// FGrown is mentioned, but no trap transfers out.grown.",
                        "-- FGrown",
                        "/* a block comment naming FGrown */"):
            with self.subTest(comment=comment):
                root = self.scratch_gate()
                traps = self.grow_the_enum(root)
                traps.write_text(traps.read_text() + "\n" + comment + "\n")
                result = self.run_static(root)
                self.assertEqual(result.returncode, 1,
                                 msg=f"a comment must not satisfy coverage: "
                                     f"{result.stdout}{result.stderr}")
                self.assertIn("FGrown", result.stderr)
                self.assertIn("pc-7d3f", result.stderr)

    def test_control_a_real_transfer_clause_satisfies_it(self) -> None:
        """The discriminating control, and the one that makes the refusal
        actionable rather than a lock: the traps grown WITH the enum — a
        real rechainedBy clause for the new member — pass."""
        root = self.scratch_gate()
        traps = self.grow_the_enum(root)
        text = traps.read_text()
        self.assertEqual(text.count(self.CONTEXT_CLAUSE), 1)
        traps.write_text(text.replace(
            self.CONTEXT_CLAUSE,
            self.CONTEXT_CLAUSE + "\n  (FGrown    in t implies out.grown    "
                                  "= mine.grown    else out.grown    = "
                                  "landed.grown)"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 0,
                         msg=f"{result.stdout}{result.stderr}")

    def test_kill_a_half_transfer_clause_does_not_satisfy_it(self) -> None:
        """Naming the member inside a clause is not transferring it either:
        a clause that reads `mine` on both sides copies the author's value
        whatever `t` says, which is trap 1's own defect written as a fix."""
        root = self.scratch_gate()
        traps = self.grow_the_enum(root)
        traps.write_text(traps.read_text().replace(
            self.CONTEXT_CLAUSE,
            self.CONTEXT_CLAUSE + "\n  (FGrown    in t implies out.grown    "
                                  "= mine.grown    else out.grown    = "
                                  "mine.grown)"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("FGrown", result.stderr)

    def test_kill_two_members_may_not_share_one_transferred_field(self) -> None:
        """The other way a clause per member can lag the enum: copy the
        FContext clause and rename only the member, and FGrown 'transfers'
        a field FContext already owns — leaving out.grown unconstrained
        while the coverage count looks complete."""
        root = self.scratch_gate()
        traps = self.grow_the_enum(root)
        traps.write_text(traps.read_text().replace(
            self.CONTEXT_CLAUSE,
            self.CONTEXT_CLAUSE + "\n  (FGrown    in t implies out.context  "
                                  "= mine.context  else out.context  = "
                                  "landed.context)"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("both transfer", result.stderr)

    STATUS_CLAUSE = ("  (FStatus   in t implies out.status   = mine.status   "
                     "else out.status   = landed.status)")
    BLOCKS_CLAUSE = ("  (FBlocks   in t implies out.blocks   = mine.blocks   "
                     "else out.blocks   = landed.blocks)")
    #: The same two clauses with the MEMBERS exchanged — FStatus transfers
    #: `blocks`, FBlocks transfers `status`. Reordering the whole clauses
    #: would change nothing, which is the distinction this fixture makes.
    SWAPPED_STATUS = ("  (FStatus   in t implies out.blocks   = mine.blocks   "
                      "else out.blocks   = landed.blocks)")
    SWAPPED_BLOCKS = ("  (FBlocks   in t implies out.status   = mine.status   "
                      "else out.status   = landed.status)")

    def swap_two_transfers(self, root: Path) -> None:
        """The record's own fixture: FStatus and FBlocks transfer each
        other's Line field. The bijection between members and fields is
        untouched — six members, six distinct fields — and two of the
        correspondences are wrong."""
        traps = root / "spec" / "pecia-v2-traps.als"
        text = traps.read_text()
        self.assertEqual(text.count(self.STATUS_CLAUSE), 1)
        self.assertEqual(text.count(self.BLOCKS_CLAUSE), 1)
        text = text.replace(self.STATUS_CLAUSE, self.SWAPPED_STATUS)
        text = text.replace(self.BLOCKS_CLAUSE, self.SWAPPED_BLOCKS)
        traps.write_text(text)

    def test_kill_swapped_transfers_are_refused(self) -> None:
        """pc-d0dd (round-8 lane C-F1): pc-7d3f moved this check from
        occurrence to transfer and stopped one step short — the test was a
        BIJECTION between enum members and Line fields and never asked
        whether FStatus transfers `status`. A traps file with the FStatus
        and FBlocks transfers SWAPPED keeps the bijection intact, keeps the
        correspondence wrong, and passed every layer of the gate: the pinned
        jar returned the same counts at exit 0 over the swapped fixture as
        over the shipped companion."""
        root = self.scratch_gate()
        self.swap_two_transfers(root)
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"swapped transfers must redden: "
                             f"{result.stdout}{result.stderr}")
        self.assertIn("pc-d0dd", result.stderr)
        self.assertIn("WRONG field", result.stderr)
        self.assertNotIn("both transfer", result.stderr,
                         msg="the bijection is intact — that is the point")

    def test_control_the_shipped_correspondence_is_what_diff_declares(self) -> None:
        """The discriminating control on the shipped files: every member's
        transfer matches the comparison `fun diff` derives it from, so the
        refusal above is about the swap and not about the check being
        unsatisfiable."""
        result = self.run_static(self.scratch_gate())
        self.assertEqual(result.returncode, 0,
                         msg=f"{result.stdout}{result.stderr}")

    DECOY_ASSERT = ("\nassert UncheckedDecoyTransfer {\n"
                    "  all t: set Field, landed, mine, out: Line |\n"
                    "  (FStatus   in t implies out.status   = mine.status   "
                    "else out.status   = landed.status)\n"
                    "}\n")

    def test_kill_a_transfer_clause_outside_the_predicate_does_not_cover(self) -> None:
        """pc-d405 (round-9 lane C-F1): the clause scan ran over the WHOLE
        comment-stripped module, so transfer-shaped text anywhere in the file
        counted as coverage. Delete the FStatus clause from `pred
        rechainedBy` and append a separate, unchecked assertion carrying the
        same text: --static exited 0 with the predicate mentioning FStatus
        nowhere, and the pinned jar returned 16/16, 0/4, 0/3 at exit 0 over
        the same fixture. Third instance of one shape, after a COMMENT
        (pc-7d3f) and a SWAP (pc-d0dd)."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        text = traps.read_text()
        self.assertEqual(text.count(self.STATUS_CLAUSE), 1)
        traps.write_text(text.replace(self.STATUS_CLAUSE + "\n", "")
                         + self.DECOY_ASSERT)
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"a decoy outside the predicate must not cover "
                             f"a member: {result.stdout}{result.stderr}")
        self.assertIn("FStatus", result.stderr)
        self.assertIn("transferred by no rechainedBy clause", result.stderr)

    def test_control_the_same_deletion_without_the_decoy_is_refused_too(self) -> None:
        """The control the record names: the check was ALIVE — deleting the
        clause with no decoy was refused before this fix and is refused now.
        What changed is only whether text outside the predicate can satisfy
        it, which is what the pair of arms isolates."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text().replace(self.STATUS_CLAUSE + "\n", ""))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("FStatus", result.stderr)

    STRING_DECOY_ASSERT = ("\nassert UncheckedDecoyTransfer {\n"
                           "  all t: set Field, landed, mine, out: Line |\n"
                           "    (FStatus in t implies out.status = mine.status "
                           "else out.status = landed.status)\n"
                           '  some "}"\n'
                           "}\n")
    PRED_HEAD = "pred rechainedBy[t: set Field, landed, mine, out: Line] {\n"

    def test_kill_a_brace_in_a_string_literal_does_not_extend_the_body(self) -> None:
        """pc-6196 (round-10 lane C-F1): pc-d405's own fix failing on its own
        new parser, and the FOURTH instance of one shape. `pred_body` counted
        `{` and `}` with no notion of a string literal, so `some "{"` inside
        the predicate raised the depth and `some "}"` inside a later,
        unchecked `assert` returned it to zero there — the extracted body ran
        past the real closing brace and swallowed the decoy, and FStatus was
        counted as covered while the predicate constrains `out.status`
        nowhere. Executed, not inferred, at the tag: the pinned jar returned
        16/16, 0/4, 0/3 at exit 0 over this fixture."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        text = traps.read_text()
        self.assertEqual(text.count(self.PRED_HEAD), 1)
        self.assertEqual(text.count(self.STATUS_CLAUSE), 1)
        text = text.replace(self.PRED_HEAD, self.PRED_HEAD + '  some "{"\n', 1)
        text = text.replace(self.STATUS_CLAUSE + "\n", "", 1)
        traps.write_text(text + self.STRING_DECOY_ASSERT)
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"a brace inside a string literal must not extend "
                             f"the predicate: {result.stdout}{result.stderr}")
        self.assertIn("FStatus", result.stderr)
        self.assertIn("transferred by no rechainedBy clause", result.stderr)

    def test_control_a_string_literal_alone_leaves_the_companions_passing(self) -> None:
        """The discriminating control the fix needs, and the one a blunter
        repair would fail: `some "{"` inside the predicate with every clause
        intact is legal Alloy and must stay green. Refusing string literals
        outright would pass the kill above and be wrong."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text().replace(
            self.PRED_HEAD, self.PRED_HEAD + '  some "{"\n', 1))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 0,
                         msg=f"{result.stdout}{result.stderr}")

    def test_kill_a_declaration_inside_a_string_literal_is_not_a_declaration(self) -> None:
        """The sibling one step earlier, in the comment stripping every check
        reads through: `decl_pairs` matched `pred foo` inside a string, so an
        ordinary literal could be refused as an undeclared companion
        declaration — the false-accusation shape pc-cb505 inverted."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text().replace(
            self.PRED_HEAD,
            self.PRED_HEAD + '  some "pred privateSmuggled"\n', 1))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 0,
                         msg=f"text inside a literal is not a declaration: "
                             f"{result.stdout}{result.stderr}")
        self.assertNotIn("privateSmuggled", result.stderr)

    def test_kill_a_comment_marker_inside_a_string_does_not_blind_the_gate(self) -> None:
        """The other half of that sibling: the stripping was two regexes, so
        a `--` inside a string literal blanked the rest of that line. Put one
        on the line before a transfer clause and the clause disappeared from
        every check that reads the stripped text — silently, and in the
        direction that loses refusals rather than inventing them."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        text = traps.read_text()
        text = text.replace(self.STATUS_CLAUSE + "\n", "", 1)
        text = text.replace(self.PRED_HEAD,
                            self.PRED_HEAD + '  some "-- ' + self.STATUS_CLAUSE.strip()
                            + '"\n', 1)
        traps.write_text(text)
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("FStatus", result.stderr)

    def test_kill_a_tree_without_the_lexer_refuses_rather_than_tracebacks(self) -> None:
        """The sibling the fix itself created, found by re-running the
        round-10 reproduction — whose case tree copies the gate and the spec
        files alone. Every structural check now reads the .als files through
        dev/alloy_lex.py, so a tree carrying the gate without it is a gate
        that cannot answer, and it said so as a Python traceback with the
        checks silently unrun. A check that cannot read its subject refuses
        by name (VP4), which is the rule the python3 guard beside it already
        followed."""
        root = self.scratch_gate()
        (root / "dev" / "alloy_lex.py").unlink()
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("alloy_lex.py is not importable", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("companion structure ok", result.stdout)

    def test_kill_a_missing_rechained_predicate_refuses_rather_than_falls_back(self) -> None:
        """A check that cannot find its subject refuses: renaming the
        predicate must not silently return the whole file's clauses."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text().replace(
            "pred rechainedBy[", "pred rechainedByRenamed[", 1))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("pc-d405", result.stderr)

    def test_kill_a_member_diff_derives_from_nothing_is_refused(self) -> None:
        """The other side of the mapping: a conflict unit the model never
        produces cannot be checked against anything, and a transfer check
        that quietly skipped it would certify whatever the traps did."""
        root = self.scratch_gate()
        model = root / "spec" / "peciaV2.als"
        model.write_text(model.read_text().replace(
            self.SHIPPED_ENUM, self.GROWN_ENUM))
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text().replace(
            self.CONTEXT_CLAUSE,
            self.CONTEXT_CLAUSE + "\n  (FGrown    in t implies out.grown    "
                                  "= mine.grown    else out.grown    = "
                                  "landed.grown)"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("derives it from no comparison", result.stderr)

    def test_kill_an_unreadable_diff_refuses_rather_than_weakens(self) -> None:
        """VP4 on the new input: without the model's mapping the check can
        only test distinctness, which a swap satisfies — so it refuses."""
        root = self.scratch_gate()
        model = root / "spec" / "peciaV2.als"
        text = model.read_text()
        self.assertIn("fun diff[a, b: Line]", text)
        model.write_text(text.replace("fun diff[a, b: Line]",
                                      "fun diffRenamedAway[a, b: Line]"))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("could not read `fun diff`", result.stderr)

    def test_the_v12_witness_carries_the_code_shaped_constraints(self) -> None:
        """pc-8a93 (round-8 lane C-F3): claim 23 said V12 'binds that
        arithmetic to the revCas over an actual log, with
        SomeRechainedCasStep keeping it non-vacuous'. Run under the pinned
        jar, that witness was satisfiable with `mine` on a DIFFERENT record
        (`rechained` never constrains mine.lid) and with `e` outside the
        log's entry set — so it did not pin the code's same-record append,
        and the coverage sentence read stronger than the model was. The
        code-shaped strengthening is itself satisfiable, which is what made
        it a gap rather than a modelling limit, and the full gate's 16/16
        is what says the strengthened run still has an instance.

        Asserted over COMMENT-STRIPPED source, per pc-7d3f: a comment
        mentioning a constraint is not the constraint."""
        model = (ROOT / "spec" / "peciaV2.als").read_text()
        model = re.sub(r'/\*.*?\*/', ' ', model, flags=re.S)
        model = re.sub(r'(?://|--).*', ' ', model)
        body = re.search(r'\brun\s+SomeRechainedCasStep\s*\{(.*?)\n\}',
                         model, re.S)
        self.assertIsNotNone(body, "the V12 witness is not in the model")
        text = body.group(1)
        for constraint in ("mine.lid = base.lid", "e not in L.es",
                           "chainCas[L, e]"):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, text,
                              msg="the witness must be the code's append: "
                                  "same record, a new entry, appended at the "
                                  "tip (pc-8a93)")

    def test_kill_a_commented_out_enum_cannot_supply_the_denominator(self) -> None:
        """The same class on the OTHER side of the check, swept here: the
        enum was read from the raw model, so a commented-out `enum Field
        { ... }` earlier in the file could have supplied the denominator.
        Commenting the real one out must fail closed (VP4), never pass with
        nothing to check."""
        root = self.scratch_gate()
        model = root / "spec" / "peciaV2.als"
        model.write_text(model.read_text().replace(
            self.SHIPPED_ENUM, "-- " + self.SHIPPED_ENUM))
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("empty denominator", result.stderr)

    def test_kill_a_traps_file_with_no_transfer_clause_fails_closed(self) -> None:
        """And the denominator on the traps side: a rechainedBy that
        transfers nothing would make every member's check fail anyway, but
        the vacuity is reported as vacuity."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        text = traps.read_text()
        for member, field in (("FStatus", "status"), ("FBlocks", "blocks"),
                              ("FParent", "parent"), ("FDisposed", "disposed"),
                              ("FRetires", "retires"), ("FContext", "context")):
            clause = (f"  ({member}{' ' * (9 - len(member))} in t implies "
                      f"out.{field}{' ' * (9 - len(field))}= mine.{field}"
                      f"{' ' * (9 - len(field))}else out.{field}"
                      f"{' ' * (9 - len(field))}= landed.{field})")
            text = text.replace(clause, f"  -- {member} removed")
        traps.write_text(text)
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg=f"{result.stdout}{result.stderr}")
        self.assertIn("vacuously", result.stderr)

    def test_control_the_enumerated_declarations_still_pass(self) -> None:
        """The closed set is not a lock: the shipped weakenings are exactly
        the enumerated names, and the shipped files pass (asserted again
        here beside the kills so the pair travels together)."""
        result = self.run_static(self.scratch_gate())
        self.assertEqual(result.returncode, 0,
                         msg=f"{result.stdout}{result.stderr}")

    def test_kill_a_line_broken_declaration_is_refused(self) -> None:
        """pc-cb505 (round-6 lane C-F1): the extraction required keyword
        and identifier on one physical line while Alloy permits whitespace
        between them — `pred` alone on one line with the identifier on the
        next evaded the closed set AND the full gate (the pinned solver
        parses the split form; the round-6 scorer ran it and the counts
        stayed green). The one-line form was already refused; both forms
        must be."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "pred\nprivateCleanLines[L: set Line] {\n"
                         + "  all h: headsL[L] | (h.blocks + h.parent) in idsL[L]\n"
                         + "}\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 1,
                         msg="the line-broken private predicate must be "
                             "refused: " + result.stdout + result.stderr)
        self.assertIn("privateCleanLines", result.stderr)

    def test_control_a_comment_naming_a_declaration_is_not_refused(self) -> None:
        """The discriminating control the widened extraction needs: with
        the line anchor gone, only comment stripping keeps a `//`, `--`
        or block comment that MENTIONS `pred somethingPrivate` from being
        refused as a declaration — the false-accusation shape, inverted."""
        root = self.scratch_gate()
        traps = root / "spec" / "pecia-v2-traps.als"
        traps.write_text(traps.read_text() + "\n"
                         + "// a comment mentioning pred commentedGhost here\n"
                         + "-- and another: fun commentedGhost too\n"
                         + "/* block form:\n   pred commentedGhost[x: Line] */\n")
        result = self.run_static(root)
        self.assertEqual(result.returncode, 0,
                         msg=f"{result.stdout}{result.stderr}")


class ClaimsStampCountGuard(unittest.TestCase):
    """pc-c1f3 (round-6 lane D-F2), the substrate half: the
    reference-implementation stamp said '657 tests discovered' against 810
    at the tag — the register-accuracy class's third recurrence, and the
    second time a stamp count rotted. The pc-e469 rule (the count lives in
    the runner's report, never in the file) is now enforced by
    claims-check over the raw lines, since YAML parsing cannot see
    comments."""

    ENTRY = ("claims:\n"
             "- id: sample\n"
             "  claim: something tested\n"
             "  tier: tested\n"
             "  evidence: python3 -m unittest tests\n"
             "  verified: '2026-09-13'{comment}\n"
             "  kill: a demonstrated kill\n")

    def run_check(self, comment: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(self.ENTRY.format(comment=comment))
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"),
                               str(path)],
                              capture_output=True, text=True, check=False)

    def test_kill_a_counting_stamp_comment_is_refused(self) -> None:
        for comment in ("  # 657 tests discovered and green",
                        "  # 10 WriteGate tests; more prose"):
            with self.subTest(comment=comment):
                result = self.run_check(comment)
                self.assertEqual(result.returncode, 1,
                                 msg=result.stdout + result.stderr)
                self.assertIn("count", result.stderr)
                self.assertIn("pc-c1f3", result.stderr)

    def test_control_a_countless_stamp_comment_passes(self) -> None:
        result = self.run_check(
            "  # suite green via the registered runner; the count lives in "
            "the runner's report (pc-e469)")
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    def test_control_the_real_ledger_passes_the_guard(self) -> None:
        result = subprocess.run([str(ROOT / "dev" / "claims-check.py")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)


class TheoremBindingsAreComplete(unittest.TestCase):
    """pc-56e7849ddc32: no test named any of the model's theorems, and the
    theorem-to-test map lived only in claims.yaml prose. spec/theorem-
    bindings.json now names, for every assertion in spec/peciaV2.als and
    spec/pecia-v2-traps.als, the tests that bind it to the code. This holds
    it to the model (exactly the model's assertions: none missing, none
    extra, GP24) and to the suite (every named test exists, GP8)."""

    REGISTRY = ROOT / "spec" / "theorem-bindings.json"

    @staticmethod
    def assertions(path: Path) -> set[str]:
        from alloy_lex import blank_noncode
        code = blank_noncode(path.read_text(encoding="utf-8"))
        return set(re.findall(r"\bassert\s+([A-Za-z_]\w*)", code))

    @staticmethod
    def resolves(ref: str) -> bool:
        kind, _, target = ref.partition(":")
        if kind == "rust":
            path, _, fn = target.partition("::")
            file = ROOT / path
            return bool(fn) and file.is_file() and re.search(
                rf"#\[test\]\s*fn\s+{re.escape(fn)}\s*\(", file.read_text()) is not None
        if kind == "python":
            *module, cls, test = target.split(".")
            file = ROOT.joinpath(*module).with_suffix(".py")
            if not file.is_file():
                return False
            import ast
            tree = ast.parse(file.read_text())
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == cls:
                    return any(isinstance(m, ast.FunctionDef) and m.name == test
                               for m in node.body)
            return False
        return False

    def problems(self, registry: dict) -> list[str]:
        out = []
        model = self.assertions(ROOT / "spec" / "peciaV2.als")
        traps = self.assertions(ROOT / "spec" / "pecia-v2-traps.als")
        for section, want in (("theorems", model), ("traps", traps)):
            have = set(registry.get(section, {}))
            out += [f"{section}: {n} is in the model and bound by nothing" for n in sorted(want - have)]
            out += [f"{section}: {n} is bound but is not in the model" for n in sorted(have - want)]
            for name, entry in registry.get(section, {}).items():
                refs = entry.get("bound_by", [])
                if not refs:
                    out.append(f"{section}: {name} names no binding")
                out += [f"{section}: {name} names {r}, which does not exist" for r in refs if not self.resolves(r)]
        return out

    def shipped(self) -> dict:
        return json.loads(self.REGISTRY.read_text())

    def test_control_the_shipped_registry_is_complete_and_resolves(self) -> None:
        self.assertEqual(self.problems(self.shipped()), [])

    def test_kill_a_theorem_bound_by_nothing_is_refused(self) -> None:
        reg = self.shipped()
        reg["theorems"].pop("ReadyBlockedPartition")
        self.assertEqual(self.problems(reg),
                         ["theorems: ReadyBlockedPartition is in the model and bound by nothing"])

    def test_kill_a_dangling_binding_is_refused(self) -> None:
        reg = self.shipped()
        reg["traps"]["BrandSurvivesRechain"]["bound_by"].append(
            "python:tests.test_pecia.SyncComposition.test_no_such_test")
        self.assertEqual(len(self.problems(reg)), 1)
        self.assertIn("does not exist", self.problems(reg)[0])

    def test_kill_a_binding_to_something_the_model_does_not_assert_is_refused(self) -> None:
        reg = self.shipped()
        reg["theorems"]["NoSuchTheorem"] = {"bound_by": reg["theorems"]["BlameContainment"]["bound_by"]}
        self.assertEqual(self.problems(reg),
                         ["theorems: NoSuchTheorem is bound but is not in the model"])


class RegisterEvidenceAndCountsAreHonest(unittest.TestCase):
    """pc-c448 and pc-d15d (round-13 lane D, F1 and F2), two register defects
    claims-check certified because it read neither shape: an evidence value
    whose second line was a verification banner, so the registered command
    exited 127 after its tests passed, and a claim stating '16/16 at this
    writing' two tags after its gate moved to 18."""

    ENTRY = ("claims:\n"
             "- id: sample\n"
             "  claim: {claim}\n"
             "  tier: tested\n"
             "  evidence: {evidence}\n"
             "  verified: '2026-09-13'\n"
             "  kill: a demonstrated kill\n")

    def run_check(self, claim: str = "something tested",
                  evidence: str = "python3 -m unittest tests"
                  ) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(self.ENTRY.format(claim=claim, evidence=evidence))
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"),
                               str(path)],
                              capture_output=True, text=True, check=False)

    def test_kill_evidence_with_a_second_line_is_refused(self) -> None:
        result = self.run_check(
            evidence='"python3 -m unittest tests\\n--- sample :: verified ---"')
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("pc-c448", result.stdout + result.stderr)

    def test_kill_a_count_stated_as_current_is_refused(self) -> None:
        result = self.run_check(
            claim="every assertion passes (16/16 at this writing)")
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("pc-d15d", result.stdout + result.stderr)

    def test_control_a_count_dated_to_a_record_passes(self) -> None:
        # The register's own idiom for history: true when written, and dated.
        result = self.run_check(
            claim="every assertion passes (18/18 at pc-dad7, 16/16 at pc-fc4c)")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_control_one_long_evidence_command_passes(self) -> None:
        # Folded YAML that parses to ONE line is one command, however long.
        result = self.run_check(
            evidence='"python3 -m unittest tests.test_a\n    tests.test_b"')
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


class RegisterSchemasAreTyped(unittest.TestCase):
    """pc-1a93 (round-7 lane D-F1), and the two registers of the same shape
    beside it — register accuracy's fourth recurrence, now at the register's
    own gates.

    claims-check tested that `claim`, `evidence` and `kill` were PRESENT and
    never what they were, so a `tested` entry whose three prose fields were
    YAML booleans was certified "all entries well-formed" at exit 0 — one
    field away from an invalid tier, which IS refused. The gate registry and
    the spec's vocabulary markers are the same untyped schema one file over,
    and they fail in the other direction as well: a mistyped field reaches a
    comparison or a set-membership test and comes out as a TypeError
    traceback from a registered commit gate, where the contract says a
    malformed input is a finding, never a crash.

    Every arm below is paired with the discriminating control — the same
    fixture with the field well-typed — because an arm that only reddens
    cannot tell "the type check fired" from "the fixture was broken".
    """

    ENTRY = ("claims:\n"
             "- id: sample\n"
             "  claim: {claim}\n"
             "  tier: tested\n"
             "  evidence: {evidence}\n"
             "  verified: '2026-09-13'\n"
             "  kill: {kill}\n")

    def claims_check(self, **fields: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(self.ENTRY.format(
            claim=fields.get("claim", "a real sentence"),
            evidence=fields.get("evidence", "python3 -m unittest tests"),
            kill=fields.get("kill", "a demonstrated kill")))
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"), str(path)],
                              capture_output=True, text=True, check=False)

    def test_kill_boolean_claim_fields_are_refused(self) -> None:
        result = self.claims_check(claim="true", evidence="true", kill="true")
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        for field in ("claim", "evidence", "kill"):
            self.assertIn(f"`{field}` must be text", result.stderr,
                          msg="each mistyped field is named, so a boolean "
                              "claim beside a boolean kill reports both")
        self.assertIn("pc-1a93", result.stderr)

    def test_control_the_same_entry_with_text_fields_passes(self) -> None:
        result = self.claims_check()
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    def test_control_the_real_ledger_stays_clean_under_the_typing(self) -> None:
        """The tightening must not redden the register it guards."""
        result = subprocess.run([str(ROOT / "dev" / "claims-check.py")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    # -- v2.16, pc-d4c3: the stamp against the entry's own dated text -------

    DATED = ("claims:\n"
             "- id: sample\n"
             "  claim: a real sentence\n"
             "  tier: tested\n"
             "  evidence: python3 -m unittest tests\n"
             "  verified: '{verified}'\n"
             "  kill: a demonstrated kill\n"
             "  notes: '{notes}'\n")

    def dated_check(self, verified: str, notes: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(self.DATED.format(verified=verified, notes=notes))
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"), str(path)],
                              capture_output=True, text=True, check=False)

    def test_kill_a_stamp_older_than_its_own_correction_is_refused(self) -> None:
        """pc-d4c3 (round-11 lane D-F3): reference-implementation carried
        `verified: 2026-09-13` beside two corrections its own notes date
        2026-09-14. A suite stamp cannot cover corrections it postdates, and
        nothing read dates against dates."""
        result = self.dated_check(
            "2026-09-13", "CORRECTED 2026-09-14 (pc-xxxx): a later statement.")
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("cannot cover what it predates", result.stderr)
        self.assertIn("pc-d4c3", result.stderr)

    def test_control_a_stamp_at_or_after_its_own_dates_passes(self) -> None:
        """Both boundaries, because an ordering rule that refuses equality
        would force a re-stamp for every same-day correction — which is the
        ordinary case in this register."""
        for verified in ("2026-09-14", "2026-09-15"):
            with self.subTest(verified=verified):
                result = self.dated_check(
                    verified,
                    "CORRECTED 2026-09-14 (pc-xxxx): a statement.")
                self.assertEqual(result.returncode, 0,
                                 msg=result.stdout + result.stderr)
        # Asserted again outside subTest: a failure inside one is invisible
        # to this repo's runner at exit 0 (pc-c8ca).
        same_day = self.dated_check(
            "2026-09-14", "CORRECTED 2026-09-14 (pc-xxxx): a statement.")
        self.assertEqual(same_day.returncode, 0,
                         msg=same_day.stdout + same_day.stderr)

    def test_control_an_entry_with_no_dated_text_is_unaffected(self) -> None:
        result = self.dated_check("2026-01-01", "no dates here at all.")
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    # -- v2.16, pc-9611: a stamp comment carries no count at all -----------

    STAMPED = ("claims:\n"
               "- id: sample\n"
               "  claim: a real sentence\n"
               "  tier: tested\n"
               "  evidence: python3 -m unittest tests\n"
               "  verified: '2026-09-18'  # {comment}\n"
               "  kill: a demonstrated kill\n")

    def stamp_check(self, comment: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(self.STAMPED.format(comment=comment))
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"), str(path)],
                              capture_output=True, text=True, check=False)

    def test_kill_a_stamp_comment_carrying_a_marker_count_is_refused(self) -> None:
        """pc-9611 (round-11 lane D-F4): the pc-c1f3 rule caught `N tests`
        and nothing else, so `registry regenerated, 90 markers` sat in a
        stamp comment while the gate and the generated registry both said 91
        — the same defect one noun over, in the same field, under a rule
        that already existed to stop it."""
        result = self.stamp_check("registry regenerated, 90 markers")
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("carries a count", result.stderr)
        self.assertIn("pc-9611", result.stderr)

    def test_kill_the_original_test_count_shape_is_still_refused(self) -> None:
        """The widening must not lose what the rule already caught."""
        for comment in ("58 tests green", "re-taken after 1 test moved"):
            result = self.stamp_check(comment)
            self.assertEqual(result.returncode, 1,
                             msg=f"{comment!r}: {result.stdout}{result.stderr}")

    def test_control_a_stamp_comment_without_a_count_passes(self) -> None:
        """The false-red direction, which is where this rule can do damage:
        a stamp comment's ordinary content is a date, a record id and a
        version, and none of them is a count."""
        for comment in ("re-taken 2026-09-18 after the pc-4b3c refusal",
                        "re-taken 2026-09-18 after the v2.16 amendments",
                        "green via the registered runner, suite once"):
            result = self.stamp_check(comment)
            self.assertEqual(result.returncode, 0,
                             msg=f"{comment!r}: {result.stdout}{result.stderr}")

    def test_control_a_version_number_is_not_read_as_a_date(self) -> None:
        """The false-red direction: `v2.16` and a record id carry digits, and
        the pattern is anchored on the century so neither can be one."""
        result = self.dated_check(
            "2026-01-01", "amended at v2.16 under pc-1206, see 12-34-56.")
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    # -- the gate registry, same shape (sibling, same commit) ----------------

    def audit_registry(self, mutate) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        reg = json.loads((ROOT / "dev" / "gates.json").read_text())
        mutate(reg)
        path = Path(td.name) / "registry.json"
        path.write_text(json.dumps(reg))
        return subprocess.run(
            [sys.executable, str(RUNNER), "--audit", "--registry", str(path)],
            cwd=str(ROOT), capture_output=True, text=True, check=False)

    def test_kill_a_mistyped_registry_is_a_finding_not_a_traceback(self) -> None:
        """All four measured on the shipped registry before the fix: `why:
        true` audited OK at exit 0 (certified, the pc-1a93 shape exactly),
        and the other three died with a TypeError traceback."""
        cases = {
            "id": lambda r: r["gates"][0].__setitem__("id", True),
            "staged_argv": lambda r: r["gates"][0].__setitem__("staged_argv", True),
            "why": lambda r: r["gates"][0].__setitem__("why", True),
            "gates": lambda r: r.__setitem__("gates", True),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field):
                result = self.audit_registry(mutate)
                self.assertEqual(result.returncode, 2,
                                 msg=result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr,
                                 msg="a malformed registry is a finding")
                self.assertIn(f"`{field}`", result.stderr)

    def test_kill_an_undeclared_registry_field_is_refused(self) -> None:
        """The typo's home: `precomit: false` is not a disabled gate, it is a
        key nothing reads — and the entry it sits in stays precommit:true."""
        result = self.audit_registry(
            lambda r: r["gates"][0].__setitem__("precomit", False))
        self.assertEqual(result.returncode, 2,
                         msg=result.stdout + result.stderr)
        self.assertIn("undeclared field `precomit`", result.stderr)

    def test_control_the_shipped_registry_audits_clean(self) -> None:
        result = self.audit_registry(lambda r: None)
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    # -- the vocabulary markers, same shape (sibling, same commit) -----------

    # A well-typed companion marker rides along so the census stays non-zero
    # when the marker under test is refused: a fixture whose ONLY marker is
    # rejected trips the separate zero-census fatal (V000) and would measure
    # that instead of the typing.
    MARKER = ('- E002 the companion code\n'
              '  <!-- vocab: {{"action":"mint","code":"E002","at":"v1.1",'
              '"by":"pc-bbbb"}} -->\n'
              '- E001 the first code\n'
              '  <!-- vocab: {{"action":{action},"code":{code},'
              '"at":{at},"by":"pc-aaaa"}} -->\n')

    def vocab_check(self, **fields: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        spec, source, registry = d / "spec.md", d / "src.py", d / "vocab.json"
        spec.write_text("# fixture spec\n\n## v1.1 amendments (fixture)\n\n"
                        + self.MARKER.format(
                            action=fields.get("action", '"mint"'),
                            code=fields.get("code", '"E001"'),
                            at=fields.get("at", '"v1.1"')))
        source.write_text('def finding(sev, code, rid, msg): return {}\n'
                          'def go():\n'
                          '    finding("error", "E001", None, "boom")\n'
                          '    finding("error", "E002", None, "boom")\n')
        return subprocess.run(
            [sys.executable, str(ROOT / "dev" / "vocab-check.py"),
             "--spec", str(spec), "--source", str(source),
             "--registry", str(registry)],
            capture_output=True, text=True, check=False)

    def test_kill_a_mistyped_marker_field_is_a_finding_not_a_traceback(self) -> None:
        """`{"at":["v1.1"]}` reached a set-membership test and died
        `TypeError: unhashable type: 'list'` — a traceback out of a
        registered commit gate."""
        for field, value in (("at", '["v1.1"]'), ("code", "true"),
                             ("action", "true")):
            with self.subTest(field=field):
                result = self.vocab_check(**{field: value})
                self.assertEqual(result.returncode, 1,
                                 msg=result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("V001", result.stdout)
                self.assertIn(repr(field), result.stdout)

    def test_control_a_well_typed_marker_passes(self) -> None:
        """The discriminating control: the identical fixture with string
        values seeds and verifies clean, so the arms above measure the type
        and not the fixture."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        result = self.vocab_check()
        # A first run writes no registry, so it reports the stale registry
        # (V010) and nothing else — never V001.
        self.assertNotIn("V001", result.stdout,
                         msg=result.stdout + result.stderr)


class TheTwoRegistersAreJoined(unittest.TestCase):
    """pc-fc37 (round-7 lane D-F2): README says the state of every project
    claim lives in claims.yaml "and nowhere else", and the editor-debris
    commit gate had its state, its red case and its passing tests only in
    dev/gates.json and tests/test_pecia.py. Neither claims-check nor
    `gates.py --audit` compared the two registries, so both audited clean
    over the split for as long as it existed — register accuracy's fourth
    consecutive round, here as a gap BETWEEN two registers rather than
    inside one.

    Six of the seven inline hook gates already carried a claim entry, which
    is what settles the scope question the finding poses: inline gates are
    not out of scope. So every gate now names the claim it enforces (or
    declares why it enforces none) and the audit resolves that name against
    the ledger. The check found two more gaps the moment it ran —
    prose-check, one of the four gates in the hook's own floor, and the
    publisher — both registered in the same commit.
    """

    def audit(self, mutate_registry=None, claims: str | None = None,
              write_claims: bool = True) -> subprocess.CompletedProcess[str]:
        """Audit a (possibly mutated) registry against a (possibly
        substituted) claims ledger, from the REAL repo root — every other
        arm of this audit (dangling entries, phantom red-cases) resolves
        against the tree, so a bare fixture root would measure those
        instead. Only the two registers are substituted, by path."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        reg = json.loads((ROOT / "dev" / "gates.json").read_text())
        if mutate_registry:
            mutate_registry(reg)
        registry = d / "gates.json"
        registry.write_text(json.dumps(reg))
        ledger = d / "claims.yaml"
        if write_claims:
            ledger.write_text((ROOT / "claims.yaml").read_text()
                              if claims is None else claims)
        return subprocess.run(
            [sys.executable, str(RUNNER), "--audit",
             "--registry", str(registry), "--claims", str(ledger)],
            cwd=str(ROOT), capture_output=True, text=True, check=False)

    def test_kill_a_gate_naming_no_claim_is_refused(self) -> None:
        """The record's own condition: hook-editor-debris as it stood — a
        live, tested commit gate with no tier in the register."""
        result = self.audit(lambda r: [g.pop("claim", None)
                                       for g in r["gates"]
                                       if g["id"] == "hook-editor-debris"])
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("GATE OUTSIDE THE REGISTER", result.stderr)
        self.assertIn("hook-editor-debris", result.stderr)

    def test_kill_a_gate_naming_a_claim_the_ledger_lacks_is_refused(self) -> None:
        """The other direction of the join: deleting the entry is as much a
        split as never writing it, and the gate keeps running either way."""
        ledger = (ROOT / "claims.yaml").read_text()
        head, _, rest = ledger.partition("- id: hook-editor-debris\n")
        self.assertTrue(rest, "fixture anchor: the entry must exist")
        _, _, after = rest.partition("\n- id: ")
        result = self.audit(claims=head + "- id: " + after)
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIM NOT IN THE LEDGER", result.stderr)
        self.assertIn("hook-editor-debris", result.stderr)

    def test_kill_an_unreadable_ledger_does_not_pass_vacuously(self) -> None:
        """A cross-register check that green-lights when one register is
        unreadable has replaced a split with a blind spot."""
        result = self.audit(claims="claims: []\n")
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIMS LEDGER UNREADABLE", result.stderr)

    def test_kill_a_ledger_absent_everywhere_is_a_finding(self) -> None:
        """Absent from the index AND the worktree: every gate's claim
        resolves nowhere, which is not a registration."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "repo"
        (root / "dev").mkdir(parents=True)
        (root / "dev" / "gates.json").write_text(
            (ROOT / "dev" / "gates.json").read_text())
        git(["init", "-q"], root)
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--audit",
             "--registry", str(root / "dev" / "gates.json")],
            cwd=str(root), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIMS LEDGER MISSING", result.stderr)

    def test_control_a_deadopted_ledger_still_resolves_from_the_worktree(self) -> None:
        """The discriminating control, and the arm that caught the first
        version of this check: a repository that de-adopted claims.yaml
        (`git rm --cached`, which the canonical-deletion gate makes a
        deliberate --no-verify act) keeps its worktree copy, and its
        ordinary commits must not be blocked. The join resolves against the
        worktree, exactly as the input materializer already does for an
        input present in HEAD but not staged — demanding the ledger here
        would duplicate a guard that exists and redden its own control."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "repo"
        (root / "dev").mkdir(parents=True)
        (root / "dev" / "gates.json").write_text(
            (ROOT / "dev" / "gates.json").read_text())
        (root / "claims.yaml").write_text((ROOT / "claims.yaml").read_text())
        git(["init", "-q"], root)
        git(["add", "dev/gates.json"], root)   # claims.yaml deliberately not
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--audit", "--index",
             "--registry", str(root / "dev" / "gates.json")],
            cwd=str(root), capture_output=True, text=True, check=False)
        self.assertNotIn("CLAIMS LEDGER", result.stderr,
                         msg=result.stdout + result.stderr)
        self.assertNotIn("CLAIM NOT IN THE LEDGER", result.stderr)

    def test_control_a_declared_reason_stands_in_for_a_claim(self) -> None:
        """The discriminating control, and the escape the finding's own
        wording offers: a gate that enforces no project claim may say so —
        but it must SAY so, in the registry, where the audit reads it."""
        def declare(reg):
            for g in reg["gates"]:
                if g["id"] == "hook-editor-debris":
                    g["claim"] = None
                    g["claim_why_not"] = ["fixture: hygiene, asserts nothing"]
        result = self.audit(declare)
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    def test_control_the_shipped_pair_joins_cleanly(self) -> None:
        """Both registers as they ship: every gate names a claim the ledger
        carries. This is the arm that would have been red before the three
        entries this commit registered."""
        result = self.audit()
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)

    def test_control_every_shipped_gate_names_a_claim(self) -> None:
        """Stated as its own assertion so the join cannot be satisfied by a
        registry that quietly declares its way out of it."""
        reg = json.loads((ROOT / "dev" / "gates.json").read_text())
        unclaimed = sorted(g["id"] for g in reg["gates"] if not g.get("claim"))
        self.assertEqual(unclaimed, [],
                         msg="a shipped gate with no claim must be a "
                             "deliberate, reviewed declaration")

    # -- the join reads the register, not the file (pc-3472) ----------------

    DECOY = ("\ndecoys:\n"
             "- id: phantom-claim\n"
             "  claim: \"a valid YAML mapping that is NOT the claims sequence\"\n")

    def test_kill_an_id_outside_the_claims_sequence_does_not_satisfy_the_join(self) -> None:
        """pc-3472 (round-9 lane D-F1): the ids were derived with a file-wide
        `^- id:` regex while claims-check parses only `ledger["claims"]`, so
        a `decoys:` block carrying the name made a gate that names no claim
        pass --audit at exit 0 with the register reported well-formed."""
        result = self.audit(
            lambda r: [g.update(claim="phantom-claim") for g in r["gates"]
                       if g["id"] == "hook-editor-debris"],
            claims=(ROOT / "claims.yaml").read_text() + self.DECOY)
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("CLAIM NOT IN THE LEDGER", result.stderr)
        self.assertIn("phantom-claim", result.stderr)

    def test_control_the_same_id_inside_the_sequence_does_satisfy_it(self) -> None:
        """The discriminating control: the identical id, identical gate, and
        the only difference is which YAML structure carries it. Without this
        the kill cannot tell "the decoy was rejected" from "the name was"."""
        ledger = (ROOT / "claims.yaml").read_text()
        real = ledger.replace("- id: v2-storage\n",
                              "- id: phantom-claim\n"
                              "  claim: \"a real entry in the claims sequence\"\n"
                              "  tier: asserted\n"
                              "  evidence: 'true'\n"
                              "  verified: never\n"
                              "  kill: none\n"
                              "- id: v2-storage\n", 1)
        result = self.audit(
            lambda r: [g.update(claim="phantom-claim") for g in r["gates"]
                       if g["id"] == "hook-editor-debris"],
            claims=real)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_kill_a_ledger_with_no_claims_key_is_unreadable_not_empty(self) -> None:
        """The scan says WHY it cannot answer rather than returning an empty
        register: a file of decoys and no `claims:` key must not audit clean
        the way an empty sequence would have."""
        result = self.audit(claims=self.DECOY.lstrip("\n"))
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        self.assertIn("CLAIMS LEDGER UNREADABLE", result.stderr)
        self.assertIn("no top-level `claims:` key", result.stderr)


#: dev/dogfood-evidence.py and the three M3 friction logs it reads are all
#: withheld from the public mirror (dev/publish-manifest.json says why: the
#: logs are sibling-project detail, and a checker pointed at files that are
#: not there is worse than no checker). This file DOES ship, because the gate
#: registry names its red-case arms — so the class below is skipped where its
#: apparatus is absent rather than failing in every public clone, which is the
#: same decision tests/test_publisher.py's withholding makes one file over.
_DOGFOOD_APPARATUS = (
    (ROOT / "dev" / "dogfood-evidence.py").exists()
    and all((ROOT / "research" / name).exists() for name in
            ("M3-axiom-friction.md", "M3-axiomdb-friction.md",
             "M3-chorusmith-friction.md")))


@unittest.skipUnless(_DOGFOOD_APPARATUS,
                     "the dogfood evidence checker and its inputs are private")
class DogfoodEvidenceDiscriminates(unittest.TestCase):
    """pc-9d9d (round-9 lane D-F3): the `dogfood` entry's registered evidence
    was `python3 -c "... read_text() for p in [three paths]"` — a PRESENCE
    check standing as the evidence for a `tested` tier whose claim is about
    pre-registration, unamended verdicts and per-repo checker findings. It
    exits 0 over three files reading "not evidence" and over three EMPTY
    files, and exits 1 only when a path is missing.

    Two moves, because narrowing is the floor and extending the evidence is
    the ceiling: the claim now stands at `asserted` (the arms ran against
    other repositories at a past state and cannot be re-run here), and its
    evidence is `dev/dogfood-evidence.py`, which holds the published record
    to what the claim says — each arm's report naming its arm, repo and
    execution date; the pre-registration it cites existing in THIS
    repository's timeline and dated no later than the arm; the
    verdict-bearing sentences still present, including the negative one.

    The arms below are the red cases the old command could not produce."""

    CHECKER = ROOT / "dev" / "dogfood-evidence.py"
    REPORTS = ("M3-axiom-friction.md", "M3-axiomdb-friction.md",
               "M3-chorusmith-friction.md")

    def reports_copy(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for name in self.REPORTS:
            shutil.copy(ROOT / "research" / name, d / name)
        return d

    def run_checker(self, reports: Path | None = None,
                    ledger: Path | None = None) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, str(self.CHECKER), "--json"]
        if reports is not None:
            argv += ["--reports", str(reports)]
        if ledger is not None:
            argv += ["--ledger", str(ledger)]
        return subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                              text=True, check=False)

    def test_control_the_published_record_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["findings"], [])

    def test_kill_emptied_reports_are_refused(self) -> None:
        """The old command's own blind spot, run against the new one: three
        empty files exited 0 there."""
        d = self.reports_copy()
        for name in self.REPORTS:
            (d / name).write_text("")
        result = self.run_checker(reports=d)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertGreaterEqual(len(json.loads(result.stdout)["findings"]), 3)

    def test_kill_a_flipped_verdict_is_refused(self) -> None:
        """The arm the claim leans on hardest: the published NEGATIVE verdict
        for the repo that already had a checker."""
        d = self.reports_copy()
        path = d / "M3-axiomdb-friction.md"
        path.write_text(path.read_text().replace(
            "Negative on the headline question", "Positive on the headline question"))
        result = self.run_checker(reports=d)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        findings = json.loads(result.stdout)["findings"]
        self.assertTrue(any("Negative on the headline question" in f for f in findings),
                        msg=findings)

    def test_kill_a_re_dated_report_is_refused(self) -> None:
        d = self.reports_copy()
        path = d / "M3-chorusmith-friction.md"
        path.write_text(path.read_text().replace("2026-08-04", "2026-09-01"))
        result = self.run_checker(reports=d)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertTrue(any("execution date" in f
                            for f in json.loads(result.stdout)["findings"]))

    def test_kill_a_pre_registration_dated_after_its_arm_is_refused(self) -> None:
        """The property the word "pre-registered" carries, checked against
        this repository's own timeline rather than the report's say-so."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        ledger = Path(td.name) / "work.jsonl"
        lines = []
        for line in (ROOT / ".pecia" / "work.jsonl").read_text().split("\n"):
            if line.strip():
                rec = json.loads(line)
                if rec.get("id") == "pc-3873":
                    rec["created"] = "2026-08-09"   # after the arm ran
                lines.append(json.dumps(rec, sort_keys=True))
        ledger.write_text("\n".join(lines) + "\n")
        result = self.run_checker(ledger=ledger)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertTrue(any("AFTER the arm ran" in f
                            for f in json.loads(result.stdout)["findings"]))

    def test_kill_a_missing_pre_registration_record_is_refused(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        ledger = Path(td.name) / "work.jsonl"
        ledger.write_text("".join(
            line + "\n" for line in
            (ROOT / ".pecia" / "work.jsonl").read_text().splitlines()
            if line.strip() and json.loads(line).get("id") != "pc-48e2"))
        result = self.run_checker(ledger=ledger)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertTrue(any("pc-48e2" in f
                            for f in json.loads(result.stdout)["findings"]))

    def test_control_the_registered_evidence_command_is_this_checker(self) -> None:
        """The register and the checker are joined: the entry's evidence must
        BE this command, or the kills above test something nothing runs."""
        text = (ROOT / "claims.yaml").read_text()
        entry = text[text.index("- id: dogfood"):]
        entry = entry[:entry.index("\n- id: ")]
        self.assertIn("dev/dogfood-evidence.py", entry)
        self.assertIn("tier: asserted", entry,
                      msg="the claim is a report of history; `tested` is the "
                          "tier this evidence cannot carry")


class ClaimsResolverReadsTheRegister(unittest.TestCase):
    """The sibling of pc-3472 found by the same question, one file over:
    `dev/claims-ref.py` — the resolver for the `claims:<id>` foreign-reference
    scheme (E011) — matched `- id:` at ANY indentation anywhere in the file,
    so a name under any other key resolved a reference claims-check never
    sees. Exit 0 from it licenses one sentence: "the claims REGISTER carries
    an entry with this id", and the register is the `claims:` sequence.

    Both readers now share dev/claims_ids.py, because the way the defect got
    in was two readers each deciding for themselves what an id is."""

    RESOLVER = ROOT / "dev" / "claims-ref.py"

    def resolve(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.RESOLVER), name],
                              cwd=str(ROOT), capture_output=True, text=True,
                              check=False)

    def test_kill_an_id_outside_the_claims_sequence_does_not_resolve(self) -> None:
        ids, why_not = CLAIM_IDS.claim_ids(
            (ROOT / "claims.yaml").read_text()
            + "\ndecoys:\n- id: phantom-claim\n")
        self.assertIsNone(why_not)
        self.assertNotIn("phantom-claim", ids)
        self.assertEqual(self.resolve("phantom-claim").returncode, 1)

    def test_kill_a_nested_id_does_not_resolve(self) -> None:
        """The resolver's regex was `^\\s*-\\s*id:`, which matched an item of
        any nested sequence as well — a strictly wider hole than the audit's,
        since a claim's own prose could carry one."""
        ids, why_not = CLAIM_IDS.claim_ids(
            "claims:\n- id: real\n  claim: \"x\"\n"
            "other:\n  inner:\n  - id: nested-decoy\n")
        self.assertIsNone(why_not)
        self.assertEqual(ids, {"real"})

    def test_control_a_real_claim_still_resolves(self) -> None:
        result = self.resolve("v2-storage")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_control_the_shipped_register_reads_as_the_checker_reads_it(self) -> None:
        """The two readers agree on the shipped file — the equality the
        file-wide regex broke, measured rather than assumed (28 ids seen,
        27 claims parsed)."""
        ids, why_not = CLAIM_IDS.claim_ids((ROOT / "claims.yaml").read_text())
        self.assertIsNone(why_not)
        parsed = subprocess.run([str(ROOT / "dev" / "claims-check.py")],
                                cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, msg=parsed.stdout + parsed.stderr)
        count = int(re.search(r"\((\d+) claims,", parsed.stdout).group(1))
        self.assertEqual(len(ids), count,
                         msg="the join's id set must be the checker's claim set")

    def test_kill_a_register_that_cannot_be_read_refuses(self) -> None:
        """A resolver that cannot answer refuses; it does not report the
        register as empty (pc-39f6's rule on this seam)."""
        ids, why_not = CLAIM_IDS.claim_ids("claims: [a, b]\n")
        self.assertEqual(ids, set())
        self.assertIn("not a block sequence", why_not)


class TheRegisterSaysOneThingToEveryReader(unittest.TestCase):
    """pc-4b3c (round-10 lane D-F1): pc-3472's own fix, reached by the route
    its docstring did not consider.

    That fix bounded WHICH lines carry an id — "a sequence item at column 0
    between the top-level `claims:` key and the next top-level key" — and
    said nothing about HOW MANY `claims:` keys a file may have. With two of
    them the raw scan reads the UNION of both blocks and PyYAML gives the
    LATER key authority, so `gates.py --audit` accepted a gate naming
    `phantom-claim` while `claims-check.py` reported the shipped 27 claims
    and never saw the id — both at exit 0, the seventh consecutive round in
    which the two registers disagree about what the register says.

    The divergence is last-key-wins versus union, and there is no reading
    that makes both right. So neither reader picks a winner: a repeated key
    is a malformation, and each reader refuses it in the terms it can see —
    dev/claims_ids.py the duplicate top-level `claims:` key (no parser, so
    column 0 is the only structure it can trust), dev/claims_yaml.py a
    repeated key at any depth (a parser, so nesting is visible)."""

    DUPLICATE_BLOCK = (
        "claims:\n"
        "- id: phantom-claim\n"
        "  claim: overwritten by the later top-level `claims:` key\n"
        "  tier: asserted\n"
        "  evidence: none\n"
        "  verified: never\n"
        "  kill: none\n"
    )

    def shipped(self) -> str:
        return (ROOT / "claims.yaml").read_text()

    def audit(self, claims: str) -> subprocess.CompletedProcess[str]:
        """The defect's own shape: gate 0 renamed to a claim only the union
        carries, audited against a substituted register."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        reg = json.loads((ROOT / "dev" / "gates.json").read_text())
        reg["gates"][0]["claim"] = "phantom-claim"
        registry = d / "gates.json"
        registry.write_text(json.dumps(reg))
        ledger = d / "claims.yaml"
        ledger.write_text(claims)
        return subprocess.run(
            [sys.executable, str(RUNNER), "--audit",
             "--registry", str(registry), "--claims", str(ledger)],
            cwd=str(ROOT), capture_output=True, text=True, check=False)

    def claims_check(self, claims: str) -> subprocess.CompletedProcess[str]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "claims.yaml"
        path.write_text(claims)
        return subprocess.run([str(ROOT / "dev" / "claims-check.py"), str(path)],
                              cwd=str(ROOT), capture_output=True, text=True,
                              check=False)

    def test_kill_two_top_level_claims_keys_are_refused_by_the_scan(self) -> None:
        ids, why_not = CLAIM_IDS.claim_ids(self.DUPLICATE_BLOCK + self.shipped())
        self.assertEqual(ids, set(),
                         msg="a reader that cannot answer returns no ids")
        self.assertIn("top-level `claims:` keys", why_not)
        self.assertIn("pc-4b3c", why_not)

    def test_kill_the_audit_refuses_the_duplicate_key_register(self) -> None:
        """The defect arm of the record's reproduction: this exited 0
        "registry audit: ok" over a gate naming a claim the parsed register
        does not carry."""
        result = self.audit(self.DUPLICATE_BLOCK + self.shipped())
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIMS LEDGER UNREADABLE", result.stderr)
        self.assertIn("top-level `claims:` keys", result.stderr)

    def test_kill_claims_check_refuses_the_duplicate_key_register(self) -> None:
        """The other reader, which used to certify the same file at exit 0
        while reporting the shipped count."""
        result = self.claims_check(self.DUPLICATE_BLOCK + self.shipped())
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("malformed", result.stderr)
        self.assertIn("`claims` is given twice", result.stderr)

    def test_kill_a_repeated_key_inside_an_entry_is_refused(self) -> None:
        """The nested sibling, met by the round-10 scorer by accident while
        building an unrelated control: a `tier:` key added above an entry's
        own was silently discarded, which is the same last-key-wins drop one
        level down. The raw scan cannot see this one; the parser can."""
        text = self.shipped().replace("- id: write-gate\n",
                                      "- id: write-gate\n  tier: asserted\n", 1)
        result = self.claims_check(text)
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("`tier` is given twice", result.stderr)

    def test_control_the_shipped_register_is_read_by_both_readers(self) -> None:
        """Discriminating control: the whole tightening is worthless if it
        also refuses the real file. Both readers take it, and agree on the
        count."""
        ids, why_not = CLAIM_IDS.claim_ids(self.shipped())
        self.assertIsNone(why_not)
        result = self.claims_check(self.shipped())
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)
        count = int(re.search(r"\((\d+) claims,", result.stdout).group(1))
        self.assertEqual(len(ids), count)

    def test_control_the_join_is_alive_over_the_shipped_register(self) -> None:
        """The same phantom registry against the real register is refused by
        name — so the audit's exit 1 above is the join firing, not the
        register having become unreadable for some unrelated reason."""
        result = self.audit(self.shipped())
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIM NOT IN THE LEDGER", result.stderr)
        self.assertIn("phantom-claim", result.stderr)

    def test_control_the_round_9_decoys_route_stays_closed(self) -> None:
        """pc-3472 has not regressed: an id under a different top-level key
        is still outside the register, and still refused."""
        result = self.audit(self.shipped() + "\ndecoys:\n- id: phantom-claim\n")
        self.assertEqual(result.returncode, 1,
                         msg=result.stdout + result.stderr)
        self.assertIn("CLAIM NOT IN THE LEDGER", result.stderr)


class StagedDeletionSubstitution(unittest.TestCase):
    """ROUND 6, and the only class so far that needs no registry edit at all.

    `git rm --cached PLAN.md` leaves the worktree copy in place. materialise()
    then found the index lookup failed and substituted the worktree file, so
    prose-check validated a PLAN.md that the commit removes — and its own
    referral-integrity check, which exists precisely to catch a referral to a
    file that does not exist, resolved `target.exists()` against the
    substitute. The F7/F13 staged-versus-worktree class through the data plane
    instead of the config.

    This is also the one class that is a plausible ACCIDENT rather than an
    attack, which is why it earns arms and the config-tampering classes are
    arguable. `git rm --cached` is a thing people type.
    """

    def _repo(self, tmp: Path):
        d = tmp / "repo"
        (d / "dev").mkdir(parents=True)
        gate_file = d / "dev" / "x-check.py"
        gate_file.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(0)\n")
        gate_file.chmod(0o755)
        (d / "subject.txt").write_text("real\n")
        git(["init", "-q"], d)
        git(["config", "user.email", "t@t"], d)
        git(["config", "user.name", "t"], d)
        git(["add", "-A"], d)
        git(["commit", "-q", "-m", "base"], d)
        return d

    def _reg(self):
        return {"discovery": {"globs": []},
                "gates": [{"id": "x-check", "why": "x",
                           "argv": ["{top}/dev/x-check.py"],
                           "staged_argv": ["{top}/dev/x-check.py", "{staged}/subject.txt"],
                           "inputs": ["subject.txt"], "triggers": None,
                           "precommit": True, "redcase": None}]}

    def _run(self, d: Path, tmp: Path):
        rp = tmp / "r.json"
        rp.write_text(json.dumps(self._reg()))
        return subprocess.run(
            [sys.executable, str(RUNNER), "--precommit",
             "--staged-root", str(tmp / "staged"), "--registry", str(rp)],
            cwd=d, capture_output=True, text=True)

    def test_kill_an_input_removed_from_the_index_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._repo(Path(td))
            git(["rm", "--cached", "-q", "subject.txt"], d)
            self.assertTrue((d / "subject.txt").exists(), "worktree copy is the bait")
            r = self._run(d, Path(td))
        self.assertEqual(r.returncode, 1, "a gate must not validate a file the commit removes")
        self.assertIn("not in the index", r.stderr)

    def test_control_the_same_repo_untouched_passes(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._repo(Path(td))
            r = self._run(d, Path(td))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_control_a_file_never_tracked_skips_rather_than_refusing(self):
        """The half the first fix got wrong: a repo that simply has no such
        file has no subject for the gate, and refusing every commit there is
        not a safety property."""
        with tempfile.TemporaryDirectory() as td:
            d = self._repo(Path(td))
            git(["rm", "--cached", "-q", "subject.txt"], d)
            git(["commit", "-q", "-m", "drop it"], d)
            (d / "subject.txt").unlink()
            r = self._run(d, Path(td))
        self.assertEqual(r.returncode, 0, r.stderr)


class DeletionGuardIsDerived(unittest.TestCase):
    """pc-4539: the staged-deletion guard named four paths while the pin named
    nine. It now derives its set, so the two cannot drift.

    The arm that matters is the third: a path that was NOT in the old
    hardcoded four must now be guarded. Without it these tests would pass
    against the old hook.
    """

    def test_canonical_paths_matches_the_pin_exactly(self):
        r = subprocess.run([sys.executable, str(RUNNER), "--canonical-paths"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        emitted = {ln for ln in r.stdout.splitlines() if ln.strip()}
        expected = {rel for paths in PIN.values() for rel in paths}
        self.assertEqual(emitted, expected)

    def test_the_hook_derives_rather_than_restates(self):
        """A hardcoded list in the hook is the failure this record is about, so
        assert the hook does not carry one."""
        hook = (ROOT / "dev" / "hooks" / "pre-commit").read_text()
        self.assertIn("--canonical-paths", hook)
        self.assertNotIn(
            "for canonical_file in claims.yaml .pecia/work.jsonl", hook,
            "the guard is restating the pin again")

    def test_every_pinned_path_is_covered_including_ones_the_old_list_missed(self):
        """The old guard named claims.yaml, .pecia/work.jsonl,
        .pecia/config.yaml and dev/gates.json. These five were pinned and
        unguarded — the gap round 6 walked through."""
        r = subprocess.run([sys.executable, str(RUNNER), "--canonical-paths"],
                           cwd=ROOT, capture_output=True, text=True)
        emitted = {ln for ln in r.stdout.splitlines() if ln.strip()}
        for missed in ("README.md", "PLAN.md", "pecia_cli.py",
                       "spec/format-v1.md", "spec/vocabulary.json"):
            self.assertIn(missed, emitted)


class AuditWalkTolerance(unittest.TestCase):
    """pc-c10a (round-4 lane D-F5, corroborated by unlisted parallel Audit
    failures in six lanes): discovered() walked with pathlib.rglob and no
    error handling of its own, so whatever the interpreter's pathlib did WAS
    the behaviour — under Apple CLT Python 3.9.6 (the lane sandbox, whose
    pathlib calls scandir unguarded) a directory vanishing during the walk
    crashed the audit with an unhandled FileNotFoundError on the first
    attempt, while 3.12's pathlib swallows the OSError (measured both ways
    by the round-4 scorer). The walk is now os.walk, whose default onerror
    ignores a vanished directory on EVERY interpreter.

    The vanish is simulated at the syscall: scandir raises
    FileNotFoundError for one subdirectory, patched at both hook points
    (os.scandir for os.walk and 3.12 pathlib; the 3.9 accessor binds
    os.scandir at import, so it is patched where it was bound). On 3.9 this
    test is a genuine kill — the pre-fix rglob walk propagates the error —
    and on 3.12 it pins the mechanism the fix made interpreter-independent."""

    def _patched_scandir(self, vanish_name: str):
        import os as _os
        import pathlib as _pathlib
        real = _os.scandir

        def scandir(path=".", *args, **kwargs):
            if Path(path).name == vanish_name:
                raise FileNotFoundError(2, "vanished during the walk", str(path))
            return real(path, *args, **kwargs)

        patched = [(_os, "scandir", real)]
        _os.scandir = scandir
        accessor = getattr(_pathlib, "_normal_accessor", None)
        if accessor is not None and hasattr(accessor, "scandir"):
            patched.append((accessor, "scandir", accessor.scandir))
            accessor.scandir = scandir
        return patched

    @staticmethod
    def _restore(patched) -> None:
        for obj, name, value in patched:
            setattr(obj, name, value)

    def _tree(self, root: Path) -> dict:
        (root / "keep").mkdir()
        (root / "keep" / "gate.py").write_text("x")
        (root / "vanishing").mkdir()
        (root / "vanishing" / "junk.py").write_text("x")
        return {"discovery": {"globs": ["keep/*.py", "vanishing/*.py"]}}

    def test_kill_a_vanishing_directory_is_tolerated_not_crashed_on(self) -> None:
        mod = _gates_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            reg = self._tree(root)
            patched = self._patched_scandir("vanishing")
            try:
                hits = mod.discovered(reg, root, from_index=False)
            finally:
                self._restore(patched)
        self.assertEqual(hits, ["keep/gate.py"],
                         msg="the walk survives the vanish and reports "
                             "everything still on disk")

    def test_control_a_stable_tree_is_discovered_completely(self) -> None:
        """VP4's other arm: tolerance must not be blindness."""
        mod = _gates_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            reg = self._tree(root)
            hits = mod.discovered(reg, root, from_index=False)
        self.assertEqual(hits, ["keep/gate.py", "vanishing/junk.py"])
