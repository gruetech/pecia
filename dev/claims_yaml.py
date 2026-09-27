#!/usr/bin/env python3
"""The register's PARSED view — refusing what YAML silently drops.

pc-4b3c (round-10 lane D-F1): a file with TWO top-level `claims:` keys made
the raw scan in dev/claims_ids.py read the UNION of both blocks while PyYAML
gave the LATER key authority, so `dev/gates.py --audit` accepted a gate
naming a claim `dev/claims-check.py` could not see, and both exited 0. That
was pc-3472's own fix reached by a route it did not consider: it bounded
WHICH lines carry an id and not HOW MANY `claims:` keys there are.

The divergence is last-key-wins versus union, and the register cannot be two
things at once. So no reader resolves it: a duplicate key is a malformation,
and every reader of this file refuses it by name.

This half is for the readers that already carry a YAML parser
(`dev/claims-check.py`, `dev/prose-check.py`). It refuses a duplicate key at
ANY depth, which is what a parser can see and a raw scan cannot: the scorer
met the nested case by accident while building an unrelated control, where a
`tier:` key added above an entry's own was silently discarded. The other
half lives in dev/claims_ids.py, which has no yaml dependency and must not
grow one to run inside a hook; it refuses the duplicate TOP-LEVEL `claims:`
key, the one a column-0 scan can see safely.
"""
from __future__ import annotations

import yaml


class DuplicateKey(yaml.YAMLError):
    """A mapping key given twice. PyYAML keeps the last and drops the rest."""


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that reports a repeated mapping key instead of dropping it.

    The check is inside `construct_mapping` rather than in a replacement
    constructor for the mapping tag, so SafeLoader's own two-step
    construction (the generator that lets a mapping hold a reference to
    itself) is left exactly as it was.
    """

    def construct_mapping(self, node, deep=False):  # noqa: D102 — see class
        seen: dict[object, int] = {}
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            line = key_node.start_mark.line + 1
            try:
                first = seen.get(key)
            except TypeError:                 # an unhashable key: not ours
                continue
            if first is not None:
                raise DuplicateKey(
                    f"line {line}: the key `{key}` is given twice in one "
                    f"mapping (first at line {first}) — a parser keeps the "
                    f"last and drops the rest, so two readers of this file "
                    f"would disagree about what it says (pc-4b3c)")
            seen[key] = line
        return super().construct_mapping(node, deep=deep)


def load(text: str) -> tuple[object, str | None]:
    """(the parsed document, why it could not be parsed that way).

    The second element is a reason string for a malformation — a duplicate
    key, or anything PyYAML itself refuses. A malformed input is a finding,
    never a crash: the caller reports the reason and exits 1, and never
    treats an unreadable register as an empty one (pc-39f6's rule).
    """
    try:
        return yaml.load(text, Loader=StrictLoader), None
    except DuplicateKey as exc:
        return None, str(exc)
    except yaml.YAMLError as exc:
        return None, f"does not parse: {exc}"
