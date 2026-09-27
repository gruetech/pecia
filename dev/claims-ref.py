#!/usr/bin/env python3
"""Resolver for the `claims:<id>` foreign-reference scheme.

Exit 0 iff claims.yaml declares the given id. Exit 0 means RESOLVABLE, never
true — it licenses exactly one sentence: "the repo's claims ledger carries an
entry with this id." Whether that entry is honest is claims-check's problem.

This lived inside pecia_cli.py until v1.7, where it was two defects at once:
adding a second scheme meant editing the core (an OCP violation), and it
returned "valid" whenever claims.yaml was absent — a gate that could not turn
red in exactly the repos most likely to hit it (VP4). Both are fixed by
relocation: the core now knows only that a scheme is *declared*, and this
script answers whether the referent is *there*. A missing claims.yaml is now
a non-zero exit, i.e. an audit finding, not silence.

Usage: dev/claims-ref.py <claim-id>
Declared in .pecia/config.yaml as: resolvers: [claims=dev/claims-ref.py]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLAIMS = ROOT / "claims.yaml"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from claims_ids import claim_ids  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: claims-ref.py <claim-id>", file=sys.stderr)
        return 2
    wanted = sys.argv[1].strip()
    if not CLAIMS.exists():
        print(f"no claims ledger at {CLAIMS.relative_to(ROOT)} — nothing to resolve against",
              file=sys.stderr)
        return 1
    # SIBLING OF pc-3472, SAME COMMIT. This matched `- id:` at ANY
    # indentation anywhere in the file, so a name under any other key — or
    # nested inside one — resolved a `claims:<id>` foreign reference that
    # claims-check never sees. Resolvable means "the claims REGISTER carries
    # an entry with this id", and the register is the `claims:` sequence.
    ids, why_not = claim_ids(CLAIMS.read_text())
    if why_not is not None:
        print(f"claims.yaml cannot be read as a register: {why_not} — a "
              f"resolver that cannot answer refuses", file=sys.stderr)
        return 1
    if wanted in ids:
        return 0
    print(f"claims.yaml declares {len(ids)} ids; {wanted!r} is not among them", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
