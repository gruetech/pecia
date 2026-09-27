#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema>=4.21"]
# ///
"""Validate ledger records against spec/record.schema.json.

RULE 1: Exit 0 means every line is schema-valid — never that the ledger is
true, and never that it is check-clean. The schema covers the single-record
shape (E001 territory); cross-record invariants (E002-E012) belong to
`pecia check`, which is the adapter validation harness. Run both.

Order of operations: the schema itself is validated against the 2020-12
metaschema first — a checker whose own instrument is malformed must not
certify anything (exit 2, cannot-run).

Usage:
  dev/schema-check.py                     # validate .pecia/work.jsonl
  dev/schema-check.py --ledger out.jsonl  # validate an adapter's output
  some-adapter | dev/schema-check.py --ledger -   # validate stdin

Diagnostics: one JSON object per finding on stdout, mirroring the checker:
{severity, code: "SCHEMA", id, message}. Exit 0 clean / 1 findings /
2 cannot-run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "spec" / "record.schema.json"


def finding(severity: str, rid: str | None, message: str) -> str:
    return json.dumps({"severity": severity, "code": "SCHEMA", "id": rid,
                       "message": message}, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", default=None,
                        help="JSONL file to validate ('-' for stdin; "
                             "default .pecia/work.jsonl)")
    parser.add_argument("--schema", default=None,
                        help="schema override (default spec/record.schema.json)")
    args = parser.parse_args()

    schema_path = Path(args.schema) if args.schema else SCHEMA_PATH
    try:
        schema = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(finding("error", None, f"cannot read schema {schema_path}: {exc}"))
        return 2
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        print(finding("error", None,
                      f"schema fails its own metaschema: {exc.message}"))
        return 2
    validator = Draft202012Validator(schema)

    # LF-delimited physical lines, not splitlines() (v2.12, pc-d96d sibling):
    # splitlines() treats U+2028/U+2029/U+0085 inside a JSON string as line
    # boundaries, so this gate refused a one-physical-line record the checker
    # accepts — two shipped gates disagreeing about the canonical shape, the
    # pc-2d40 class at the line level.
    def lf_lines(text: str) -> list[str]:
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts.pop()
        return parts

    if args.ledger == "-":
        lines = lf_lines(sys.stdin.read())
    else:
        ledger = Path(args.ledger) if args.ledger else Path.cwd() / ".pecia" / "work.jsonl"
        if not ledger.exists():
            print(finding("error", None, f"no ledger at {ledger}"))
            return 2
        lines = lf_lines(ledger.read_text())

    bad = 0
    for n, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(finding("error", None, f"line {n} does not parse: {exc}"))
            bad += 1
            continue
        rid = rec.get("id") if isinstance(rec, dict) and isinstance(rec.get("id"), str) else None
        errors = sorted(validator.iter_errors(rec), key=lambda e: list(e.absolute_path))
        for err in errors:
            where = "/".join(str(p) for p in err.absolute_path) or "(record)"
            print(finding("error", rid, f"line {n} at {where}: {err.message}"))
        bad += bool(errors)

    print(json.dumps({"ok": bad == 0, "lines_failing": bad,
                      "schema_version": schema.get("version"),
                      "note": 'Schema-valid is the single-record shape only: '
                              'run `pecia check` for E002-E012, and exit 0 '
                              'means "well-formed," never "true."'},
                     sort_keys=True))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
