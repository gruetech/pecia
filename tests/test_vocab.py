#!/usr/bin/env python3
"""Kills and controls for the vocabulary allocation gate — pc-e9cf.

Every test drives `dev/vocab-check.py` as a subprocess over fixture specs and
a fixture source, which is why the script takes `--spec/--source/--registry`
path arguments: the pre-commit hook must be able to feed it STAGED blobs
rather than the worktree, and a gate that can only read the worktree passes a
staged collision masked by an unstaged repair.

WHAT THE CONTROLS ARE FOR. This repository has shipped three gates that were
green over an empty denominator (`pc-47d6`, `pc-085a`, `pc-cb50`), each
indistinguishable at a glance from a passing gate. A census alone does not fix
that: measured on the real source, 19 codes appear in comments and docstrings,
so an extractor reading the wrong surface reports a healthy NON-ZERO census
while measuring nothing. So the controls here are not "does it pass on clean
input" — they are: an empty source is cannot-run, a comments-only source
naming every code is cannot-run, and adding one real emit site MOVES the
census.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "dev" / "vocab-check.py"

SPEC_HEAD = """# fixture spec

## v1.1 amendments (fixture)

- E001 the first code
  <!-- vocab: {"action":"mint","code":"E001","at":"v1.1","by":"pc-aaaa"} -->
"""

SOURCE = '''"""Fixture module. Mentions E001 in prose, which must not count."""
def finding(sev, code, rid, msg): return {}
def go():
    finding("error", "E001", None, "boom")
'''


class VocabGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.spec = self.dir / "spec.md"
        self.source = self.dir / "src.py"
        self.registry = self.dir / "vocabulary.json"
        self.spec.write_text(SPEC_HEAD)
        self.source.write_text(SOURCE)

    def run_gate(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GATE), "--spec", str(self.spec),
             "--source", str(self.source), "--registry", str(self.registry),
             *extra],
            capture_output=True, text=True)

    def seed(self) -> None:
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

    def census(self, out: str) -> dict:
        for line in out.splitlines():
            d = json.loads(line)
            if "census" in d:
                return d["census"]
        self.fail(f"no census in output: {out!r}")

    # ---- the control: a clean fixture is clean, and says what it measured --

    def test_control_a_clean_fixture_passes_with_a_real_census(self) -> None:
        self.seed()
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, msg=r.stdout)
        c = self.census(r.stdout)
        self.assertEqual(c["markers"], 1)
        self.assertEqual(c["emitted_codes"], 1)
        self.assertEqual(c["amendment_headers"], 1)

    # ---- kills -----------------------------------------------------------

    def test_kill_two_live_allocations_of_one_code(self) -> None:
        # THE defect this gate exists for. Note the second mint cites a
        # DIFFERENT record and the prose says "supersedes" — both of which
        # defeated the first design. Neither helps here: two live allocations
        # are two array elements.
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
- E001 supersedes the earlier meaning, allegedly
  <!-- vocab: {"action":"mint","code":"E001","at":"v1.1","by":"pc-bbbb"} -->
""")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V003", r.stdout)
        self.assertIn("2 live allocations", r.stdout)

    def test_kill_duplicate_amendment_level(self) -> None:
        self.seed()
        self.spec.write_text(SPEC_HEAD + "\n## v1.1 amendments (a second one)\n")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V004", r.stdout)

    def test_kill_marker_citing_an_amendment_that_does_not_exist(self) -> None:
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
- E002 cites a level nobody declared
  <!-- vocab: {"action":"mint","code":"E002","at":"v9.9","by":"pc-cccc"} -->
