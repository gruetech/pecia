#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Claims ledger well-formedness check for pecia.

Usage:  dev/claims-check.py [path]   (from repo root; pre-commit runs it
        against the STAGED claims.yaml blob — review-3 F13: git commits the
        index, so the gate must check the index)

claims.yaml is canonical; prose is projection. This checks the ledger itself:
parseability, required fields, tier vocabulary, and the honesty rules the
tiers imply (an aspirational claim may not carry evidence or a verified date;
a tested claim must carry both). It deliberately does NOT re-run evidence
commands — run those separately.

Ported (reduced) from chorusmith dev/claims-check.py; grows counts-vs-tree
comparison and prose tripwires when the repo has counts and prose to guard.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from claims_yaml import load as load_register  # noqa: E402

ROOT = HERE.parent
failures = 0


def fail(msg: str) -> None:
    global failures
    failures += 1
    print(f"  ✗ {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


VALID_TIER = re.compile(r"^(proved|machine-checked\([^)]+\)|tested|asserted|aspirational)$")
REQUIRED = ("id", "claim", "tier", "evidence", "verified", "kill")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: A dated statement INSIDE an entry's prose (pc-d4c3). Anchored on the
#: century so a version number or an id cannot be read as a date.
TEXT_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
#: A count stated as current (pc-d15d): `16/16 at this writing`. A count
#: dated to a tag or record (`18/18 at pc-dad7`) is history and does not match.
PRESENT_COUNT = re.compile(r"\b\d+/\d+\s+at\s+this\s+writing\b", re.IGNORECASE)

# THE SCHEMA IS TYPED, NOT MERELY POPULATED (pc-1a93, round-7 lane D-F1;
# register accuracy's fourth recurrence, now at the register's own gate).
# Presence was the whole test, so `claim: true / evidence: true / kill: true`
# was certified "well-formed" at exit 0 — a YAML boolean is neither None nor
# "" — one field away from an invalid tier, which IS refused. Every field
# carrying prose or a command must be a STRING: a bool, a number, a list or a
# mapping there is a malformation whatever its truthiness, and certifying it
# is the register's own gate making exactly the claim rule 1 forbids.
#
# The key set is closed in the same pass, for the other half of the shape: an
# undeclared key is a field nothing reads and nothing checks, and the entry
# vocabulary is small and stable enough that admitting anything is not a
# service to a future author — it is the place a typo lives silently.
TEXT_FIELDS = ("id", "claim", "tier", "evidence", "kill")
OPTIONAL_FIELDS = ("notes",)
ID_SHAPE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def type_name(value: object) -> str:
    return type(value).__name__

CLAIMS_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "claims.yaml"

print(f"\nLedger well-formedness ({CLAIMS_PATH.name})")

# A DUPLICATE KEY IS A MALFORMATION, NOT A PREFERENCE (pc-4b3c, round-10 lane
# D-F1; register accuracy's seventh consecutive recurrence). PyYAML keeps the
# LAST of two keys and silently drops the rest, while the raw scan the gate
# join uses reads their UNION — so a second top-level `claims:` key made
# `dev/gates.py --audit` accept a claim this checker could not see, both at
# exit 0. The register cannot be two things at once, so neither reader picks:
# dev/claims_yaml.py refuses a repeated key at any depth here, and
# dev/claims_ids.py refuses the duplicate top-level `claims:` key there.
try:
    ledger, why_not = load_register(CLAIMS_PATH.read_text())
except Exception as exc:  # noqa: BLE001 — any read failure is the finding
    print(f"  ✗ claims.yaml cannot be read: {exc}", file=sys.stderr)
    sys.exit(1)
if why_not is not None:
    print(f"  ✗ {CLAIMS_PATH.name} is malformed — {why_not}", file=sys.stderr)
    print("\nclaims-check: 1 failure(s)", file=sys.stderr)
    sys.exit(1)

claims = (ledger or {}).get("claims") or []
if not claims:
    fail("no claims found in claims.yaml")

seen: set[str] = set()
for position, entry in enumerate(claims, start=1):
    if not isinstance(entry, dict):
        fail(f"claim #{position}: an entry must be a mapping, got "
             f"`{type_name(entry)}`")
        continue

    cid = entry.get("id", "<missing id>")
    if cid in seen:
        fail(f"{cid}: duplicate id")
    seen.add(cid)

    for field in REQUIRED:
        if field not in entry or entry[field] in (None, ""):
            fail(f"{cid}: missing required field `{field}`")

    # Typed, not merely present (pc-1a93). Reported per field so a boolean
    # claim beside a boolean kill names both, and separately from presence so
    # "missing" never has to stand in for "malformed".
    for field in TEXT_FIELDS:
        value = entry.get(field)
        if value is None or value == "":
            continue                      # already reported as missing
        if not isinstance(value, str):
            fail(f"{cid}: `{field}` must be text, got `{type_name(value)}` "
                 f"({value!r}) — presence is not well-formedness (pc-1a93)")
        elif not value.strip():
            fail(f"{cid}: `{field}` is blank — whitespace is not content")
    for field in OPTIONAL_FIELDS:
        if field in entry and not isinstance(entry[field], str):
            fail(f"{cid}: `{field}` must be text, got "
                 f"`{type_name(entry[field])}`")

    unknown = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL_FIELDS))
    if unknown:
        fail(f"{cid}: undeclared field(s) {', '.join(repr(u) for u in unknown)} "
             f"— the entry vocabulary is closed ({', '.join(REQUIRED + OPTIONAL_FIELDS)})")

    if isinstance(cid, str) and cid != "<missing id>" and not ID_SHAPE.match(cid):
        fail(f"{cid!r}: an id is a lowercase-kebab slug — it is the name prose "
             f"markers bind to, so it may not carry spaces or case")

    tier = str(entry.get("tier", ""))
    if not VALID_TIER.match(tier):
        fail(f"{cid}: invalid tier `{tier}`")

    verified = entry.get("verified")
    # A DATE OR THE WORD `never`, and nothing else (pc-1a93 sibling, same
    # pass). The tested/proved arms below already refuse anything that is not
    # YYYY-MM-DD, but `asserted` reached no arm at all — so `verified: true`
    # there was certified, in the one field whose whole job is saying when
    # somebody last looked.
    if verified not in (None, "") and not isinstance(verified, (str, date)):
        fail(f"{cid}: `verified` must be a date or `never`, got "
             f"`{type_name(verified)}` ({verified!r})")
    verified_str = verified.isoformat() if isinstance(verified, date) else str(verified)
    if isinstance(verified, str) and verified_str != "never" \
            and not DATE.match(verified_str):
        fail(f"{cid}: `verified` must be YYYY-MM-DD or `never`, got "
             f"`{verified_str}`")

    if tier == "aspirational":
        if verified_str != "never":
            fail(f"{cid}: aspirational claims must have `verified: never`, got `{verified_str}`")
        if str(entry.get("evidence")) != "none":
            fail(f"{cid}: aspirational claims must have `evidence: none`")
    elif tier in ("tested", "proved") or tier.startswith("machine-checked"):
        if not DATE.match(verified_str):
            fail(f"{cid}: tier `{tier}` requires a verified date (YYYY-MM-DD), got `{verified_str}`")
        if str(entry.get("evidence")) in ("none", "None"):
            fail(f"{cid}: tier `{tier}` requires real evidence, got `none`")
        if DATE.match(verified_str) and date.fromisoformat(verified_str) > date.today():
            fail(f"{cid}: verified date {verified_str} is in the future")

    # A STAMP MAY NOT PREDATE THE ENTRY'S OWN DATED TEXT (pc-d4c3, round-11
    # lane D-F3; register accuracy's eighth consecutive recurrence). This
    # register records corrections in dated sentences inside the entry —
    # "CORRECTED 2026-09-14 (pc-87e0, spec v2.12)" — and `verified` is a
    # separate field that nothing compared with them. reference-implementation
    # carried `verified: 2026-09-13` beside two corrections dated 2026-09-14:
    # a suite stamp cannot cover corrections it postdates, and no gate read
    # dates against dates.
    #
    # THE RULE IS AN ORDERING, NOT A RECOMPUTATION. It cannot tell whether the
    # evidence was really re-run; it refuses the one state that is provably
    # wrong — a stamp older than something the entry itself says happened —
    # which is all a text comparison is entitled to assert (rule 1).
    #
    # A DECLARED HEURISTIC, like the STAMP_COUNT convention below: any
    # YYYY-MM-DD in the entry's own claim, kill or notes is taken as a dated
    # statement about this entry. Every date in this register is one.
    if DATE.match(verified_str):
        own = sorted(set(TEXT_DATE.findall(
            " ".join(str(entry.get(f) or "") for f in ("claim", "kill", "notes")))))
        if own and own[-1] > verified_str:
            fail(f"{cid}: `verified` is {verified_str} while the entry's own "
                 f"text carries a statement dated {own[-1]} — a stamp cannot "
                 f"cover what it predates (pc-d4c3). Re-run the evidence and "
                 f"re-take the stamp; never move the date without it")

    # EVIDENCE IS ONE COMMAND (pc-c448, round-13 lane D-F1). `v2-storage`
    # registered its test command followed by a second line, a verification
    # banner, so running the evidence exactly as registered ran the tests to OK
    # and then exited 127 on `---`. This gate certified it because nothing
    # here ran or read the value's shape. A command is one line; anything
    # after it is either a second command nobody declared or prose that
    # cannot execute.
    if "\n" in str(entry.get("evidence") or "").strip():
        fail(f"{cid}: `evidence` spans more than one line — it is run as "
             f"registered, and a second line is a second command or prose "
             f"that cannot execute (pc-c448)")

    # NO FIELD STATES A COUNT AS CURRENT (pc-d15d, round-13 lane D-F2). The
    # formal-model claim said "16/16 at this writing" two tags after its own
    # gate moved to 18. Dated history ("18/18 at pc-dad7") is this register's
    # idiom and stays true; a count asserted as present is a second copy of a
    # number some gate owns, and it rots the day that gate moves. A DECLARED
    # HEURISTIC like STAMP_COUNT below: it catches the phrasing that rotted,
    # not every way of saying "now".
    for field in ("claim", "evidence", "kill", "notes"):
        if PRESENT_COUNT.search(str(entry.get(field) or "")):
            fail(f"{cid}: `{field}` states a count as current (`N/N at this "
                 f"writing`) — cite the gate that owns the number, or date "
                 f"the count to the tag or record it was true at (pc-d15d)")

