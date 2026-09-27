#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["ruamel.yaml>=0.18"]
# ///
"""claims.yaml is a GENERATION TARGET — never hand-write YAML prose.

Four times in this repo's first week, a hand-edited notes/kill scalar broke
YAML parsing on an unquoted colon, caught only by claims-check at commit
time. The failure class is human discipline; the fix is substrate: values
enter as CLI arguments and the YAML library emits them — quoting, colons,
folding, and width are the machine's problem.

Usage:
  dev/claims-edit.py update <id> [--claim T] [--tier T] [--evidence T]
                                 [--verified T] [--kill T] [--notes T]
                                 [--comment FIELD=TEXT] [--keep-comment FIELD]
  dev/claims-edit.py add <id> --claim T --tier T --evidence T --verified T
                              --kill T [--notes T] [--comment FIELD=TEXT]
  dev/claims-edit.py replace-all claims.json

replace-all accepts a JSON array of complete entries and regenerates the
register through the same validation gate. It is intended for a deliberate
new-repository cut, not a way to erase claim history in an established one.

Comments (including the dated `# ...` annotations) are preserved via
round-trip mode. Preservation is deliberate and stays — but it is NOT
allowed to be silent across a value this tool just changed: an inline
comment routinely carries the count or scope the value stands for
(`verified: '2026-07-30' # 58 tests`), and preserving it verbatim while the
date moves leaves a false count inside the one file that is canonical for
counts. AGENTS.md gives claims.yaml no higher tier to be checked against,
and claims-check validates shape, not comment truth — so the decision is
forced here instead: updating a field that carries an inline comment is
REFUSED unless you say what happens to it, with `--comment FIELD=TEXT` to
replace it (empty TEXT removes it) or `--keep-comment FIELD` to assert it
survives the change. (defect pc-9dea)

The write is GATED: the mutated document is validated with
dev/claims-check.py first; an ill-formed result is refused and nothing is
written. Exit 0 well-formed-and-written / 1 refused / 2 cannot-run.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "claims.yaml"
CHECKER = ROOT / "dev" / "claims-check.py"
FIELDS = ("claim", "tier", "evidence", "verified", "kill", "notes")

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 78


def eol_comment(entry: CommentedMap, field: str) -> str | None:
    """The field's end-of-line comment as plain text, or None if it has none."""
    slot = getattr(entry, "ca", None) and entry.ca.items.get(field)
    token = slot[2] if slot else None
    if token is None:
        return None
    return token.value.lstrip("#").strip() or None


