#!/usr/bin/env python3
"""The ids claims.yaml's `claims:` SEQUENCE declares — and nothing else.

pc-3472 (round-9 lane D-F1): two readers derived the register's ids with a
file-wide regular expression while `dev/claims-check.py` parses only
`ledger["claims"]`, so an `- id:` in ANY top-level YAML structure satisfied
the join. Appending

    decoys:
    - id: phantom-claim

to claims.yaml made a gate naming no claim pass `dev/gates.py --audit` at
exit 0, and `dev/claims-ref.py` resolve a `claims:phantom-claim` foreign
reference, while the checker went on reporting the same 27 claims and never
seeing the id. The comment above each extraction said WHY it read raw lines
— no yaml dependency inside a hook, and none in the resolver either — and
neither bounded WHICH lines.

Raw lines still, and one implementation for both readers, because the way
that defect got in was two readers each deciding for themselves what an id
is. What is bounded now is the structure: a sequence item at column 0
between the top-level `claims:` key and the next top-level key. YAML cannot
put an unindented line inside a block scalar, so column 0 is exactly the
top level — the property that makes this scan safe without a parser.

pc-4b3c (round-10 lane D-F1) is the same divergence through the one thing
that bounding said nothing about: HOW MANY `claims:` keys the file has. Two
of them made this scan read the UNION of both blocks while PyYAML gave the
LATER key authority, so a gate could name a claim `dev/claims-check.py`
never sees. The register cannot be two things at once and no reader gets to
pick, so a second top-level `claims:` key is a malformation reported by
name. The parser-side half of the same rule — a duplicate key at any depth,
which only a parser can see — is dev/claims_yaml.py.
"""
from __future__ import annotations

import re

TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*):(.*)$")
ID_ITEM = re.compile(r"^- id:\s*(\S+)\s*$")


def claim_ids(text: str) -> tuple[set[str], str | None]:
    """(the ids in `claims:`, why the file could not be read that way).

    The second element is a reason string when the register's own shape is
    not the one this scan understands — no `claims:` key, a flow-style
    `claims: [...]`, or two of them (pc-4b3c). A reader that cannot answer
    says so; it never returns an empty set as if the register were empty
    (pc-39f6's rule, on this seam).
    """
    ids: set[str] = set()
    in_claims = False
    claims_at: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        top = TOP_KEY.match(line)
        if top:
            key, rest = top.group(1), top.group(2).strip()
            in_claims = key == "claims"
            if in_claims:
                claims_at.append(number)
                if rest and not rest.startswith("#"):
                    return set(), (f"`claims:` is not a block sequence "
                                   f"(found `claims: {rest[:40]}`)")
            continue
        if not in_claims:
            continue
        item = ID_ITEM.match(line)
        if item:
            ids.add(item.group(1))
    if not claims_at:
        return set(), "claims.yaml carries no top-level `claims:` key"
    if len(claims_at) > 1:
        return set(), (
            f"claims.yaml carries {len(claims_at)} top-level `claims:` keys "
            f"(lines {', '.join(str(n) for n in claims_at)}) — a parser keeps "
            f"the last and this scan would read their union, so the register "
            f"would say two different things to two readers (pc-4b3c)")
    return ids, None