# THE STAMP MAY NOT CARRY A COUNT (pc-c1f3, the register-accuracy class's
# third recurrence; the pc-e469 rule made substrate). A test count inside a
# verified-stamp comment is a prose count beside the one field that moves
# without it — it rotted twice ("58 suite-wide", then "657 tests discovered"
# against 810 at the tag). The count lives in the registered runner's own
# report, re-taken by running the evidence, never in this file. YAML parsing
# cannot see comments, so this reads the raw lines. A DECLARED HEURISTIC
# (the V015 convention): it catches the number-adjacent-to-"tests"
# signature, not every conceivable count phrasing.
#
# WIDENED 2026-09-18 (pc-9611, v2.16) FROM TEST COUNTS TO ANY COUNT. The rule
# was written for the shape that had rotted twice and caught only `N tests`,
# so `registry regenerated, 90 markers` sat in a stamp comment while the gate
# and the generated registry both said 91 — the same defect one noun over, in
# the same field, under a rule that already existed to stop it. A stamp
# comment says when somebody last looked; every count in one is a number
# beside machinery that moves without it, and the machinery prints its own
# (`vocab-check` emits `census.markers` in the run that regenerates).
#
# The lookbehind is what keeps a DATE and a VERSION out of it: the `18` of
# `2026-09-18` and the `16` of `v2.16` are both preceded by a separator, and
# neither is a count.
STAMP_COUNT = re.compile(
    r"^\s*verified:.*#.*?(?<![\d.\-])\b\d+\s+(?:\w+[- ]?)?"
    r"(?:[a-z][a-z-]{2,}s|tests?)\b",
    re.IGNORECASE)
for n, line in enumerate(CLAIMS_PATH.read_text().splitlines(), start=1):
    if STAMP_COUNT.search(line):
        fail(f"claims.yaml:{n}: the verified-stamp comment carries a count — "
             f"stamp counts rot (pc-c1f3, pc-9611); cite the gate or runner "
             f"that prints it and re-take the stamp by running the evidence, "
             f"never by editing the comment (pc-e469)")

if failures == 0:
    ok(f"all entries well-formed ({len(claims)} claims, ids unique, tiers valid)")

print()
if failures:
    print(f"claims-check: {failures} failure(s)", file=sys.stderr)
    sys.exit(1)
print("claims-check: clean")
