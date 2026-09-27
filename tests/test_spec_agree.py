#!/usr/bin/env python3
"""Kills and controls for the spec/implementation agreement check — pc-ae3a.

Every test drives `dev/spec-agree.py` as a subprocess over fixture documents,
which is why the script takes `--spec/--cli/--schema/--tests/--baseline`: a
gate that can only read the worktree passes a staged drift masked by an
unstaged repair, which is the shape `dev/vocab-check.py` was built against and
the same shape applies here.

WHAT THE CONTROLS ARE FOR. This check exists because a section can be green by
being empty, and a comparison can be green by comparing nothing. Two of its
findings were FALSE when it was first run against the real repository, and both
false findings are pinned here as controls rather than described in a comment:

  * `test_control_a_deleted_code_asserted_absent_is_not_drift` — the suite
    asserts E010's ABSENCE, because E010 was deleted at v2 along with the state
    it reported. A mention scan files that as drift. It is the null arm working.

  * `test_control_a_loop_bound_assertion_resolves_as_present` — D007 is
    asserted through `for expected in (...): assertIn(expected, codes)`. A line
    scan reads it as untested and reports a gate nobody tests, which is exactly
    the false alarm that trains a reader to ignore the check.

And the control that matters most is that the check can go GREEN: a check
permanently red over real drift is indistinguishable, from the outside, from a
check that cannot compute agreement at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "dev" / "spec-agree.py"

# A spec whose base sections state the whole contract: nothing is deferred and
# nothing is defined only below. This is the shape the real spec has drifted out
# of, so it is what "agreement" looks like.
SPEC_AGREEING = """# fixture format

## 7. Record

The record carries `id`, `rev`, `title` and `edges`.

## 8. Invariants (the checker contract)

- **E001** every line parses.
  <!-- vocab: {"action":"mint","code":"E001","at":"v2","by":"pc-aaaa"} -->
- **E002** duplicate revision.
  <!-- vocab: {"action":"mint","code":"E002","at":"v2","by":"pc-bbbb"} -->
"""

CLI_AGREEING = '''"""Fixture implementation."""
CORE_STATUSES = ["open", "done"]
CORE_TYPES = ["defect", "task"]
SCALAR_EDGES = ["parent"]
LIST_EDGES = ["blocks"]
REQUIRED_FIELDS = ["id", "rev", "title", "edges"]
TOUCHED_FIELDS = ["title"]
PRESENCE_FIELDS = frozenset({"title"})
PER_REVISION_FIELDS = frozenset({"id", "rev", "edges"})

def go(report):
    report("error", "E001", None, "boom")
    report("error", "E002", None, "boom")
'''

SCHEMA_AGREEING = {
    "title": "fixture record",
    "type": "object",
    "required": ["id", "rev", "title", "edges"],
    "properties": {
        "id": {"type": "string"}, "rev": {"type": "integer"},
        "title": {"type": "string"},
        "edges": {"type": "object", "properties": {"blocks": {}, "parent": {}}},
    },
}

TESTS_AGREEING = '''
class T:
    def test_codes(self):
        self.assertIn("E001", codes)
        self.assertIn("E002", codes)
'''

BASELINE_V1 = """# fixture v1

## Record

