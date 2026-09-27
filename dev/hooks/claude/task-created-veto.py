#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Claude Code TaskCreated veto (M4, pc-24fa).

SUBTRACTIVE, not generative (format-v2.md 4.1 / AGENTS.md Methodology):
this vetoes ABSENCE of a pc-xxxx-shaped token in the task's subject or
description. It never validates that a cited id is real (that is `pecia
audit`'s job, over the ledger, not a harness hook's, over free text) and
never judges content quality beyond shape. Requiring more than shape here
would reproduce the measured fabrication result the catalog warns about
(GP8/BP4's scope qualifier: forcing a well-formed GENERATIVE field a writer
must author gets satisfied by fabrication, not by disclosure) — this field
is not one an agent must invent, only one it discloses if the project's own
process was already followed.

`no-pecia-id` is a declared-absence escape valve for genuinely off-ledger
tasks, mirroring `edges.no_edges` (rule 4: absence is declared, not
silent).

Reads stdin once (Claude Code's TaskCreated payload: task_id, task_subject,
optionally task_description — the docs say description "may be absent").
Malformed or unreadable input fails OPEN (exit 0, no veto) — this hook's
job is to catch a missing citation, not to become a second point of
failure for task creation generally.

Tier stays `asserted` in claims.yaml, never `tested`/`enforced` — rule 1
extended to a third-party harness: a green test here proves this SCRIPT's
own stdin/stdout contract is well-formed, never that Claude Code will
invoke or honor it in a given install/version (CLAUDE.md: "treat
TaskCreated/TaskCompleted hook wiring as convenience, not enforcement")."""
import json
import re
import sys

# Mirrors pecia_cli.py's ID_RE (pc- + one alnum + [alnum.-]*) rather than a
# narrower pc-[0-9a-f]{4}: ids are adaptive-length per collision budget
# (spec v1) and adapter-imported ids are not guaranteed 4 hex chars.
ID_RE = re.compile(r"pc-[A-Za-z0-9][A-Za-z0-9.-]*")
ABSENCE_MARKER = re.compile(r"no-pecia-id", re.I)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    text = f"{payload.get('task_subject') or ''}\n{payload.get('task_description') or ''}"
    if ID_RE.search(text) or ABSENCE_MARKER.search(text):
        return 0
    print(json.dumps({
        "decision": "block",
        "reason": ("pecia: this task cites no pc-xxxx ledger id (subject or "
                   "description). Add one — an existing id, or `pecia add` a "
                   "new record first — and recreate the task. If genuinely "
                   "off-ledger, say so with the literal marker `no-pecia-id`. "
                   "Shape check only: no id is verified to exist."),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
