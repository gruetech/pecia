#!/usr/bin/env python3
"""Kills and controls for the consolidated-contract generator — pc-ae3a.

WHAT THE CONTROLS ARE FOR. A generator is the easiest place in this repository
to build a green gate over an empty denominator: emit a document, check it
matches what you just emitted, and the check passes forever while carrying
nothing. So the tests here are not "does it run" — they are that the document
CARRIES the sections, that a section the index cannot see is carried anyway,
and that --check actually turns red on a stale file.

The position-driven requirement is the one that matters. `pc-4f6a` measured the
marker index and it covers 48% of the amendment sections; the obvious
marker-driven generator omits the rest IN SILENCE, which is why
`test_kill_an_unmarked_section_is_still_carried` exists and why it asserts the
TEXT is present rather than that some count moved.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "dev" / "spec-consolidate.py"

SPEC = """# fixture format

## 7. Record

The record inside `rec` is **v1's record, unchanged**.

## 8. Invariants (the checker contract)

**Retained from v1, unchanged:** E005, E009.

- **E001** every line parses.
  <!-- vocab: {"action":"mint","code":"E001","at":"v2","by":"pc-aaaa"} -->

## v2.1 amendments (fixture — the sitting)

### E001 gets narrowed and says so (defect `pc-bbbb`)

MARKED-NARROWING-TEXT
  <!-- vocab: {"action":"amend","code":"E001","at":"v2.1","by":"pc-bbbb"} -->

### E004 is narrowed and marks nothing (defect `pc-cccc`)

TITLE-ONLY-NARROWING-TEXT

### Something behavioural with no code at all (defect `pc-dddd`)

UNINDEXED-BEHAVIOURAL-TEXT
"""

SCHEMA = {
    "title": "fixture", "type": "object", "required": ["id"],
    "$defs": {"isoDate": {"type": "string", "description": "A YYYY-MM-DD date."}},
    "properties": {
        "id": {"type": "string", "description": "The id."},
        "created": {"$ref": "#/$defs/isoDate"},
        "edges": {"type": "object", "additionalProperties": False,
                  "properties": {"blocks": {"type": "array",
                                            "description": "Scheduling edge."}}},
    },
}

REGISTRY = {"allocations": [], "meanings": {"E001": "line parses",
                                            "E004": "cycle", "E005": "transition"},
            "d_codes": {}}


class Consolidate(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.spec = self.dir / "format.md"
        self.schema = self.dir / "schema.json"
        self.registry = self.dir / "vocabulary.json"
        self.out = self.dir / "consolidated.md"
        self.spec.write_text(SPEC)
        self.schema.write_text(json.dumps(SCHEMA))
        self.registry.write_text(json.dumps(REGISTRY))

    def run_gen(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GEN), "--spec", str(self.spec),
             "--schema", str(self.schema), "--registry", str(self.registry),
             "--out", str(self.out), *flags],
            capture_output=True, text=True)

    def generated(self) -> str:
        result = self.run_gen("--write")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return self.out.read_text()

    # ---------------------------------------------------------------- controls
    def test_kill_an_unmarked_section_is_still_carried(self) -> None:
        """The whole design decision, as an assertion on the text.

        A marker-driven generator carries MARKED-NARROWING-TEXT and silently
        drops the other two. `pc-4f6a` measured that as half the real document.
        """
        doc = self.generated()
        self.assertIn("MARKED-NARROWING-TEXT", doc)
        self.assertIn("TITLE-ONLY-NARROWING-TEXT", doc,
                      msg="a section the index cannot see is still normative")
        self.assertIn("UNINDEXED-BEHAVIOURAL-TEXT", doc,
                      msg="a section allocating no code is still normative")

    def test_a_title_only_narrowing_is_flagged_not_silently_indexed(self) -> None:
        doc = self.generated()
        self.assertIn("without marking it", doc)
        self.assertIn("`title`", doc, msg="the weaker authority must be visible")

    def test_a_v1_retained_code_says_where_its_contract_is(self) -> None:
        """E005 has no v2 definition at all; a table of amendments would imply none
        exists anywhere."""
        doc = self.generated()
        self.assertIn("### E005", doc)
        self.assertIn("format-v1.md", doc)

    def test_a_ref_field_takes_its_meaning_from_the_definition(self) -> None:
        doc = self.generated()
        self.assertIn("A YYYY-MM-DD date.", doc,
                      msg="a $ref'd field carries its meaning on the definition")

    def test_the_stale_base_sections_are_carried_and_marked(self) -> None:
        doc = self.generated()
        self.assertIn("v1's record, unchanged", doc, msg="reported, not corrected")
        self.assertIn("pc-c38d", doc)
        self.assertIn("pc-0e2a", doc)

    def test_it_does_not_restate_a_count_another_tool_owns(self) -> None:
        """v2.16 (pc-9611): a count beside machinery is stale when the machinery
        moves. This document and dev/spec-agree.py regenerate at different times."""
        doc = self.generated()
        head = doc.split("## Part 1")[0]
        self.assertIn("belong to `dev/spec-agree.py`", head)

    # ------------------------------------------------------------------- kills
    def test_kill_check_is_red_on_a_stale_document(self) -> None:
        self.generated()
        self.assertEqual(self.run_gen("--check").returncode, 0)
        self.out.write_text(self.out.read_text() + "\nhand edit\n")
        result = self.run_gen("--check")
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertTrue(json.loads(result.stdout)["stale"])

    def test_kill_check_is_red_when_the_source_moves(self) -> None:
        self.generated()
        self.spec.write_text(SPEC + "\n### A new sitting section\n\nNEW-TEXT\n")
        result = self.run_gen("--check")
        self.assertEqual(result.returncode, 1)

    def test_kill_check_is_red_when_the_document_is_absent(self) -> None:
        result = self.run_gen("--check")
        self.assertEqual(result.returncode, 1)

    def test_cannot_run_without_an_amendment_sitting(self) -> None:
        """A v1-shaped document is not v2 and must be refused, not half-rendered."""
        self.spec.write_text("# fixture\n\n## 8. Invariants\n\nnothing\n")
        result = self.run_gen("--write")
        self.assertEqual(result.returncode, 2, msg=result.stdout)

    def test_cannot_run_on_a_missing_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GEN), "--spec", str(self.dir / "nope.md"),
             "--schema", str(self.schema), "--registry", str(self.registry)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