""")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V005", r.stdout)

    def test_kill_code_defined_in_prose_with_no_marker(self) -> None:
        self.seed()
        self.spec.write_text(SPEC_HEAD + "\n- E003 defined in prose only\n")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V006", r.stdout)

    def test_kill_emitted_code_that_is_not_registered(self) -> None:
        self.seed()
        self.source.write_text(SOURCE + '\ndef more():\n    finding("error", "E009", None, "x")\n')
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V007", r.stdout)

    def test_kill_registered_code_the_implementation_never_emits(self) -> None:
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
- E004 registered but never emitted
  <!-- vocab: {"action":"mint","code":"E004","at":"v1.1","by":"pc-dddd"} -->
""")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V008", r.stdout)

    def test_kill_an_amended_meaning_moves_the_registry(self) -> None:
        """pc-439d (round-1 lanes A-F8/A'-F9): a gloss was captured at MINT
        only, so amendments moved a code's semantics while the registry kept
        serving the mint-time meaning as if current. The `meanings` view now
        carries the LATEST gloss per live code."""
        self.spec.write_text(SPEC_HEAD + """
## v1.2 amendments (fixture)

- E001 now means something newer and narrower
  <!-- vocab: {"action":"amend","code":"E001","at":"v1.2","by":"pc-eeee"} -->
""")
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        d = json.loads(self.registry.read_text())
        self.assertIn("meanings", d)
        self.assertIn("newer and narrower", d["meanings"]["E001"],
                      msg="the amended gloss must win over the mint gloss")

    def test_control_an_unamended_code_keeps_its_mint_gloss(self) -> None:
        self.seed()
        d = json.loads(self.registry.read_text())
        self.assertIn("the first code", d["meanings"]["E001"])

    def test_kill_a_fragment_serving_gloss_is_refused(self) -> None:
        """V015 (v2.9, pc-fea8): a combined amendment bullet donates a
        captured tail as the owning code's gloss — E006 served '/E007 are
        state checks…', E008 served '= contiguous…' — and V013 accepted
        both because the markers were owned. The splice signature is a
        leading joiner character; the serving gloss is refused."""
        self.spec.write_text(SPEC_HEAD + """
## v1.2 amendments (fixture)

- **E001** = contiguous from whatever the combined bullet said
  <!-- vocab: {"action":"amend","code":"E001","at":"v1.2","by":"pc-ffff"} -->
""")
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("V015", r.stdout)
        self.assertIn("fragment", r.stdout)

    def test_control_a_path_opening_gloss_is_not_a_fragment(self) -> None:
        """The discriminating control that shaped V015's signature: D005's
        real gloss opens with '.pecia/.lock', a path — a leading
        non-alphanumeric is NOT sufficient evidence of a splice; only the
        joiner characters are."""
        self.spec.write_text(SPEC_HEAD + """
## v1.2 amendments (fixture)

- **E001** .pecia/something is checked for lock hygiene
  <!-- vocab: {"action":"amend","code":"E001","at":"v1.2","by":"pc-ffff"} -->
""")
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

    def test_shipped_registry_serves_usable_e006_and_e008_glosses(self) -> None:
        """Pinned against the real artifact (the pc-130e convention): the
        two fragments pc-fea8 found cannot silently return."""
        registry = json.loads(
            (Path(__file__).resolve().parents[1] / "spec" /
             "vocabulary.json").read_text())
        self.assertIn("terminal status", registry["meanings"]["E006"],
                      msg="E006's gloss must state its own base condition")
        self.assertNotIn("/E007", registry["meanings"]["E006"][:8])
        self.assertIn("revision gap", registry["meanings"]["E008"],
                      msg="E008's gloss must state its own base condition")
        self.assertNotEqual(registry["meanings"]["E008"][:1], "=")

    def test_kill_a_long_gloss_is_served_whole(self) -> None:
        """pc-8704 (round-6 lane A-F3): the meanings view assigned
        gloss[:160], a mid-word prefix nothing declared — E014 served
        'presence-awa' and E015 'content mat', the v2.9/v2.10
        qualifications truncated out of the register that claims to carry
        the latest gloss per live code. The register serves what the spec
        bullet states, entire."""
        tail = ("with a deliberately long qualification clause that "
                "carries the amendment past one hundred and sixty "
                "characters and ends on the word sentinel")
        self.spec.write_text(SPEC_HEAD + f"""
## v1.2 amendments (fixture)

- **E001** — the first code, restated {tail}
  <!-- vocab: {{"action":"amend","code":"E001","at":"v1.2","by":"pc-eeee"}} -->
""")
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        d = json.loads(self.registry.read_text())
        meaning = d["meanings"]["E001"]
        self.assertGreater(len(meaning), 160,
                           msg="fixture: the gloss must exceed the old cap")
        self.assertTrue(meaning.endswith("sentinel"),
                        msg="the served gloss must end at the bullet's own "
                            "last word, never at a byte count: " + meaning)

    def test_shipped_registry_serves_the_qualified_e014_and_e015(self) -> None:
        """Pinned against the real artifact (the pc-130e convention): the
        two truncations pc-8704 found cannot silently return."""
        registry = json.loads(
            (Path(__file__).resolve().parents[1] / "spec" /
             "vocabulary.json").read_text())
        e014 = registry["meanings"]["E014"]
        self.assertIn("presence-aware", e014,
                      msg="the v2.7 qualification must survive whole")
        self.assertIn("never a crash", e014,
                      msg="the v2.9 bullet's own tail must be served")
        e015 = registry["meanings"]["E015"]
        self.assertIn("content matching", e015,
                      msg="the truncated 'content mat' must be whole again")
        self.assertIn("non-text bytes", e015,
                      msg="the v2.10 qualification must reach the register")

    def test_shipped_e013_gloss_carries_the_narrowed_claim(self) -> None:
        """v2.7 (pc-130e): the SHIPPED registry's E013 meaning derived from
        un-regenerated base prose and still said 'Detects truncation,
        splicing' after the v2.6 interior-only narrowing — pc-439d's fix
        moved amended glosses, but the v2.6 amendment's marker sits under
        prose that is not a code bullet, so the base bullet is what feeds
        the gloss and the base owed regeneration (pc-ae3a). Pinned against
        the real artifact so the stale claim cannot silently return."""
        registry = json.loads(
            (Path(__file__).resolve().parents[1] / "spec" /
             "vocabulary.json").read_text())
        meaning = registry["meanings"]["E013"]
        self.assertIn("INTERIOR", meaning,
                      msg="the narrowed scope must reach the machine-read gloss")
        self.assertNotIn("Detects truncation, splicing", meaning,
                         msg="the superseded truncation claim is back")

    def test_kill_a_hand_edited_registry(self) -> None:
        # The registry is GENERATED. If it can be edited without the gate
        # noticing, it is a second authority, which is the thing the design
        # rejected.
        self.seed()
        d = json.loads(self.registry.read_text())
        d["allocations"][0]["meaning"] = "quietly rewritten by hand"
        self.registry.write_text(json.dumps(d, indent=2) + "\n")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V010", r.stdout)

    def test_kill_retirement_without_a_predecessor_anchor(self) -> None:
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
- E001 retired vaguely
  <!-- vocab: {"action":"delete","code":"E001","at":"v1.1","by":"pc-eeee"} -->
