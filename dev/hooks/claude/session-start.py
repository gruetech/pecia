#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Claude Code SessionStart prime (M4, pc-24fa).

MINIMAL by design — CLAUDE.md: "Prefer on-demand ledger queries (`pecia next
--json`, once M2 lands) over large session-start context primes — primed
constraints decay across compaction." Emits the top few ids and nothing
else, always pointing back at `pecia next` for the live query, never the
full board or record bodies.

Deliberately states NO ready-work total (pc-3373). It used to: it ran
`next --limit 5` and printed `len(items)`, but that list is already
truncated, so the "count" was the limit — it said "5 ready" against a true
39 in every session it ever primed. The arithmetic fix was available and
was not taken. A census is the most decay-prone thing to prime, and
CLAUDE.md already routes that question to the live query, so the honest
prime is the one that does not answer it at all.

No matcher is set in .claude/settings.json, so this fires on every `source`
INCLUDING "compact" — deliberately: the prime is meant to survive
compaction (the one place a stale, un-refreshed prime would misinform an
agent), so refreshing it there is the point, not an oversight.

Fails soft, always: SessionStart has no block mechanism in the Claude Code
hook contract (exit 2 here only surfaces stderr to the user, never to
Claude), so there is nothing to gain by exiting non-zero on any failure
path — every branch below exits 0."""
import json
import os
import subprocess
import sys

#: How many ids to fetch AND to print — one constant, deliberately, so the
#: two can never drift back apart the way they did in pc-3373.
TOP_N = 3


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    cli = os.path.join(root, "pecia_cli.py")
    if not os.path.exists(cli):
        return 0
    try:
        proc = subprocess.run([sys.executable, cli, "next", "--limit", str(TOP_N), "--json"],
                              cwd=root, capture_output=True, text=True, timeout=8)
    except Exception:
        return 0
    # next's own exit code IS the "is this repo primeable" check — 0 means a
    # real timeline resolved cleanly; anything else (no timeline yet, a
    # broken chain, outside a repo) means silence, not a confusing prime.
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0
    try:
        items = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return 0
    if not isinstance(items, list) or not items:
        return 0
    # `items` is post-truncation, so every number derivable from it describes
    # this fetch and not the ledger. The fetch limit and the display count are
    # therefore the SAME constant: with nothing left over, there is no second
    # number to mistake for a census. "top N" below counts the lines printed
    # underneath it, which is a claim about this text, not about the queue.
    lines = [f"pecia: ready queue at priority <=3, top {len(items)}:"]
    for rec in items:
        title = str(rec.get("title", ""))[:60]
        lines.append(f"  {rec.get('id')} (p{rec.get('priority')}, {rec.get('type')}) {title}")
    lines.append("Live query: `./pecia_cli.py next`, which is also where the ready "
                 "TOTAL lives — it is deliberately not primed here. This is a "
                 "snapshot, not authority.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