def set_eol_comment(entry: CommentedMap, field: str, text: str) -> None:
    """Replace the field's end-of-line comment; empty text removes it."""
    if text:
        entry.yaml_add_eol_comment(text, field)
        return
    slot = entry.ca.items.get(field)
    if slot:
        slot[2] = None


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="claims-edit",
        description="Gated, generated edits to claims.yaml. Never hand-write YAML prose.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("update", "add"):
        p = sub.add_parser(name)
        p.add_argument("id")
        for field in FIELDS:
            p.add_argument(f"--{field}")
        p.add_argument("--comment", action="append", default=[], metavar="FIELD=TEXT",
                       help="set a field's inline comment; empty TEXT removes it")
        p.add_argument("--keep-comment", action="append", default=[], metavar="FIELD",
                       dest="keep_comment",
                       help="assert the field's existing inline comment survives the new value")
    replace = sub.add_parser("replace-all", help="generate a new register from a JSON array of claims")
    replace.add_argument("json_path", type=Path)
    args = parser.parse_args()

    if args.command == "replace-all":
        try:
            entries = json.loads(args.json_path.read_text())
            if not isinstance(entries, list) or not entries:
                raise ValueError("expected a nonempty JSON array")
            if any(not isinstance(item, dict) for item in entries):
                raise ValueError("every claim must be an object")
            data = CommentedMap({"schema_version": 1,
                                 "audit": "2026-09-26",
                                 "claims": [CommentedMap(item) for item in entries]})
        except (OSError, ValueError) as exc:
            print(f"cannot-run: replacement input: {exc}", file=sys.stderr)
            return 2
        return write_checked(data, "replace-all")

    comments: dict[str, str] = {}
    for raw in args.comment:
        field, sep, text = raw.partition("=")
        field = field.strip()
        if not sep or field not in FIELDS:
            print(f"cannot-run: --comment expects FIELD=TEXT with FIELD in {'/'.join(FIELDS)}; "
                  f"got {raw!r}", file=sys.stderr)
            return 2
        comments[field] = text.strip()
    keep = set(args.keep_comment)
    if not keep <= set(FIELDS):
        print(f"cannot-run: --keep-comment expects a field name in {'/'.join(FIELDS)}; "
              f"got {sorted(keep - set(FIELDS))}", file=sys.stderr)
        return 2
    if overlap := keep & set(comments):
        print(f"cannot-run: {sorted(overlap)} given to both --comment and --keep-comment; "
              "the comment either changes or it does not", file=sys.stderr)
        return 2

    # A MALFORMED REGISTER IS A FINDING, NEVER A TRACEBACK (pc-4b3c's
    # sibling on the write path). ruamel already refuses a repeated key —
    # the one reader of this file that always did — but it refuses by
    # raising, so the editor answered a duplicate-key register with a stack
    # trace. Same refusal, said in this tool's own cannot-run vocabulary.
    try:
        data = yaml.load(CLAIMS.read_text())
    except YAMLError as exc:
        # Flattened, not first-line-only: ruamel opens with "while
        # constructing a mapping" and names the offending key several lines
        # down, and the name is the whole point of saying it.
        detail = " ".join(str(exc).split())[:400] or type(exc).__name__
        print(f"cannot-run: claims.yaml is malformed — {detail}; nothing "
              f"written", file=sys.stderr)
        return 2
    claims = (data or {}).get("claims")
    if claims is None:
        print("cannot-run: no claims list in claims.yaml", file=sys.stderr)
        return 2

    provided = {f: getattr(args, f) for f in FIELDS if getattr(args, f) is not None}
    by_id = {entry.get("id"): entry for entry in claims}

    if args.command == "update":
        entry = by_id.get(args.id)
        if entry is None:
            print(f"cannot-run: no claim with id {args.id!r}", file=sys.stderr)
            return 2
        if not provided:
            print("cannot-run: nothing to update (pass at least one field)", file=sys.stderr)
            return 2
        # The gate: a value this tool is about to change must not silently keep
        # a comment describing the old value (pc-9dea).
        undeclared = [(f, eol_comment(entry, f)) for f in provided
                      if eol_comment(entry, f) is not None
                      and f not in comments and f not in keep]
        if undeclared:
            print("write refused: these fields carry an inline comment that would survive "
                  "a value you are changing:", file=sys.stderr)
            for field, text in undeclared:
                print(f"   {field}: {text}", file=sys.stderr)
            print("   Say what happens to it — a comment beside a changed value is a claim:",
                  file=sys.stderr)
            for field, _ in undeclared:
                print(f"     --comment '{field}=<new text>'   (empty to remove)   "
                      f"or  --keep-comment {field}", file=sys.stderr)
            print("   Nothing written.", file=sys.stderr)
            return 1
        for field, value in provided.items():
            entry[field] = value
    else:  # add
        if args.id in by_id:
            print(f"cannot-run: claim id {args.id!r} already exists", file=sys.stderr)
            return 2
        entry = CommentedMap()
        entry["id"] = args.id
        for field in FIELDS:
            entry[field] = provided.get(field, "none" if field != "notes" else "")
        claims.append(entry)

    for field, text in comments.items():
        set_eol_comment(entry, field, text)

    return write_checked(data, f"{args.command} {args.id}")


def write_checked(data: CommentedMap, description: str) -> int:
    buf = io.StringIO()
    yaml.dump(data, buf)
    # The emitter may wrap a plain scalar after a space. Keep the generated
    # target free of trailing whitespace without changing its parsed values.
    candidate = "\n".join(line.rstrip(" \t\r")
                          for line in buf.getvalue().rstrip("\n").split("\n")) + "\n"

    # The gate: validate the generated document before touching the real file.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(candidate)
        tmp = fh.name
    result = subprocess.run([str(CHECKER), tmp], capture_output=True, text=True)
    Path(tmp).unlink(missing_ok=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        print("write refused: the edit would leave claims.yaml ill-formed; nothing written",
              file=sys.stderr)
        return 1

    CLAIMS.write_text(candidate)
    print(f"claims.yaml updated ({description}); checker clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