""")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V011", r.stdout)

    def test_kill_an_unowned_latest_amend_is_refused(self) -> None:
        """pc-c05a (round-3 lane A-F2): a mint/amend marker not owned by a
        same-code bullet records no gloss, so combined amendment prose
        left `meanings` serving an older gloss as current, green. A live
        code's LATEST mint/amend event must own its gloss (V013)."""
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
- E005 another code entirely, owning the paragraph below
  <!-- vocab: {"action":"mint","code":"E005","at":"v1.1","by":"pc-ffff"} -->

## v1.2 amendments (fixture)

Combined prose that moves E001's semantics without restating its gloss.
  <!-- vocab: {"action":"amend","code":"E001","at":"v1.2","by":"pc-eeee"} -->
""")
        self.source.write_text(
            SOURCE + '\ndef more():\n    finding("error", "E005", None, "x")\n')
        self.run_gate("--write")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V013", r.stdout)
        self.assertIn("E001", r.stdout)

    def test_control_an_owned_latest_amend_passes(self) -> None:
        # The same amendment with the gloss restated in an owned bullet —
        # the exact move V013 demands — is clean.
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
## v1.2 amendments (fixture)

- E001 now means something newer and narrower
  <!-- vocab: {"action":"amend","code":"E001","at":"v1.2","by":"pc-eeee"} -->
""")
        self.run_gate("--write")
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, msg=r.stdout)

    D_SPEC = SPEC_HEAD + """
- **D001** — the doctor's first posture check
  <!-- vocab: {"action":"mint","code":"D001","at":"v1.1","by":"pc-dddd"} -->
"""
    D_SOURCE = SOURCE + '\nD_CODES = {"D001": "the doctor\'s first posture check"}\n'

    def test_kill_an_implementation_only_d_meaning_change_is_refused(self) -> None:
        """pc-085ad (round-3 lane D-F3): the registry declared the specs
        canonical for meaning while d_codes was read from D_CODES, so an
        implementation-only change to a D-code's meaning regenerated and
        passed. The spec is now the authored home; V014 refuses either
        side moving alone."""
        self.spec.write_text(self.D_SPEC)
        self.source.write_text(self.D_SOURCE)
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.assertEqual(self.run_gate().returncode, 0, msg="control: agreed state is clean")
        self.source.write_text(
            SOURCE + '\nD_CODES = {"D001": "quietly means something else now"}\n')
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V014", r.stdout)

    def test_kill_a_spec_only_d_meaning_change_is_refused(self) -> None:
        self.spec.write_text(self.D_SPEC)
        self.source.write_text(self.D_SOURCE)
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.spec.write_text(self.D_SPEC.replace(
            "the doctor's first posture check <", "a re-specified posture check <")
            .replace("— the doctor's first posture check",
                     "— a re-specified posture check"))
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V014", r.stdout)

    def test_control_moving_spec_and_table_together_passes(self) -> None:
        self.spec.write_text(self.D_SPEC.replace(
            "— the doctor's first posture check",
            "— a re-specified posture check"))
        self.source.write_text(
            SOURCE + '\nD_CODES = {"D001": "a re-specified posture check"}\n')
        self.assertEqual(self.run_gate("--write").returncode, 0)
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, msg=r.stdout)

    def test_kill_a_d_bullet_without_a_marker_is_refused(self) -> None:
        # V006 covers the D family now that the specs own it.
        self.spec.write_text(SPEC_HEAD + "\n- **D002** — defined in prose only\n")
        self.source.write_text(SOURCE)
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, msg=r.stdout)
        self.assertIn("V006", r.stdout)
        self.assertIn("D002", r.stdout)

    def test_the_real_d_codes_are_spec_owned_and_agree(self) -> None:
        # Pinned against the real artifacts: every implementation D-code has
        # a spec-owned meaning and the two agree byte-for-byte, which is
        # what makes "the specs are canonical" true rather than declared.
        registry = json.loads((ROOT / "spec" / "vocabulary.json").read_text())
        spec_text = (ROOT / "spec" / "format-v2.md").read_text()
        for code, meaning in registry["d_codes"].items():
            self.assertIn(f'"code":"{code}"', spec_text,
                          msg=f"{code} has no spec marker")
        # 12 since 2026-09-12: D012 minted at v2.9 (pc-87d8, the checker-
        # resolution posture). The count moves only with a deliberate edit
        # here, mirroring the spec mint.
        self.assertEqual(len(registry["d_codes"]), 12)

    # ---- anti-vacuity: the census must be able to be WRONG, not just zero --

    def test_an_empty_source_is_cannot_run_not_clean(self) -> None:
        self.seed()
        self.source.write_text("")
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, msg=r.stdout)
        self.assertIn("V000", r.stdout)

    def test_a_comments_only_source_is_cannot_run_not_clean(self) -> None:
        # THE discriminating control. This file NAMES every code in prose and
        # emits none. A grep-based extractor reports a full, healthy census
        # over it; the AST extractor must report zero and refuse to run.
        self.seed()
        self.source.write_text(
            '"""E001 E002 E003 mentioned in a docstring."""\n'
            "# E001 named in a comment too\n"
            "def nothing():\n    pass\n")
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, msg=r.stdout)
        self.assertIn("V000", r.stdout)
        self.assertIn("emitted_codes", r.stdout)

    def test_adding_one_real_emit_site_moves_the_census(self) -> None:
        # A census that cannot move is a constant wearing a measurement's
        # clothes. Prove it responds to the thing it claims to count.
        self.seed()
        before = self.census(self.run_gate().stdout)["emitted_codes"]
        self.spec.write_text(SPEC_HEAD + """
- E005 a second real code
  <!-- vocab: {"action":"mint","code":"E005","at":"v1.1","by":"pc-ffff"} -->
""")
        self.source.write_text(SOURCE + '\ndef more():\n    finding("error", "E005", None, "x")\n')
        self.run_gate("--write")
        after = self.census(self.run_gate().stdout)["emitted_codes"]
        self.assertEqual((before, after), (1, 2),
                         msg="the census did not move when an emit site was added")

    def test_a_dynamically_built_code_is_cannot_run(self) -> None:
        self.seed()
        self.source.write_text(SOURCE + '\ndef sneaky(n):\n    finding("error", f"E{n:03d}", None, "x")\n')
        r = self.run_gate()
        self.assertEqual(r.returncode, 2, msg=r.stdout)
        self.assertIn("V002", r.stdout)

    def test_a_marker_inside_a_fenced_example_is_not_an_allocation(self) -> None:
        # The spec must be able to SHOW the syntax while documenting it. The
        # v2.3 amendment does, and the first version of the parser read its
        # example as a real mint.
        self.seed()
        self.spec.write_text(SPEC_HEAD + """
Documentation showing the syntax:

```
<!-- vocab: {"action":"mint","code":"E001","at":"v1.1","by":"pc-9999"} -->
```
""")
        r = self.run_gate()
        self.assertEqual(r.returncode, 0,
                         msg=f"a fenced example minted a code: {r.stdout}")

    # ---- the real repository ---------------------------------------------

    def test_the_real_specs_and_registry_agree(self) -> None:
        r = subprocess.run([sys.executable, str(GATE)], capture_output=True,
                           text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        c = self.census(r.stdout)
        for key in ("markers", "amendment_headers", "emitted_codes",
                    "declared_d_codes"):
            self.assertGreater(c[key], 0, msg=f"{key} census is zero")

    def test_the_registry_records_the_real_E012_collision(self) -> None:
        # The registry's own evidence that it can hold what it exists to
        # detect: E012 was allocated twice, and both allocations are present.
        d = json.loads((ROOT / "spec" / "vocabulary.json").read_text())
        e012 = [a for a in d["allocations"] if a["code"] == "E012"]
        minters = {a["by"] for a in e012 if a["action"] == "mint"}
        self.assertEqual(minters, {"pc-4d19", "pc-0033"},
                         msg="the historical E012 collision is not represented")
        self.assertTrue(any(a["action"] == "renumber" for a in e012),
                        msg="the renumbering that resolved it is not recorded")


if __name__ == "__main__":
    unittest.main()


class TheServedGlossIsTheLivingMeaning(unittest.TestCase):
    """pc-d935 (round-10 lanes A-F1 and A'-F2): v2.14's pc-54c7 moved E016's
    semantics — its printed alternative "reopen this record" was replaced,
    BECAUSE E005 refuses it, by "drop the `blocks` claim that no longer
    holds, or close the blocker(s); this record cannot be reopened" — and the
    §v2.4 owner bullet the `meanings` view is generated from was not amended.
    So the registry served the superseded wording, and the remedy it served
    is one the object itself refuses. V013 was clean throughout at 89
    markers, because it sees the DECLARED case (a live code whose latest
    event carries no gloss) and nothing tells it an amendment elsewhere in
    the document obliged one.

    These arms are over the SHIPPED registry and the REAL checker, because
    the property is an agreement between two files that a fixture cannot
    stand in for: what the registry serves and what the code prints.
    """

    REGISTRY = json.loads((ROOT / "spec" / "vocabulary.json").read_text())
    CLI = ROOT / "pecia_cli.py"

    def emitted_e016_message(self) -> str:
        """A real E016 finding from the real checker, not a grep."""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        base = {"rev": 1, "type": "task", "priority": 2, "created": "2026-01-01",
                "updated": "2026-01-01", "body": "", "labels": [], "owner": "t",
                "evidence": None, "disposition": None,
                "edges": {"blocks": [], "retires": [], "parent": None,
                          "duplicate_of": None, "discovered_from": None,
                          "caused_by": None, "validates": None,
                          "supersedes": None}}
        blocked = {**base, "id": "pc-0002", "title": "closed under a blocker",
                   "status": "done", "disposition": "d"}
        blocker = {**base, "id": "pc-0001", "title": "the blocker",
                   "status": "open"}
        blocker["edges"] = {**base["edges"], "blocks": ["pc-0002"]}
        ledger = d / "work.jsonl"
        ledger.write_text("".join(json.dumps(r) + "\n" for r in (blocked, blocker)))
        r = subprocess.run([sys.executable, str(self.CLI), "check",
                            "--ledger", str(ledger), "--json"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            try:
                d_ = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d_.get("code") == "E016":
                return d_["message"]
        self.fail(f"the fixture produced no E016: {r.stdout}{r.stderr}")

    def test_kill_the_served_e016_gloss_prescribes_the_remedy_that_runs(self) -> None:
        """The record's own condition: the served gloss ended "or reopen the
        record that closed under it", and performing it — `edit <id> --status
        open` on a terminal record — exits 1 naming E005."""
        gloss = self.REGISTRY["meanings"]["E016"]
        self.assertIn("close the blocker(s)", gloss)
        self.assertIn("cannot be reopened", gloss)
        self.assertNotIn("or reopen the record", gloss)

    def test_kill_the_gloss_and_the_emitted_message_prescribe_the_same_thing(self) -> None:
        """The property, rather than the wording: what the registry serves and
        what a real finding prints must name the same repairs. Measured by
        running the checker over a ledger that violates E016."""
        gloss = self.REGISTRY["meanings"]["E016"]
        message = self.emitted_e016_message()
        for phrase in ("drop the `blocks` claim that no longer holds",
                       "close the blocker(s)",
                       "cannot be reopened"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, message)
                self.assertIn(phrase, gloss)

    def test_kill_the_e016_bullet_carries_an_amend_event_at_this_level(self) -> None:
        """The mechanism the fix is: the gloss moved because the BULLET was
        amended and carries its own vocab event, which is what V013 reads. A
        bullet edited without one would serve the new words and record no
        allocation.

        Stated as a membership rather than as the whole list (v2.16): E016's
        bullet was amended again at v2.16 under pc-1130, and a test that pins
        the history's LENGTH turns red at every subsequent amendment for a
        reason that has nothing to do with what it is checking. What it is
        checking is that the v2.15 sitting's bullet edit carries its own
        event; removing that marker still fails this."""
        events = [a for a in self.REGISTRY["allocations"]
                  if a["code"] == "E016"]
        self.assertEqual(events[0]["action"], "mint")
        self.assertTrue(all(a["action"] == "amend" for a in events[1:]))
        self.assertIn(("v2.15", "pc-d935"),
                      [(a["at"], a["by"]) for a in events],
                      msg="the pc-d935 bullet edit records its own allocation")

    def test_control_the_amendment_that_moved_the_semantics_is_still_named(self) -> None:
        """The v2.14 narrative is not rewritten by this: the amendment that
        moved the meaning stays where it was, and the bullet now agrees with
        it rather than replacing it."""
        spec = (ROOT / "spec" / "format-v2.md").read_text()
        self.assertIn("A repair that narrows a grouped finding is not a new "
                      "error (defect `pc-54c7`)", spec)

    def test_kill_no_served_gloss_carries_the_bullets_own_markup(self) -> None:
        """pc-d935's aside, same bullet: the extractor stripped the code and
        left the closing `**` of a bold span that covers the code AND its
        name, so E000 served `cannot-run** — …` and E016
        `closed-while-blocked** — …` while the seventeen whose span covers the
        code alone did not."""
        for code, gloss in self.REGISTRY["meanings"].items():
            with self.subTest(code=code):
                head = gloss.split("—")[0]
                self.assertNotIn("**", head,
                                 msg=f"{code} serves the bullet's own markup")
        for code in ("E000", "E016"):
            self.assertFalse(self.REGISTRY["meanings"][code].startswith("**"))

    def test_control_emphasis_inside_a_gloss_survives(self) -> None:
        """The discriminating control: only the span the extractor opened is
        removed. Markup the gloss itself uses is the spec's words and stays —
        E002's meaning quotes two codes in bold."""
        v1 = [a for a in self.REGISTRY["allocations"]
              if a["code"] == "E002" and a["at"] == "v1"][0]
        self.assertIn("**identical**", v1["meaning"])