```json
{
  "id": "pc-a3f8",
  "rev": 1,
  "title": "x",
  "edges": { "blocks": [], "parent": null }
}
```
"""


class SpecAgree(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.spec = self.dir / "format.md"
        self.cli = self.dir / "cli.py"
        self.schema = self.dir / "record.schema.json"
        self.tests = self.dir / "suite.py"
        self.baseline = self.dir / "format-v1.md"
        self.spec.write_text(SPEC_AGREEING)
        self.cli.write_text(CLI_AGREEING)
        self.schema.write_text(json.dumps(SCHEMA_AGREEING))
        self.tests.write_text(TESTS_AGREEING)
        self.baseline.write_text(BASELINE_V1)

    def run_gate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GATE), "--spec", str(self.spec),
             "--cli", str(self.cli), "--schema", str(self.schema),
             "--tests", str(self.tests), "--baseline", str(self.baseline)],
            capture_output=True, text=True)

    def findings(self, result: subprocess.CompletedProcess[str]) -> list[dict]:
        out = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            obj = json.loads(line)
            if "kind" in obj:
                out.append(obj)
        return out

    def kinds(self, result) -> set[str]:
        return {f["kind"] for f in self.findings(result)}

    # ---------------------------------------------------------------- controls
    def test_control_the_check_can_go_green(self) -> None:
        """The null arm. A check that is red on everything measures nothing."""
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        errors = [f for f in self.findings(result) if f["severity"] == "error"]
        self.assertEqual(errors, [], msg="agreeing inputs must produce no error")

    def test_control_a_deleted_code_asserted_absent_is_not_drift(self) -> None:
        """E010's shape: deleted from the spec, asserted absent by the suite.

        The suite requiring a code NOT to appear is the null arm for a deleted
        gate, and reporting it as an undocumented code is a false finding this
        check made before the assertion resolver could tell the two apart.
        """
        self.tests.write_text(TESTS_AGREEING + '''
    def test_deleted(self):
        self.assertNotIn("E010", codes)
''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertNotIn("code-undocumented", self.kinds(result),
                         msg="a code the suite asserts ABSENT is not drift")

    def test_control_a_loop_bound_assertion_resolves_as_present(self) -> None:
        """D007's shape: the code literal is in the loop, not in the call.

        A line scan sees `assertIn(expected, codes)` with no code on the line
        and reports the gate untested. That false finding is what the syntax
        tree walk exists for, so it is pinned rather than trusted.
        """
        self.tests.write_text('''
class T:
    def test_codes(self):
        for expected in ("E001", "E002"):
            self.assertIn(expected, codes)
''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertNotIn("code-untested", self.kinds(result))
        self.assertNotIn("code-tested-negative-only", self.kinds(result))

    def test_control_an_unresolvable_mention_is_undecided_not_a_finding(self) -> None:
        """What the check cannot resolve, it must decline to score either way."""
        self.tests.write_text(TESTS_AGREEING + '''
    def test_indirect(self):
        wanted = pick_one()          # E003 is named only in this comment
        self.assertIn(wanted, codes)
''')
        self.cli.write_text(CLI_AGREEING.replace(
            'report("error", "E002", None, "boom")',
            'report("error", "E002", None, "boom")\n    report("error", "E003", None, "x")'))
        self.spec.write_text(SPEC_AGREEING + '''
- **E003** third.
  <!-- vocab: {"action":"mint","code":"E003","at":"v2","by":"pc-cccc"} -->
''')
        result = self.run_gate()
        kinds = self.kinds(result)
        self.assertIn("test-status-undecided", kinds,
                      msg="an unresolvable mention must be reported undecided")
        undecided = next(f for f in self.findings(result)
                         if f["kind"] == "test-status-undecided")
        self.assertEqual(undecided["severity"], "note")
        self.assertIn("E003", undecided["evidence"]["codes"])

    # ------------------------------------------------------------------- kills
    def test_kill_a_code_the_spec_never_states(self) -> None:
        self.cli.write_text(CLI_AGREEING.replace(
            'report("error", "E002", None, "boom")',
            'report("error", "E002", None, "boom")\n    report("error", "E099", None, "x")'))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("code-undocumented", self.kinds(result))

    def test_kill_a_code_nothing_implements(self) -> None:
        self.spec.write_text(SPEC_AGREEING + '''
- **E098** stated and built by nobody.
  <!-- vocab: {"action":"mint","code":"E098","at":"v2","by":"pc-dddd"} -->
''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("code-unimplemented", self.kinds(result))

    def test_kill_a_code_defined_only_below_the_base_section(self) -> None:
        """The real repository's condition: the contract moved into amendments."""
        self.spec.write_text(SPEC_AGREEING + '''
## v2.1 amendments (fixture — a code minted below the base)

### E050 arrives in an amendment (defect `pc-eeee`)

  <!-- vocab: {"action":"mint","code":"E050","at":"v2.1","by":"pc-eeee"} -->
''')
        self.cli.write_text(CLI_AGREEING.replace(
            'report("error", "E002", None, "boom")',
            'report("error", "E002", None, "boom")\n    report("error", "E050", None, "x")'))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "base-section-incomplete")
        self.assertIn("E050", finding["evidence"]["missing_from_base"])

    def test_kill_a_record_section_that_defers_while_the_schema_moved(self) -> None:
        """Section 7's condition: 'v1's record, unchanged' beside novel fields."""
        self.spec.write_text(SPEC_AGREEING.replace(
            "The record carries `id`, `rev`, `title` and `edges`.",
            "The record inside `rec` is **v1's record, unchanged**."))
        schema = json.loads(json.dumps(SCHEMA_AGREEING))
        schema["properties"]["ratified_by"] = {"type": "string"}
        self.schema.write_text(json.dumps(schema))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "record-section-stale")
        self.assertIn("ratified_by", finding["evidence"]["fields_postdating_v1"])

    def test_kill_a_deferring_record_section_also_catches_a_new_edge(self) -> None:
        self.spec.write_text(SPEC_AGREEING.replace(
            "The record carries `id`, `rev`, `title` and `edges`.",
            "The record inside `rec` is **v1's record, unchanged**."))
        schema = json.loads(json.dumps(SCHEMA_AGREEING))
        schema["properties"]["edges"]["properties"]["retires"] = {}
        self.schema.write_text(json.dumps(schema))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "record-section-stale")
        self.assertIn("retires", finding["evidence"]["edges_postdating_v1"])

    def test_kill_the_required_sets_diverge(self) -> None:
        schema = json.loads(json.dumps(SCHEMA_AGREEING))
        schema["required"] = ["id", "rev", "title"]
        self.schema.write_text(json.dumps(schema))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("required-fields-disagree", self.kinds(result))

    def test_kill_a_conflict_unit_the_schema_does_not_declare(self) -> None:
        self.cli.write_text(CLI_AGREEING.replace(
            'TOUCHED_FIELDS = ["title"]', 'TOUCHED_FIELDS = ["title", "context"]'))
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "touched-field-undeclared")
        self.assertIn("context", finding["evidence"]["fields"])

    def test_kill_a_code_with_no_allocation_anywhere(self) -> None:
        """E005/E009's shape: named in prose, emitted, allocated nowhere."""
        self.spec.write_text(SPEC_AGREEING + "\n- **E007** named in prose only.\n")
        self.cli.write_text(CLI_AGREEING.replace(
            'report("error", "E002", None, "boom")',
            'report("error", "E002", None, "boom")\n    report("error", "E007", None, "x")'))
        result = self.run_gate()
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "code-has-no-allocation")
        self.assertIn("E007", finding["evidence"]["codes"])

    def test_kill_an_amendment_narrows_a_code_without_marking_it(self) -> None:
        """The v2.11/v2.12 condition: the title amends a code, the body marks nothing.

        v2.15 (pc-d935) already says an amendment that moves a meaning restates
        it where the machine reads it. Six of the eight real instances are in
        two consecutive sittings AFTER that rule landed, which is why this is a
        check and not a convention.
        """
        self.spec.write_text(SPEC_AGREEING + '''
## v2.1 amendments (fixture)

### E001's anchors are full matches (defect `pc-ffff`)

Normative text that narrows E001 and marks nothing.
''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 1)
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "amendment-narrows-without-marking")
        self.assertEqual(finding["evidence"]["sections"][0]["unmarked"], ["E001"])

    def test_control_an_amendment_that_marks_its_code_is_not_flagged(self) -> None:
        """The null arm: the same section, marked, must produce no finding."""
        self.spec.write_text(SPEC_AGREEING + '''
## v2.1 amendments (fixture)

### E001's anchors are full matches (defect `pc-ffff`)

Normative text that narrows E001 and says so where the machine reads it.
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.1","by":"pc-ffff"} -->
''')
        result = self.run_gate()
        self.assertNotIn("amendment-narrows-without-marking", self.kinds(result))
        self.assertNotIn("amendment-coverage", self.kinds(result),
                         msg="a fully marked amendment set omits nothing")

    def test_amendment_coverage_is_a_note_and_carries_its_denominator(self) -> None:
        """A share without its denominator is the count-beside-machinery defect
        v2.16 (pc-9611, pc-5d02) closed in the claims register; this check does
        not get to repeat it in its own output."""
        self.spec.write_text(SPEC_AGREEING + '''
## v2.1 amendments (fixture)

### Something behavioural with no code at all

Normative text that allocates nothing.
''')
        result = self.run_gate()
        finding = next(f for f in self.findings(result)
                       if f["kind"] == "amendment-coverage")
        self.assertEqual(finding["severity"], "note")
        for key in ("sections_without_marker", "sections_total",
                    "lines_without_marker", "lines_total", "share"):
            self.assertIn(key, finding["evidence"])

    def test_cannot_run_on_a_missing_spec(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GATE), "--spec", str(self.dir / "nope.md")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
