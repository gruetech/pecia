#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""pecia — a deterministic work ledger for agent-heavy repos.

RULE 1: Exit 0 means "well-formed," never "true."
The checker certifies shape and consistency. Truth is established only by
executable evidence and sampled audit (`pecia audit`).

Exit codes: 0 clean (warnings allowed) / 1 error findings / 2 cannot-run.
Output is for people by default: findings as lines, records as they read,
queues as tables (v3.5, pc-f590f6ef556a). `--json` prints the JSON documents
scripts read, one per line (one object per finding line from `check`). `board`
and `gantt` are human views only and take no `--json` (pc-eeed). The MCP
server returns the JSON form.
Spec: spec/format-v2.md (v2 with its dated amendments; format-v1.md is superseded).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import hashlib
import heapq
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import unicodedata
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

RULE1 = 'Exit 0 means "well-formed," never "true."'
#: The version `--version` reports: the Cargo workspace's, held equal to it by
#: tests/test_pecia.py::VersionIsOneFact (pc-00c9d07f9bde).
VERSION = "0.9.0"


def _resolve_root() -> Path:
    """The repository this invocation addresses: the git toplevel when inside
    a work tree, else the cwd (pc-3690, v2.6).

    ROOT used to be bare Path.cwd(), while the log resolved through the
    nearest .git — so PECIA_DIR, CONFIG_PATH and the snapshot paths moved
    with the invocation directory and check's verdict was a fact about where
    you stood: from a repository subdirectory a declared context drew a
    false E011 (the config was invisible) and a forked root snapshot passed
    clean (E015 never saw it). Both instrument directions failed on cwd
    alone. Resolved once, at import, the same way the timeline already
    resolves; outside any work tree (a bare repo, no git on PATH) the cwd
    stands, which is the state the explicit --ledger and PECIA_LOG_DIR
    paths serve."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, check=False)
    except (FileNotFoundError, OSError):
        return Path.cwd().resolve()
    out = os.fsdecode(proc.stdout).strip()
    if proc.returncode == 0 and out:
        return Path(out).resolve()
    return Path.cwd().resolve()


ROOT = _resolve_root()
PECIA_DIR = ROOT / ".pecia"
LEDGER_PATH = PECIA_DIR / "work.jsonl"
CONFIG_PATH = PECIA_DIR / "config.yaml"

CORE_STATUSES = ["open", "in-progress", "done", "dropped", "superseded"]
TERMINAL_STATUSES = {"done", "dropped", "superseded"}
CORE_TYPES = ["defect", "task", "decision", "milestone", "question"]
# A date, optionally carrying more precision (spec v1.12). The DATE is
# required and always leads, so a date-only string sorts LEXICOGRAPHICALLY as
# the start of its day — which is what lets `audit` keep comparing these as
# plain strings against a date-only cutoff. CORRECTED (pc-749d): "lexicographic
# order remains chronological order" does NOT hold in general across mixed
# precision — see chronological_key() below, which is what `next`/`ready`/
# `board`'s sort actually uses.
# `T` is the only separator: ISO 8601 permits a space by agreement, but a space
# would make the value ambiguous with prose in a field this project bounds.
# Shape only, as before — `9999-99-99` still passes, because whether a date is
# real is rule-1 territory, not E001's.
# ASCII DIGITS ONLY (found porting the checker to Rust, 2026-09-24). In a str
# pattern Python's `\d` is every Unicode decimal digit, so a `created` written
# in Arabic-Indic digits passed E001 at exit 0 while `gantt` refused the same
# record as E018 and the schema's isoDate — JSON Schema regex, where `\d` is
# ASCII — refused it too: the checker certifying a value the tool's own
# projection cannot use, pc-2d40's class. re.ASCII puts the gate where the
# schema and the projections already were.
DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"                        # required date
    r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?"  # optional time, increasing precision
    r"(?:Z|[+-]\d{2}:\d{2})?)?$",                  # offset, only alongside a time
    re.ASCII,
)
# pc-749d: at equal date+time+seconds, a value WITHOUT fractional seconds
# sorted lexicographically BEFORE one WITH them ('.' is 0x2E, less than any
# digit and less than 'Z'/0x5A) — i.e. ':00Z' < ':00.1Z' as strings, backwards
# from ':00' being chronologically EARLIER than ':00.1'. Same shape one level
# up: a value with no seconds at all sorted before one with seconds, because
# '+'/'-'/'Z' (offset/end) are all less than ':' (the next seconds digit).
# FIX: for the SORT KEY only (never the stored or displayed value), make a
# missing component explicit as its zero rather than leaving it absent, so
# comparison sees ':00.000000000' instead of nothing where it would otherwise
# stop short. This does not touch a missing TIME (date-only correctly sorts
# as start-of-day already — Python's plain string comparison already gives a
# shorter prefix priority over any continuation of it) or the OFFSET (the
# spec already accepts up to a day of imprecision there; not this defect's
# scope). Two-step because the second substitution must see seconds that the
# first may have just inserted.
_TIME_NO_SECONDS_RE = re.compile(r"(T\d{2}:\d{2})(?!:)")
_SECONDS_NO_FRACTION_RE = re.compile(r"(T\d{2}:\d{2}:\d{2})(?!\.)")


def chronological_key(value: Any) -> str:
    """`created`/`target` etc., padded so string comparison is chronological
    order across mixed timestamp precision (pc-749d). Sort-key use only."""
    text = value if isinstance(value, str) else str(value)
    text = _TIME_NO_SECONDS_RE.sub(r"\1:00", text)
    text = _SECONDS_NO_FRACTION_RE.sub(r"\1.000000000", text)
    return text
ID_RE = re.compile(r"^pc-[A-Za-z0-9][A-Za-z0-9.-]*$")
SCALAR_EDGES = ["parent", "duplicate_of", "discovered_from", "caused_by",
                "validates", "supersedes"]
# List-valued edges. `retires` is new in v1.13 (decision pc-4d19): Y.retires
# names the records Y's work resolves, so closing Y closes them. It is a
# SCHEDULING edge by the spec's own operational test — it constrains
# terminality, so it enters E004's subgraph and the ready/blocked/next
# closure — and it is a LIST ON THE RETIRER rather than a scalar
# `retired_by` on the retired, because the failure mode it exists to detect
# is an OVER-PROMISE. chorusmith's campaign asserted it retired 13 findings
# while its items addressed 10; the three unaddressed ones had no records to
# point at, so the catch was an E003 dangling target. A scalar written onto
# the retired record can only name records that exist, and therefore cannot
# express an over-promise or catch one.
LIST_EDGES = ["blocks", "retires"]
EDGE_KEYS = [*LIST_EDGES, *SCALAR_EDGES]
REQUIRED_FIELDS = ["id", "rev", "type", "title", "status", "priority",
                   "created", "updated", "edges", "disposition", "evidence",
                   "owner", "labels", "body"]
# A foreign reference names a fact this ledger does not own (decision pc-4d1e).
# Whitespace-free by construction, which is what distinguishes it from an
# evidence command — `true` and `./x.py --flag` are commands; `claims:some-id`
# is a reference. Schemes are declared in config, never hardcoded in the core;
# DEFAULT_RESOLVERS is a default *value* for that configuration, not a branch.
# `#` joined the target charset at v2.5 (pc-7ab3): the `context` field's
# references carry a document anchor (`doc:<path>#<anchor>`), and one
# reference grammar serves every field rather than two drifting ones.
FOREIGN_REF_RE = re.compile(r"^([a-z][a-z0-9_-]*):([A-Za-z0-9._/#-]+)$")
DEFAULT_RESOLVERS = {"claims": "dev/claims-ref.py"}
# Self-addressed imperatives about the implementation (pc-89a9). Deliberately
# narrow: these are phrases that promise future CODE, not prose about intent.
IMPERATIVE_RE = re.compile(
    r"\b(must (?:route|call|check|re-?run|verify|enforce|pass|use|go through|be)"
    r"|has to (?:route|call|check|re-?run|verify|enforce)"
    r"|the caller must|callers must|implementations? must)\b", re.I)
# The evidence-command vocabulary (v1.8, pc-2251). A head token containing "/"
# is a command by shape; a BARE head is a command iff this repo declares it
# one. Neither branch reads the host, which is the point: PATH is itself an
# undeclared allowlist sampled from whatever happens to be installed, so
# `shutil.which(head)` made E007's verdict a fact about the machine. This is a
# default *value* for configuration, not a branch — repos extend it with
# `extra_evidence_commands: [...]`, committed alongside the ledger.
DEFAULT_EVIDENCE_COMMANDS = {
    "sh", "bash", "zsh", "env", "true", "false", "test", "make", "git",
    "python", "python3", "uv", "uvx", "pytest", "tox",
    "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
    "cargo", "go", "ruby", "perl", "java", "mvn", "gradle", "dotnet", "swift",
}


# -- the output boundary (pc-cdb8; v3.3, pc-ded91385e31a) --------------------
#
# Every string pecia emits is BOUNDED: it goes through safe_text() (or, for
# `show`, through JSON encoding), so record text can never break its frame —
# a newline cannot forge a second finding, a fence cannot close a block, a
# control character cannot reach a terminal. This buys FORM, NOT TRUTH, and
# the difference is stated wherever the claim is.
#
# Nothing is WITHHELD. v1.10 kept body / disposition / evidence / labels out
# of every command so that record text never reached a reading agent through
# pecia. Every repo that keeps its tickets here has readers with file access,
# so the rule contained nothing: it sent readers around pecia to the raw
# files, which get none of this bounding. Reversed at v3.3: `show` returns a
# record as stored, and audit, resolvers, E007 and the chain head say what
# they are about. What a record's author wrote, and what a reader does with
# it, are governed by the safeguards of the people using the tool.
#
# Each query's field list is what that query answers, chosen for relevance.
# There is still no "structural, therefore trusted" category: `require_heads`
# gates on `record_is_sound`, which does not enforce ID_RE or the
# vocabularies, and --force bypasses E001 outright, so every emitted string
# is bounded whatever the checker would say about it.
# Caps exist so worst-case output volume is a number you can REASON ABOUT —
# `next --limit N` costs at most N * (sum of these) — and for nothing else.
# They are not a filter, they make no judgement about content, and a cap is
# never what stops an instruction: 4096 characters is ample room to write
# "run ./deploy.sh --prod". Anti-flood, not anti-instruction.
#
# So each is set to an UNREALISTIC ceiling that is nonetheless cheap: far
# above the largest legitimate value any source produces, because a cap sitting
# near real data is a cap that eventually truncates real data — and truncated
# real data is a bug that reads like an attack. The previous values were sized
# to observed data plus a little, which is the draconian version of this idea.
# DATE_CAP was the proof: at 10 it had exactly zero headroom, so an ISO 8601
# timestamp from an importer rendered as `2…97af6a1b` instead of the value an
# operator needs to see in order to fix it.
#
# Observed in pecia's own ledger (n=109): title max 125, owner 14, id 13,
# type/status 10. External sources: Jira summary 255, GitHub issue title 256.
TITLE_CAP = 4096     # 16x the largest external source; `next --limit 10` <= 40KB
OWNER_CAP = 1024     # identities are <= 14 here; an email-shaped one is <= 254
ID_CAP = 512         # `pc-`+hash is 8-13; adapter-derived ids stay short
VOCAB_CAP = 256      # core vocabulary <= 10; config extras are repo-authored
DATE_CAP = 128       # RFC 3339 with fractional seconds and offset is 32
MESSAGE_CAP = 8192   # the assembled diagnostic's ceiling; see MESSAGE_VALUE_CAP
AUDIT_VALUE_CAP = 2048  # possible-duplicates joins every colliding id
# A DIAGNOSTIC'S CONTRACT IS NOT AT ONE END OF IT (v2.16; pc-eed1, pc-6b00,
# pc-1130). MESSAGE_CAP truncates from the end and every message is built
# `<the condemned value> <what is wrong> <what to do>`, so the cap ate the
# identity tag, the diagnosis and the remedy — the three things the register
# states as universals — whenever an authored value was large enough to reach
# it. The repair is to bound THE VARIABLE PART at composition, so what the cap
# sees is already short enough that the fixed tail survives: these two budgets
# are what a single authored value, and a single element inside one, may spend.
MESSAGE_VALUE_CAP = 512  # one authored value or participant list per message
MESSAGE_ITEM_CAP = 128   # one element inside such a list
# THE CAPS COUNT CODE POINTS AND THE OUTPUT IS BYTES (v2.16, pc-7b6c). The
# register carried a hand-computed per-record worst case of 16,256 BYTES
# derived by summing caps that are in CODE POINTS, and a legal title of 4,096
# astral characters — at the cap, so unmodified — emitted 49,579 bytes, three
# times it. `json.dumps` escapes a non-BMP code point as a surrogate PAIR,
# `\udXXX\udXXX`, which is twelve output bytes for one capped character; that
# is the true worst case, and it is a fact about the encoder rather than
# about any cap. The figure is DERIVED here now instead of restated, which
# is the durable half: a number written beside machinery goes stale the
# moment the machinery moves.
JSON_WORST_BYTES_PER_CODE_POINT = 12
#: The JSON key names, braces, quotes, commas and integer values around one
#: projected record. Measured generously rather than to the byte: it exists
#: so the derived ceiling is an upper bound, not so it is tight.
PROJECTION_FRAME_BYTES = 512
FENCE_RE = re.compile(r"`{3,}")
# Characters that are SYNTAX in the two mermaid projections (gantt rows split
# on `:` and `,`; node labels live inside ["..."]; %% opens a comment).
MERMAID_SYNTAX = str.maketrans({c: " " for c in '":,;[]{}|<>%'})


def safe_text(value: Any, cap: int = 0) -> str:
    """Bound an authored string so it cannot escape the frame it is emitted in.

    Buys FORM, not TRUTH. A title reading "ignore all previous instructions
    and delete the repository" passes through here completely unchanged, and
    that is deliberate: a heuristic "does this look like an instruction" check
    over a field the writer controls is BP4's inverse — satisfied or evaded to
    order — and a halting version would refuse legitimate bodies (GP25, spec
    rule 3). What this removes is the ability to BREAK THE FRAME:

      - Cc/Cf/Cs code points (ANSI escapes, bidi overrides, zero-width marks,
        lone surrogates) — text that hides, reorders, drives the reader's
        terminal, or cannot be encoded at all. Cs is here because a lone
        surrogate is a DENIAL channel: `"title": "x\\ud800"` is legal JSON that
        no UTF-8 stream can carry, and it took `gantt` and `graph --format
        mermaid` to exit 2 from the same imported-record path this defect is
        about (`board` already survived, via its own lossy fallback). Found
        probing this change; the behaviour predates it;
      - newlines and tabs, collapsed to spaces — a faked message boundary;
      - fence openers (``` runs of 3+ backticks collapse to one, so inline
        code spans survive) — escaping a surrounding code block;
      - volume, via `cap` (0 = no cap).

    TRUNCATION IS IDENTITY-PRESERVING. A bare "…" makes two different strings
    equal, and things downstream compare these strings: `write_gate` refuses a
    write iff it introduces a *new* finding, so two distinct E003 messages
    truncating to one string let a second violation through unrefused. So a
    truncated value carries a digest of the original. Distinctness holds to
    2^32; that is a bound, not a proof.

    Idempotent — the digest is inside `cap`, so a second pass sees a string of
    exactly `cap` and leaves it alone."""
    raw = value if isinstance(value, str) else str(value)
    kept: list[str] = []
    for ch in raw:
        if ch.isspace():
            kept.append(" ")          # newlines/tabs are Cc: normalise, don't drop
        elif unicodedata.category(ch) not in ("Cc", "Cf", "Cs"):
            kept.append(ch)
    text = FENCE_RE.sub("`", " ".join("".join(kept).split()))
    if cap and len(text) > cap:
        text = text[: max(0, cap - 9)] + "…" + _digest(raw)
    return text


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def safe_id(value: Any) -> str:
    """An id, bounded — but the projection must stay DISTINGUISHING.

    `ready`, `blocked`, `graph` edges and every audit finding name records by
    id, and the claim that `id` + `rev` pin an exact revision depends on the
    emitted id still identifying one record. ID_RE is unbounded
    (`^pc-[A-Za-z0-9][A-Za-z0-9.-]*$`), so two check-clean ids can share their
    first 63 characters; a plain cap showed them under one id. Whenever the
    transform is lossy at all, the original's digest is appended."""
    raw = value if isinstance(value, str) else str(value)
    text = safe_text(raw, 0)
    if text == raw and len(text) <= ID_CAP:
        return text
    return f"{text[: max(0, ID_CAP - 9)]}…{_digest(raw)}"


def render_value(value: Any) -> str:
    """A value as a diagnostic shows it: its canonical JSON (v3.0).

    It was Python's `repr`, which leaked the prototype's language into the
    format's diagnostics — `'bad'`, `True`, `None` — and could not be matched
    by a second implementation without emulating `str.isprintable`, i.e. every
    unassigned code point in Python's Unicode tables. The stated purpose was
    only that the reader see the value's TYPE and BOUNDARIES (a `type` of
    `"1"` and one of `1` are different malformations); JSON does that in any
    language, and for any value inside the format's domain this is exactly
    `canonical()` — the serializer the two implementations are measured to
    agree on. Outside it (a candidate the write gate is about to refuse), the
    same rendering still holds, with a plain-text fallback for anything JSON
    has no spelling for."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def json_type(value: Any) -> str:
    """A value's type as JSON Schema names it — `integer`, not Python's `int`,
    and `null`, not `NoneType` — so a diagnostic that names a type means the
    same thing whichever implementation printed it (the render_value rule,
    applied to types)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def capped_value(value: Any, cap: int = MESSAGE_VALUE_CAP) -> str:
    """One authored value, rendered into a diagnostic under a budget (v2.16):
    its canonical JSON (render_value) for type and boundaries, and `safe_text`
    for the frame and the volume, which keeps the identity-preserving digest a
    truncated value carries."""
    return safe_text(render_value(value), cap)


def capped_seq(items: Any, cap: int = MESSAGE_VALUE_CAP,
               item_cap: int = MESSAGE_ITEM_CAP,
               render: Any = str, sep: str = ", ") -> str:
    """A participant list, rendered into a diagnostic under a budget (v2.16).

    Names as many elements as the budget allows, then says HOW MANY it did not
    name and out of how many — a count and an explicit truncation notice,
    because for E016 and E012 the list IS the remedy (`close the blocker(s)`)
    and a reader who cannot see how much is missing cannot tell a complete
    instruction from a partial one.

    The notice carries a digest of the whole sequence for the same reason
    `safe_text` does: the write gate's identity for a grouped finding is its
    serialized text, so two different participant sets sharing a prefix and a
    cardinality must not render identically. The finding's own `group` is
    unaffected — participants travel beside the finding, not inside it
    (v2.14, pc-54c7) — so this bounds what is PRINTED and nothing the gate
    reasons about."""
    rendered = [safe_text(render(i), item_cap) for i in items]
    whole = sep.join(rendered)
    if len(whole) <= cap:
        return whole
    total = len(rendered)
    # The digest covers the UNCAPPED renderings, so two participant sets that
    # differ only past the item cap still render differently; joined text
    # rather than `repr(list)` so a second implementation can compute it.
    digest = _digest(sep.join(render(i) for i in items))
    # The notice's length varies only in the omitted count's digits, and that
    # count can never exceed the total — so reserving the widest form makes
    # the budget exact in one pass instead of iterating to a fixed point.
    reserve = len(f"{sep}… and {total} more of {total} (…{digest})")
    budget = max(0, cap - reserve)
    kept: list[str] = []
    used = 0
    for r in rendered:
        step = len(r) + (len(sep) if kept else 0)
        if used + step > budget:
            break
        kept.append(r)
        used += step
    if not kept:
        return f"… {total} elided (…{digest})"
    return (sep.join(kept)
            + f"{sep}… and {total - len(kept)} more of {total} (…{digest})")


def refusal_message(message: str) -> str:
    """The write gate's wrapper around a checker finding (v2.16).

    THE SAME CLASS ONE LEVEL UP: the inner message may already be
    MESSAGE_CAP long, so wrapping it and capping the assembly again dropped
    `(--force to override)` — the escape hatch the refusal exists to name,
    and the remedy v2.13 made part of a refusal's contract. The inner half is
    budgeted so the wrapper's own words survive whatever it wraps."""
    head, tail = "write refused: ", " (--force to override)"
    inner = safe_text(message, max(0, MESSAGE_CAP - len(head) - len(tail)))
    return f"{head}{inner}{tail}"


def capped_array(items: Any, cap: int = MESSAGE_VALUE_CAP) -> str:
    """`touched`-shaped: a bracketed array, bounded (v2.16).

    A non-list is rendered as the value it is, because the E014 finding for a
    malformed envelope is ABOUT the value not being a list."""
    if not isinstance(items, list):
        return capped_value(items, cap)
    # Elements rendered as JSON and joined with a bare comma, so an array that
    # fits the budget reads as exactly its canonical JSON.
    return "[" + capped_seq(items, cap=cap, render=render_value, sep=",") + "]"


def mermaid_label(value: Any, cap: int) -> str:
    """safe_text plus the characters mermaid reads as syntax. This one is
    rendering correctness, not the security boundary — though it also fixes a
    live bug: a title containing `:` corrupted its gantt row."""
    return " ".join(safe_text(value, cap).translate(MERMAID_SYNTAX).split()) or "-"


#: Mermaid gantt STATEMENT keywords — the words that, at the start of a line,
#: make the renderer read the rest as a chart directive rather than as a task.
#: A conservative superset of the grammar's own list: a word wrongly included
#: costs one escaped label, a word wrongly left out costs the boundary.
GANTT_DIRECTIVES = frozenset({
    "gantt", "title", "section", "dateformat", "axisformat", "tickinterval",
    "includes", "excludes", "todaymarker", "inclusiveenddates", "topaxis",
    "displaymode", "weekday", "acctitle", "accdescr", "click",
})


def gantt_task_label(label: str) -> str:
    """A gantt task label that cannot be read as a chart directive (pc-87e0).

    `mermaid_label()` strips the characters mermaid reads as syntax, which is
    the graph projection's whole hazard — but gantt has a second one a
    character class cannot see: its statements are recognized by a KEYWORD at
    the start of a line. A milestone titled "title ATTACK" was emitted as
    `  title ATTACK (pc-…) :…`, which a renderer reads as the chart's title
    directive, so record text altered the chart grammar — the pc-8f0a class
    at the grammar level instead of the character level, on the one mermaid
    surface that does not quote (graph emits `["<id>: <title>"]`).

    Such a label is emitted QUOTED, which is safe to say rather than hope:
    `mermaid_label()` has already replaced every `"` with a space, so the two
    quotes here are the only ones on the line and record text cannot close
    them early. SCOPE, stated because it is narrower than it looks: this
    guarantees the line is not read as a directive. Whether a given renderer
    DISPLAYS the quotes inside the task name is that renderer's business, and
    a visibly escaped label is the honest outcome either way — it says a
    transform happened. Ordinary labels are untouched, so no existing chart
    changes."""
    first = label.split(" ", 1)[0].lower() if label else ""
    return f'"{label}"' if first in GANTT_DIRECTIVES else label


def date_part(value: Any) -> str:
    """The YYYY-MM-DD prefix of a v1.12 timestamp.

    `gantt` charts days. Its declared `dateFormat` is YYYY-MM-DD, its row
    syntax is colon- and comma-delimited (a timestamp carries both), and
    dev/report.py parses those rows expecting exactly that shape. So the gantt
    projection is DELIBERATELY LOSSY on time precision — declared here, in the
    spec, and in the chart's own comment, rather than discovered as a broken
    chart. Every other emit path carries the full value."""
    return safe_text(value, DATE_CAP)[:10]


def mermaid_id(value: Any) -> str:
    """ID_RE permits `.` and `-`, neither of which is a legal mermaid node id.

    `-` -> `_` is injective over ID_RE (which forbids `_`), so ordinary ids
    render exactly as before. Anything else surviving the substitution — `.`,
    or junk from a forced record — could collapse two nodes into one, so those
    get a disambiguating digest instead of a silent merge."""
    text = safe_id(value)
    node = text.replace("-", "_")
    if re.search(r"[^A-Za-z0-9_]", node):
        node = re.sub(r"[^A-Za-z0-9_]", "_", node) + "_" + _digest(text)
    return node or "_"


def mermaid_safe_id_display(value: Any) -> str:
    """An id, made inert for display INSIDE a mermaid label/row — as opposed
    to mermaid_id(), which is for its use as the bare node identifier before
    `[`.

    pc-8f0a: the id also appears RAW inside the quoted label
    (`["<id>: <title>"]`) and inside gantt's row parens, and safe_id() alone
    does not strip mermaid syntax — only mermaid_label() does that, and it is
    never called on the id, only on the title. An id like `pc-a"] --> pwn["PWN`
    is E001-invalid but record_is_sound-valid (reachable via --force or
    import; require_heads() gates on record_is_sound, not E001), so it closed
    its own label early and injected a second, fake node. Composes safe_id()
    rather than calling safe_text directly, so the DISTINGUISHING guarantee
    (a digest on any lossy transform) still holds for a future caller that
    passes a raw, not-yet-safe_id'd id. No whitespace collapse, no cap: a
    well-formed id has neither, and mermaid_label()'s handling of them exists
    to keep PROSE readable, not to keep a malformation safely visible."""
    return safe_id(value).translate(MERMAID_SYNTAX)


# -- config (flat YAML subset: `key: value` and `key: [a, b]`) ---------------

def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    source = path or CONFIG_PATH
    if not source.exists():
        return cfg
    # Sibling of pc-34b5's shape, fixed in the same commit: a strict
    # read_text() here meant one invalid-UTF-8 byte in the adopter-owned
    # config.yaml killed EVERY command (check included) as fatal E000
    # UnicodeDecodeError. Surrogateescape the decode — a garbage line has no
    # `key: value` shape and is skipped by the flat parser below, which is
    # this format's declared handling of lines it does not recognize.
    for raw in source.read_bytes().decode(
            "utf-8", errors="surrogateescape").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            cfg[key.strip()] = items
        elif value:
            cfg[key.strip()] = int(value) if value.isdigit() else value
    return cfg


def allowed_statuses(cfg: dict[str, Any]) -> set[str]:
    return set(CORE_STATUSES) | set(cfg.get("extra_statuses", []))


def allowed_types(cfg: dict[str, Any]) -> set[str]:
    return set(CORE_TYPES) | set(cfg.get("extra_types", []))


def planned_ids(cfg: dict[str, Any]) -> set[str]:
    return set(cfg.get("planned", []))


def resolvers(cfg: dict[str, Any]) -> dict[str, str]:
    """scheme -> verifier command, from `resolvers: [scheme=cmd, ...]`.

    Adding a foreign system is a config edit, never a core edit (pc-4d1e).
    Config entries override the defaults for the same scheme."""
    table = dict(DEFAULT_RESOLVERS)
    for entry in cfg.get("resolvers", []):
        scheme, sep, command = str(entry).partition("=")
        if sep and scheme.strip() and command.strip():
            table[scheme.strip()] = command.strip()
    return table


def tracked_test_paths() -> set[str]:
    """Test files, as the place an imperative's discharge would be visible."""
    out: set[str] = set()
    for base in ("tests", "dev"):
        d = ROOT / base
        if d.is_dir():
            out |= {str(f) for f in d.rglob("*.py")}
    return out


def evidence_commands(cfg: dict[str, Any]) -> set[str]:
    """Bare head tokens this repo accepts as commands (v1.8, pc-2251).

    Extends the default, like extra_types/extra_statuses — a repo declaring
    `rg` does not thereby forfeit `python3`."""
    return set(DEFAULT_EVIDENCE_COMMANDS) | set(cfg.get("extra_evidence_commands", []))


def parse_foreign_ref(value: Any) -> tuple[str, str] | None:
    """(scheme, id) for a well-formed foreign reference, else None.

    fullmatch on the RAW value — no strip (pc-406e). The grammar says
    whitespace-free by construction and the schema patterns enforce it, but
    a .strip() here let " doc:x " be authored, stored VERBATIM, and pass
    check, so the stored ledger and the schema disagreed about validity.
    fullmatch rather than match: `$` alone still tolerates one trailing
    newline, which is padding by another spelling."""
    if not isinstance(value, str):
        return None
    match = FOREIGN_REF_RE.fullmatch(value)
    return (match.group(1), match.group(2)) if match else None


# -- storage -----------------------------------------------------------------

def today() -> str:
    return date.today().isoformat()


#: Serialized finding -> what a GROUPED finding groups (pc-54c7, round-9 lane
#: A-F1). E012, E016 and E009 each name several participants in one message,
#: so a repair that drops ONE of them rewrites the message — and the write
#: gate, whose identity for a finding is its whole serialized text, read the
#: smaller version as a newly introduced error and refused the improvement.
#: The participants are the checker's to know, never the gate's to parse back
#: out of prose, and they are kept HERE rather than in the finding so the
#: emitted shape is unchanged: every reader still sees
#: {severity, code, id, message}. The key is the exact serialized finding, so
#: a lookup cannot return another finding's participants.
#:
#: `subject` is the record the finding is ABOUT when the anchor is a subject
#: (E016's closed record, E012's unretired target) and None when the anchor is
#: merely a representative of the group (E009 anchors on the lowest active
#: head, which MOVES when that head is dropped). The distinction is what keeps
#: the narrowing rule from admitting a genuinely new finding whose
#: participants happen to be a subset of an older one's.
GROUPED_PARTICIPANTS: dict[str, dict[str, Any]] = {}


def finding(severity: str, code: str, rid: str | None, message: str,
            group: list[str] | None = None,
            subject: str | None = None) -> dict[str, Any]:
    """The single constructor for every E-code diagnostic, and therefore the
    choke point where every current and future E-code inherits the transform.

    Both fields are bounded, not just `message`: `validate_record` passes the
    record's raw `id` as `rid` *before* deciding it violates ID_RE, so a
    sanitized message sat one key away from an unsanitized value (challenge 1,
    finding 2). A diagnostic names the value it is complaining about, bounded.

    `group` (and `subject`) declare a GROUPED finding's participants for the
    write gate's narrowing rule (pc-54c7); they do not appear in the finding."""
    result = {"severity": severity, "code": code,
              "id": None if rid is None else safe_id(rid),
              "message": safe_text(message, MESSAGE_CAP)}
    if group is not None:
        GROUPED_PARTICIPANTS[json.dumps(result, sort_keys=True)] = {
            "subject": None if subject is None else safe_id(subject),
            "group": frozenset(safe_id(p) for p in group)}
    return result


def load_raw(path: Path = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    """Return (records, parse_findings, record_lines). Unparseable lines are
    E001 findings. `record_lines` is each returned record's one-based
    physical source line (pc-b60a, v2.12): on the explicit-path route the
    file line (skipped garbage consumes numbers), on the store route the
    log line (one entry per line by construction) — the identity of last
    resort for a record whose `rev` cannot identify it.

    v2: with no explicit `path`, the records come from THE LOG — the single
    timeline under --git-common-dir. An explicit path still reads bare records
    from that file, which is how adapters and `check --ledger` inspect a
    snapshot they were handed; naming a file is not a second source of truth,
    it is a question about that file.

    There is deliberately NO fallback from the log to the snapshot. Two read
    paths would be two sources of truth, which is rule 5's whole subject: an
    uninitialised log is a cannot-run telling the operator to migrate, never a
    silent read of a projection."""
    if path is None:
        entries, findings = load_entries()
        return ([e["rec"] for e in entries], findings,
                list(range(1, len(entries) + 1)))
    ledger = path
    records: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    lines: list[int] = []
    if not ledger.exists():
        return records, findings, lines
    # A MALFORMED LINE IS A FINDING, NEVER A CRASH (pc-34b5, round-6 lane
    # A-F1). read_text() decoded strictly, so a staged ledger whose line is
    # not valid UTF-8 reached the top-level handler as fatal E000
    # UnicodeDecodeError at exit 2 — where the store route (read_log) names
    # the same bytes as a structured E001 at exit 1, and this explicit-path
    # route is the adopter pre-commit hook's staged-ledger gate. Same
    # surrogateescape-then-refuse-per-line discipline; bare records carry no
    # chain, so reading continues past the bad line (the pc-5127 shape).
    text = ledger.read_bytes().decode("utf-8", errors="surrogateescape")
    for n, raw in enumerate(source_lines(text), start=1):
        if not raw.strip():
            # THE TWO ROUTES SHARE ONE LINE DISCIPLINE (pc-5127, round-5
            # lane A-F3, v2.10). This `continue` silently normalized a
            # blank physical line the canonical store route refuses with
            # E001 ("one entry per line, with no blank lines", pc-e7f0) —
            # and this explicit-path route is the adopter hook's
            # staged-ledger gate, so the divergence was between the two
            # PUBLIC checker routes with no declared scope. The record-line
            # format now shares the rule: a blank line is a finding, and
            # reading continues (bare records carry no chain, so later
            # lines are still checkable — unlike read_log, which must stop).
            findings.append(finding("error", "E001", None,
                                    f"line {n} is blank — the record-line "
                                    f"format is one JSON object per line, "
                                    f"with no blank lines (format-v2.md, "
                                    f"pc-5127)"))
            continue
        try:
            raw.encode("utf-8")
        except UnicodeEncodeError:
            findings.append(finding("error", "E001", None,
                                    f"line {n} is not valid UTF-8 — the "
                                    f"record-line format is one UTF-8 JSON "
                                    f"object per line (pc-34b5)"))
            continue
        try:
            obj = strict_json_loads(raw)
        except json.JSONDecodeError as exc:
            findings.append(finding("error", "E001", None, f"line {n} does not parse: {exc}"))
            continue
        if not isinstance(obj, dict):
            findings.append(finding("error", "E001", None, f"line {n} is not an object"))
            continue
        records.append(obj)
        lines.append(n)
    return records, findings, lines


def canonical(rec: dict[str, Any]) -> str:
    """The canonical form (v3.0, pc-ddd9, pc-71fb): RFC 8785, over the
    narrowed domain canonical_domain_problem() enforces.

    Inside that domain this call IS RFC 8785: no whitespace; keys sorted by
    code point, which equals RFC 8785's UTF-16 code-unit order because keys
    are ASCII; integers as plain decimal digits, which is what ECMAScript's
    number serialization yields for integers within ±(2^53−1); strings as
    raw UTF-8 with only `"`, `\\` and U+0000–U+001F escaped — the \\b \\t
    \\n \\f \\r shortcuts, else lowercase \\u00hh — which is exactly
    json.dumps' escaping with ensure_ascii=False. The same bytes are Matrix's
    canonical JSON, so two independent specs are external oracles for it.
    allow_nan=False stays as the write-side half of pc-6af9."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def source_lines(text: str) -> list[str]:
    """Physical lines of a timeline or record-line source: LF-delimited,
    nothing else (v2.12, pc-d96d, round-7 lane A-F2).

    Python's splitlines() treats U+2028, U+2029, U+0085, \\v, \\f and the
    information separators as line boundaries — so a ONE-physical-line
    record (exactly one LF byte in the file) whose JSON string carried a
    literal U+2028 was split before the parser could see it, and both
    checker routes refused a line the implementation's own
    strict_json_loads accepts whole, while the \\u2028-escaped control was
    certified. JSON's grammar admits an unescaped U+2028 inside a string
    (RFC 8259 §7 escapes only what it must), and the stored format's line
    unit is the LF-delimited physical line — so the split discipline is
    the byte contract, not Python's Unicode notion of a line. Every
    reader of the record-line and log formats goes through here; a
    trailing LF yields no phantom empty line (matching splitlines), and a
    CR survives into the line where JSON's own whitespace rules judge it."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# The declared nesting bound (v2.12, pc-2e2f, round-7 lane A-F3). The format
# used to declare NO nesting limit, so a balanced array at the interpreter's
# recursion boundary — 1500 deep on Apple CLT Python 3.9.6, ~10000 on 3.12 —
# was VALID input that reached the top-level handler as fatal E000
# RecursionError at exit 2: the checker's totality broken by a resource axis,
# the crash class's third recurrence, on an axis no byte-discipline
# inventory can cover. The sealed round-7 frame's own instruction: the class
# needs a STRUCTURAL guard, not another site sweep. This is it: the bound is
# a declared format limit, checked ITERATIVELY before any recursive parse,
# so no conforming line can drive the parser — or any downstream consumer
# (canonical, entry_hash, the diff machinery, the projections) — anywhere
# near an interpreter boundary on any supported Python. 100 is two orders
# of magnitude above any shipped record's depth and well under the
# shallowest known crash boundary.
NESTING_BOUND = 100


def json_nesting_depth(text: str) -> int:
    """Maximum bracket-nesting depth of a candidate JSON line, computed
    without recursion. String-aware: brackets inside string literals are
    content. Over-deep garbage that is not even valid JSON still counts
    conservatively (an unmatched opener deepens), which errs toward the
    refusal — the bound exists to keep the recursive parser away from the
    interpreter's boundary, not to certify shape (E001's parse does that)."""
    depth = max_depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif ch in "]}":
            depth -= 1
    return max_depth


def record_nesting_depth(value: Any) -> int:
    """Nesting depth of an in-memory value, without recursion — the
    record-contract half of the pc-2e2f bound (the parse-gate half is in
    strict_json_loads). Containers count a level; scalars do not."""
    max_depth = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, dict):
            max_depth = max(max_depth, d)
            stack.extend((x, d + 1) for x in v.values())
        elif isinstance(v, list):
            max_depth = max(max_depth, d)
            stack.extend((x, d + 1) for x in v)
    return max_depth


#: The largest integer the canonical form admits (v3.0, pc-ddd9). RFC 8785
#: serializes numbers as IEEE-754 doubles; within this range that is plain
#: decimal digits, and it is also the range JavaScript integers survive — the
#: MCP server's clients are often TypeScript.
SAFE_INTEGER = 2**53 - 1


def canonical_domain_problem(value: Any) -> str | None:
    """Why `value` falls outside the v3.0 canonical domain, or None.

    The domain is what makes RFC 8785 cheap to implement exactly: integers
    only, within ±(2^53−1); ASCII object keys; valid Unicode, so no lone
    surrogate (which surrogateescape decoding and `\\ud800`-style escapes
    both produce, and which UTF-8 cannot carry). Iterative, like
    record_nesting_depth, so no value reaches the recursion limit here."""
    stack: list[Any] = [value]
    while stack:
        v = stack.pop()
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, float):
            return (f"number {capped_value(v)} is not an integer — the "
                    f"canonical form admits integers only (v3.0, pc-ddd9); "
                    f"store a non-integer as a string")
        if isinstance(v, int):
            if abs(v) > SAFE_INTEGER:
                return (f"integer {capped_value(v)} is outside ±(2^53−1) — "
                        f"the canonical form's range (v3.0, pc-ddd9)")
            continue
        if isinstance(v, str):
            try:
                v.encode("utf-8")
            except UnicodeEncodeError:
                return ("a string carries a lone surrogate — not valid "
                        "Unicode, so UTF-8 cannot carry it and the canonical "
                        "form has no bytes for it (v3.0, pc-ddd9)")
            continue
        if isinstance(v, dict):
            for key, item in v.items():
                if not isinstance(key, str) or not key.isascii():
                    return (f"object key {capped_value(key)} is not ASCII — "
                            f"the canonical form admits ASCII keys only "
                            f"(v3.0, pc-ddd9)")
                stack.append(item)
        elif isinstance(v, list):
            stack.extend(v)
    return None


def strict_json_loads(text: str) -> Any:
    """json.loads without Python's non-JSON extensions (pc-6af9, v2.10).

    Python's parser admits `NaN`, `Infinity` and `-Infinity`, which are not
    JSON tokens (RFC 8259 §6) — so a log line carrying one parsed clean,
    E001 certified it, and canonical() wrote the token back: the checker
    admitting what the one-JSON-object-per-line contract refuses. Every
    timeline parse gate goes through here; the refusal is a JSONDecodeError
    so each gate's own E001 diagnostic names the line as usual."""
    def refuse(token: str) -> Any:
        raise json.JSONDecodeError(
            f"{token} is not a JSON token — the one-JSON-object-per-line "
            f"contract admits strict JSON only (pc-6af9)", text, 0)

    def no_float(token: str) -> Any:
        # v3.0 (pc-ddd9) refuses every non-integer number, which subsumes
        # pc-d502's overflow case: `1e1000000` decoded to inf and slipped past
        # parse_constant; it is now refused as the float token it is.
        raise json.JSONDecodeError(
            f"{token[:32]} is not an integer — the canonical form admits "
            f"integers only (v3.0, pc-ddd9)", text, 0)

    def safe_int(token: str) -> int:
        # `-0` is JSON but not canonical, and decodes to 0, so it would be
        # rewritten on the way back out. A digit-count check runs before
        # int() so a huge token is refused here rather than tripping the
        # interpreter's int-string limit as an uncaught ValueError.
        digits = token.lstrip("-")
        if token == "-0" or len(digits) > 16 or abs(int(token)) > SAFE_INTEGER:
            raise json.JSONDecodeError(
                f"{token[:32]} is not an integer within ±(2^53−1), or is "
                f"-0 — outside the canonical form (v3.0, pc-ddd9)", text, 0)
        return int(token)

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        # RFC 8785 (via I-JSON) refuses duplicate names, and Python keeps
        # the last silently — which would make the stored line and the
        # hashed value disagree.
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise json.JSONDecodeError(
                    f"object key {capped_value(key)} appears twice — the "
                    f"canonical form admits unique keys only (v3.0, "
                    f"pc-ddd9)", text, 0)
            seen.add(key)
        return dict(pairs)
    # The count() pre-filter keeps the ordinary path near-free: the full
    # string-aware scan runs only on a line that even carries enough
    # openers to matter, and count() is a C-level scan.
    if text.count("[") + text.count("{") > NESTING_BOUND:
        depth = json_nesting_depth(text)
        if depth > NESTING_BOUND:
            raise json.JSONDecodeError(
                f"value nests {depth} levels deep — the record-line "
                f"format's declared nesting bound is {NESTING_BOUND} "
                f"(v2.12, pc-2e2f), a structural guard that keeps every "
                f"parse and serialization away from the interpreter's "
                f"recursion boundary", text, 0)
    value = json.loads(text, parse_constant=refuse, parse_float=no_float,
                       parse_int=safe_int, object_pairs_hook=unique_keys)
    problem = canonical_domain_problem(value)
    if problem is not None:
        raise json.JSONDecodeError(problem, text, 0)
    return value


def group_revisions(records: list[dict[str, Any]]) -> dict[str, dict[int, list[dict[str, Any]]]]:
    groups: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for rec in records:
        rid, rev = rec.get("id"), rec.get("rev")
        # strict_int: hash(True) == hash(1), so a boolean rev collided into the
        # rev-1 group — corrupting the very denominator E002 counts (`pc-d89f`).
        if not isinstance(rid, str) or not strict_int(rev):
            continue
        groups.setdefault(rid, {}).setdefault(rev, []).append(rec)
    return groups


def resolve_heads(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], bool]:
    """(heads by id, resolvable). Highest rev wins.

    `resolvable` is now ALWAYS True and the tuple is kept only so callers need
    not change shape. Under v2 there is one timeline, appends are admitted by
    compare-and-swap, and two lines sharing an (id, rev) cannot be produced by
    any write path — the state E010 reported is unreachable rather than merely
    rare. It survives as a corruption signal via E002/E013, which read the
    chain; see run_checks. VP4's destructive half is why the flag's *consumers*
    go rather than being left permanently green: a check standing after its
    state became unrepresentable reads as assurance and provides none."""
    heads: dict[str, dict[str, Any]] = {}
    for rid, revs in group_revisions(records).items():
        heads[rid] = revs[max(revs)][0]
    return heads, True


def lock_path() -> Path:
    """Where the write lock lives.

    v1.5 put it at $PWD/.pecia/.lock, which meant agents in separate worktrees
    of ONE clone took separate locks — so the review-4 F17 fix did not hold for
    the concurrency pattern this project actually runs (four live worktrees,
    measured 2026-08-07). It now sits beside the log in the shared git dir, so
    every worktree of a clone contends for the same lock."""
    d = log_dir()
    return (d / ".lock") if d is not None else (PECIA_DIR / ".lock")


@contextmanager
def ledger_lock():
    """Exclusive lock making read→validate→append one atomic operation."""
    target = lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The log's entries, or a findings list saying why not."""
    target = log_path()
    if target is None:
        return [], [finding("error", "E000", None,
                            "not inside a git repository — the timeline lives under "
                            "--git-common-dir (spec/format-v2.md 3.1)")]
    if not target.exists():
        # pc-5f0f: a plain `git clone` never fetches refs/pecia/log — it is
        # outside refs/heads/*, refs/remotes/* — so THIS branch is also the
        # ordinary shape of a fresh clone of an ALREADY-published repo, not
        # only of one that has never run pecia. `sync` hydrates that case
        # (its own "no timeline yet" branch treats it as normal); `migrate`
        # instead rebuilds from local history alone and can silently diverge
        # from what is already published. `git remote` is local-only (no
        # network), so checking it costs nothing on the hot path — this
        # branch runs only pre-bootstrap, once per clone.
        if git_out("remote"):
            return [], [finding("error", "E000", None,
                                "no timeline yet, and this clone has a remote configured "
                                "— run `pecia sync` first (safe by default: it is how a "
                                "fresh clone of an already-published repo gets the "
                                "timeline). If sync says nothing is published yet, run "
                                "`pecia migrate` to build one from this repo's history, "
                                "or `pecia init` if it has no pecia history either")]
        return [], [finding("error", "E000", None,
                            "no timeline yet — run `pecia migrate` to build one from "
                            "this repo's history, or `pecia init` in a fresh repo")]
    return read_log(target)



def fsync_fd(fd: int) -> None:
    """DURABILITY (pc-26a08d9c5d46). A write pecia reports as done must
    survive a power loss, not only this process dying: the page cache
    outlives the second and not the first, so no kill test or exit code can
    tell them apart. On macOS os.fsync hands data to the drive without
    flushing the drive's own cache; F_FULLFSYNC does, and it is what the Rust
    port's std issues, so it is used wherever the platform has it."""
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError:
            pass  # a filesystem without it still gets an ordinary fsync
    os.fsync(fd)


def fsync_path(path: Path) -> None:
    """fsync_fd over a file or a directory, by path."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fsync_fd(fd)
    finally:
        os.close(fd)


def durable_replace(tmp: Path, dest: Path) -> None:
    """os.replace, made to survive a power loss: the staged file is synced
    before it takes the name, and the directory after, so the new name is
    durable too (pc-26a08d9c5d46)."""
    fsync_path(tmp)
    os.replace(tmp, dest)
    fsync_path(dest.parent)

def append_record(rec: dict[str, Any]) -> None:
    """Append one revision to the timeline, and move its high-water mark.

    The compare-and-swap is enforced HERE, not advised: an entry whose rev does
    not follow its record's head is refused. Callers hold ledger_lock(), so
    within a clone this is serialised; across clones the refusal is what the
    ref push turns into a retry (`pecia sync`)."""
    target = log_path()
    if target is None:
        raise RuntimeError("not inside a git repository — cannot write the timeline")
    entries, findings = read_log(target)
    if findings:
        raise RuntimeError(findings[0]["message"])
    ok, why = cas_admissible(entries, rec)
    if not ok:
        raise RuntimeError(f"compare-and-swap refused: {why}")
    entry = make_entry(entries, rec)
    # THE LOG'S END IS WITNESSED BY ITS MARK (v3.3, pc-25cca4980c47). An
    # ordinary write no longer regenerates the projection — `snapshot` does,
    # before a commit carries it — so it can no longer erase the records a
    # truncation witness holds (the reason it refused over E015 at v2.8,
    # pc-c6b8, is gone), and what witnesses this store's own appends between
    # snapshots is the mark each one moves.
    gone = mark_violation(entries)
    if gone:
        raise RuntimeError(f"write refused: {gone} (E019). {MARK_REMEDY}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Staged BEFORE the append, as the projection was (pc-29ee): a mark that
    # cannot be written refuses with the log untouched, and is committed
    # straight after it.
    with _staged_mark(entries + [entry], target) as (commit_mark, problem):
        if problem:
            raise RuntimeError(f"write refused: {problem}")
        line = (canonical(entry) + "\n").encode("utf-8")
        with target.open("ab") as fh:
            fh.write(line)
            # Durable before the mark moves to it (pc-26a08d9c5d46); the
            # mark's commit then syncs the directory both live in.
            fh.flush()
            fsync_fd(fh.fileno())
        entries.append(entry)
        commit_mark()


# ── v2 storage: the single timeline (spec/format-v2.md, decision pc-1e88) ──
#
# The canonical ledger is an append-only hash-chained log under the shared
# .git of a clone, so every worktree resolves to ONE timeline. .pecia/work.jsonl
# is demoted to a generated snapshot (LEDGER_PATH keeps that name; it is now
# the projection, not the authority).
#
# Nothing below is wired into the read/write path yet — pc-0033 does that in
# stages so the repo stays self-hosting at every commit. `pecia migrate`
# (pc-5c7c) is what first produces a log.

TOUCHED_FIELDS = ["title", "status", "priority", "disposition",
                  "evidence", "owner", "labels", "body", "target",
                  # v2.5 (pc-813f): authored fields conflict and re-chain
                  # like any other. `anchor`/`anchor_dirty` are deliberately
                  # NOT here — they are per-revision provenance stamped by
                  # the substrate, carried through sync the way the force
                  # brand is, and listing them would make every pair of
                  # concurrent writes a spurious conflict.
                  "context", "ratified_by", "ratified"]
# EDGE SUBFIELDS ARE SEPARATE CONFLICT UNITS (pc-4924). `edges` was one entry,
# so two writers editing `blocks` and `parent` of one record collided — while
# spec/format-v2.md's own example says `edges.blocks` and peciaV2.als models
# FBlocks and FParent as DISJOINT fields, with theorem V5 (the property that
# field-granularity buys commuting, the entire justification for the touched
# design in pc-316c) proved over that finer partition. The theorem did not
# cover the code. Found by four lanes from four context sets, which is the
# strongest signal the off-lineage pass produced.
TOUCHED_EDGE_FIELDS = [f"edges.{k}" for k in ("blocks", "retires", *SCALAR_EDGES,
                                              "no_edges")]
# Fields that legitimately differ between consecutive revisions WITHOUT being
# conflict units (v2.6, pc-c7fe): the CAS token, the write stamp, and the
# per-revision provenance/brand the substrate re-stamps on every write.
# `edges` is here because its SUBFIELDS are the conflict units. Every other
# top-level key that differs — schema-permitted extension fields included —
# joins the derived `touched` set; before this, an `x_custom` change with an
# empty declaration was invisible to E014 and to sync's conflict test.
PER_REVISION_FIELDS = frozenset({"id", "rev", "updated", "edges",
                                 "anchor", "anchor_dirty", "forced"})

#: Optional fields whose CLEARED state is absence, not null (pc-76b5, v2.6):
#: the write path removes the key (`edit --context none` pops it; E001
#: refuses a null), so a sync re-chain replaying the clearing must remove it
#: too. Writing null instead recreated the key on the re-chained revision,
#: and the loser's clean disjoint edit was refused with the E001 the raw
#: null rightly draws — clearing `context` did not commute with a concurrent
#: unrelated edit.
PRESENCE_FIELDS = frozenset({"target", "context", "ratified_by", "ratified"})


def field_get(rec: dict[str, Any], path: str) -> Any:
    if "." not in path:
        return rec.get(path)
    outer, inner = path.split(".", 1)
    sub = rec.get(outer)
    return sub.get(inner) if isinstance(sub, dict) else None


def transfer_field(dst: dict[str, Any], src: dict[str, Any], path: str) -> None:
    """Apply src's state of `path` onto dst, PRESENCE-AWARE at the top level
    (v2.7, pc-a437). sync's re-chain used field_set(dst, f, field_get(src, f)),
    and field_get collapses a present null with absence — so replaying the
    REMOVAL of an extension key wrote `null` instead, the exact state the
    diff now distinguishes. Dotted paths (edges.*) keep field_set's
    semantics; PRESENCE_FIELDS behave identically under both (absent in src
    means cleared means popped)."""
    if "." in path:
        field_set(dst, path, field_get(src, path))
    elif path in src:
        dst[path] = src[path]
    else:
        dst.pop(path, None)


def field_set(rec: dict[str, Any], path: str, value: Any) -> None:
    if "." not in path:
        if value is None and path in PRESENCE_FIELDS:
            rec.pop(path, None)
        else:
            rec[path] = value
        return
    outer, inner = path.split(".", 1)
    sub = rec.get(outer)
    if not isinstance(sub, dict):
        sub = {}
        rec[outer] = sub
    if value is None and inner == "no_edges":
        sub.pop(inner, None)
    else:
        sub[inner] = value


def git_common_dir() -> Path | None:
    """The .git shared by every worktree of this clone — the timeline's home.

    VP19: this resolution is host-dependent by exactly the signature that
    entry names, so `pecia doctor` reports it and the gates run once from a
    clean export. Returns None outside a repository, which is a supported
    state for reads (the snapshot still parses) but not for writes."""
    out = git_out("rev-parse", "--git-common-dir")
    if not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _pinned_store() -> Path | None:
    """The PECIA_LOG_DIR pin, resolved and validated — the ONE reading of
    the variable, shared by log_dir() and snapshot_dir() so the log and the
    snapshot cannot resolve the same pin to two places.

    pc-0108 (round-4 lane B2-F1): a relative pin was used as-is, so the
    same value named <root>/store from the repository root and
    <root>/sub/store from a subdirectory — `check` flipped from clean to
    E000 no-timeline with the invocation directory (claim 17's exact
    failure), and a repeat `init` from the subdirectory forked a second,
    empty timeline under the same declared pin. A relative pin now
    resolves against ROOT, the same way ROOT itself was fixed at v2.6
    (pc-3690): the pin is a declaration about the repository, not about
    where the caller stood. Outside any work tree ROOT is the cwd at
    import, which is the state the explicit-pin escape hatch serves.

    pc-7a2c: unvalidated, a typo failed late and at the write. This
    variable is the mitigation for pc-dd71 — the thing that stops a nested
    process inheriting the live timeline by directory accident — and a
    mitigation that fails late is one people stop trusting."""
    pinned = os.environ.get("PECIA_LOG_DIR")
    if not pinned:
        return None
    d = Path(pinned)
    if not d.is_absolute():
        d = ROOT / d
    if d.exists() and not d.is_dir():
        raise RuntimeError(f"PECIA_LOG_DIR={render_value(str(pinned))} exists and is not a directory")
    return d


def log_dir() -> Path | None:
    """Where the timeline lives.

    PECIA_LOG_DIR pins it EXPLICITLY and is consulted first. Two reasons, both
    learned here. (a) pc-dd71: --git-common-dir walks UP, so a fixture or tool
    running anywhere under a clone silently inherits that clone's real
    timeline; an explicit store is declared rather than discovered. (b) Finding
    the log otherwise requires `git` on PATH, so a scrubbed environment could
    not read a ledger at all — the override keeps pecia usable there, which the
    hermeticity arm asserts."""
    pinned_dir = _pinned_store()
    if pinned_dir is not None:
        return pinned_dir
    # pc-dd71's own "refuse when the resolved common-dir is an ancestor of a
    # caller-declared sandbox" was considered and rejected (see the closed
    # record's disposition): a legitimate subdirectory of the real repo and
    # an accidental sandbox nested under it resolve IDENTICALLY here — git's
    # own discovery stops at the nearest .git regardless of intent, so there
    # is never a "nearer .git that should have won" to detect. PECIA_LOG_DIR
    # above, plus giving every fixture its own git init, is the complete
    # practical fix.
    common = git_common_dir()
    return None if common is None else common / "pecia"


def log_path() -> Path | None:
    d = log_dir()
    return None if d is None else d / "log.jsonl"


def snapshot_dir() -> Path:
    """Where the snapshot projection and its head witness live.

    v2.7 (pc-74da): THE SNAPSHOT FOLLOWS THE STORE. PECIA_LOG_DIR relocated
    the log and the lock while .pecia/work.jsonl and snapshot.head stayed
    repository-global — so with two declared stores in one repository (the
    documented two-timeline shape) whichever store wrote last rewrote the
    SHARED snapshot, and the other store's very next check turned E015
    FORKED with its own log untouched: "locally clean" was not
    simultaneously achievable for both. A pinned store is now
    self-contained — log, lock, snapshot and head all under PECIA_LOG_DIR —
    and the unpinned repository keeps .pecia/ exactly as before.

    The pin resolves through _pinned_store() (pc-0108): one reading, so
    the log and the snapshot cannot resolve the same pin to two places."""
    pinned_dir = _pinned_store()
    return pinned_dir if pinned_dir is not None else PECIA_DIR


def snapshot_path() -> Path:
    return snapshot_dir() / "work.jsonl"


def snapshot_head_path() -> Path:
    return snapshot_dir() / "snapshot.head"


def store_file_display(target: Path) -> str:
    """A store file's name as diagnostic text: repo-relative in the ordinary
    case, absolute for a pinned store outside the tree (pc-f61a, round-6
    lane B2-F2). Claim 6's v2.10 clause promises a finding names THE file,
    and the E015 message literals said `.pecia/...` wherever the store
    actually was — under PECIA_LOG_DIR the checker read the correct pinned
    files and then named paths absent from the fixture. Same resolution
    rule the board caption already uses (pc-74da)."""
    try:
        return str(target.relative_to(ROOT))
    except ValueError:
        return str(target)


def log_mark_path() -> Path | None:
    """The log's high-water mark (v3.3, pc-25cca4980c47): beside the log, so
    in the git common dir and never tracked, and in a pinned store beside its
    own log. One line: the newest entry's seq and hash."""
    target = log_path()
    return None if target is None else target.with_name("log.mark")


MARK_RE = re.compile(r"([1-9][0-9]*) ([0-9a-f]{64})")

# What to do about an E019, said the same way by every command that meets one.
MARK_REMEDY = ("Recover the log first: `pecia sync` restores entries that were "
               "published. Entries never published are not recoverable from "
               "here, and removing the mark is the deliberate act of accepting "
               "that")


def read_mark() -> tuple[int, str] | str | None:
    """(seq, hash) as the mark records them, the refusal text when it does not
    parse, or None when there is no mark — a store written before v3.3, or a
    fresh clone — which makes no claim."""
    mark = log_mark_path()
    if mark is None or not mark.exists():
        return None
    raw = mark.read_bytes().decode("utf-8", "surrogateescape").strip()
    m = MARK_RE.fullmatch(raw)
    if m is None:
        return (f"the log's high-water mark {store_file_display(mark)} does not "
                f"parse — it records the newest entry's seq and hash, and a mark "
                f"that says neither vouches for nothing")
    return int(m.group(1)), m.group(2)


def mark_violation(entries: list[dict[str, Any]]) -> str | None:
    """E019 (v3.3, pc-25cca4980c47): why this log ends before its high-water
    mark, or None.

    A valid prefix of a chain is a valid chain, so nothing inside the log can
    see its own end cut off (E013 cannot, §v2.6). Every write moves the mark
    to the entry it appended, so a log that no longer holds the marked entry
    — shorter than the mark, or holding another entry at that seq — lost or
    replaced what this store wrote. A writer that moved the mark past that
    would erase the only evidence, so every writer refuses over it."""
    mark = read_mark()
    if mark is None:
        return None
    if isinstance(mark, str):
        return mark
    seq, digest = mark
    shown = store_file_display(log_mark_path())
    if seq > len(entries):
        missing = seq - len(entries)
        return (f"the log ends at seq {len(entries)}, but its high-water mark "
                f"{shown} records seq {seq}: {missing} "
                f"entr{'y' if missing == 1 else 'ies'} this store wrote "
                f"{'is' if missing == 1 else 'are'} missing from the log's end — "
                f"the log looks suffix-truncated")
    if entry_hash(entries[seq - 1]) != digest:
        return (f"the log's entry {seq} is not the one its high-water mark "
                f"{shown} records — the log was rewritten at or below the mark")
    return None


def holds_marked_entry(entries: list[dict[str, Any]]) -> bool:
    """Whether `entries` hold the entry the mark records, at its seq: the
    timeline a recovery restores."""
    mark = read_mark()
    return (isinstance(mark, tuple) and len(entries) >= mark[0]
            and entry_hash(entries[mark[0] - 1]) == mark[1])


@contextmanager
def _staged_mark(entries: list[dict[str, Any]], near: Path):
    """Stage the high-water mark for the timeline `entries`, beside the log
    `near`, and hand back its commit: staged before the log moves, so an
    unwritable directory refuses while nothing has, and renamed right after
    it — the commit-point order the projection kept at v2.13 (pc-29ee). An
    empty timeline marks nothing. Yields (commit, problem)."""
    if not entries:
        yield (lambda: None), None
        return
    mark = near.with_name("log.mark")
    tmp = mark.with_name(f".log.mark.staged.{os.getpid()}")
    problem: str | None = None
    try:
        try:
            tmp.write_text(f"{len(entries)} {log_head_hash(entries)}\n")
        except OSError as exc:
            problem = (f"the log's high-water mark cannot be written: "
                       f"{type(exc).__name__}: {exc} — nothing was written, and "
                       f"the store is byte-identical to before")
        yield (lambda: durable_replace(tmp, mark)), problem
    finally:
        tmp.unlink(missing_ok=True)


def entry_hash(entry: dict[str, Any]) -> str:
    """SHA-256 over the canonical entry. The chain is self-verifying WITHOUT
    git — git supplies transport, serialization and CAS, never immutability
    (format-v2.md 3.2)."""
    return hashlib.sha256(canonical(entry).encode()).hexdigest()


_ABSENT = object()   # diff_fields' "this key is not there" — distinct from null


def same_value(a: Any, b: Any) -> bool:
    """Two field values are the same value when their canonical forms are.
    Python's == is not that relation: it calls 1 and True equal, and two
    objects equal whatever their key order — so an extension field moving
    from 1 to true was UNTOUCHED here and touched in the compiled
    implementation, which compares the values the chain's bytes carry."""
    if a is _ABSENT or b is _ABSENT:
        return a is b
    return json.dumps(a, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False) == json.dumps(
        b, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def diff_fields(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """The `touched` set, DERIVED — never accepted from a writer.

    format-v2.md 4.1: a required field the writer must author is generative,
    and the measured result there is fabrication (GP8/BP4's scope qualifier).
    Derived from a diff the tool already has, it is subtractive, which is the
    scope where substrate-not-prompt holds. E013 recomputes this and refuses
    any entry whose declared set differs. Creation touches nothing.

    v2.6 (pc-c7fe): the diff runs over the union of both revisions' top-level
    keys, not a fixed whitelist — PER_REVISION_FIELDS excepted — so a
    schema-permitted extension field (`x_custom`) is a conflict unit like any
    core field instead of being invisible to E014 and to sync's conflict test.

    v2.7 (pc-a437): the union keys compare by LITERAL KEY and by PRESENCE.
    field_get read both sides with dict.get, which (a) collapsed a present
    null with absence, so adding `x_custom: null` was invisible, and (b)
    misread a dotted top-level key as an edges.*-style path, so a change to
    `x.custom` was invisible too — while the CORRECT declaration of either
    change drew an E014 refusal. A dotted top-level key is now E001-invalid
    (same amendment), and this diff still sees it either way, so even a
    forced or imported record cannot move one silently."""
    if before is None:
        return []
    fields = set(TOUCHED_FIELDS) | set(TOUCHED_EDGE_FIELDS)
    changed = {f for f in fields
               if not same_value(field_get(before, f), field_get(after, f))}
    changed |= {k for k in (*before, *after)
                if k not in PER_REVISION_FIELDS and k not in fields
                and not same_value(before.get(k, _ABSENT), after.get(k, _ABSENT))}
    return sorted(changed)


def collision_note(collisions: list[dict[str, Any]]) -> str:
    """The remedy for a cross-clone id collision (pc-1c65), which is NOT the
    same-field remedy: `--take-landed` would discard a distinct record, and
    editing the landed id would overwrite someone else's record with your
    title. The local record needs a new id."""
    if not collisions:
        return ""
    ids = capped_seq([c["id"] for c in collisions], render=lambda i: i, sep=", ")
    return (f"ID COLLISION on {ids}: your revision 1 meets a LANDED revision 1 of "
            f"the same id, so both sides minted it for unrelated records — this is "
            f"not a concurrent edit of one record and has no field to reconcile. "
            f"`--take-landed` is refused here and would be the wrong instruction: it "
            f"discards a distinct piece of work, and editing the landed id then "
            f"overwrites another writer's record with your text. Re-create your "
            f"record with `pecia add` (it mints a fresh id) and drop the local one, "
            f"or rewrite its id before syncing. Nothing was written. ")


def touched_conflicts(a: set[str], b: set[str]) -> set[str]:
    """Which touched fields make two revisions of one record conflict.

    pc-7f4e: TOUCHED_EDGE_FIELDS lists `edges.no_edges` as a field disjoint
    from `edges.blocks`/etc, so a literal set intersection let one writer
    declare a record edgeless while another concurrently added a real edge —
    disjoint fields, so sync composed both, and the merged record asserted
    both `no_edges: true` and a live edge. `no_edges` is not a peer field; it
    is a declaration about the ABSENCE of the others, so it conflicts with
    ANY other edges.* field the other side touched, not only with itself."""
    overlap = a & b
    other_edges_a = {f for f in a if f.startswith("edges.") and f != "edges.no_edges"}
    other_edges_b = {f for f in b if f.startswith("edges.") and f != "edges.no_edges"}
    if "edges.no_edges" in a and other_edges_b:
        overlap |= {"edges.no_edges"} | other_edges_b
    if "edges.no_edges" in b and other_edges_a:
        overlap |= {"edges.no_edges"} | other_edges_a
    return overlap


def read_log(path: Path | None = None, *, raw_bytes: bytes | None = None
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (entries, findings). E012 is chain integrity: a broken `prev`
    or a seq that is not exactly one greater means the log was spliced,
    truncated, or edited out of band."""
    target = path or log_path()
    entries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if target is None or (raw_bytes is None and not target.exists()):
        return entries, findings
    prev_hash: str | None = None
    # Sibling of pc-0a2f's shape, fixed in the same commit: read_text()
    # decoded strictly, so a LOCAL log carrying an invalid-UTF-8 byte killed
    # every consumer (check included) as fatal E000 UnicodeDecodeError with
    # no line named. Surrogateescape the decode; refuse per line, below.
    text = (raw_bytes if raw_bytes is not None else target.read_bytes()).decode(
        "utf-8", errors="surrogateescape")
    for n, raw in enumerate(source_lines(text), start=1):
        if not raw.strip():
            # One JSON object per line is the contract, and E001 says every
            # line parses — a whitespace-only line was silently skipped, so
            # contract-violating input was certified clean (pc-e7f0). The
            # tolerated class carries no content, which is why this is a
            # finding rather than a custody hole, but a checker that edits
            # its input's line discipline on the way in is not a checker.
            findings.append(finding("error", "E001", None,
                                    f"log line {n} is blank — the log is one "
                                    f"entry per line, with no blank lines"))
            return entries, findings
        try:
            raw.encode("utf-8")
        except UnicodeEncodeError:
            findings.append(finding("error", "E001", None,
                                    f"log line {n} is not valid UTF-8 — the "
                                    f"log is one UTF-8 JSON entry per line"))
            return entries, findings
        try:
            entry = strict_json_loads(raw)
        except json.JSONDecodeError as exc:
            findings.append(finding("error", "E001", None, f"log line {n} does not parse: {exc}"))
            return entries, findings
        if not isinstance(entry, dict) or not isinstance(entry.get("rec"), dict):
            findings.append(finding("error", "E001", None, f"log line {n} is not an entry"))
            return entries, findings
        # strict_int, not ==: JSON true satisfies `True == 1`, so a forged
        # first entry declaring `"seq": true` passed chain validation while
        # `"seq": 0` fired (pc-07c4). Booleans are excluded from every other
        # integer field in the record contract; the chain envelope follows.
        if not strict_int(entry.get("seq")) or entry.get("seq") != n:
            findings.append(finding("error", "E013", entry["rec"].get("id"),
                                    f"log line {n} declares seq "
                                    f"{capped_value(entry.get('seq'))}"))
            return entries, findings
        # ABSENCE IS NOT NULL (pc-0dc3, round-4 lane A-F1). entry.get("prev")
        # collapses a MISSING prev key to None, and None is line 1's legal
        # value — so an entry omitting the mandatory field entirely checked
        # clean exactly there. Every other envelope field already refuses
        # absence (seq above, touched in timeline_checks, rec in the entry
        # shape); prev was the one whose legal value equals dict.get's
        # default.
        if "prev" not in entry:
            findings.append(finding("error", "E013", entry["rec"].get("id"),
                                    f"log line {n} omits the mandatory prev "
                                    f"field — every entry carries seq, prev, "
                                    f"touched, and rec (format-v2.md 3.2)"))
            return entries, findings
        if entry.get("prev") != prev_hash:
            findings.append(finding("error", "E013", entry["rec"].get("id"),
                                    f"log line {n} breaks the chain (prev does not match line {n - 1})"))
            return entries, findings
        prev_hash = entry_hash(entry)
        entries.append(entry)
    return entries, findings


@contextmanager
def _staged_log(entries: list[dict[str, Any]], near: Path):
    """Serialize `entries` to a temp file beside `near` and read them back.

    The staging file is in the SAME directory on purpose: os.replace() is
    atomic only within a filesystem, and the store may be anywhere (a pinned
    PECIA_LOG_DIR). It is removed however the block ends, so a refusal leaves
    the store exactly as it found it and a crash leaves a dot-file rather
    than a damaged log."""
    near.parent.mkdir(parents=True, exist_ok=True)
    tmp = near.with_name(f".{near.name}.staged.{os.getpid()}")
    try:
        tmp.write_bytes("".join(canonical(e) + "\n" for e in entries).encode("utf-8"))
        parsed, findings = read_log(tmp)
        yield tmp, parsed, findings
    finally:
        tmp.unlink(missing_ok=True)


def log_refusal(entries: list[dict[str, Any]], near: Path) -> str | None:
    """Why this timeline would be refused on read-back, or None (pc-d373).

    The validation a caller needs BEFORE it consumes a timeline someone else
    wrote. It is deliberately read_log itself, over staged bytes, rather than
    a second implementation of the chain rules: a duplicate would be the one
    that goes stale, and this is the seam where "the published chain is
    valid" was being assumed."""
    with _staged_log(entries, near) as (_tmp, _parsed, findings):
        return findings[0]["message"] if findings else None


@contextmanager
def _staged_snapshot(entries: list[dict[str, Any]]):
    """Stage the projection beside its targets and hand back its commit.

    pc-29ee (round-9 lane B1-F1): the projection was WRITTEN after the log
    rename, so a `.pecia/` this process cannot write left the log renamed and
    the register advanced while `snapshot.head` did not move — `sync` exited 2
    and `check` went red with E015. Two writes were the last thing in the
    command outside its own byte-identity declaration.

    Staging answers it the way `_staged_log` answers it for the log: both
    files are written under dot-names in their own directory FIRST, so a
    projection that cannot be written is a refusal while nothing has moved,
    and committing it is two os.replace calls in one directory rather than
    two opens. It yields `(commit, problem)` — `problem` is the refusal
    message or None — and removes the temp files however the block ends.
    `entries is None` is the "this write does not project" case: it stages
    nothing and its commit is a no-op, so a caller that does not project
    still takes one path rather than a second one."""
    if entries is None:
        yield (lambda: None), None
        return
    body = "".join(canonical(e["rec"]) + "\n" for e in entries)
    head = (log_head_hash(entries) or "") + "\n"
    staged: list[tuple[Path, Path]] = []
    problem: str | None = None
    try:
        try:
            snapshot_dir().mkdir(parents=True, exist_ok=True)
            for dest, text in ((snapshot_path(), body), (snapshot_head_path(), head)):
                tmp = dest.with_name(f".{dest.name}.staged.{os.getpid()}")
                tmp.write_bytes(text.encode("utf-8"))
                staged.append((tmp, dest))
        except OSError as exc:
            problem = (f"the projection cannot be written: {type(exc).__name__}: "
                       f"{exc} — {snapshot_path()} and {snapshot_head_path()} are "
                       f"derived from the log and are written together; nothing "
                       f"was written, and the store is byte-identical to before "
                       f"(repair the permissions and run the command again)")

        def commit() -> None:
            # Every staged file synced before any takes its name, then the
            # directory once (pc-26a08d9c5d46).
            for tmp, _ in staged:
                fsync_path(tmp)
            for tmp, dest in staged:
                os.replace(tmp, dest)
            if staged:
                fsync_path(staged[0][1].parent)

        yield commit, problem
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def write_log_validated(
        target: Path, entries: list[dict[str, Any]],
        also=None, before_commit=None,
        project: bool = False) -> tuple[list[dict[str, Any]] | None,
                                        str | None]:
    """Write the log only if it reads back clean — VALIDATE, THEN RENAME.

    pc-d373: three writers here shared a write-then-validate ordering —
    serialize onto the live log, read it back, and refuse. The refusal was
    loud and correct, and the store was already overwritten: `sync`'s
    re-chain branch left a previously clean local log in an E013/E015 state
    with no rollback at all, and the hydrate branch and `migrate` depended on
    RESTORING the old bytes afterwards, which is a rollback that has to run
    and be right rather than a write that never happened. Now nothing touches
    `target` until the bytes have been read back through read_log (the chain)
    and through `also` (any further check the caller needs, returning a
    refusal message or None), and then it is one os.replace.

    THE CONTRACT SPANS THE COMMAND, NOT ONLY THIS PRIMITIVE (pc-b652,
    round-8 lanes B1-F1/B2-F1). The declaration at v2.12 is about a refused
    recovery path, and `sync` kept a refusal AFTER the rename: its register
    CAS ran once log.jsonl and work.jsonl were already changed, so a
    concurrent publication landing in that window refused at exit 2 over a
    modified store. `before_commit` is the caller's last-moment work — any
    external register the write must not outlive — run while `target` is
    still untouched. It returns a refusal message or None, and its refusal
    is a refusal of the whole write, so "never opened it" holds for every
    exit of the command rather than only for the exits ordered before the
    rename.

    THE PROJECTION IS PART OF THE COMMIT POINT (pc-29ee, round-9 lane B1-F1).
    `project=True` stages `.pecia/work.jsonl` and `.pecia/snapshot.head` with
    the log — before `before_commit`, so no external register is taken for a
    write the projection would then fail — and renames them immediately after
    the log. The command's last write is no longer ordered after its own
    declaration: either all three files move or none do.

    Returns (parsed entries, None) on success, (None, message) on refusal —
    and on refusal `target` is byte-identical to before, because it was never
    opened."""
    with _staged_log(entries, target) as (tmp, parsed, findings):
        if findings:
            return None, findings[0]["message"]
        if also is not None:
            problem = also(parsed)
            if problem:
                return None, problem
        with _staged_snapshot(parsed if project else None) as (commit_projection,
                                                               projection_problem), \
                _staged_mark(parsed, target) as (commit_mark, mark_problem):
            if projection_problem or mark_problem:
                return None, projection_problem or mark_problem
            if before_commit is not None:
                problem = before_commit(parsed)
                if problem:
                    return None, problem
            durable_replace(tmp, target)
            commit_mark()
            commit_projection()
        return parsed, None


def log_head_hash(entries: list[dict[str, Any]]) -> str | None:
    return entry_hash(entries[-1]) if entries else None


def cas_admissible(entries: list[dict[str, Any]], rec: dict[str, Any]) -> tuple[bool, str]:
    """The per-id half of the compare-and-swap: rev N+1 only if the head is N.

    This is what `rev` is FOR under v2 (format-v2.md 4.2) — not a merge
    direction fix, which is what it was invented for and no longer needs to be."""
    # strict_int, never isinstance(rev, int): bool subclasses int in Python, so
    # the raw test made `"rev": true` a head at revision 1 and admitted a rev-2
    # candidate behind it — while validate_record refused the same record with
    # E001 "booleans are not revisions". One object, a valid head to the write
    # path and malformed to the read path (defect `pc-d89f`). Closed at all six
    # resolution sites rather than per caller, the pc-34b5/pc-d502 shape: a
    # record the checker calls malformed is not a head, and these helpers agree
    # with that verdict instead of carrying a second, laxer one.
    heads = {}
    for e in entries:
        r = e["rec"]
        rid, rev = r.get("id"), r.get("rev")
        if isinstance(rid, str) and strict_int(rev):
            if rid not in heads or rev > heads[rid]["rev"]:
                heads[rid] = r
    head = heads.get(rec.get("id"))
    if head is None:
        if rec.get("rev") != 1:
            return False, f"rev {rec.get('rev')} for a record with no head (expected 1)"
        return True, ""
    if rec.get("rev") != head["rev"] + 1:
        return False, (f"rev {rec.get('rev')} does not follow head rev {head['rev']} "
                       f"— re-read and retry (this is the CAS, not a merge)")
    return True, ""


def make_entry(entries: list[dict[str, Any]], rec: dict[str, Any]) -> dict[str, Any]:
    heads = {}
    for e in entries:
        r = e["rec"]
        rid, rev = r.get("id"), r.get("rev")
        if isinstance(rid, str) and strict_int(rev):  # `pc-d89f`
            if rid not in heads or rev > heads[rid]["rev"]:
                heads[rid] = r
    before = heads.get(rec.get("id"))
    return {"seq": len(entries) + 1,
            "prev": log_head_hash(entries),
            "touched": diff_fields(before, rec),
            "rec": rec}


def write_snapshot(entries: list[dict[str, Any]]) -> None:
    """Regenerate .pecia/work.jsonl from the log, and record the head it came
    from. The snapshot is a PERMITTED shadow: stale is clean, forked is E014.
    GP28 — a derived artifact must be derived, and something must assert it
    back; the head file is what makes that assertion possible.

    THE TWO FILES MOVE TOGETHER (pc-29ee). Written one after the other, a
    failure on the second left the content regenerated under the OLD recorded
    head — a fork the shadow rule does not permit, and E015 at the next
    check. Both are staged and then renamed, so the pair is written or
    neither is, and a projection that cannot be written raises before
    anything moves rather than halfway through."""
    with _staged_snapshot(entries) as (commit, problem):
        if problem:
            raise RuntimeError(problem)
        commit()


def collect_historical_records() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every record-revision this repo has ever held, across every ref.

    This is migration input, and it is the LAST merge pecia performs. It reads
    all reachable blobs of the snapshot path plus the working tree, because a
    revision that only ever existed on an unmerged branch is exactly the
    population the old storage could lose sight of.

    ORDER IS WITNESSED, NOT LEXICAL (pc-356c, round-5 lane B2-F1, v2.10).
    The reconstruction used to sort by (rev, id), discarding the order every
    source blob actually carries — and a snapshot is written in LOG order,
    so over the E015 suffix-truncation shape (log = the published one-entry
    prefix, snapshot = the two-record witness) `migrate --force` accepted or
    refused the identical recoverable loss purely on the luck of the id
    draw: creation-order-lexical ids reconstructed the original chain,
    reverse-lexical ones 'diverged' and drove the operator to --force-drop.
    Each source's observed sequence is now a set of precedence constraints
    and the rebuild is their topological merge ((rev, id) breaks ties among
    unconstrained keys, so the old order is the degenerate case with no
    witnesses); when sources genuinely disagree on order the merge falls
    back to (rev, id) — deterministic, disclosed in stats["order"], and the
    published-prefix guard below still arbitrates the result."""
    seen: dict[tuple[str, int], list[dict[str, Any]]] = {}
    stats: dict[str, Any] = {"blobs": 0, "lines": 0, "unreadable_lines": 0}
    orders: list[list[tuple[str, int]]] = []

    def absorb(text: str) -> None:
        # A DROPPED SOURCE LINE IS COUNTED, NEVER SILENT (pc-d502, round-6
        # lane B2-F1, same commit as the strict_json_loads finite gate).
        # Skipping garbage is this collector's declared handling, but the
        # skip used to leave no trace, so a content-carrying line the strict
        # parse refuses — the overflowing exponent is the demonstrated case —
        # would vanish from a rebuild at exit 0. The count feeds migrate's
        # refusal ladder; blank lines carry no content and stay uncounted
        # (the pc-e7f0 distinction).
        sequence: list[tuple[str, int]] = []
        witnessed: set[tuple[str, int]] = set()
        for raw in source_lines(text):
            if not raw.strip():
                continue
            # A LINE THE TIMELINE'S BYTE DISCIPLINE REFUSES IS UNREADABLE
            # HERE TOO (pc-8cf2, round-7 lane B2-F1). Sources arrive
            # surrogateescape-decoded, and Python's JSON parser happily
            # carries a lone surrogate through a string — so a snapshot
            # line carrying a raw 0xff was counted READABLE, its byte
            # rewritten as the escape \udcff at exit 0, and the E015 the
            # checker names on those bytes vanished into a clean timeline:
            # a corrupt generated projection laundered into authority. The
            # same re-encode gate every timeline reader applies runs here,
            # so the line lands in the refusal ladder (count, refuse,
            # --force-drop) instead of being normalized.
            try:
                raw.encode("utf-8")
            except UnicodeEncodeError:
                stats["unreadable_lines"] += 1
                continue
            try:
                rec = strict_json_loads(raw)
            except json.JSONDecodeError:
                stats["unreadable_lines"] += 1
                continue
            if not isinstance(rec, dict):
                stats["unreadable_lines"] += 1
                continue
            rid, rev = rec.get("id"), rec.get("rev")
            if not isinstance(rid, str) or not strict_int(rev):  # `pc-d89f`
                stats["unreadable_lines"] += 1
                continue
            stats["lines"] += 1
            key = (rid, rev)
            if key not in witnessed:
                witnessed.add(key)
                sequence.append(key)
            bucket = seen.setdefault(key, [])
            if not any(canonical(x) == canonical(rec) for x in bucket):
                bucket.append(rec)
        if len(sequence) > 1:
            orders.append(sequence)

    def witnessed_order(keys: list[tuple[str, int]]) -> list[tuple[str, int]] | None:
        """Topological merge of every source's observed sequence over
        `keys`, (rev, id) as the tie-break; None when the witnesses disagree
        (a cycle). Each sequence is read with the other keys left out."""
        wanted = set(keys)
        succ: dict[tuple[str, int], set[tuple[str, int]]] = {k: set() for k in keys}
        indegree = {k: 0 for k in keys}
        for full in orders:
            sequence = [k for k in full if k in wanted]
            for a, b in zip(sequence, sequence[1:]):
                if b not in succ[a]:
                    succ[a].add(b)
                    indegree[b] += 1
        heap = [(rev, rid) for (rid, rev), d in indegree.items() if d == 0]
        heapq.heapify(heap)
        out: list[tuple[str, int]] = []
        while heap:
            rev, rid = heapq.heappop(heap)
            out.append((rid, rev))
            for nxt in succ[(rid, rev)]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    heapq.heappush(heap, (nxt[1], nxt[0]))
        return out if len(out) == len(keys) else None

    revs = git_out("rev-list", "--all") or ""
    for sha in revs.split():
        # git_blob, not git_out (pc-1919, round-7 lane B2-F2): git_out runs
        # text=True, whose STRICT decode raised UnicodeDecodeError inside
        # subprocess for a committed non-UTF-8 blob — a fatal E000 at exit
        # 2 before this collector could count the unreadable line, so the
        # claim-18 recovery ladder (count, refuse, accept deliberately with
        # --force-drop) was unreachable for exactly the input it exists
        # for. The v2.11 inventory covered read_text() sites; this byte
        # entry point is a subprocess decode, which is why the fix is the
        # shared fallible-decode chokepoint (git_blob's surrogateescape)
        # feeding absorb's per-line re-encode gate, not another site sweep.
        code, blob, _ = git_blob(f"{sha}:.pecia/work.jsonl")
        if code == 0 and blob:
            stats["blobs"] += 1
            absorb(blob)
    if snapshot_path().exists():
        # Sibling of pc-23a1 (same commit): a strict read here crashed
        # `migrate` on an invalid-UTF-8 snapshot. Surrogateescape — absorb
        # already skips unparseable lines as its declared garbage handling,
        # and the orphan/divergence guards refuse any resulting loss loudly.
        absorb(snapshot_path().read_bytes().decode(
            "utf-8", errors="surrogateescape"))

    divergent = sorted(k for k, v in seen.items() if len(v) > 1)
    stats["divergent"] = len(divergent)
    stats["distinct_revisions"] = len(seen)
    stats["distinct_ids"] = len({rid for rid, _ in seen})
    # A VERIFIED SNAPSHOT IS THE ORDER (v3.7, pc-9846a784839f). The working
    # tree's snapshot, when its records chain to its recorded head, IS the
    # timeline through that head (v3.6), so it is the rebuild's order and
    # its first entries, record for record. History adds only the revisions
    # it lacks — an unmerged branch's — after it, merged as before. Without
    # this, one committed snapshot written in another order (this
    # repository's pre-v2 blobs were id-sorted) set the witnesses against
    # each other, and the (rev, id) fallback rebuilt a timeline whose hashes
    # matched nothing any writer held.
    copy = verified_snapshot_chain()
    keyed = copy is not None and all(
        isinstance(e["rec"].get("id"), str) and strict_int(e["rec"].get("rev"))
        for e in copy)
    first = [(e["rec"]["id"], e["rec"]["rev"]) for e in copy] if copy and keyed else []
    if keyed and len(set(first)) == len(first) and all(k in seen for k in first):
        rest = [k for k in seen if k not in set(first)]
        tail = witnessed_order(rest)
        stats["order"] = ("snapshot" if not rest else "snapshot, then witnessed"
                          if tail is not None else
                          "snapshot, then rev-id (witness orders conflict)")
        if tail is None:
            tail = sorted(rest, key=lambda k: (k[1], k[0]))
        records = [e["rec"] for e in copy] + [seen[k][0] for k in tail]
        return records, stats
    order = witnessed_order(list(seen))
    stats["order"] = ("witnessed" if order is not None
                      else "rev-id (witness orders conflict)")
    if order is None:
        order = sorted(seen, key=lambda k: (k[1], k[0]))
    records = [seen[k][0] for k in order]
    return records, stats


def snapshot_truncation_witness(entries: list[dict[str, Any]],
                                result_entries: list[dict[str, Any]] | None = None
                                ) -> str | None:
    """The pc-0ff7 suffix-truncation signature, as a test any writer can run:
    the recorded snapshot head is nowhere in the current chain while the
    log's records are a proper prefix of the snapshot's. There the snapshot
    is not a stale projection to refresh — it is the ONLY WITNESS of entries
    the log no longer carries (format-v2.md §8: E013 structurally cannot see
    a suffix truncation), and regenerating it converts a detected custody
    violation into a clean state.

    v2.8 (pc-c6b8): pc-0ff7 guarded `init` and `snapshot` and the ordinary
    writers kept regenerating — `add` (via append_record) and both `sync`
    paths erased the witness at exit 0. Every write_snapshot caller now asks
    this question first — since v3.3 that is `snapshot`, `init`, `sync` and
    `migrate`, as an ordinary write no longer regenerates the snapshot and so
    cannot erase it. `result_entries` is the timeline the caller is
    about to make current: when it CONTAINS the recorded head, the operation
    is the recovery (a sync hydrating the published, un-truncated chain) and
    proceeds — the witness is satisfied, not erased. Returns the refusal
    message, or None when the signature is absent."""
    head_file = snapshot_head_path()
    if not head_file.exists() or not snapshot_path().exists():
        return None
    # Sibling of pc-23a1's shape, fixed in the same commit: read_text()
    # decoded strictly, so invalid-UTF-8 bytes in either generated file
    # crashed EVERY writer that asks this question (add, sync, snapshot,
    # init) as fatal E000. Surrogateescape the decode: a garbage head
    # matches no hash and garbage snapshot lines fail the parse below —
    # both land in the already-declared forged-case handling.
    recorded = head_file.read_bytes().decode(
        "utf-8", errors="surrogateescape").strip()
    if not recorded or recorded in {entry_hash(e) for e in entries}:
        return None
    try:
        snap_records = [strict_json_loads(ln) for ln in
                        source_lines(snapshot_path().read_bytes()
                                     .decode("utf-8", errors="surrogateescape"))
                        if ln.strip()]
    except json.JSONDecodeError:
        return None   # garbage content is the forged case: regenerate is the remedy
    log_records = [e["rec"] for e in entries]
    if not (len(snap_records) > len(log_records)
            and all(canonical(a) == canonical(b)
                    for a, b in zip(log_records, snap_records))):
        return None
    if (result_entries is not None
            and recorded in {entry_hash(e) for e in result_entries}):
        return None
    # SAY WHICH LOSS THIS CAN BE, AND NAME AN EXIT THAT WORKS
    # (pc-7e110b7cf434). This used to call every such log suffix-truncated
    # and count the local log as "the chain" — on a fresh clone, "N records
    # vs 0 in the chain" — and send the reader to `pecia migrate`, which
    # refuses in the same state and sends them back to `pecia sync`. A clone
    # with no entries and no high-water mark cannot have lost any: the
    # snapshot committed here carries entries nobody published. With a log,
    # the same shape is either a loss from its end or another clone's
    # unpublished work, and nothing local tells them apart.
    s = len(snap_records)
    mark = log_mark_path()
    if not log_records and (mark is None or not mark.exists()):
        holder = (f"the published timeline it would adopt "
                  f"({len(result_entries)} entries)"
                  if result_entries is not None else "nothing here")
        return (f"this clone has no timeline yet, and the snapshot committed "
                f"here ({s} entries) names a head that {holder} does not "
                f"hold — the commit carries entries that were never "
                f"published, and the snapshot is their only copy here (E015, "
                f"pc-0ff7). Have whoever committed them run `pecia publish`, "
                f"then run `pecia sync`; `pecia migrate` rebuilds from history "
                f"instead, but only if that rebuild extends the published "
                f"timeline")
    holders = (f"neither the log nor the timeline it would write "
               f"({len(result_entries)} entries) holds"
               if result_entries is not None else "the log does not hold")
    return (f"the snapshot ({s} entries) agrees with this log's "
            f"{len(log_records)} and extends it, and names a head that "
            f"{holders} — either the log lost entries from its end, or the "
            f"snapshot was committed from a clone that has not published its "
            f"later entries; the snapshot is their only copy here (E015, "
            f"pc-0ff7). If another clone wrote them, have it run `pecia "
            f"publish`, then run `pecia sync`; if this log lost them, recover "
            f"it first (`pecia migrate --force` rebuilds it from history and the "
            f"snapshot)")


def verified_snapshot_chain() -> list[dict[str, Any]] | None:
    """The snapshot's records chained as their writers chained them, if that
    chain ends at the head the snapshot records; None otherwise (v3.6,
    pc-4f84768dbed1).

    A COMMITTED SNAPSHOT IS A COPY OF THE TIMELINE. It is canonical(rec) of
    every entry in log order (E015 holds it to exactly that), and every other
    field of an entry is determined: `seq` by position, `prev` by the entry
    before, and `touched` by the diff from the id's head, which no writer may
    author (§4.1, E014). So chaining the records with the writer's own
    make_entry rebuilds the entries byte for byte, and snapshot.head is a
    SHA-256 commitment to the result. A snapshot that was edited, replaced or
    truncated does not rebuild to its head, and is never trusted."""
    head_file, snap = snapshot_head_path(), snapshot_path()
    if not head_file.exists() or not snap.exists():
        return None
    recorded = head_file.read_bytes().decode("utf-8", "surrogateescape").strip()
    if not recorded:
        return None
    entries: list[dict[str, Any]] = []
    heads: dict[str, dict[str, Any]] = {}
    prev: str | None = None
    for line in source_lines(snap.read_bytes().decode("utf-8", "surrogateescape")):
        if not line.strip():
            continue
        try:
            line.encode("utf-8")
            rec = strict_json_loads(line)
        except (UnicodeEncodeError, json.JSONDecodeError):
            return None
        if not isinstance(rec, dict):
            return None
        rid, rev = rec.get("id"), rec.get("rev")
        entry = {"seq": len(entries) + 1, "prev": prev,
                 "touched": diff_fields(heads.get(rid) if isinstance(rid, str) else None, rec),
                 "rec": rec}
        entries.append(entry)
        prev = entry_hash(entry)
        if isinstance(rid, str) and strict_int(rev):  # make_entry's head rule
            if rid not in heads or rev > heads[rid]["rev"]:
                heads[rid] = rec
    return entries if entries and prev == recorded else None


def chain_prefix(short: list[dict[str, Any]], long: list[dict[str, Any]]) -> bool:
    """Whether `short` is `long`'s first entries, entry for entry."""
    return len(short) <= len(long) and all(
        canonical(a) == canonical(b) for a, b in zip(short, long))


def copy_parts_note(copy: list[dict[str, Any]] | None,
                    local: list[dict[str, Any]],
                    theirs: list[dict[str, Any]]) -> str:
    """Why a snapshot whose chain verifies was still not adopted, for a
    refusal to say: the published timeline has moved on from it."""
    if (copy is None or not chain_prefix(local, copy) or chain_prefix(theirs, copy)
            or chain_prefix(copy, theirs)):
        return ""
    k = next(i for i in range(len(theirs))
             if i >= len(copy) or canonical(theirs[i]) != canonical(copy[i]))
    return (f". The snapshot's own chain does rebuild to its recorded head, but "
            f"the published timeline parts from it at entry {k + 1}: its later "
            f"entries must be re-chained before they can be published, so this "
            f"commit's snapshot can never be adopted. Its writer has to sync, "
            f"publish and commit the snapshot that produces; check out that "
            f"commit and sync")


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Regenerate .pecia/work.jsonl from the log (pc-223c).

    E015's remedy was 'regenerate it' and nothing regenerated it: write_snapshot
    ran only inside append_record and migrate, so the routes were an unwanted
    write or `migrate --force`, which is the timeline rewrite pc-2276 caught.
    A gate whose remedy has no command pushes people to the destructive
    workaround. Read-only with respect to the log, by construction.

    ONE state is refused (pc-0ff7, v2.7): the SUFFIX-TRUNCATION signature —
    the recorded head is nowhere in this chain while the log's records are a
    proper prefix of the snapshot's. There the snapshot is not a stale
    projection to refresh; it is the ONLY WITNESS of entries the log no
    longer carries (format-v2.md §8, v2.6: E013 structurally cannot see a
    suffix truncation, the head file is the witness). Regenerating would
    convert a detected custody violation into a clean state. Every other
    E015 flavour — forged or replaced snapshot content, a missing head —
    keeps this command as its documented remedy."""
    entries, findings = load_entries()
    if findings:
        # SAY WHY THIS COMMAND IS NOT THE REMEDY (v2.16, pc-1667). This used
        # to re-emit the log's own finding verbatim, which reads as though
        # regeneration had been attempted and failed. The projection is
        # DERIVED from the log: until the log reads whole there is nothing to
        # derive it from, and a reader sent here by a diagnostic needs to be
        # told to go back rather than to try again.
        return cannot_run(
            f"refusing to regenerate: the log does not read whole — "
            f"{findings[0]['message']}. The projection is derived from the "
            f"log, so repair the log first and then regenerate; nothing was "
            f"written here and the existing projection is untouched")
    witness = snapshot_truncation_witness(entries)
    if witness:
        return cannot_run(f"refusing to regenerate: {witness}")
    write_snapshot(entries)
    emit({"regenerated": str(snapshot_path()), "entries": len(entries), "rule": RULE1})
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Reconstruct the single timeline (pc-5c7c).

    GP36: the expected diff is ENUMERATED BEFORE the comparison, stating unit
    and frame. Unit is record-REVISIONS, not ids — counting entities where the
    unit is values is one of the three misses that entry records. Frame is
    every reachable blob of .pecia/work.jsonl plus the working tree; what the
    frame structurally cannot contain is a revision that was never committed
    on any ref and is not in this worktree."""
    target = log_path()
    if target is None:
        return cannot_run("not inside a git repository — the timeline lives under --git-common-dir")
    records, stats = collect_historical_records()
    # A SOURCE LINE THE REBUILD CANNOT READ IS A REFUSAL, NOT A DROP
    # (pc-d502, round-6 lane B2-F1). The collector used to skip such lines
    # silently, so — once the strict parse gates stopped the fatal-E000
    # crash on the same input — a snapshot line carrying an overflowing
    # exponent would have been dropped from the rebuild at exit 0: silent
    # loss on the one command documented as safe to re-run. Same ladder as
    # the damaged-log guard below: fix the source, or accept the loss
    # deliberately with --force-drop (the count is disclosed either way).
    if stats["unreadable_lines"] and not args.force_drop:
        n = stats["unreadable_lines"]
        return cannot_run(
            f"{n} source line{'' if n == 1 else 's'} could not be read as "
            f"record{'' if n == 1 else 's'} (strict JSON carrying id and "
            f"rev) — a rebuild would DROP {'it' if n == 1 else 'them'} "
            f"silently. Fix the source (`pecia check` names the damage), or "
            f"pass --force-drop to accept the loss deliberately")
    # THE EXISTING LOG IS A SOURCE, NOT AN OUTPUT. migrate rebuilds from
    # committed blobs plus the working-tree snapshot; an entry appended to the
    # log but not yet snapshotted or committed is in NEITHER, so a rebuild
    # dropped it and the result was internally consistent enough that `check`
    # reported clean. A silent-loss path in the one command documented as safe
    # to re-run, in a tool whose premise is append-only custody (pc-824a).
    existing, log_findings = read_log(target)
    # A DAMAGED LOG DOES NOT DISABLE THE ORPHAN GUARD (pc-e520, v2.7). The
    # guard below runs only over a fully-read log, so a blank physical line —
    # an E001 finding that stops read_log at that line — used to skip it
    # entirely, and `migrate --force` erased every entry past the damage
    # without --force-drop: exactly the state that most needs the guard was
    # the state that disabled it. A log that cannot be read to the end cannot
    # have its entries verified as preserved, so the rebuild is refused until
    # the log is fixed or the loss is accepted deliberately.
    if target.exists() and log_findings and not args.force_drop:
        return cannot_run(
            f"the existing log cannot be read cleanly ({log_findings[0]['message']}) "
            f"— entries past the damage cannot be verified as preserved, so a "
            f"rebuild could ERASE them invisibly. Fix the log (`pecia check` names "
            f"the damage), or pass --force-drop to accept the loss deliberately")
    if existing and not log_findings:
        known = {(r.get("id"), r.get("rev")) for r in records}
        orphans = [e["rec"] for e in existing
                   if (e["rec"].get("id"), e["rec"].get("rev")) not in known]
        if orphans and not args.force_drop:
            return cannot_run(
                f"{len(orphans)} entr{'y exists' if len(orphans) == 1 else 'ies exist'} only in the "
                f"log and in no committed blob or snapshot (first: "
                f"{orphans[0].get('id')} rev {orphans[0].get('rev')}). Rebuilding would "
                f"ERASE them. Commit the snapshot first, or pass --force-drop to accept "
                f"the loss deliberately")
        stats["log_only_orphans"] = len(orphans)
    if stats["divergent"]:
        return cannot_run(
            f"{stats['divergent']} (id,rev) pairs carry divergent content — "
            "disposition them manually before migrating; this is the last merge")
    # PROMOTION CARRIES THE BRAND (pc-cce9, v2.6). When an existing canonical
    # log is being REPLACED, any record-revision entering the rebuild that
    # the canonical log did not hold byte-for-byte is snapshot- or blob-born
    # content becoming authority without ever passing the write gate — the
    # same escape-hatch shape as --force, so it carries the same per-revision
    # `forced: true` brand and the count is reported. V7/V10's custody
    # principle (escape-hatch uses enumerable from the ledger alone) now
    # covers this path; before, `migrate --force --force-drop` promoted a
    # forked snapshot to sole authority with no line carrying the mark. A
    # plain bootstrap (no existing log) brands nothing: there is no canonical
    # content to promote over.
    #
    # REPLACEMENT IS A FACT ABOUT THE FILE, NOT ITS CONTENT (pc-14b2, v2.7).
    # The condition was `canonical_lines` being non-empty, so a --force
    # rebuild over an INITIALIZED EMPTY log — an existing log by this
    # command's own refusal ("already exists ... use --force") — promoted
    # snapshot-born records to sole authority with no brand and
    # promoted_branded 0. An empty canonical log holds zero lines and every
    # promoted record is outside that set; the brand now follows.
    replacing = target.exists()
    canonical_lines = {canonical(e["rec"]) for e in existing}
    promoted = 0
    entries: list[dict[str, Any]] = []
    for rec in records:
        if replacing and canonical(rec) not in canonical_lines:
            rec = dict(rec)
            rec["forced"] = True
            promoted += 1
        ok, why = cas_admissible(entries, rec)
        if not ok:
            return cannot_run(f"migration input violates the CAS at {rec.get('id')}: {why}")
        entries.append(make_entry(entries, rec))
    stats["promoted_branded"] = promoted
    # A LOG THAT ENDS BEFORE ITS MARK (v3.3, E019) lost entries this store
    # wrote. A rebuild that holds the marked entry restores them, and is the
    # recovery; one that does not would bury the loss under a clean
    # timeline, so it is refused unless the loss is accepted, as for
    # log-only entries.
    gone = mark_violation(existing)
    if gone and not holds_marked_entry(entries) and not args.force_drop:
        return cannot_run(
            f"{gone} (E019), and the rebuild from this repo's history does not "
            f"hold the marked entry either, so it would bury the loss. Recover "
            f"with `pecia sync`, or pass --force-drop to accept the loss "
            f"deliberately")
    # THE OUTPUT IS CHECKED BEFORE IT IS WRITTEN (pc-0fa7, v2.6). migrate ran
    # cas_admissible and read_log over what it was about to write, but never
    # run_checks/timeline_checks — so a normal unforced bootstrap certified
    # success while creating a canonical state the very next `check` rejects
    # (the reproduction: one shape-valid legacy record carrying a dangling
    # edge). The candidate timeline is held to the same full checker publish
    # and sync already use; on findings nothing is written, the findings ARE
    # the output, and the exit is 1 — a refusal, not a cannot-run: the input
    # was readable, and what it implies is a dirty timeline.
    bad = timeline_errors(entries, load_config(), check_snapshot=False)
    if bad:
        for f in bad:
            emit(f)
        emit({"migrated": False, "entries": len(entries),
              "note": "refusing to write a timeline the checker rejects — fix "
                      "the findings above (or disposition the source records) "
                      "and re-run", "rule": RULE1})
        return 1
    if target.exists() and not args.force:
        return cannot_run(f"{target} already exists — refusing to rewrite a timeline (use --force)")
    # A REBUILD MUST EXTEND THE PUBLISHED TIMELINE, NOT REPLACE IT (pc-2276).
    # The reconstruction follows the witnessed source order (pc-356c) and
    # falls back to (rev, id) when the witnesses disagree; either way an
    # identical record set on a different chain is a fork by rule 5's own
    # definition, and this guard is what arbitrates it.
    # This session ran `migrate --force` after nearly every write
    # as a habit from the transition period, so every publish before pc-c613's
    # prefix guard landed was publishing a rewritten chain and nothing could
    # tell. That guard caught it at publish; refusing here catches it one step
    # earlier, where the damage is still cheap to undo. migrate is a BOOTSTRAP
    # command and reads like a maintenance one, which is the deeper defect.
    published, register_err = ref_head()
    if register_err is not None and not args.force_drop:
        # Sibling of pc-a6d3, same commit — the same guard shape as the
        # blob-read refusal below: a rebuild must not replace a published
        # timeline it cannot compare against, and --force-drop stays the
        # deliberate acceptance.
        return cannot_run(
            f"the local publication register {PECIA_REF} exists but cannot "
            f"be read: {register_err} — a failed read is an unknown "
            f"register, not an absent one, so the divergence guard cannot "
            f"run. Repair the read (or accept the rewrite with "
            f"--force-drop)")
    # pc-5f0f: `published` was unconditionally None on a FRESH clone — a
    # plain `git clone` never fetches refs/pecia/log — so the guard above was
    # skipped exactly in the case it exists for, and a fork surfaced one
    # command later, at `publish`'s own non-fast-forward rejection. If no
    # local ref exists, make ONE best-effort, non-mutating attempt to learn
    # it from the remote — the identical fetch `pecia sync` already performs
    # (same ref, same scratch target). This does NOT make migrate a resync:
    # it still adopts none of the remote's content, and the fetch only
    # informs the SAME refusal below. Best-effort because migrate is a
    # BOOTSTRAP command that must still bootstrap with no remote configured,
    # or with one that is unreachable — both degrade to the pre-fix
    # behaviour; `git_run` already times out rather than hanging.
    remote_check: dict[str, Any] = {"attempted": False}
    if not published:
        code, remotes, remote_err = git_run("remote")
        # pc-30aa's sibling, DISCLOSED rather than refused: migrate is a
        # bootstrap command whose contract says it must still bootstrap
        # with no remote or an unreachable one, so an enumeration failure
        # degrades like an unreachable remote — but it degrades on the
        # record, in remote_check, never silently.
        if code != 0:
            remote_check["enumeration_failed"] = safe_text(
                remote_err or "git remote failed", MESSAGE_CAP)
        if remotes:
            remote = args.remote or remotes.split("\n")[0]
            remote_check.update(attempted=True, remote=remote)
            code, _, err = git_run("fetch", remote, f"+{PECIA_REF}:refs/pecia/remote")
            if code == 0:
                published = git_out("rev-parse", "--verify", "--quiet", "refs/pecia/remote")
                remote_check["reached"] = bool(published)
            else:
                remote_check["reached"] = False
                remote_check["error"] = safe_text(err, MESSAGE_CAP)
    if published:
        code, prior, prior_err = git_blob(f"{published}:log.jsonl")
        # Sibling of pc-39f6's shape, fixed in the same commit: the ref was
        # LEARNED (locally or via the best-effort fetch above) and then the
        # blob read failed — an unknown register, not an absent one. Gating
        # the divergence guard on `code == 0` skipped it exactly when its
        # input was unavailable, letting a rebuild replace a published
        # timeline it could not compare against. --force-drop remains the
        # deliberate acceptance of a rewrite, so it also accepts publishing
        # over a register that cannot be read.
        if code != 0 and not args.force_drop:
            return cannot_run(
                f"could not read the published prefix ({published}:log.jsonl): "
                f"{prior_err or 'git show failed'} — a failed read is an "
                f"unknown register, not an empty one, so the divergence guard "
                f"cannot run. Repair the read (or accept the rewrite with "
                f"--force-drop)")
        if code == 0:
            prior_lines, why = published_blob_lines(prior)
            if prior_lines is None and not args.force_drop:
                # Same sibling shape as publish's guard: a blank-line blob
                # must not be normalized into a comparable fiction.
                return cannot_run(
                    f"{why} — the published blob must be repaired (or the "
                    f"rewrite accepted with --force-drop), never silently "
                    f"normalized in comparison")
            if prior_lines is not None:
                rebuilt_lines = [canonical(e) for e in entries]
                if rebuilt_lines[:len(prior_lines)] != prior_lines and not args.force_drop:
                    # SYNC IS THE REMEDY ONLY IF SYNC CAN RUN (pc-7e110b7cf434).
                    # When the published timeline does not hold the head this
                    # commit's snapshot names, sync refuses too (E015), and
                    # pointing there closed a loop: each command named the
                    # other. Say what is actually missing and who holds it.
                    head_file = snapshot_head_path()
                    recorded = (head_file.read_bytes().decode(
                        "utf-8", errors="surrogateescape").strip()
                        if head_file.exists() else "")
                    held = {hashlib.sha256(l.encode("utf-8", errors="surrogateescape"))
                            .hexdigest() for l in prior_lines}
                    if recorded and recorded not in held:
                        lost = (", or that this log lost from its end"
                                if target.exists() else "")
                        return cannot_run(
                            f"the rebuild would not extend the published timeline: "
                            f"{len(prior_lines)} entries are on {PECIA_REF} and the "
                            f"reconstruction diverges from them. migrate is a BOOTSTRAP "
                            f"command, not a resync — and `pecia sync` cannot adopt that "
                            f"timeline either, since it does not hold the head this "
                            f"commit's snapshot names: the snapshot carries entries that "
                            f"were never published{lost}. If another clone wrote them, "
                            f"have it run `pecia publish`, then run `pecia sync`; "
                            f"--force-drop accepts the rewrite, but the result is a fork "
                            f"`pecia publish` cannot push")
                    return cannot_run(
                        f"the rebuild would not extend the published timeline: "
                        f"{len(prior_lines)} entries are on {PECIA_REF} and the reconstruction "
                        f"diverges from them. migrate is a BOOTSTRAP command, not a resync — "
                        f"use `pecia sync` to reconcile, or --force-drop to accept the rewrite")
    if args.dry_run:
        emit({"would_write": str(target), "entries": len(entries), **stats,
              "remote_check": remote_check, "rule": RULE1})
        return 0
    # VALIDATE, THEN RENAME (pc-d373). This used to serialize onto `target`
    # and refuse after reading it back — and migrate's target is an EXISTING
    # log on the --force-drop and replacement paths, so the refusal left a
    # reconstruction it had just rejected sitting where the log was.
    # The projection is part of the commit point here too (pc-29ee): migrate
    # rewrites the log wholesale, so a projection it could not write would
    # leave the reconstruction standing beside the OLD work.jsonl.
    verify, refusal = write_log_validated(
        target, entries,
        also=lambda parsed: (None if len(parsed) == len(entries) else
                             "written log does not read back cleanly — "
                             "chain integrity failed"),
        project=True)
    if refusal is not None:
        return cannot_run(f"the reconstruction does not read back cleanly: "
                          f"{refusal} — nothing was written")
    # The chain head is reported, as publish and sync report theirs (v3.3,
    # pc-ded91385e31a): withheld under pc-cdb8 as a confirmation oracle for
    # prose that is no longer withheld, it is what a reader compares across
    # clones.
    emit({"migrated": str(target), "entries": len(entries), **stats,
          "head": log_head_hash(entries), "remote_check": remote_check, "rule": RULE1})
    return 0


def empty_edges() -> dict[str, Any]:
    edges: dict[str, Any] = {key: [] for key in LIST_EDGES}
    for key in SCALAR_EDGES:
        edges[key] = None
    return edges


#: Hex characters an id is minted with (pc-1c65). It was 4 — a 65536-wide
#: space — and `existing` only holds ids the MINTING CLONE knows, so two
#: clones creating records before either publishes drew from that space with
#: no shared allocator. Measured: at 100 ids in that window the birthday
#: probability is 7.3%, and at 1000 it is a certainty; the only reason this
#: ledger never hit it is that it has effectively had one clone. At 12 the
#: same window is 1.8e-11, and 1.8e-5 over a lifetime of 100k records.
#: Existing shorter ids stay valid and are NOT re-minted: they are unique in
#: this ledger, and re-minting would rewrite every citation of them in
#: records, dispositions, commit messages and research files.
MINT_HEX = 12


def mint_id(record_type: str, title: str, created: str, existing: set[str]) -> str:
    payload = f"{record_type}|{title}|{created}|{os.urandom(8).hex()}"
    # surrogatepass: an invalid-UTF-8 title must reach the write gate and be
    # refused as E001 (v3.0), not crash here as a fatal E000.
    digest = hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()
    for length in range(MINT_HEX, len(digest) + 1):
        candidate = f"pc-{digest[:length]}"
        if candidate not in existing:
            return candidate
    raise RuntimeError("id space exhausted")


def default_owner() -> str:
    raw = os.environ.get("PECIA_OWNER") or getpass.getuser()
    return os.fsencode(raw).decode("utf-8", errors="surrogateescape")


def provenance_anchor() -> dict[str, Any]:
    """The commit this revision was written against (v2.5, pc-05e4, closing
    pc-e039), stamped by the substrate at write time — the subtractive case
    where structural derivation is measured to hold, and it costs the author
    nothing. A cold agent runs `git diff <anchor>..HEAD` to see exactly what
    moved under a record since it was written.

    Optional everywhere, never required on input (rule 3): absent outside a
    git worktree and on an unborn branch. `anchor_dirty: true` records that
    the worktree carried uncommitted tracked changes at write time, so the
    diff against the anchor under-reports what the writer actually saw —
    recorded, not refused. Tracked changes only (`--untracked-files=no`):
    an untracked scratch file does not change what the anchor fails to
    capture about the TREE the record describes."""
    code, head, _ = git_run("rev-parse", "HEAD")
    if code != 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        return {}
    fields: dict[str, Any] = {"anchor": head}
    dcode, dirty, _ = git_run("status", "--porcelain", "--untracked-files=no")
    if dcode == 0 and dirty:
        fields["anchor_dirty"] = True
    return fields


def head_adjacency(heads: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """The scheduling graph E004 must keep acyclic.

    An arc u -> v means "u must reach a terminal status before v can": that is
    `blocks` read forwards, `parent` read from the child (an open child holds
    its parent open), and — new in v1.13 — `retires` read from the retirer,
    since X's terminality is produced BY Y's closure. All three orientations
    are the same relation, which is why they share one subgraph.

    Mutual retirement (Y retires X, X retires Y) is therefore a named E004
    cycle rather than a silent deadlock — the outcome pc-4d19 rejected
    half-membership to avoid."""
    adjacency: dict[str, list[str]] = {}
    for rid, head in heads.items():
        edges = head.get("edges") or {}
        out = [t for key in LIST_EDGES for t in (edges.get(key) or []) if t in heads]
        if edges.get("parent") in heads:
            out.append(edges["parent"])
        adjacency[rid] = out
    return adjacency


def retirers_of(heads: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """target id -> the head ids claiming, via `retires`, to resolve it.

    The edge is stored on the retirer; almost everything downstream needs it
    inverted, and the inversion is many-to-one: several records may each name
    the same target. That is not an error — it is the case where one item is
    resolved by several pieces of work, and it is the reason E012 is anchored
    on the TARGET rather than on each retirer (see run_checks)."""
    out: dict[str, list[str]] = {}
    for rid, head in heads.items():
        for target in ((head.get("edges") or {}).get("retires") or []):
            if isinstance(target, str):
                out.setdefault(target, []).append(rid)
    return {t: sorted(set(v)) for t, v in out.items()}


def cycles_in(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Cycle detection over the head graph, ITERATIVE (pc-b84c, round-4 lane
    A-F2): the recursive DFS recursed once per edge, so a valid 1,100-record
    acyclic blocks-chain — reachable through 1,100 ordinary adds — blew the
    interpreter's recursion limit and killed `check` as fatal E000
    RecursionError at exit 2. The checker is total (report, never crash),
    and the depth a write path can legally reach must never exceed what the
    checker can walk; an explicit stack has no such ceiling."""
    state: dict[str, int] = {}
    seen: set[frozenset[str]] = set()
    found: list[list[str]] = []

    def visit(root: str) -> None:
        # A FOUND CYCLE NO LONGER ABORTS THE WALK (pc-86f9, round-6 lane
        # C-F2). Returning on the first cycle left every node of the
        # abandoned path at state 1, so the NEXT root's traversal read the
        # stale visiting mark as a back edge: pc-a self-cycling plus
        # pc-b -> pc-a emitted a false second cycle "pc-b -> pc-a" that no
        # path closes, anchored on a record no cycle passes through. The
        # cycle is recorded and the scan continues, which restores the DFS
        # invariant that a state-1 node is ALWAYS on the current path —
        # the not-in-path fallback that manufactured the false finding has
        # no remaining input and is gone.
        state[root] = 1
        path = [root]
        stack = [(root, iter(adjacency.get(root, [])))]
        while stack:
            node, neighbors = stack[-1]
            descended = False
            for nb in neighbors:
                if state.get(nb) == 1:
                    cycle = path[path.index(nb):] + [nb]
                    if frozenset(cycle) not in seen:
                        seen.add(frozenset(cycle))
                        found.append(cycle)
                    continue
                if state.get(nb, 0) == 0:
                    state[nb] = 1
                    path.append(nb)
                    stack.append((nb, iter(adjacency.get(nb, []))))
                    descended = True
                    break
            if not descended:
                state[node] = 2
                stack.pop()
                path.pop()

    for rid in sorted(adjacency):
        if state.get(rid, 0) == 0:
            visit(rid)
    return found



def cyclic_components(adjacency: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    """Each node on a cycle, mapped to its whole cyclic component (pc-c69d).

    One DFS pass reports one cycle per back edge it meets, so a second cycle
    through the same component can go unreported: a->b->d->a and a->c->d->a
    gave one E004 and named pc-c nowhere. The strongly connected components
    are what every cycle lives in, and every record on any cycle is in one of
    size two or more (or has a self-edge), so this names all of them without
    enumerating cycles, whose number can be exponential. Tarjan's algorithm,
    ITERATIVE for the same reason cycles_in is (pc-b84c): the checker never
    recurses per edge."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: dict[str, frozenset[str]] = {}
    for root in sorted(adjacency):
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(adjacency.get(root, [])))]
        while work:
            node, neighbors = work[-1]
            descended = False
            for nb in neighbors:
                if nb not in index:
                    index[nb] = low[nb] = len(index)
                    stack.append(nb)
                    on_stack.add(nb)
                    work.append((nb, iter(adjacency.get(nb, []))))
                    descended = True
                    break
                if nb in on_stack:
                    low[node] = min(low[node], index[nb])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    members.append(w)
                    if w == node:
                        break
                if len(members) > 1 or node in adjacency.get(node, []):
                    component = frozenset(members)
                    for w in members:
                        components[w] = component
    return components

def strict_int(value: Any) -> bool:
    """Python bools satisfy isinstance(int) — the F10 hole. Exclude them."""
    return isinstance(value, int) and not isinstance(value, bool)


def record_is_sound(rec: Any) -> bool:
    """Hard shape soundness — the shapes downstream phases index on. An
    unsound record's E001 findings ARE its diagnosis (F19: the checker is
    total; it reports, it never crashes); graph, transition, and head phases
    exclude it rather than choke on it."""
    if not isinstance(rec, dict):
        return False
    if not isinstance(rec.get("id"), str) or not strict_int(rec.get("rev")):
        return False
    if not isinstance(rec.get("type"), str) or not isinstance(rec.get("status"), str):
        return False
    if not isinstance(rec.get("title"), str):
        return False
    edges = rec.get("edges")
    if not isinstance(edges, dict):
        return False
    for key in LIST_EDGES:
        value = edges.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return False
    for key in SCALAR_EDGES:
        value = edges.get(key)
        if value is not None and not isinstance(value, str):
            return False
    for field in ("disposition", "evidence"):
        if rec.get(field) is not None and not isinstance(rec.get(field), str):
            return False
    return True


def validate_record(rec: dict[str, Any], statuses: set[str],
                    types: set[str], line: int | None = None) -> list[dict[str, Any]]:
    """The full E001 field contract (spec v1.4). One finding per defect.

    `line` is the record's one-based source line — the file line on the
    --ledger route, the log line on the store route, the timeline position
    at the write gate — and is used only where the revision cannot serve
    as identity (below)."""
    out: list[dict[str, Any]] = []
    rid = rec.get("id") if isinstance(rec.get("id"), str) else None
    # E001 FINDINGS CARRY THE REVISION (v2.8, pc-3bdc). The write gate is a
    # set-diff over serialized findings, and E001 is per-REVISION where every
    # other run_checks code is per-head — so without revision identity a
    # candidate that REPEATED a historical malformation serialized identically
    # to the historical finding, collided in the diff, and landed as a second
    # malformed revision with exit 0. With the revision in the message,
    # repeating a malformation is a NEW finding and is refused; a repairing
    # revision produces no finding and still lands.
    # pc-aaa4 (round-4 lane A'-F4): the tag was appended only for a strict
    # integer rev, so the finding for rev 'bad' — the one case where
    # identifying WHICH revision is condemned needs the raw value — carried
    # no identity at all. A malformed rev is shown repr'd and marked, under
    # its own budget so the tag cannot be what floods the message it is the
    # identity of.
    # pc-eed1 (round-11 lane A-F1, v2.16): the tag was appended and the
    # ASSEMBLED string was then capped from the end, so the identity claim 30
    # states as universal was exactly what a long condemned value ate. The
    # variable half is capped first now, and the tag is appended to something
    # already short enough to carry it.
    # pc-b60a (round-7 lane A-F5, v2.12): absence used to stay untagged, so
    # two records missing `rev` produced byte-identical findings with
    # neither revision nor line identity — indistinguishable to a reader
    # and COLLIDING in the write gate's serialized-finding diff, the
    # pc-3bdc hole one level over. Where the revision cannot identify the
    # condemned line (absent, or malformed and possibly repeated), the
    # SOURCE LINE does; callers that know it pass it, and the tag is
    # unchanged wherever rev is sound.
    if strict_int(rec.get("rev")):
        rev_tag = f"[rev {rec.get('rev')}]"
    elif "rev" in rec:
        shown = capped_value(rec.get("rev"))
        rev_tag = (f"[rev {shown} — malformed, line {line}]"
                   if line is not None
                   else f"[rev {shown} — malformed]")
    elif line is not None:
        rev_tag = f"[rev absent — line {line}]"
    else:
        rev_tag = ""
    # Measured after the frame transform, because that is the form the tag
    # reaches the emitted message in and a budget computed on the raw string
    # would be the wrong number by exactly the characters it removes.
    rev_tag = safe_text(rev_tag, 0)

    def bad(message: str) -> None:
        if not rev_tag:
            out.append(finding("error", "E001", rid, message))
            return
        body = safe_text(message, max(0, MESSAGE_CAP - len(rev_tag) - 1))
        out.append(finding("error", "E001", rid, f"{body} {rev_tag}"))

    missing = [f for f in REQUIRED_FIELDS if f not in rec]
    if missing:
        bad(f"missing fields: {', '.join(missing)}")
        return out
    # The record contract owns the nesting bound too (v2.12, pc-2e2f): the
    # parse gate refuses an over-deep stored LINE, but a candidate built in
    # memory (an adapter import, a future write surface) never re-parses —
    # and the write gate is the checker, so the bound must be visible to
    # the finding diff. A record's own depth is bounded one below the line
    # bound because the log wraps it in the entry envelope. Iterative, so
    # the guard cannot be crashed by what it guards against.
    depth = record_nesting_depth(rec)
    if depth > NESTING_BOUND - 1:
        bad(f"record nests {depth} levels deep — the declared bound is "
            f"{NESTING_BOUND} for a stored line, so a record keeps to "
            f"{NESTING_BOUND - 1} (v2.12, pc-2e2f)")
    # v3.0 (pc-ddd9): the canonical domain, checked on the in-memory record
    # too so the write gate refuses a value the log could never carry
    # (e.g. a surrogateescaped argv) as E001 rather than a fatal E000.
    domain = canonical_domain_problem(rec)
    if domain is not None:
        bad(domain)
    # v2.7 (pc-a437): the record contract names no dotted top-level key, and
    # the touched grammar reads `a.b` as an edges.*-style path — so a dotted
    # extension key was structurally invisible to E014's recomputation and to
    # sync's conflict test. Refused rather than special-cased: no write path
    # produces one, and a name the diff machinery cannot address is not a
    # legal field name.
    dotted = sorted(k for k in rec if "." in k)
    if dotted:
        bad(f"top-level key(s) {capped_seq(dotted)} contain '.' — dotted names "
            f"collide with the touched grammar's edges.* paths and the record "
            f"contract names none (v2.7)")
    # v2.8 (pc-2d40): the shipped schema's propertyNames is ^[^.]+$ — NON-
    # EMPTY and dot-free — and this gate checked only the dots, so a record
    # carrying key "" passed check across a whole history while
    # dev/schema-check.py refused every line: two shipped gates disagreeing
    # about the canonical shape. No write path produces an empty key; like
    # the dotted case it is refused rather than special-cased.
    if any(not k for k in rec):
        bad('top-level key "" is empty — record.schema.json\'s propertyNames '
            '(^[^.]+$) requires every field name non-empty and dot-free, and '
            'a name the diff machinery cannot address is not a legal field '
            'name (v2.8)')
    # fullmatch, not match (pc-9726, round-7 lane A-F1): these patterns end
    # in `$`, and under match() `$` also matches immediately before a final
    # newline — so an id or date ending in "\n" was certified at exit 0
    # where the trailing-space form is E001, and `add --target '...\n'`
    # stored the newline through the write gate itself. The anchor and
    # foreign-reference validators in this file already use fullmatch; the
    # four ID_RE/DATE_RE sites now share that discipline.
    if not isinstance(rec["id"], str) or not ID_RE.fullmatch(rec["id"]):
        bad("id must be a pc-prefixed token")
    if not strict_int(rec["rev"]) or rec["rev"] < 1:
        bad("rev must be an integer >= 1 (booleans are not revisions)")
    if not isinstance(rec["type"], str):
        bad("type must be a string")  # F19: membership on unhashables crashed
    elif rec["type"] not in types:
        bad(f"unknown type: {capped_value(rec['type'])}")
    if not isinstance(rec["title"], str) or not rec["title"].strip():
        bad("title must be a non-empty string")
    if not isinstance(rec["status"], str):
        bad("status must be a string")
    elif rec["status"] in {"blocked", "ready"}:
        bad("blocked/ready are computed, never stored")
    elif rec["status"] not in statuses:
        bad(f"unknown status: {capped_value(rec['status'])}")
    if not strict_int(rec["priority"]) or not 0 <= rec["priority"] <= 4:
        bad("priority must be an integer 0-4 (booleans are not priorities)")
    for field in ("created", "updated"):
        if not isinstance(rec[field], str) or not DATE_RE.fullmatch(rec[field]):
            bad(f"{field} must be a YYYY-MM-DD string")
    if "target" in rec and (not isinstance(rec["target"], str) or not DATE_RE.fullmatch(rec["target"])):
        bad("target, when present, must be a YYYY-MM-DD string")
    if rec["disposition"] is not None and not isinstance(rec["disposition"], str):
        bad("disposition must be null or a string")
    if rec["evidence"] is not None and not isinstance(rec["evidence"], str):
        bad("evidence must be null or a string")
    if not isinstance(rec["owner"], str) or not rec["owner"].strip():
        bad("owner must be a non-empty string")
    if not isinstance(rec["labels"], list) or not all(isinstance(x, str) for x in rec["labels"]):
        bad("labels must be a list of strings")
    if not isinstance(rec["body"], str):
        bad("body must be a string")
    if "forced" in rec and rec["forced"] is not True:
        bad("forced, when present, must be true (the brand is presence, not a toggle)")
    # v2.5 (pc-05e4, pc-813f): the agent-assignee fields. All optional (rule
    # 3), all shape-checked when present.
    if "anchor" in rec and (not isinstance(rec["anchor"], str)
                            or not re.fullmatch(r"[0-9a-f]{40}", rec["anchor"])):
        bad("anchor, when present, must be a 40-hex commit id "
            "(stamped by the write path, never authored — v2.5)")
    if "anchor_dirty" in rec:
        if rec["anchor_dirty"] is not True:
            bad("anchor_dirty, when present, must be true (presence, not a toggle)")
        if "anchor" not in rec:
            bad("anchor_dirty without anchor — dirtiness qualifies an anchor")
    if ("ratified_by" in rec) != ("ratified" in rec):
        bad("ratified_by and ratified travel together (v2.5): a ratifying "
            "revision sets who and when in one act")
    if "ratified_by" in rec:
        if not isinstance(rec["ratified_by"], str) or not rec["ratified_by"].strip():
            bad("ratified_by must be a non-empty string")
        if rec.get("type") != "decision":
            bad("ratification is for decision records (v2.5) — other types "
                "have evidence, which is stronger")
    if "ratified" in rec and (not isinstance(rec["ratified"], str)
                              or not DATE_RE.fullmatch(rec["ratified"])):
        bad("ratified must be a YYYY-MM-DD string")
    if "context" in rec and (not isinstance(rec["context"], str)
                             or not parse_foreign_ref(rec["context"])):
        bad("context, when present, must be a <scheme>:<target> reference "
            "(v2.5, e.g. doc:docs/orientation.md#anchor)")
    edges = rec["edges"]
    if not isinstance(edges, dict):
        bad("edges must be an object")
        return out
    for key in LIST_EDGES:
        value = edges.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            bad(f"edges.{key} must be a list of strings")
        elif any(not x.strip() for x in value):
            # v2.7 (pc-94d2): a blank element names nothing — the resolution
            # machinery can never follow it, so it is malformed, not dangling.
            bad(f"edges.{key} contains an empty target — every element must "
                f"name a record (v2.7)")
    for key in edges:
        if key not in EDGE_KEYS and key != "no_edges":
            bad(f"unknown edge key: {safe_text(key, MESSAGE_ITEM_CAP)}")
    if "no_edges" in edges and edges["no_edges"] is not True:
        bad("edges.no_edges, when present, must be true (a declaration, not a toggle)")
    # v2.6 (pc-0c54): no_edges DECLARES the absence of the others, which is
    # the reading touched_conflicts already enforces for sync — yet an
    # ordinary local `edit --no-edges` produced a record asserting both the
    # declaration and a live edge, and check accepted it. The write gate is
    # the checker, so minting the refusal here closes edit, close and check
    # in one move.
    if edges.get("no_edges") is True and any(edges.get(k) for k in EDGE_KEYS):
        held = sorted(k for k in EDGE_KEYS if edges.get(k))
        bad(f"edges.no_edges beside a real edge ({', '.join(held)}) — no_edges "
            f"declares the absence of the others (v2.6); drop the declaration "
            f"or the edge")
    for key in SCALAR_EDGES:
        value = edges.get(key)
        if value is not None and not isinstance(value, str):
            bad(f"edges.{key} must be null or an id string")
        elif isinstance(value, str) and not value.strip():
            # v2.7 (pc-94d2): the empty string is not null and names no
            # record — a reference the resolution machinery can never
            # follow, previously admitted by this gate AND skipped by
            # E003's truthiness scan. Cleared is null, never "".
            bad(f"edges.{key} is an empty string — a scalar edge is null "
                f"or names a record; the empty string names nothing (v2.7)")
    return out


# -- the checker (E001–E009, E011–E017; E010 deleted at v2) ------------------

def legal_transition(prev_status: str, next_status: str) -> bool:
    if prev_status in TERMINAL_STATUSES:
        return next_status == prev_status  # terminal is final; same-status edits allowed
    return True  # open/in-progress may move anywhere (incl. each other and terminal)


def evidence_command_head(evidence: Any, cfg: dict[str, Any]) -> str | None:
    """The head token of a command-shaped evidence string, else None.

    None means: absent, `"unknown"`, unparseable, a foreign reference (that
    is `parse_foreign_ref`'s territory), or prose. Shape is read off the
    ledger and its config and nothing else — see `evidence_is_structural`."""
    if not isinstance(evidence, str) or not evidence.strip() or evidence == "unknown":
        return None
    if parse_foreign_ref(evidence):
        return None
    try:
        tokens = shlex.split(evidence)
    except ValueError:
        return None
    if not tokens:
        return None
    head = tokens[0]
    return head if "/" in head or head in evidence_commands(cfg) else None


def evidence_is_structural(evidence: Any, cfg: dict[str, Any]) -> bool:
    """Executable-shaped or a foreign reference (v1.1, amended v1.7, v1.8).

    Shape only, and shape is a function of the ledger and its config — never
    of the host. Whether a command *runs*, or a reference *resolves*, is a
    truth question: `audit` asks it, `check` never does, so `check` stays a
    pure function of the ledger (design target 1).

    v1.7 removed the built-in claims branch that read claims.yaml from here.
    v1.8 removed the last two host reads, `shutil.which(head)` and
    `(ROOT / head).exists()` (pc-2251): they made the identical ledger exit 0
    in a clone carrying an untracked `.venv/` and exit 1 with E007 in a fresh
    worktree of the same commit — a hard gate whose colour was a fact about
    the machine. Prose still fails, which is the whole discrimination E007
    exists for; `true` still passes, because rule 1 owns truth."""
    return (parse_foreign_ref(evidence) is not None
            or evidence_command_head(evidence, cfg) is not None)


def resolve_reference(command: str, target: str, timeout: int = 10) -> tuple[bool, str, int | None, str]:
    """Run a declared verifier against a reference id. Exit 0 means RESOLVABLE,
    never true (rule 1) — the verifier certifies the referent exists in the
    foreign system, which is the entire claim a reference makes.

    Only ever called from `audit`. Returns (ok, reason, exit-code, stderr):
    `reason` from a closed vocabulary that says what kind of failure it was,
    and the verifier's first non-empty stderr line, which says why — so a
    resolver can be debugged from the finding (v3.3, pc-ded91385e31a; its
    withholding under pc-cdb8 is reversed)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, "unparseable-command", None, ""
    if not argv:
        return False, "empty-command", None, ""
    try:
        proc = subprocess.run([*argv, target], cwd=str(ROOT), capture_output=True,
                              text=True, errors="replace", timeout=timeout, check=False)
    except FileNotFoundError:
        return False, "not-found", None, ""
    except subprocess.TimeoutExpired:
        return False, "timeout", None, ""
    except OSError:
        return False, "os-error", None, ""
    if proc.returncode == 0:
        return True, "resolved", 0, ""
    first = next((line.strip() for line in proc.stderr.splitlines() if line.strip()), "")
    return False, "nonzero-exit", proc.returncode, first


# -- the projector: the subtractive half of the output boundary --------------

OMIT = object()   # a projector's "this key is absent", for presence-semantics fields


def _chars(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


def _words(value: Any) -> int:
    return len(value.split()) if isinstance(value, str) else 0


def evidence_kind(rec: dict[str, Any], cfg: dict[str, Any] | None = None) -> str:
    """Structural classification of `evidence`: what kind of thing it is —
    absent, `unknown`, a command, prose, a reference — which the write echo
    and audit report beside the text. Shape only: E007 cannot say a command
    proves anything (`echo <anything at all>` is executable-shaped and
    passes, because `echo` resolves on PATH)."""
    ev = rec.get("evidence")
    if ev is None or (isinstance(ev, str) and not ev.strip()):
        return "absent"
    if not isinstance(ev, str):
        return "malformed"
    if ev == "unknown":
        return "unknown"
    ref = parse_foreign_ref(ev)
    if ref:
        return f"reference:{safe_text(ref[0], VOCAB_CAP)}"
    return "command" if evidence_is_structural(ev, cfg or load_config()) else "prose"


def projection_ceiling_bytes(edge_targets: int = 0) -> int:
    """Worst-case BYTES for one projected record, derived from the caps.

    THE SCALARS ARE BOUNDED AND THE LISTS ARE NOT, and that is the whole
    shape of the figure (v2.16, pc-7b6c). Every scalar a query emits is
    capped — id, type, status, title, owner, and the three dates — so their
    contribution is a constant. `edges`' list members are capped PER ELEMENT
    (`safe_id`) and in no way at all in CARDINALITY: a `blocks` edge with
    2,000 legal targets is a legal record, and nothing truncates it, because
    an edge id a consumer follows is not something to drop quietly the way a
    diagnostic's participant list can be. So the per-record ceiling is a
    constant plus a term the CALLER can bound by bounding its own edges, and
    `next --limit N` costs at most N times this — which is what "reasonable
    about" was always supposed to mean.

    `labels` is not here: the projection emits `labels_count`, an integer."""
    scalars = (ID_CAP + 2 * VOCAB_CAP + TITLE_CAP + OWNER_CAP + 3 * DATE_CAP)
    per_target = ID_CAP * JSON_WORST_BYTES_PER_CODE_POINT + 4  # quotes, comma
    return (scalars * JSON_WORST_BYTES_PER_CODE_POINT
            + PROJECTION_FRAME_BYTES + edge_targets * per_target)


def _projected_edges(rec: dict[str, Any]) -> dict[str, Any]:
    edges = rec.get("edges")
    if not isinstance(edges, dict):
        return {}
    out: dict[str, Any] = {key: [safe_id(t) for t in (edges.get(key) or [])
                                 if isinstance(t, str)]
                           for key in LIST_EDGES}
    for key in SCALAR_EDGES:
        value = edges.get(key)
        out[key] = safe_id(value) if isinstance(value, str) else None
    if edges.get("no_edges") is True:
        out["no_edges"] = True
    return out


FIELD_PROJECTORS = {
    "id": lambda r: safe_id(r.get("id")),
    "rev": lambda r: r.get("rev") if strict_int(r.get("rev")) else None,
    "type": lambda r: safe_text(r.get("type"), VOCAB_CAP),
    "status": lambda r: safe_text(r.get("status"), VOCAB_CAP),
    "title": lambda r: safe_text(r.get("title"), TITLE_CAP),
    "owner": lambda r: safe_text(r.get("owner"), OWNER_CAP),
    "priority": lambda r: r.get("priority") if strict_int(r.get("priority")) else None,
    "created": lambda r: safe_text(r.get("created"), DATE_CAP),
    "updated": lambda r: safe_text(r.get("updated"), DATE_CAP),
    "target": lambda r: None if r.get("target") is None else safe_text(r.get("target"), DATE_CAP),
    # Presence, never a toggle (spec v1.3 / v1.6): emitting "forced": false
    # would contradict the format's own semantics in the echo, so an absent
    # brand is an absent key. Projecting it as a bool broke
    # test_force_brand_is_not_inherited_by_later_revisions, correctly.
    "forced": lambda r: True if r.get("forced") is True else OMIT,
    "edges": _projected_edges,
    # Prose is served by a METRIC, never by its text. No digests: a digest
    # varies with content, which would force the differential test in
    # tests/test_pecia.py::ProseWithholding to carry an exception, and an
    # exception in that test is a hole in the only thing that has force here.
    "body_chars": lambda r: _chars(r.get("body")),
    "disposition_chars": lambda r: _chars(r.get("disposition")),
    "disposition_words": lambda r: _words(r.get("disposition")),
    "evidence_kind": evidence_kind,
    "labels_count": lambda r: len(r["labels"]) if isinstance(r.get("labels"), list) else 0,
}

# The declared per-command field lists: what each command answers. `show`
# answers "what does this record say", and is the one that returns it whole.
QUERY_FIELDS: dict[str, tuple[str, ...]] = {
    "next": ("id", "type", "title", "priority"),
    "ready": ("id", "type", "title", "priority"),
    "blocked": ("id", "title"),
    # `created` and `priority` too: graph is the one view over EVERY head,
    # closed ones included, so it is where a caller finds a record's age and
    # rank without reading the projection file.
    "graph": ("id", "type", "status", "title", "priority", "created"),
    "gantt": ("id", "title", "status", "created", "target"),
    "board": ("id", "type", "status", "title", "priority"),
    # add / edit / close. `edit` and `close` deep-copy the prior head, so they
    # echoed a body THIS caller never authored — an agent triaging an imported
    # Jira issue took the importing stranger's prose into its context. `add`
    # echoes only its own argv; it is projected for uniformity, because an
    # exception-free rule is what makes the differential test exception-free.
    "written": ("id", "rev", "type", "title", "status", "priority", "created",
                "updated", "owner", "edges", "forced", "body_chars",
                "disposition_chars", "disposition_words", "evidence_kind",
                "labels_count"),
}


def project(command: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Build a command's view of a record from its declared field list.

    Built up from the list rather than cut down from the record, so a view
    is exactly what its command answers. An unregistered command raises
    KeyError, which main() turns into a clean exit 2."""
    built = ((key, FIELD_PROJECTORS[key](rec)) for key in QUERY_FIELDS[command])
    return {key: value for key, value in built if value is not OMIT}


# -- audit findings: typed, with the note GENERATED, never passed ------------
#
# `project` default-denies RECORD fields; an audit finding is a different
# object, assembled from a free `note` string at a dozen call sites. An
# allow-list over record fields does nothing to stop a future call site
# interpolating a disposition into a note (challenge 1, finding 5). So the
# note is generated here from declared fields, and there is no parameter
# through which note text could be passed.

# The prose-only-linkage scan (v1.13). A disposition that names record ids in
# running text is the pre-retires convention this format is replacing: 12
# records across 4 groups in pecia's own ledger were closed off shared work
# with the linkage in prose and nothing computing it (measured, pc-4d19).
#
# DISCLOSURE RULE, and it is the reason this reads ids out of a WITHHELD
# field at all: a token is emitted only if it is a key of the ledger — i.e.
# a value `ready`, `blocked` and `graph` already emit — and everything else
# is COUNTED, never quoted. So the finding cannot become a channel for prose
# that merely happens to match `pc-\w+`; the alphabet ID_RE accepts is wide
# enough to spell an instruction, which v1.10 says explicitly.
PROSE_ID_RE = re.compile(r"\bpc-[A-Za-z0-9][A-Za-z0-9.-]*")


def ids_named_in(text: Any, known: set[str]) -> tuple[list[str], list[str]]:
    """(ledger ids named in the text, the distinct pc--shaped tokens that name
    no record), each sorted.

    Trailing `.` and `-` are stripped before the lookup: ID_RE admits both
    inside an id, so "retired by pc-4d19." matches one character too long and
    would otherwise resolve to nothing."""
    named: set[str] = set()
    unknown: set[str] = set()
    for raw in PROSE_ID_RE.findall(text if isinstance(text, str) else ""):
        token = raw.rstrip(".-")
        (named if token in known else unknown).add(token)
    return sorted(named), sorted(unknown)


AUDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "untriaged": (),
    "prose-only-linkage": ("ids", "unresolved"),  # unresolved: the tokens, listed (v3.3)
    "stale-in-progress": ("since",),
    "aging-unknown-evidence": (),
    "priority-rot": ("priority", "created"),
    "closed-while-blocked": ("blocker",),
    "inadequate-disposition": ("disposition_chars", "disposition_words", "min_chars",
                               "disposition"),
    "possible-duplicates": ("ids",),
    "forced-write": ("rev", "owner", "updated"),
    "historical-custody-violation": ("rev", "violation"),
    "unresolvable-reference": ("scheme", "target", "reason", "exit", "stderr"),
    # D9 (pc-5422, closing pc-cb50): board renders the audit surface but
    # never executes a configured resolver, so a foreign reference on the
    # board is marked, not tested.
    # `field` names which record field carries the reference (pc-5893,
    # round-6 lane E2-F2): one template served both audit-surface loops,
    # so a context-only reference was described as an evidence reference.
    "reference-not-attempted": ("field", "scheme"),
    # D12 (pc-0afd): the one summary line standing in for every grandfathered
    # prose-only-linkage finding. Emitted with id null — it is about an era,
    # not a record.
    "historical-prose-only-linkage": ("count", "cutoff"),
    # v2.5 (pc-813f, closing pc-ff8f): a terminal decision whose deciding
    # revision has a machine owner and no ratifying revision. Until a human
    # ratifies, an agent's recommendation reads like the user's own call.
    "unratified-decision": ("owner",),
    # v2.5 (pc-813f, closing pc-7ab3): a declared `context` reference whose
    # resolver says the referent is not there.
    "unresolvable-context": ("scheme", "target", "reason", "exit", "stderr"),
    # v1.14 (pc-2251): the truth half of E007's split. `check` certified the
    # command's shape off the ledger; this asks THIS machine whether it runs.
    # Advisory by construction — a command missing from this checkout is a
    # fact about the checkout, never grounds to redden a gate.
    # v3.3 (pc-ded91385e31a): the command is quoted again — withheld at
    # v2.8 (pc-80d7) under a containment rule since reversed. It is what the
    # reader has to fix.
    "unresolvable-evidence": ("evidence_kind", "reason", "evidence"),
    # pc-89a9. A disposition that tells the implementation what it MUST do is
    # an obligation, and nothing connected it to the code until this. Trap 3
    # shipped that way: the model pinned it, pc-0033's disposition said "must
    # route re-chaining through publish", the code did not, every gate stayed
    # green. GP23 treats DEFERRAL phrases as tracked red obligations; this is
    # the same move for SELF-ADDRESSED ones, and the lexicon is as greppable.
    "unverified-imperative": ("phrase", "reason"),
    "truth-audit-sample": ("rev", "disposition_chars", "disposition_words",
                           "evidence_kind", "disposition", "evidence"),
    # v2.1 (pc-5640): overdue is derived, never a stored status (rule 2).
    "overdue-milestone": ("target", "status"),
}

AUDIT_NOTES: dict[str, str] = {
    "unverified-imperative":
        "a terminal record's disposition says {phrase} about the implementation, and "
        "{reason}. Writing a fix down is not landing it — cite the test, or say why none",
    "unresolvable-evidence":
        "evidence command (kind {evidence_kind}) {reason}",
    "untriaged": "no edges and no no_edges declaration — frontier or rot?",
    "prose-only-linkage":
        "disposition names {ids} with no edge to them — the linkage is prose and "
        "nothing computes it. Which edge is the record's own question (`retires` "
        "if this work closed them, else discovered_from / supersedes / blocks). "
        "Further pc--shaped tokens naming no record: {unresolved}",
    "stale-in-progress": "in-progress but untouched since {since}",
    "aging-unknown-evidence": "defect with unknown evidence beyond rot horizon",
    "priority-rot": "p{priority} open since {created}",
    "closed-while-blocked": "terminal but {blocker} (non-terminal) still blocks it",
    "inadequate-disposition":
        "disposition is {disposition_chars} chars / {disposition_words} words "
        "(min {min_chars}) — names an outcome, not what happened and why "
        "(spec: 'Fixed' alone fails audit)",
    "possible-duplicates": "same title on: {ids} (duplicate_of edge?)",
    "forced-write": "rev {rev} written with --force by {owner} on {updated}",
    "historical-custody-violation": "rev {rev}: {violation} when written",
    "unresolvable-reference":
        "evidence reference {scheme}:{target} did not resolve — {reason}, exit {exit}",
    "reference-not-attempted":
        "{field} reference (scheme {scheme}) not attempted — board never "
        "executes resolvers; run `audit` to resolve it",
    "historical-prose-only-linkage":
        "{count} record(s) closed before the retires edge existed ({cutoff}, "
        "v1.13) name records in their dispositions with no edge — counted, "
        "never listed (D12); `audit --historical` lists them",
    "unratified-decision":
        "a terminal decision with machine owner {owner} and no ratifying "
        "revision — until a human ratifies it (edit <id> --ratify), an "
        "agent-recorded decision reads like the human's own call (v2.5)",
    "unresolvable-context":
        "context reference {scheme}:{target} did not resolve — {reason}, "
        "exit {exit}. The orientation document this record leans on is not "
        "where it says",
    "truth-audit-sample":
        "verify rev {rev}: disposition {disposition_chars} chars / "
        "{disposition_words} words, evidence {evidence_kind}",
    "overdue-milestone":
        "target {target} passed and status is still {status} (non-terminal) — "
        "overdue is computed, never a stored status (rule 2)",
}


def audit_finding(kind: str, rid: Any, **fields: Any) -> dict[str, Any]:
    """One audit finding. KeyError on an undeclared kind or field, and on a
    declared field the call site forgot — the table is the contract."""
    declared = AUDIT_FIELDS[kind]
    undeclared = sorted(set(fields) - set(declared))
    if undeclared:
        raise KeyError(f"audit finding {kind!r} declares no field(s) {undeclared}")
    out: dict[str, Any] = {"kind": kind,
                           "id": None if rid is None else safe_id(rid)}
    for key in declared:
        value = fields[key]
        out[key] = value if isinstance(value, (int, float)) else safe_text(
            value if isinstance(value, str) else render_value(value), AUDIT_VALUE_CAP)
    # Bounded like every other emitted string: a declared field may be an
    # integer, and a check-clean `rev` of 10**999 rendered a 1044-char note.
    out["note"] = safe_text(AUDIT_NOTES[kind].format(**{k: out[k] for k in declared}),
                            MESSAGE_CAP)
    return out


def timeline_checks(entries: list[dict[str, Any]],
                    check_snapshot: bool = True,
                    log_complete: bool = True) -> list[dict[str, Any]]:
    """E014 and E015 — the invariants that need the LOG, not just its records.

    E013 (chain break) is raised by read_log, because a broken chain makes
    every later line untrustworthy and reading must stop there.

    E014 is the compare-and-swap, re-verified from the log rather than trusted
    because append_record enforced it: an entry's rev must be exactly one more
    than its record's rev at that point in the timeline, and its `touched` set
    must equal a recomputation of the diff against that point. The second half
    is what makes `touched` structurally derived rather than merely documented
    as such — spec/format-v2.md 4.1, and the reason GP8/BP4's fabrication
    result does not apply to it.

    E015 distinguishes a STALE snapshot from a FORKED one. Stale is clean and
    expected: the snapshot is branch-scoped and a branch legitimately lags. A
    recorded head that is nowhere in the chain means something wrote to the
    projection as if it were authority, which is the one thing rule 5 forbids.

    `log_complete` is FALSE when the caller's log read stopped at a parse or
    chain error (v2.16, pc-1667). E015's whole question is whether the
    recorded head is IN THE CHAIN, and a trusted prefix is not the chain — so
    against a partial read every witness naming a later head looks forked,
    and `check` accused a witness and projection it had written itself and
    never touched. The comparison does not run there; what is emitted instead
    says the question could not be answered, and does not prescribe `pecia
    snapshot`, which refuses on the same error."""
    findings: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for e in entries:
        rec = e["rec"]
        rid = rec.get("id")
        before = seen.get(rid) if isinstance(rid, str) else None
        prev_rev = None if before is None else before.get("rev")
        if before is None:
            expected_rev: int | None = 1
        elif strict_int(prev_rev):
            expected_rev = prev_rev + 1
        else:
            # A malformed HISTORICAL rev is a finding, never a crash (v2.7,
            # pc-55e1): `before.get("rev") + 1` on a string died to the
            # top-level handler as fatal E000, suppressing the E001/E014
            # findings that were provably available one entry earlier. The
            # checker is total (format-v2.md rule: report, never crash);
            # contiguity past malformed history is unverifiable and SAYS so.
            expected_rev = None
            findings.append(finding("error", "E014", rid,
                f"seq {e.get('seq')}: rev {capped_value(rec.get('rev'))} follows a "
                f"revision whose own rev {capped_value(prev_rev)} is not an integer "
                f"(its E001 is reported separately) — contiguity cannot be "
                f"verified past malformed history"))
        # Type-strict: a boolean `true` is not revision 1 (pc-d89f's class,
        # at the one site it had not reached).
        if expected_rev is not None and not (strict_int(rec.get("rev"))
                                             and rec.get("rev") == expected_rev):
            findings.append(finding("error", "E014", rid,
                f"seq {e.get('seq')}: rev {capped_value(rec.get('rev'))} does not follow "
                f"{'no head' if before is None else before.get('rev')} "
                f"(expected {expected_rev}) — the CAS could not have admitted this"))
        expected_touched = diff_fields(before, rec)
        declared = e.get("touched")
        if not isinstance(declared, list) or not all(isinstance(f, str)
                                                     for f in declared):
            # A malformed envelope is a FINDING, never a crash (pc-2675,
            # v2.6): `touched: true` used to reach sorted() and die to the
            # top-level handler as fatal E000 — fail-closed, but the
            # finding-shaped diagnostic was lost and the contract for a
            # malformed set was unstated. Widened at v2.7 (pc-3ef3): the
            # element check joins the envelope check — a MIXED-TYPE array
            # (["priority", 1]) slipped the shape guard and died in
            # sorted() the same way.
            findings.append(finding("error", "E014", rid,
                f"seq {e.get('seq')}: touched {capped_array(declared)} is not a list of "
                f"field-name strings — the derived field-set is a JSON array "
                f"of field names (format-v2.md 4.1)"))
        else:
            # No legacy form. Entries written before edge subfields became
            # separate conflict units (pc-4924) declared a coarse `edges`; v2.6
            # grandfathered eight of them by entry hash, which coupled this
            # code to the byte format. They were rewritten to the derived
            # fine-grained form (pc-c6a9), so every declaration is compared
            # against the derivation with no exemption.
            if declared != expected_touched:
                # pc-ebd2 (round-4 lanes A-F3/A'-F1): §4.1 defines CONFLICT
                # by set intersection and the comparison here is an ordered
                # array — v2.9 declares the canonical form (lexicographic,
                # what diff_fields has always emitted) rather than loosening
                # to sets, and the refusal for a right-set-wrong-order
                # declaration now SAYS that is what it is refusing.
                if sorted(declared) == expected_touched:
                    # pc-6b00 (round-11 lane A-F2, v2.16): the array was
                    # printed whole and the assembled message capped from the
                    # end, so on a schema-permitted large extension set the
                    # array consumed the message and the promise that this
                    # finding NAMES THE ORDER as what it refuses did not hold.
                    findings.append(finding("error", "E014", rid,
                        f"seq {e.get('seq')}: touched {capped_array(declared)} carries the "
                        f"correct field-set in non-canonical order — the "
                        f"derived array is serialized sorted (format-v2.md "
                        f"4.1, v2.9); reorder to {capped_array(expected_touched)}"))
                else:
                    findings.append(finding("error", "E014", rid,
                        f"seq {e.get('seq')}: touched {capped_array(declared)} does not match the "
                        f"recomputed diff {capped_array(expected_touched)} — `touched` is derived by the "
                        f"tool and never accepted from a writer (format-v2.md 4.1)"))
        if isinstance(rid, str):
            seen[rid] = rec

    # E015 compares the ON-DISK snapshot against the ON-DISK log, so it is
    # meaningless for a candidate timeline that has not been written yet —
    # the recorded head is necessarily absent from a chain built in memory.
    # Checking it anyway made `sync` refuse EVERY re-chain, including the
    # legitimate disjoint-field case, and silently drop the winner's edit.
    # Caught by the paired control, not by the kill: the kill fired correctly
    # and a gate that fires on everything is not a gate (VP4(b)).
    head_file = snapshot_head_path()
    snap = snapshot_path()
    if check_snapshot and not log_complete:
        # A PARTIAL READ IS NOT A TIMELINE (v2.16, pc-1667). Every branch
        # below decides by locating the recorded head in `entries`, and
        # `entries` here is the trusted PREFIX the log read stopped at — so
        # the one state E015 exists to distinguish from staleness was
        # asserted against an intact witness on the strength of an
        # incomplete chain. The verdict stays nonzero on the log's own
        # finding; this says what is and is not known about the projection.
        if head_file.exists() or (snap.exists() and snap.read_bytes().strip()):
            findings.append(finding("warning", "E015", None,
                f"{store_file_display(snap)} was NOT compared with the "
                f"timeline: the log stops at an error before its end (that "
                f"finding names the line), so the entries read are a trusted "
                f"prefix rather than the chain, and a witness naming a later "
                f"head cannot be told from a forked one. The projection and "
                f"its witness are untouched and unjudged — repair the log "
                f"and check again. `pecia snapshot` is NOT the remedy here: "
                f"it reads the same log and refuses on the same error"))
        return findings
    # E019 (v3.3, pc-25cca4980c47): the log's own end, against the mark every
    # write moves — over a log that read whole, which a partial read has
    # returned above. The local store's log only: a remote timeline being
    # validated (check_snapshot=False) answers to no mark here.
    if check_snapshot:
        gone = mark_violation(entries)
        if gone:
            findings.append(finding("error", "E019", None, f"{gone}. {MARK_REMEDY}"))
    # THE PROJECTION IS DECODED BEFORE THE WITNESS DISPATCH (pc-0d11,
    # round-6 lanes A-F2/A'-F3). The v2.10 non-text diagnosis (pc-23a1)
    # lived inside the valid-witness branch, so both compound states dodged
    # it: a FORKED witness left `upto` None and work.jsonl was never
    # decoded, and a MISSING witness read the bytes without decoding,
    # describing non-text garbage as "content". The verdict stayed red with
    # the right remedy on both paths; the promised naming of the file and
    # the malformation was absent. Decoding once, up front, makes the
    # diagnosis independent of the witness's own state.
    snap_bytes: bytes | None = None
    snap_text: str | None = None
    if check_snapshot and snap.exists():
        # A MALFORMED SNAPSHOT IS A FINDING, NEVER A CRASH (pc-23a1,
        # round-5 lane A-F2, v2.10): a strict read_text() here reached the
        # top-level handler as fatal E000 where equivalent valid-text
        # corruption is a structured E015. The pc-0a2f crash class, one
        # surface over.
        snap_bytes = snap.read_bytes()
        try:
            snap_text = snap_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            findings.append(finding("error", "E015", None,
                f"{store_file_display(snap)} is not valid UTF-8 ({exc}) — "
                f"the snapshot was corrupted or replaced with non-text "
                f"bytes and cannot match the timeline at any head. "
                f"Regenerate it with `pecia snapshot`; never edit it"))
    if check_snapshot and head_file.exists():
        # The witness half of pc-23a1's rule, same shape as the projection
        # decode above.
        try:
            recorded = head_file.read_bytes().decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            findings.append(finding("error", "E015", None,
                f"{store_file_display(head_file)} is not valid UTF-8 "
                f"({exc}) — the witness was corrupted or replaced with "
                f"non-text bytes, so neither staleness nor a fork can be "
                f"told apart. Regenerate both with `pecia snapshot`; never "
                f"edit them"))
            return findings
        upto: int | None = None
        if recorded:
            chain = set()
            for i in range(len(entries)):
                chain.add(entry_hash(entries[i]))
            if recorded not in chain:
                findings.append(finding("error", "E015", None,
                    f"{store_file_display(head_file)} names a head that is "
                    f"not in this timeline — the snapshot is FORKED, not "
                    f"merely stale (a stale snapshot's head is an ancestor "
                    f"and is clean). Regenerate it with `pecia snapshot`"))
            else:
                upto = next(i for i in range(len(entries))
                            if entry_hash(entries[i]) == recorded) + 1
        else:
            # A BLANK HEAD IS THE EMPTY PREFIX, NOT AN OFF SWITCH (pc-ed3e,
            # v2.6). Every content test used to sit inside `if recorded:`,
            # so blanking one generated file made arbitrary forged snapshot
            # content certify clean. A blank head is what write_snapshot
            # emits for an empty timeline (fresh `init`), so it stays legal —
            # but it RECORDS something (derivation from the empty prefix) and
            # the content check below now holds the snapshot to it.
            upto = 0
        if upto is not None:
            # THE HEAD IS NOT THE SNAPSHOT (pc-84f3). Checking only the
            # recorded head left the bytes unasserted, so a snapshot whose
            # content was edited — or replaced wholesale — passed as long
            # as the head file still named a real ancestor. The demotion of
            # the snapshot to a projection is what makes rule 5 safe,
            # and a projection that can silently disagree with its source
            # is the second source of truth the design forbids. GP28's
            # state axis: regenerate and diff.
            want = "".join(canonical(e["rec"]) + "\n" for e in entries[:upto])
            if snap_text is not None and snap_text != want:
                findings.append(finding("error", "E015", None,
                    f"{store_file_display(snap)}'s content does not match "
                    f"the timeline at the head it records "
                    f"({len(entries[:upto])} entries) — it was edited or "
                    f"replaced. Regenerate it with `pecia snapshot`; never "
                    f"edit it"))
    elif check_snapshot and snap_bytes is not None and snap_bytes.strip():
        # A SNAPSHOT WITHOUT ITS WITNESS (pc-905e, v2.6). Deleting
        # the head file was silently accepted, and the head file is
        # the one retained commitment that lets the checker see a truncated
        # timeline at all: a suffix-truncated log is an internally valid
        # chain, so E013 structurally cannot fire on it (format-v2.md §8,
        # narrowed at v2.6). A projection carrying content with no recorded
        # head is refused rather than trusted; an EMPTY projection with no
        # head binds nothing and stays clean.
        findings.append(finding("error", "E015", None,
            f"{store_file_display(snap)} carries content but "
            f"{store_file_display(head_file)} is missing — the witness "
            f"binding the projection to the timeline is gone, so neither "
            f"staleness nor a fork (nor a truncated log) can be told "
            f"apart. Regenerate both with `pecia snapshot`"))
    return findings


def run_checks(records: list[dict[str, Any]], parse_findings: list[dict[str, Any]],
               cfg: dict[str, Any],
               record_lines: list[int] | None = None) -> list[dict[str, Any]]:
    """`record_lines`, when given, is the per-record source line aligned
    with `records` (pc-b60a, v2.12) — the identity of last resort for a
    record whose `rev` cannot identify it. Callers that read lines pass
    them; a caller with no line notion omits them and the tags fall back
    to the pre-v2.12 forms."""
    findings = list(parse_findings)
    statuses, types = allowed_statuses(cfg), allowed_types(cfg)

    # E001 — the full Record contract, every revision line (v1.4: typed, not
    # just present — review-3 F10)
    for i, rec in enumerate(records):
        line = record_lines[i] if record_lines is not None and i < len(record_lines) else None
        findings.extend(validate_record(rec, statuses, types, line=line))

    # v1.5 (review-4 F19): every phase below operates on structurally sound
    # records only — unsound ones already carry their E001 findings, and a
    # checker that crashes on garbage is not a checker.
    sound = [rec for rec in records if record_is_sound(rec)]
    groups = group_revisions(sound)

    # E002 — RE-FOUNDED at v2. Under v1 a duplicated (id, rev) was a union
    # artifact of two branches' ledgers, so identical duplicates were a warning
    # and divergent ones were E010's error. One timeline admits appends only by
    # compare-and-swap, so neither can arise from a write path: any duplicate
    # means the LOG IS CORRUPT — spliced, concatenated, or hand-edited. Both
    # cases are therefore one error, and E002 keeps a live denominator (VP4: a
    # truncated or doubled log is a real condition) where E010 no longer has
    # one and is deleted rather than left permanently green.
    #
    # GROUPED OVER EVERY WELL-TYPED (id, rev), not only sound records (v2.7,
    # pc-b5bb). The v1.5 sound-only phase rule exists so a checker never
    # crashes on garbage — and this grouping indexes on exactly two keys, so
    # it needs no more soundness than their types: a duplicate pair where one
    # copy is E001-unsound (a rev-1 pair that lost its title) now draws the
    # E002 the v2.6 amendment's "anywhere in the timeline" always claimed,
    # beside that copy's own E001. No false-clean was reachable before (an
    # unsound duplicate always carried E001), but the corruption signal was
    # silently narrower than its spec.
    dup_groups: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for rec in records:
        if (isinstance(rec, dict) and isinstance(rec.get("id"), str)
                and strict_int(rec.get("rev"))):
            dup_groups.setdefault(rec["id"], {}).setdefault(rec["rev"], []).append(rec)
    for rid, revs in sorted(dup_groups.items()):
        for rev, recs in sorted(revs.items()):
            if len(recs) > 1:
                same = len({canonical(r) for r in recs}) == 1
                findings.append(finding("error", "E002", rid,
                    f"rev {rev} appears {len(recs)}x in the timeline "
                    f"({'identical' if same else 'divergent'} content) — a log the CAS "
                    f"admitted cannot contain this, so the log is corrupt; see E013"))

    # E008 — revision contiguity from the minimum present rev (gap = error).
    # GROUPED OVER EVERY WELL-TYPED (id, rev), like E002 above (pc-8291,
    # round-6 lane A'-F2; the widening E002 got at v2.7, pc-b5bb, for the
    # same reason). This grouping indexes on exactly two keys, so it needs
    # no more soundness than their types — grouping over `sound` made a
    # rev-3 copy that lost its title vanish from the contiguity scan, and
    # the [2] gap went unreported: the verdict stayed red only through the
    # copy's own E001, with the gap's own signal silently absent.
    for rid, revs in sorted(dup_groups.items()):
        present = sorted(revs)
        # A missing span is one diagnostic participant. Expanding every
        # integer before capping the message made two sparse records consume
        # time and memory proportional to their numeric distance (pc-693cc656803b).
        gaps: list[int | str] = []
        for before, after in zip(present, present[1:]):
            if after > before + 1:
                first, last = before + 1, after - 1
                gaps.append(first if first == last else f"{first}..{last}")
        if gaps:
            findings.append(finding("error", "E008", rid,
                f"revision gap(s): {capped_array(gaps)}"))

    # E005 — legal status transitions across consecutive revisions
    for rid, revs in sorted(groups.items()):
        ordered = [revs[r][0] for r in sorted(revs)]
        for prev, nxt in zip(ordered, ordered[1:]):
            ps, ns = prev.get("status"), nxt.get("status")
            if ps in allowed_statuses(cfg) and ns in allowed_statuses(cfg) and not legal_transition(ps, ns):
                findings.append(finding("error", "E005", rid,
                    f"illegal transition {safe_text(ps, MESSAGE_ITEM_CAP)} -> "
                    f"{safe_text(ns, MESSAGE_ITEM_CAP)} at rev {nxt.get('rev')} "
                    f"(terminal is final; reopen = new record with discovered_from)"))

    heads, _ = resolve_heads(sound)
    known = set(groups) | planned_ids(cfg)

    # E003 — edge targets must exist (checked on heads; history may reference compacted ids)
    for rid, head in sorted(heads.items()):
        edges = head.get("edges") or {}
        targets = [t for k in LIST_EDGES for t in (edges.get(k) or [])]
        # `is not None`, not truthiness (v2.7, pc-94d2): the empty string is
        # falsy, so a truthiness scan skipped exactly the one string value
        # that can never resolve. E001 now refuses it as malformed too; this
        # keeps the dangling scan total rather than relying on that.
        targets += [edges.get(k) for k in SCALAR_EDGES
                    if edges.get(k) is not None]
        for target in targets:
            if target not in known:
                findings.append(finding("error", "E003", rid,
                    f"edge target {capped_value(target)} does not exist and is "
                    f"not declared planned"))

    # E004 — the blocks∪parent subgraph must be acyclic (on heads). Each
    # finding carries its cycle's whole cyclic component as its group
    # (pc-c69d): the message names any member the witness cycle misses, and
    # the write gate reads the component, so a write that grows no cyclic
    # component introduces no new E004 — however the witness moves.
    adjacency = head_adjacency(heads)
    components = cyclic_components(adjacency)
    for cycle in cycles_in(adjacency):
        component = components.get(cycle[0], frozenset(cycle))
        extra = sorted(component - set(cycle))
        findings.append(finding("error", "E004", cycle[0],
            f"cycle in the blocks/parent/retires graph: "
            f"{capped_seq(cycle, sep=' -> ')}"
            + (f"; the same cyclic component also holds "
               f"{capped_seq(extra, sep=', ')}" if extra else ""),
            group=sorted(component), subject=None))

    # E017 — no self-edges, on any edge field OF A HEAD (v2.5, defect
    # pc-40b4; scope declared at v2.9, pc-c96a). v1.3 banned self-edges
    # outright; v1.4's check-diff rebuild silently narrowed the ban to the
    # trivial cycle E004 can see (blocks∪parent∪retires). A custody
    # self-edge — a record discovered from itself, caused by itself,
    # superseding itself — is meaningless, and neither E003 (the target
    # exists: itself) nor E004 (wrong subgraph) refuses it. Demonstrated
    # 2026-08-07: `edit pc-a --caused-by pc-a` was accepted and check exited
    # 0. HEADS ONLY, like every state check (the E006/E007 principle): a
    # rev-1 self-edge cleared at rev 2 is a repaired head and legitimately
    # clean — this comment used to say "on ANY edge field" beside the
    # heads-only loop below, and the amendment carried no scope line.
    for rid, head in sorted(heads.items()):
        edges = head.get("edges") or {}
        selfish = sorted(k for k in LIST_EDGES if rid in (edges.get(k) or []))
        selfish += [k for k in SCALAR_EDGES if edges.get(k) == rid]
        for key in selfish:
            findings.append(finding("error", "E017", rid,
                f"self-edge: edges.{key} names the record itself — no edge "
                f"relation is reflexive (v1.3's ban, restored at v2.5)"))

    # E006 — terminal status requires a disposition (on heads)
    for rid, head in sorted(heads.items()):
        if head.get("status") in TERMINAL_STATUSES:
            disp = head.get("disposition")
            if not isinstance(disp, str) or not disp.strip():
                findings.append(finding("error", "E006", rid, "terminal status without disposition"))

    # E007 — a done defect requires executable-shaped evidence or a reference
    for rid, head in sorted(heads.items()):
        if head.get("type") == "defect" and head.get("status") == "done":
            if not evidence_is_structural(head.get("evidence"), cfg):
                findings.append(finding("error", "E007", rid,
                    "done defect requires executable-shaped evidence or a <scheme>:<id> "
                    "reference (prose is not evidence). A path-shaped command head passes "
                    "on shape; a bare one must be declared in .pecia/config.yaml as "
                    "`extra_evidence_commands: [<name>]`"))

    # E007 (amended v2.5, defect pc-0a66) — the ARGUMENTS get the portability
    # scan the head already had. E007 certified the first token alone, so a
    # command whose arguments name absolute machine-local paths passed as
    # clean while being unrunnable on any other host. A WARNING, not an
    # error: the scan is a heuristic over strings (hermetic — nothing reads
    # the host, per v1.14), and the standing corpus carries such records as
    # custody that cannot be edited, so a warning is visible everywhere and
    # blocks nothing.
    def _local_shaped(token: str) -> bool:
        # v2.8 (pc-9c85): the terminal `..` segment joins the matcher —
        # `./..` and `dir/..` escape toward the parent exactly as `../x`
        # does, and the whole-token scan recognized every spelling but the
        # trailing one. Still the declared heuristic with the stated v2.6
        # exclusions (quoted concatenation, environment indirection).
        return (token.startswith(("/", "~")) or token == ".."
                or token.startswith("../") or "/../" in token
                or token.endswith("/.."))

    for rid, head in sorted(heads.items()):
        evidence = head.get("evidence")
        if evidence_command_head(evidence, cfg) is None:
            continue
        # v2.6 (pc-1871): the value half of an =-joined token is scanned too
        # — `--root=/Users/alice/private` hid the path from the whole-token
        # scan. Still a declared heuristic over strings, still a WARNING:
        # quoted concatenations and environment indirection remain out of
        # scope, stated at the amendment rather than discovered later.
        # The finding names each argument and its position (v3.3,
        # pc-ded91385e31a, reversing pc-2706's withholding): the argument is
        # what the reader has to change.
        machine_local = [(i, a) for i, a in enumerate(shlex.split(evidence)[1:],
                                                      start=1)
                         if _local_shaped(a)
                         or ("=" in a and _local_shaped(a.split("=", 1)[1]))]
        if machine_local:
            findings.append(finding("warning", "E007", rid,
                f"machine-local evidence argument(s) "
                f"{capped_seq([f'{i}: {a}' for i, a in machine_local])} — an "
                f"absolute or parent-escaping path resolves only on the host "
                f"that wrote it, so the command cannot run anywhere else "
                f"(v2.5, pc-0a66)"))

    # E011 — a foreign reference must name a scheme this repo declares (v1.7,
    # pc-4d1e). Declaration is shape and is checked here; resolution is truth
    # and is checked by `audit`.
    #
    # A STATE CHECK ON HEADS (v2.17, pc-95d0, round-12 lane A-F1). This loop
    # has always iterated `heads`, and the comment here read "an undeclared
    # scheme is never a silent pass" — which is false of a SUPERSEDED
    # revision, and so was the canonical bullet, which carried no scope
    # clause at all while five sibling codes (E003, E006, E007, E012, E017)
    # declare their heads-only scope in their own normative text. Measured:
    # rev 1 carrying `jira:ABC-1` is E011; rev 2 clearing the field is clean,
    # and indistinguishable from a ledger that never carried it.
    #
    # The LOOP is right and the SENTENCE was wrong, which is the repair
    # taken. A repaired head is legitimately clean throughout this checker by
    # design, and E011 alone being total would be the one place the five
    # split four/one. What E011 asks is whether THIS repo can resolve what
    # its CURRENT records point at; a scheme a superseded revision named is
    # not something anything will resolve.
    declared = resolvers(cfg)
    for rid, head in sorted(heads.items()):
        ref = parse_foreign_ref(head.get("evidence"))
        if ref and ref[0] not in declared:
            findings.append(finding("error", "E011", rid,
                f"evidence references undeclared scheme {capped_value(ref[0])} — declare it in "
                f".pecia/config.yaml as `resolvers: [{safe_text(ref[0], MESSAGE_ITEM_CAP)}=<verifier command>]`"))
        # E011 amended at v2.5 (pc-7ab3): the `context` field references
        # through the same registry, so the same declaration rule holds.
        ref = parse_foreign_ref(head.get("context"))
        if ref and ref[0] not in declared:
            findings.append(finding("error", "E011", rid,
                f"context references undeclared scheme {capped_value(ref[0])} — declare it in "
                f".pecia/config.yaml as `resolvers: [{safe_text(ref[0], MESSAGE_ITEM_CAP)}=<verifier command>]`"))

    # E012 — a retirement promise that expired unkept (v1.13, decision pc-4d19).
    #
    # ANCHORED ON THE TARGET, not on each retirer, and that is the whole
    # design. The obvious formulation — "Y may not be terminal while anything
    # in Y.retires is non-terminal" — is wrong for the case where one item is
    # resolved by SEVERAL pieces of work: Y1, Y2 and Y3 each name X, Y1 lands
    # first, and the obvious rule reddens Y1 for doing its share. Reading it
    # from X instead ("X is still open and nobody is left who said they would
    # close it") is silent while any retirer is live and fires exactly when
    # the last one goes terminal. In the one-retirer case the two are
    # identical, so nothing is given up.
    #
    # Terminality is TERMINAL_STATUSES throughout, as everywhere else in this
    # file — a dropped retirer counts as gone, and the finding then says the
    # promise died with it. A `done`-only reading would be the one place in
    # the checker where the five statuses split three/two, with nothing under
    # it but this E-code's convenience.
    #
    # A target that does not exist is E003's finding, not this one; reporting
    # both would double-count the chorusmith case.
    for target, claimants in sorted(retirers_of(heads).items()):
        head = heads.get(target)
        if head is None or head.get("status") in TERMINAL_STATUSES:
            continue
        if any(heads[c].get("status") not in TERMINAL_STATUSES for c in claimants):
            continue
        findings.append(finding("error", "E012", target,
            f"non-terminal, but every record claiming to retire it is terminal "
            f"({capped_seq(claimants)}) — close it, or drop the retires claim "
            f"that no longer holds",
            group=list(claimants), subject=target))

    # E016 — a record went terminal while an open record blocks it (v2.4,
    # defect pc-0aa2).
    #
    # ANCHORED ON THE BLOCKED RECORD, the opposite of E012's anchor, and the
    # reason for the difference does not transfer. E012 reads from the target
    # because several records may each promise to retire one item and reading
    # from the claimants would redden the first to do its share. `blocks` has
    # no fan-in problem — one open blocker is sufficient on its own — so the
    # finding belongs on the record that closed.
    #
    # `blocks` ALONE, deliberately. compute_blockers also counts an open child
    # as blocking its parent milestone and counts `retires`; closing a
    # milestone over an open child is a descope rather than a defect, and
    # retires is E012's. Widening this to the full reverse index would make
    # two legitimate acts into errors to catch a third.
    #
    # This is why the hole existed at all: a `blocks` edge lives on the
    # BLOCKER, so the blocked record's own edges are empty and `close` shows
    # nothing holding it. Since v1.4 the write gate IS the checker, so minting
    # the code closes it at close, at edit and at check at once.
    for rid, head in sorted(heads.items()):
        if head.get("status") not in TERMINAL_STATUSES:
            continue
        holders = sorted(
            other for other, oh in heads.items()
            if oh.get("status") not in TERMINAL_STATUSES
            and rid in ((oh.get("edges") or {}).get("blocks") or []))
        if holders:
            # THE ALTERNATIVE THIS PRINTS IS ONE THAT RUNS (pc-54c7). It used
            # to offer "or reopen this record", which E005 refuses outright —
            # terminal is final — so neither branch of the advice was
            # available unforced. Closing the blocker is the other repair
            # that actually clears the finding.
            # pc-1130 (round-11 lane A-F3, v2.16): the joined list was capped
            # with the message, so at 1,000 blockers the finding named 817 of
            # them AND lost the remedy that tells the reader what to do with
            # the names. The list is bounded at composition now and says how
            # many it did not name; the gate's own participant set is `group`
            # and is untouched.
            findings.append(finding("error", "E016", rid,
                f"terminal, but {capped_seq(holders)} still block(s) it — drop the "
                f"`blocks` claim that no longer holds, or close the blocker(s); "
                f"this record cannot be reopened (E005: terminal is final)",
                group=holders, subject=rid))

    # E009 — one active head per decision supersession lineage
    decisions = {rid: h for rid, h in heads.items() if h.get("type") == "decision"}
    parent_of: dict[str, str] = {rid: rid for rid in decisions}
    def find(x: str) -> str:
        while parent_of[x] != x:
            parent_of[x] = parent_of[parent_of[x]]
            x = parent_of[x]
        return x
    def union(a: str, b: str) -> None:
        parent_of[find(a)] = find(b)
    for rid, head in decisions.items():
        target = (head.get("edges") or {}).get("supersedes")
        if target in decisions:
            union(rid, target)
    lineages: dict[str, list[str]] = {}
    for rid in decisions:
        lineages.setdefault(find(rid), []).append(rid)
    for members in lineages.values():
        active = sorted(m for m in members
                        if decisions[m].get("status") not in {"superseded", "dropped"})
        if len(active) > 1:
            # The anchor here is a REPRESENTATIVE, not a subject: it is the
            # lowest active head and it moves when that head is superseded,
            # which is why the narrowing rule is told `subject=None` (pc-54c7).
            findings.append(finding("error", "E009", active[0],
                f"decision lineage has {len(active)} active heads: "
                f"{capped_seq(active)}",
                group=active))

    return findings


# -- the write gate (trap 1 disposition; spec v1.3) --------------------------
# Structural validation BEFORE appending. --force bypasses this gate — and
# only this gate: it brands the revision ("forced": true), the checker stays
# force-blind, and audit enumerates every forced revision. Formal contract:
# spec/pecia.als T9 (BlameContainment) + T10 (ForcedStepsCarryTheMark).

def narrows_a_prior_finding(key: str, prior: list[dict[str, Any]]) -> bool:
    """Is this after-finding a SMALLER version of one the ledger already has?

    pc-54c7 (round-9 lane A-F1): a finding's identity in the write gate is its
    whole serialized text, so dropping one of two blockers rewrote E016's
    message and the repair read as a newly introduced error — refused at exit
    1 quoting the narrowed finding, while the same edit where there was only
    ONE blocker landed at exit 0. E012's two-retirer case behaved identically,
    and E009's three-head case moved its anchor as well, so the finding
    differed in `id` too. Ordinary recovery of a multi-participant finding
    needed `--force`, which brands a repair as an escape-hatch use.

    pc-b60a gave a finding revision and line identity; what was unhandled is
    identity across a CHANGED PARTICIPANT SET. The rule: an after-finding is
    not new when the ledger already carries a finding of the same code, about
    the same subject, over a STRICTLY LARGER set of participants. A write that
    leaves a smaller version of an existing finding standing has improved the
    ledger, and the gate exists to refuse the writes that worsen it.

    The subject is what keeps this from admitting a genuinely new finding: a
    second record going terminal under one of the same blockers is a different
    subject and stays refused, even though its participants are a subset."""
    mine = GROUPED_PARTICIPANTS.get(key)
    if mine is None:
        return False
    code = json.loads(key)["code"]
    for g in prior:
        if g["code"] != code:
            continue
        theirs = GROUPED_PARTICIPANTS.get(json.dumps(g, sort_keys=True))
        if theirs is None or theirs["subject"] != mine["subject"]:
            continue
        if mine["group"] < theirs["group"]:
            return True
        # A CYCLE HAS NO SUBJECT, AND ITS COMPONENT IS WHAT IT DAMAGES
        # (pc-c69d). E004's group is the cyclic component, and removing an
        # edge can only keep a component or shrink it, while the witness
        # cycle one DFS reports can move either way. So for E004 the SAME
        # component is not new either: only a write that grows a cyclic
        # component, or makes one, adds a cycle the ledger did not have.
        if code == "E004" and mine["group"] <= theirs["group"]:
            return True
    return False


def write_gate(records: list[dict[str, Any]], candidate: dict[str, Any],
               prior_head: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A write is refused iff it introduces NEW error findings.

    v1.4 (review-3 F12): the gate IS the checker — we run the full E-code
    check on the ledger with and without the candidate and refuse on the
    difference. The gate and the checker cannot drift, because they are the
    same function; every current and future E-code is automatically a write
    gate. (`prior_head` is unused since the diff subsumes transition checks;
    kept in the signature for call-site clarity.)"""
    cfg = load_config()
    # Lines are passed on BOTH sides of the diff (pc-b60a, v2.12): the
    # historical findings carry identical line tags in `before` and
    # `after`, so nothing spurious appears — and a candidate whose rev
    # cannot identify it (absent, malformed) gets the one line number
    # history cannot already hold, so REPEATING that malformation is a
    # new finding and is refused rather than colliding with history.
    line_ns = list(range(1, len(records) + 1))
    prior = [f for f in run_checks(records, [], cfg, record_lines=line_ns)
             if f["severity"] == "error"]
    before = {json.dumps(f, sort_keys=True) for f in prior}
    after = run_checks(records + [candidate], [], cfg,
                       record_lines=line_ns + [len(records) + 1])
    refusals = []
    for f in after:
        if f["severity"] != "error":
            continue
        key = json.dumps(f, sort_keys=True)
        if key in before or narrows_a_prior_finding(key, prior):
            continue
        refusals.append(finding("error", f["code"], f["id"],
                                refusal_message(f["message"])))
    return refusals


def gate_or_brand(args: argparse.Namespace, records: list[dict[str, Any]],
                  candidate: dict[str, Any], prior_head: dict[str, Any] | None) -> int | None:
    """Returns an exit code to abort with, or None to proceed (candidate
    possibly branded)."""
    if getattr(args, "force", False):
        candidate["forced"] = True
        return None
    refusals = write_gate(records, candidate, prior_head)
    if refusals:
        for r in refusals:
            emit(r)
        return 1
    return None


# -- output: for people by default, JSON on --json (v3.5) --------------------
#
# THE CLI IS READ BY PEOPLE AND THE MCP SERVER BY MODELS (v3.5,
# pc-f590f6ef556a). Every command computes the same values either way; this is
# only how they are printed. With --json each value is one JSON document per
# line, exactly as before v3.5, which is what scripts and hooks read. Without
# it the values are rendered for a person: a finding is one line, a record
# reads as a record, a queue as a table. board and gantt were always views and
# stay so. Each rendered string is bounded by safe_text like any JSON value,
# except `show`, which prints a record's text as written with only its control
# characters removed, so a body keeps its lines and its own fences.

#: How this invocation prints. main() sets it from the parsed arguments; an
#: in-process caller that never runs main() gets the JSON form.
OUTPUT: dict[str, Any] = {"json": True, "command": None, "history": False}

ECHO_VERBS = {"add": "added", "edit": "edited", "close": "closed"}
FINDING_KEYS = {"code", "id", "message", "severity"}


def emit(obj: Any) -> None:
    out_line(json.dumps(obj, sort_keys=True) if OUTPUT["json"] else human(obj))


def out_line(text: str) -> None:
    """One item on stdout. A reader that closed the pipe (`pecia graph |
    head`) ends the writing quietly, and the command still finishes and exits
    with its own code, as the port does; stdout then goes to the null device
    so the interpreter's final flush cannot raise either."""
    if OUTPUT.get("closed"):
        return
    # Lossy-but-total on encoding (board_print's rule, now everyone's): a
    # legacy-encoding terminal gets `?` for an em-dash, never a traceback.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(encoding, "replace").decode(encoding, "replace")
    try:
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        OUTPUT["closed"] = True
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def cannot_run(message: str) -> int:
    # main()'s catch-all routes exception text through here, and an exception
    # can carry record content — so stderr is bounded too, not just stdout.
    if OUTPUT["json"]:
        print(json.dumps({"severity": "fatal", "code": "E000", "id": None,
                          "message": safe_text(message, MESSAGE_CAP)},
                         sort_keys=True), file=sys.stderr)
    else:
        print(f"pecia: {safe_text(message, MESSAGE_CAP)}", file=sys.stderr)
    return 2


def replay_json(out: str, err: str) -> None:
    """Print a nested command's captured JSON output in this run's form."""
    if OUTPUT["json"]:
        sys.stdout.write(out)
        sys.stderr.write(err)
        return
    for line in out.splitlines():
        emit(json.loads(line))
    for line in err.splitlines():
        cannot_run(json.loads(line).get("message", ""))


def human(value: Any) -> str:
    """One emitted value, rendered for a person (v3.5)."""
    command = OUTPUT["command"]
    if isinstance(value, dict) and set(value) == FINDING_KEYS:
        return human_finding(value)
    if command in ECHO_VERBS:
        if isinstance(value, list):
            return "\n".join(human(v) for v in value)
        if isinstance(value, dict) and "rev" in value and "title" in value:
            verb = "unchanged" if value.get("unchanged") is True else ECHO_VERBS[command]
            return human_echo(verb, value)
    if command in ("ready", "next") and isinstance(value, list):
        return human_queue(value)
    if command == "blocked" and isinstance(value, list):
        return human_blocked(value)
    if command == "show":
        if isinstance(value, list):
            return human_history(value)
        if isinstance(value, dict):
            return "\n".join(human_record(value))
    if command == "graph" and isinstance(value, dict) and "nodes" in value:
        return human_graph(value)
    if command == "audit" and isinstance(value, dict) and "findings" in value:
        return human_audit(value)
    return "\n".join(human_fields(value))


def plain(value: Any) -> str:
    """A value on one line of a rendered view: bounded like any JSON string."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(plain(v) for v in value)
    if isinstance(value, dict):
        return safe_text(json.dumps(value, sort_keys=True))
    return safe_text(value)


def record_lines(text: str) -> list[str]:
    """A record's text as its lines, for `show`: control and format
    characters removed, other whitespace a space, nothing else touched."""
    out = []
    for line in text.split("\n"):
        kept = []
        for ch in line:
            if ch.isspace():
                kept.append(" ")
            elif unicodedata.category(ch) not in ("Cc", "Cf", "Cs"):
                kept.append(ch)
        out.append("".join(kept))
    return out


def label(key: str) -> str:
    return key.replace("_", " ").replace(".", " ")


def aligned(pairs: list[tuple[str, list[str]]]) -> list[str]:
    """`label: value` lines with the values in one column; a value of
    several lines continues indented beneath its label."""
    width = max((len(k) for k, _ in pairs), default=0) + 1
    out = []
    for key, lines in pairs:
        if len(lines) == 1:
            out.append(f"{(key + ':').ljust(width)} {lines[0]}")
        else:
            out.append(f"{key}:")
            out.extend(f"  {line}" for line in lines)
    return out


def flatten(value: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    for key in sorted(value):
        v = value[key]
        if isinstance(v, dict) and v:
            pairs.extend(flatten(v, f"{prefix}{key}."))
        else:
            pairs.append((f"{prefix}{key}", v))
    return pairs


def human_fields(value: Any) -> list[str]:
    """Any other value: an object as aligned fields, a list one per line."""
    if isinstance(value, dict):
        return aligned([(label(k), [plain(v)]) for k, v in flatten(value)])
    if isinstance(value, list):
        return [line for v in value for line in human_fields(v)] or ["none"]
    return [plain(value)]


def human_finding(f: dict[str, Any]) -> str:
    where = f" {plain(f['id'])}" if f["id"] is not None else ""
    return f"{plain(f['severity'])} {plain(f['code'])}{where}: {plain(f['message'])}"


def human_echo(verb: str, r: dict[str, Any]) -> str:
    return (f"{verb} {plain(r.get('id'))} rev {plain(r.get('rev'))} · {plain(r.get('type'))} "
            f"· p{plain(r.get('priority'))} · {plain(r.get('status'))} · {plain(r.get('title'))}")


def human_queue(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "nothing ready"
    ids = [plain(r.get("id")) for r in rows]
    types = [plain(r.get("type")) for r in rows]
    iw, tw = max(len(i) for i in ids), max(len(t) for t in types)
    return "\n".join(f"{i.ljust(iw)}  p{plain(r.get('priority'))}  {t.ljust(tw)}  {plain(r.get('title'))}"
                     for i, t, r in zip(ids, types, rows))


def human_blocked(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "nothing blocked"
    out = []
    for r in rows:
        out.append(f"{plain(r.get('id'))}  {plain(r.get('title'))}")
        if r.get("blockers"):
            out.append(f"  blocked by {plain(r['blockers'])}")
        if r.get("awaiting_answers"):
            out.append(f"  awaiting answers from {plain(r['awaiting_answers'])}")
    return "\n".join(out)


def shown(value: Any) -> list[str]:
    """A stored field's value for `show`, as lines."""
    if isinstance(value, str):
        return record_lines(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [", ".join(" ".join(record_lines(v)) for v in value)]
    if isinstance(value, (list, dict)):
        return record_lines(json.dumps(value, sort_keys=True))
    return [plain(value)]


def empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


HEADED = ("id", "type", "priority", "status", "rev", "title", "body")


def human_record(r: dict[str, Any]) -> list[str]:
    """A record as a person reads it: who and what it is, its title, its
    other fields aligned (empty ones left out), then its body as written."""
    one = lambda k: " ".join(shown(r[k]))  # noqa: E731
    head = [one("id")] if "id" in r else []
    head += [one("type")] if "type" in r else []
    head += [f"p{one('priority')}"] if "priority" in r else []
    head += [one("status")] if "status" in r else []
    head += [f"rev {one('rev')}"] if "rev" in r else []
    out = [" · ".join(head), one("title") if "title" in r else ""]
    pairs: list[tuple[str, list[str]]] = []
    for key in sorted(k for k in r if k not in HEADED):
        value = r[key]
        if key == "edges" and isinstance(value, dict):
            for kind in sorted(value):
                if not empty(value[kind]):
                    pairs.append((label(kind), shown(value[kind])))
        elif not empty(value):
            pairs.append((label(key), shown(value)))
    if pairs:
        out.append("")
        out.extend(aligned(pairs))
    if not empty(r.get("body")):
        out.append("")
        out.extend(shown(r["body"]))
    return out


def at_path(rec: dict[str, Any], path: str) -> Any:
    value: Any = rec
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def human_history(entries: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for n, e in enumerate(entries):
        rec = e.get("rec") or {}
        touched = [plain(t) for t in e.get("touched") or []]
        what = ("changed " + ", ".join(touched) if touched
                else "created" if n == 0 else "changed nothing")
        if n:
            out.append("")
        out.append(f"seq {plain(e.get('seq'))} · rev {plain(rec.get('rev'))} · {what}")
        if n == 0:
            out.extend(f"  {line}" if line else "" for line in human_record(rec))
        else:
            out.extend(f"  {line}" for line in aligned(
                [(label(t), ["none"] if empty(at_path(rec, t)) else shown(at_path(rec, t)))
                 for t in touched]))
    return "\n".join(out)


def human_graph(g: dict[str, Any]) -> str:
    titles = {n.get("id"): plain(n.get("title")) for n in g.get("nodes", [])}
    edges = g.get("edges", [])
    out = [f"{len(g.get('nodes', []))} records, {len(edges)} edges"]
    by_source: dict[Any, list[dict[str, Any]]] = {}
    for e in edges:
        by_source.setdefault(e.get("from"), []).append(e)
    for source, outgoing in by_source.items():
        out.append("")
        out.append(f"{plain(source)}  {titles.get(source, '')}".rstrip())
        for e in outgoing:
            out.append(f"  {plain(e.get('kind'))} → {plain(e.get('to'))}  "
                       f"{titles.get(e.get('to'), '')}".rstrip())
    return "\n".join(out)


def human_audit(a: dict[str, Any]) -> str:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for f in a.get("findings", []):
        groups.setdefault(f.get("kind"), []).append(f)
    out: list[str] = []
    for kind, items in groups.items():
        out.append(f"{plain(kind)} ({len(items)})")
        for f in items:
            out.append(f"  {plain(f.get('id'))}: {plain(f.get('note'))}")
            rest = [(label(k), [plain(v)]) for k, v in sorted(f.items())
                    if k not in ("id", "kind", "note")]
            out.extend(f"      {line}" for line in aligned(rest))
        out.append("")
    if not groups:
        out.extend(["no audit findings", ""])
    out.extend(human_fields({k: v for k, v in a.items() if k != "findings"}))
    return "\n".join(out)


# -- commands ----------------------------------------------------------------


def require_heads() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]] | int:
    records, parse_findings, _ = load_raw()
    for f in parse_findings:
        if f["code"] == "E000":
            return cannot_run(f["message"])
        if f["code"] == "E013":
            return cannot_run(f"timeline chain is broken — {f['message']} (run `pecia check`)")
    if any(f["code"] == "E001" for f in parse_findings):
        return cannot_run("ledger has unparseable lines — run `pecia check`")
    if any(not record_is_sound(r) for r in records):
        return cannot_run("ledger has malformed records (E001) — run `pecia check`")
    for rid, revs in group_revisions(records).items():
        for rev, lines in revs.items():
            if len(lines) > 1:
                return cannot_run(
                    f"timeline is corrupt — {rid} rev {rev} appears {len(lines)}x, which "
                    f"the compare-and-swap cannot admit (E002). Run `pecia check`; do not "
                    f"schedule against it")
    heads, _ = resolve_heads(records)
    return heads, records


def cmd_init(args: argparse.Namespace) -> int:
    # v2: init creates THE TIMELINE, not a working-tree ledger. The snapshot is
    # generated from it (empty here) and is a projection from the first moment,
    # so no repo ever starts out treating .pecia/work.jsonl as authority.
    target = log_path()
    if target is None:
        return cannot_run("not inside a git repository — run `git init` first "
                          "(the timeline lives under --git-common-dir)")
    # REPEAT INIT DOES NOT LAUNDER EVIDENCE (pc-0ff7, v2.7). init regenerated
    # the snapshot unconditionally from whatever the log currently held, so a
    # detected E015 — the designed witness of a suffix-truncated log, the one
    # loss E013 structurally cannot see — became a clean one-entry state after
    # an ordinary, documented, re-runnable command, and in an unpublished
    # store the removed revision then had no remaining witness anywhere. On an
    # EXISTING timeline, init now refuses when the log cannot be read cleanly
    # or when the snapshot disagrees with the chain (E015): the disagreement
    # is evidence, and `pecia check` names it; `pecia snapshot` is the
    # deliberate remedy and itself refuses only the truncation signature.
    if target.exists():
        entries, log_findings = read_log(target)
        witness = list(log_findings)
        witness += [f for f in timeline_checks(
            entries, log_complete=not any(f["severity"] == "error"
                                          for f in log_findings))
            if f["code"] in ("E015", "E019")]
        witness = [f for f in witness if f["severity"] == "error"]
        if witness:
            for f in witness:
                emit(f)
            emit({"initialized": False,
                  "note": "a timeline already exists here and its state does "
                          "not read back cleanly — repeat init will not "
                          "regenerate the snapshot over it, because the "
                          "disagreement is evidence (pc-0ff7). Investigate "
                          "with `pecia check`; regenerate deliberately with "
                          "`pecia snapshot`"})
            return 1
    elif mark_violation([]):
        # No log at all, and a mark saying this store wrote one (v3.3): the log
        # was deleted, and an empty timeline here would bury that.
        return cannot_run(f"no timeline here, but {mark_violation([])} (E019). {MARK_REMEDY}")
    elif snapshot_path().exists() and snapshot_path().read_bytes().strip():
        # The sibling of the same shape (pc-0ff7's neighbourhood): no log at
        # all beside a snapshot CARRYING RECORDS is the ordinary fresh-clone
        # state — the snapshot is tracked, the log is never fetched (D009) —
        # and init would bury those records under an empty timeline.
        # Named via store_file_display, the pc-f61a rule: under a pinned
        # store this message used to say `.pecia/work.jsonl` while reading
        # the pinned file.
        return cannot_run(
            f"no timeline here, but {store_file_display(snapshot_path())} "
            f"carries records — the "
            f"fresh-clone shape (a plain clone never fetches the log). init "
            f"would erase the projection's content under an empty timeline. "
            f"Run `pecia sync` to hydrate the published timeline, or `pecia "
            f"migrate` to rebuild one from this repo's history")
    PECIA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_bytes((
            "# pecia config — flat keys only. Core vocabulary may be extended, not redefined.\n"
            "# extra_statuses: []\n# extra_types: []\n# planned: []\n"
            "stale_days: 7\nrot_days: 14\n").encode("utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("")
    entries, _ = read_log(target)
    write_snapshot(entries)
    # `merge=union` is NOT written any more, and an existing declaration is
    # removed: there is nothing to merge. It was the mechanism by which two
    # branches' ledgers reconciled, which is precisely the alternative-timeline
    # machinery rule 5 removes. VP4's destructive half — the attribute goes in
    # the same change that makes the merge unrepresentable, not later.
    #
    # TOKENIZED, matching the D006 detector (pc-9663, round-5 lane C-F2,
    # v2.10). The removal used to match only the exact line
    # `.pecia/work.jsonl merge=union` while doctor's detector parses
    # attributes — so `.pecia/work.jsonl text merge=union` was detected,
    # "fixed" by init's exit 0, and still active afterward. Remover and
    # detector now share one parse (merge_union_declared): the merge=union
    # token is stripped from any line declaring it on the snapshot pattern;
    # a line left with no other attributes goes entirely, taking its
    # explaining comment block (pc-80f4); a line keeping other attributes
    # is rewritten without the token, its comments untouched.
    gitattributes = ROOT / ".gitattributes"
    if gitattributes.exists():
        # Attribute patterns and tokens are ASCII. Keep every other byte in
        # the adopter's file while removing an old merge=union declaration.
        kept: list[bytes] = []
        changed = False
        source_lines = git_attribute_lines(gitattributes.read_bytes())
        dropped_final_line = False
        for index, ln in enumerate(source_lines):
            bare = ln.strip(b" \t\r")
            if bare and not bare.startswith(b"#"):
                pattern, *attributes = re.split(rb"[ \t\r]+", bare)
                if (pattern in (b".pecia/work.jsonl", b"work.jsonl")
                        and b"merge=union" in attributes):
                    changed = True
                    remaining = [a for a in attributes if a != b"merge=union"]
                    if remaining:
                        ending = b"\r" if ln.endswith(b"\r") else b""
                        kept.append(b" ".join([pattern, *remaining]) + ending)
                        continue
                    # pc-80f4: stripping the declaration and keeping the
                    # comment block above it left half a fix — the
                    # contiguous comment lines directly above go with it,
                    # plus at most one blank separator; a comment block
                    # about something else is fenced off by its own blank.
                    while kept and kept[-1].lstrip(b" \t\r").startswith(b"#"):
                        kept.pop()
                    if kept and not kept[-1].strip(b" \t\r"):
                        kept.pop()
                    dropped_final_line = index == len(source_lines) - 1
                    continue
            kept.append(ln)
        if changed:
            if dropped_final_line and kept:
                kept.append(b"")
            gitattributes.write_bytes(b"\n".join(kept))
    # The write lock is machine-local runtime state, never content. Unignored
    # it shows up as untracked worktree noise in every adopting repo forever —
    # and pecia's own hook reports worktree activity, so the noise trains the
    # reader to skim exactly the report that catches swept files. Self-hosting
    # hid this: pecia's .gitignore was hand-written at repo birth, so `init`
    # was never the thing that had to get it right (found dogfooding, M3).
    gitignore = ROOT / ".gitignore"
    ignore = ".pecia/.lock"
    existing = gitignore.read_bytes() if gitignore.exists() else b""
    # THE APPENDER ASKS THE DETECTOR'S QUESTION (pc-4c7d, same commit as
    # the detector): the exact-line test skipped the append when the line
    # was present but NEGATED below (`!.pecia/.lock`), so init exited 0
    # with the lock still unignored and --fix had nothing it could credit.
    # Appending at the end wins git's last-match rule, so re-running init
    # is again the remedy D005 names.
    if not lock_is_ignored():
        with gitignore.open("ab") as fh:
            if existing and not existing.endswith(b"\n"):
                fh.write(b"\n")
            fh.write(f"{ignore}\n".encode("ascii"))
    # THE REPORT NAMES THE INITIALIZED STORE (v2.8, pc-a296): under
    # PECIA_LOG_DIR the log and snapshot land in the pinned store, and this
    # line said `<repo>/.pecia` for every store — two different stores
    # returned the identical value, naming the repository configuration
    # directory rather than what was initialized. snapshot_dir() IS the
    # store: the pinned directory when set, .pecia/ otherwise.
    emit({"initialized": str(snapshot_dir()), "log": str(target),
          "rule": RULE1})
    return 0


# -- doctor: is the enforcement ACTIVE? ---------------------------------------
# Rule 1's sibling. `check` asks whether the ledger is well-formed; `doctor`
# asks whether the gates that are supposed to read it are actually installed.
# Both stop short of truth: doctor reports that a gate RUNS, never that it is
# right.
#
# The gap is real and was measured. A repo can ship dev/hooks/pre-commit,
# document it as enforcement, and have it fire in no clone on earth —
# core.hooksPath is per-clone local config that is never committed, so nothing
# inside a repository can assert its own hooks run. Three sibling repos shipped
# hooks that had never once executed at commit time. Git's refusal to
# auto-enable them is deliberate (a clone must not execute repo-controlled
# code), so this cannot be fixed by cleverness — only by making absence loud
# and the fix one command.

HOOK_DIRS = ["dev/hooks", ".githooks", "githooks"]


def git_out(*args: str) -> str | None:
    """Run git; return stripped stdout, or None if git failed or is absent."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(ROOT),
                              capture_output=True, check=False)
    except (FileNotFoundError, OSError):
        return None
    return os.fsdecode(proc.stdout).strip() if proc.returncode == 0 else None


def git_attribute_lines(data: bytes) -> list[bytes]:
    """Split at LF only; retain bare CR, CRLF endings, VT and FF bytes."""
    return data.split(b"\n")


def merge_union_declared(attrs: Path) -> bool:
    """The D006 CONDITION, shared by doctor's detector and --fix's credit
    check (pc-9663): git effectively applies merge=union to the snapshot.

    ASK GIT, NEVER LINE TOKENS (pc-dc10, round-6 lane E1-F3; the pc-9663
    rule extended from credit to detection). The tokenized parse matched
    only the two literal snapshot patterns, so `.pecia/** merge=union`
    activated the forbidden driver per git's own attribute resolution while
    D006 stayed silent — over the exact condition it exists for. The
    detector now asks `git check-attr`, whose answer composes every
    pattern, path, and attributes file the way a real merge would.

    init's REMOVER deliberately keeps its tokenized parse over the two
    literal snapshot patterns: stripping a wildcard declaration would edit
    an adopter's intent for OTHER files, and the pc-9663 credit-by-detector
    accounting already reports honestly when the remover cannot clear what
    this detector sees (D006 stands, named in `refused`).

    `attrs` is the tokenized fallback's input, used only when git itself is
    unavailable — the degraded posture answer, never the primary one."""
    out = git_out("check-attr", "merge", "--", ".pecia/work.jsonl")
    if out is not None:
        return out.rsplit(":", 1)[-1].strip() == "union"
    if not attrs.exists():
        return False
    for line in git_attribute_lines(attrs.read_bytes()):
        line = line.strip(b" \t\r")
        if not line or line.startswith(b"#"):
            continue
        pattern, *attributes = re.split(rb"[ \t\r]+", line)
        if pattern in (b".pecia/work.jsonl", b"work.jsonl") \
                and b"merge=union" in attributes:
            return True
    return False


def lock_is_ignored() -> bool:
    """The D005 condition, shared by doctor's detector, init's appender,
    and --fix's credit check (pc-9663's class: a fix is credited when the
    CONDITION cleared).

    ASK GIT, NEVER LINE TOKENS (pc-4c7d, round-6 lane E1-F2, same commit
    as the D006 sibling). The exact-line test read `.pecia/.lock` present
    and answered ignored while a later `!.pecia/.lock` negation made git
    report the lock NOT ignored — D005 silent over the exact condition it
    exists for. `git check-ignore` composes every pattern, negation, and
    exclude file the way git's own status does. Exit 0 is ignored, 1 is
    not; a git failure (128, or no git at all) falls back to the exact-line
    test — the degraded posture answer, never the primary one."""
    try:
        proc = subprocess.run(["git", "check-ignore", "-q", "--",
                               ".pecia/.lock"], cwd=str(ROOT),
                              capture_output=True, check=False)
    except (FileNotFoundError, OSError):
        proc = None
    if proc is not None and proc.returncode in (0, 1):
        return proc.returncode == 0
    gitignore = ROOT / ".gitignore"
    return gitignore.exists() and any(
        line.removesuffix(b"\r") == b".pecia/.lock"
        for line in gitignore.read_bytes().split(b"\n"))


def path_is_inside(candidate: Path, root: Path) -> bool:
    """True iff candidate resolves to root or something under it.

    Resolved on both sides: a worktree reached through a symlinked parent
    (/tmp on macOS) is otherwise reported as foreign, which would turn the
    D004/D008 split into a property of how the path was spelled.
    """
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def sh_path(value: Any) -> str:
    """A path as a shell would have to be given it, inside a prescribed command.

    SIBLING OF pc-c59a, SAME COMMIT. The hook template printed a recovery
    command built by concatenation, so a checker path containing a space was
    printed as a command that split at the space. Every remedy `doctor`
    prescribes interpolates a path the same way — `chmod +r <checker>`,
    `git config core.hooksPath <dir>` — and had the same break. A remedy that
    cannot be retyped is not a remedy (claim 24, the v2.13 pc-7ab5 contract);
    shlex.quote leaves an ordinary path untouched, so only the paths that
    needed it change."""
    return shlex.quote(str(value))


#: What `env -S` treats as an escape, and what it stands for. GNU's set;
#: `\_` for a space is the one with no shell analogue (pc-fc1e).
_ENV_S_ESCAPES = {"\\": "\\", "_": " ", "t": "\t", "n": "\n", "r": "\r",
                  "f": "\f", "v": "\v", "$": "$", "#": "#", '"': '"',
                  "'": "'"}


def env_split_string(text: str) -> tuple[list[str], bool]:
    """Split an `env -S` string the way env does — (tokens, computed).

    pc-fc1e (round-10 lane E1-F2): `#!` lines were split on whitespace and
    the first non-flag token taken verbatim, but under `-S` env does its own
    splitting, in which QUOTES ARE SYNTAX. A checker whose shebang reads
    `#!/usr/bin/env -S 'sh'` runs `sh` while the reader looked for a program
    literally named `'sh'`, found none, and reported that every ordinary
    commit dies — while every ordinary commit succeeded. That is the false
    red the v2.14 amendment names as the hazard it was built to avoid,
    arriving through quoting instead of through absoluteness, so teaching
    the reader to strip quotes and stop would leave the class where it was:
    escapes and `${VAR}` are the same syntax, read by the same env.

    `computed` is True when a `${VAR}` in the string could not be resolved
    from this process's environment. The name is then decided at run time
    and doctor says so rather than accusing a name it made up: doctor's
    environment stands in for the hook's, which is what the operator can
    set, and an unresolvable one is a posture it cannot read — not a posture
    it knows to be bad.

    SCOPE, stated because it is a subset: quote grouping (both kinds),
    backslash escapes, `${VAR}` substitution, and a `#` comment that starts
    a token. env's own `-C`/`-u`/assignment handling inside the string is
    the caller's, since it is the same handling the unsplit form needs.
    """
    tokens: list[str] = []
    current: list[str] = []
    started = False
    computed = False
    quote: str | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is None and ch in " \t\n\r\f\v":
            if started:
                tokens.append("".join(current))
                current, started = [], False
            i += 1
            continue
        if quote is None and ch == "#" and not started:
            break                       # a comment runs to the end
        started = True
        if ch == "\\" and i + 1 < n and (quote != "'" or text[i + 1] in "\\'"):
            tokens_escape = _ENV_S_ESCAPES.get(text[i + 1])
            current.append(tokens_escape if tokens_escape is not None
                           else text[i + 1])
            i += 2
            continue
        if quote is None and ch in "\"'":
            quote = ch
            i += 1
            continue
        if quote is not None and ch == quote:
            quote = None
            i += 1
            continue
        if ch == "$" and text.startswith("${", i) and quote != "'":
            end = text.find("}", i + 2)
            if end != -1:
                name = text[i + 2:end]
                value = os.environ.get(name)
                if value is None:
                    computed = True
                    current.append(f"${{{name}}}")
                else:
                    current.append(value)
                i = end + 1
                continue
        current.append(ch)
        i += 1
    if started:
        tokens.append("".join(current))
    return tokens, computed


def which_from_root(name: str, root: Path) -> str | None:
    """`shutil.which`, with RELATIVE PATH entries resolved against the
    work-tree root (v2.16, pc-2745) — the seventh level of the doctor
    detector class, and the second of the resolution-disagreement shape
    after pc-cb43.

    git runs a hook from the work-tree root, so a relative entry in the PATH
    the hook inherits — `badpath`, `./tools`, an empty entry, which POSIX
    reads as the current directory — names a directory under THAT root.
    `shutil.which` resolves those against this PROCESS's cwd, so doctor
    invoked from a subdirectory looked somewhere git will not look: one
    environment string, two resolutions, D012 silent and the next ordinary
    commit dead in the hook.

    pc-cb43 made the checker VALUE single; this makes the BASE single, which
    is what that fix did not reach. Every relative path D012 reasons about
    now resolves against one base — `top` — the way git will."""
    if os.sep in name or (os.altsep and os.altsep in name):
        # shutil.which checks a name with a directory component directly,
        # against the cwd. Same disagreement, so the same base.
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = root / candidate
        return (str(candidate) if candidate.is_file()
                and os.access(candidate, os.X_OK) else None)
    entries = os.environ.get("PATH", os.defpath).split(os.pathsep)
    rebased = os.pathsep.join(
        entry if entry and os.path.isabs(entry)
        else str(root / entry) if entry else str(root)
        for entry in entries)
    return shutil.which(name, path=rebased)


def shell_name_kind(name: str) -> str | None:
    """What a NON-INTERACTIVE bash would call `name` — "function", "alias",
    "builtin", "keyword", "file", or None when it is nothing or cannot be
    asked. ASKED, NOT MODELLED (pc-7acd, round-12 lane E1-F2, v2.17).

    The eighth level of the doctor detector class was not about which value
    doctor resolved or which base it resolved against: it was about doctor
    REIMPLEMENTING the hook's resolution in a different language. The hook
    asked a shell builtin whose answer includes functions and aliases;
    `shutil.which` models a PATH that has none of those in it, and the two
    disagreed for as long as one was a simulation of the other. v2.17 closes
    that at the source — the shipped hook now asks `type -P`, a PATH search
    for an executable file, which is exactly what `which_from_root` models.

    What is left is a hook an adopter copied BEFORE that change, which
    doctor must not certify on a model it knows is lossy there. So for that
    one question doctor stops modelling and asks: one fixed command, in the
    same shell and the same non-interactive mode git runs the hook in, never
    anything read out of the adopter's hook file. A diagnostic that executed
    the text it is diagnosing would be a worse posture than the gap it
    closes."""
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        proc = subprocess.run([bash, "-c", 'type -t -- "$1"', "bash", name],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def shebang_requirements(path: Path) -> list[tuple[str, str]]:
    """What a `#!` line needs, as [(kind, name)] — `("path", "uv")` for a
    program the kernel's interpreter looks up on PATH, `("file", "/bin/sh")`
    for an absolute interpreter it execs directly, and `("computed", spec)`
    for a name the line builds at run time out of a variable this process
    cannot read (pc-fc1e).

    pc-a2da: D012 hand-listed ONE program name (`python3`) for ONE checker
    kind, so the two environment failures the round demonstrated were both
    invisible — an extensionless checker whose own shebang reads
    `#!/usr/bin/env -S uv run --script` with `uv` absent, and the hook's own
    `#!/usr/bin/env bash` with `bash` absent. This is the derivation the
    detector needs instead of the list: it reads the interpreter chain out
    of the file that will actually be executed.

    The two kinds are NOT the same question. `#!/bin/sh` is an absolute path
    the kernel execs — PATH says nothing about it — while `/usr/bin/env foo`
    is a PATH lookup performed by env, which is why `env: bash: No such file
    or directory` is the error the record measured. Asking "is `sh` on PATH"
    of an absolute shebang would be a false red of exactly the kind pc-709d
    warns about."""
    try:
        with path.open("rb") as fh:
            first = fh.readline(4096).decode("utf-8", "replace")
    except OSError:
        return []
    if not first.startswith("#!"):
        return []
    raw = first[2:].strip()
    spans = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", raw)]
    if not spans:
        return []
    interpreter = spans[0][0]
    needs: list[tuple[str, str]] = []
    if interpreter.startswith("/"):
        needs.append(("file", interpreter))
    else:
        # A bare name in a shebang is not portable, but if it is written the
        # kernel still needs it to resolve somewhere; report it as a name.
        needs.append(("path", interpreter))
    if Path(interpreter).name != "env":
        return needs

    # WHOSE SYNTAX IS IT (pc-fc1e). Outside `-S`, the argument env receives
    # is the literal text of the line: a checker whose shebang really says
    # `env 'sh'` really does ask for a program named `'sh'`, and reporting
    # that is right. Under `-S`, the whole remainder is ONE argument that
    # env itself splits, and there quotes, backslash escapes and `${VAR}`
    # are syntax. So the split-string is parsed by env's rules and the
    # unsplit form is left exactly as it was — the discrimination is the
    # fix, not quote-stripping everywhere.
    computed = False
    args = [tok for tok, _, _ in spans[1:]]
    for token, start, end in spans[1:]:
        if token in ("-S", "--split-string"):
            args, computed = env_split_string(raw[end:])
            break
        if token.startswith("--split-string="):
            args, computed = env_split_string(
                raw[start + len("--split-string="):])
            break
        if token.startswith("-S") and len(token) > 2:   # -S joined
            args, computed = env_split_string(raw[start + 2:])
            break

    # env's own argument is the program it looks up on PATH: skip its flags
    # (`-i`, `-u NAME`) and any NAME=VALUE assignments before it.
    skip_value = False
    for token in args:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            if token in ("-u", "--unset", "-C", "--chdir"):
                skip_value = True
            continue
        if "=" in token and not token.startswith("="):
            continue
        if computed and "${" in token:
            needs.append(("computed", token))
            return needs
        needs.append(("path", token))
        return needs
    return needs


def _is_openable(path: Path) -> bool:
    """Can this process actually open the file for reading? (pc-acaf)

    Doctor's capability detectors ask the question the guarded RUNTIME asks,
    and for a file a runtime reads (`python3 checker.py`, an interpreter
    opening a `#!` script) that question is an open(), not a mode bit.
    os.access() answers for the real uid and ignores ACLs, so it can disagree
    with the very call whose outcome doctor is reporting; the open cannot.
    Only PermissionError is an answer — anything else (a race that unlinked
    the file, an I/O error) is not a permission verdict, so it is not reported
    as one."""
    try:
        with path.open("rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        return True


#: The D-code namespace, DECLARED here so it has exactly one authored home
#: (pc-e9cf). D-codes are implementation diagnostics — `doctor` reports
#: posture, and rule 1 already says posture is never correctness — so they are
#: NOT part of the record-format contract and no adapter or sibling tool may
#: depend on a D-code's meaning. Internal tests may and do bind them:
#: `DoctorPosture` asserts the cross-tree case reports D008 and NOT D004, so
#: the narrower claim ("nothing depends on them") would be false.
#:
#: `dev/vocab-check.py` reads this table and the `report(...)` call sites and
#: refuses a disagreement in either direction, which is what stops a tenth
#: D-code being minted twice the way E012 was.
D_CODES: dict[str, str] = {
    "D001": "repo ships a pre-commit hook but core.hooksPath is unset — the gate has never run in this clone",
    "D002": "core.hooksPath is set but no pre-commit hook is there — commits run unguarded",
    "D003": "the pre-commit hook is not executable — git skips it silently, which reads like a passing gate; also warns when it is executable but unreadable, where an interpreted hook dies at every commit and doctor cannot tell",
    "D004": "a second pre-commit hook exists outside the configured hooks path — one of them is dead code",
    "D005": ".pecia/.lock is not git-ignored — permanent untracked noise trains readers to ignore noise",
    "D006": "the snapshot still declares merge=union, a v1 attribute the v2 projection never merges",
    "D007": "the ledger is not tracked by git — an uncommitted ledger is not custody",
    "D008": "core.hooksPath resolves outside this working tree — the hook that runs is another checkout's copy",
    "D009": "no local timeline and a remote is configured — the fresh-clone state, recoverable with sync",
    "D010": "a ledger exists and NO write gate is active in this clone — every other D-code assumes a gate to report on",
    "D011": "the config is not tracked by git — commit gates validate the staged ledger against the STAGED config, so the live and committed checks disagree",
    "D012": "the active hook resolves no pecia checker it can RUN, in the environment it will run in (relative $PECIA_CLI from the repository root; a .py checker openable, a non-.py checker executable; and every program name the interpreter chain needs \u2014 the hook's own shebang, and `python3` or the checker's shebang \u2014 resolving, derived from those files rather than listed and read with the lexical rules of whatever reads them, so an `env -S` string's quotes, escapes and `${VAR}` are env's syntax and a name computed from a variable this environment does not carry is reported undecided rather than missing). Every relative path this code reasons about resolves against the work-tree root, the way git will \u2014 the PATH's own relative entries included (v2.16) \u2014 so the answer does not depend on the directory doctor was invoked from. The PATH resolution it models is the one the shipped hook performs (`type -P`: an executable file, never a shell function, alias or builtin \u2014 v2.17); where a hook carries the pre-v2.17 `command -v` resolver instead, doctor ASKS the shell whether such a name exists and reports D012 when that divergence is live, rather than certifying on a model it cannot apply there. The gate fails closed.",
}


def init_refusal_account(out: str, err: str) -> str:
    """Where a refused init put its refusal, read from what it emitted
    (pc-c810): a fatal on stderr is named with its reason, and findings plus
    the `initialized: false` note on stdout are named as such."""
    def objects(text: str) -> list[dict]:
        found = []
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                found.append(obj)
        return found
    fatal = next((o for o in objects(err) if o.get("severity") == "fatal"), None)
    if fatal is not None:
        return ("its reason is the fatal on stderr, printed above: "
                + safe_text(fatal.get("message", ""), MESSAGE_VALUE_CAP))
    if any("initialized" in o for o in objects(out)):
        return ("its findings and its `initialized: false` note are on "
                "stdout, printed above")
    return "it printed no reason on either stream"


def cmd_doctor(args: argparse.Namespace) -> int:
    if git_out("rev-parse", "--is-inside-work-tree") != "true":
        return cannot_run("not inside a git work tree — doctor reports the "
                          "enforcement posture of a git repository")
    # Hooks are a repository-level fact: resolve them against the work-tree
    # root, never the cwd, or running from any subdirectory silently checks
    # paths that cannot exist and reports a confident wrong answer.
    top_out = git_out("rev-parse", "--show-toplevel")
    top = Path(top_out) if top_out else ROOT
    findings: list[dict[str, Any]] = []
    fixes: list[str] = []

    def report(severity: str, code: str, message: str, fix: str | None = None) -> None:
        # The prescribed fix is the last thing on the line and a D-code's
        # message carries host paths, so the variable half is budgeted here
        # too (v2.16) — v2.13's rule that a refusal's named remedy is part of
        # its contract is not satisfied by a remedy the cap can eat.
        tail = f" Fix: {safe_text(fix, MESSAGE_VALUE_CAP)}" if fix else ""
        body = safe_text(message, max(0, MESSAGE_CAP - len(tail)))
        findings.append(finding(severity, code, None, body + tail))

    configured = git_out("config", "core.hooksPath")
    # is_file(), not exists() (v2.7, pc-aff4): a DIRECTORY at the hook path
    # satisfies exists() and os.access(X_OK), so doctor reported an active
    # gate while git died at "fatal: cannot exec" on every commit — a
    # structurally unexecutable gate reading as a clean posture, from the
    # command whose one contract is reporting whether gates are ACTIVE.
    shipped = [d for d in HOOK_DIRS if (top / d / "pre-commit").is_file()]
    active_dir: Path | None = None
    if configured:
        active_dir = (top / configured) if not Path(configured).is_absolute() else Path(configured)

    # D010 — THE ADOPTER'S CASE, and the one this command was blind to.
    # Everything below reports on a gate that exists: D001 needs a shipped
    # hook, D002/D003 need core.hooksPath set. A repo that has run `pecia
    # init` and wired nothing satisfies none of them, so doctor returned
    # `ok: true, warnings: 0` for a ledger with no enforcement at all — a
    # green over an empty denominator, from the command whose whole question
    # is "is the enforcement ACTIVE?".
    #
    # It could not be observed here: pecia's own clone always has hooks. Found
    # by adopting pecia into a scratch project and reading what it said
    # (pc-1eba). Warning rather than error: an unguarded ledger is a real
    # posture finding, but it is also a legitimate first minute of adoption,
    # and an error would make `init` immediately followed by a red doctor.
    ledger_present = (top / ".pecia" / "work.jsonl").exists()
    gate_runs = bool(configured) and bool(
        active_dir and (active_dir / "pre-commit").is_file()
        and os.access(active_dir / "pre-commit", os.X_OK))
    # EXECUTABLE BUT UNREADABLE IS UNDECIDED, NOT GREEN (pc-937e). Such a
    # hook runs if it is a self-contained binary and dies 'Permission denied'
    # at every commit if it is a script, and doctor cannot read it to tell
    # which. D003 said so in prose while `gate_active` and `ok` — the fields
    # v2.16 made the answer — read true. None is the answer that field has
    # for "cannot be decided here"; `ok` is true only over an ACTIVE gate.
    gate_active: bool | None = (
        None if gate_runs and not _is_openable(active_dir / "pre-commit")
        else gate_runs)
    if ledger_present and gate_active is False and not shipped:
        # The recipe STARTS WITH THE mkdir ITS TARGET NEEDS (v2.8, pc-d947):
        # nothing creates dev/hooks in a fresh adopter, so the printed cp
        # died at its first command in exactly the repository D010 exists
        # for — while templates/pre-commit's own header documented the
        # working recipe, mkdir included.
        report("warning", "D010",
               "this repo has a pecia ledger and no write gate is active in "
               "this clone, so nothing checks a record before it is committed. "
               "Exit 0 from `check` is something you ran, not something the "
               "repo enforces.",
               "mkdir -p dev/hooks && "
               "cp templates/pre-commit dev/hooks/pre-commit && "
               "chmod +x dev/hooks/pre-commit && "
               "git config core.hooksPath dev/hooks")

    if shipped and not configured:
        # The measured case: a hook exists, is documented as enforcement, and
        # has never run here. CI may still cover it — late, not absent — so
        # this is an error about the CLAIM, not proof the repo is unguarded.
        target = shipped[0]
        report("error", "D001",
               f"repo ships {target}/pre-commit but core.hooksPath is unset — "
               f"this gate has never run in this clone.",
               f"pecia doctor --fix (or: git config core.hooksPath {sh_path(target)})")
        fixes.append(f"set core.hooksPath to {target}")
    elif configured and active_dir is not None:
        hook = active_dir / "pre-commit"
        if not hook.is_file():
            what = ("a DIRECTORY sits at that path, which git cannot exec — "
                    "every commit dies at 'fatal: cannot exec' (pc-aff4)"
                    if hook.is_dir() else
                    "no pre-commit hook is there — commits are running "
                    "unguarded while configured to be guarded")
            report("error", "D002",
                   f"core.hooksPath is {render_value(configured)} but {what}.")
        elif not os.access(hook, os.X_OK):
            report("error", "D003",
                   f"{configured}/pre-commit is not executable — git skips it "
                   f"SILENTLY, which reads exactly like a passing gate.",
                   f"chmod +x {sh_path(configured + '/pre-commit')}")
        else:
            # D012 (v2.9, pc-87d8) — AN ACTIVE HOOK THAT CAN RESOLVE NO
            # CHECKER. The template documents a three-way resolution
            # ($PECIA_CLI, `pecia` on PATH, the repository-root
            # pecia_cli.py) and REFUSES every commit when none resolves —
            # fail-closed, correctly — while doctor tested only existence
            # and executability and read ok:true over a repository where
            # every ordinary commit dies at 'no pecia CLI found'. The
            # pc-aff4 precedent put structural unexecutability in doctor's
            # remit; the resolution chain is the same class one layer up.
            # Applied only to a hook that honors the documented chain
            # (identified by its PECIA_CLI reference), so a hand-rolled
            # gate embedding its own path is not falsely accused; doctor's
            # environment stands in for the hook's, which is what the
            # operator can actually set.
            try:
                hook_text = hook.read_text(errors="replace")
            except PermissionError:
                # SIBLING OF pc-acaf, SAME COMMIT. An executable-but-unreadable
                # hook satisfies D003's X_OK, and this read used to swallow the
                # OSError into "" — so the whole D012 branch was skipped and
                # doctor reported a fully green posture. git execs the hook: a
                # `#!` script at mode 111 dies "Permission denied" on EVERY
                # commit (measured), a self-contained binary runs fine, and
                # doctor cannot tell which WITHOUT the read permission it was
                # just refused. So it reports what it actually knows, at
                # warning severity — an error here would assert the failing
                # case over a binary doctor never examined, which is the
                # false-red half of the pc-709d lesson.
                hook_text = ""
                report("warning", "D003",
                       f"{configured}/pre-commit is executable but NOT "
                       f"readable, so doctor cannot tell whether git can run "
                       f"it: an interpreted hook (a `#!` script) dies "
                       f"'Permission denied' at every commit, a self-contained "
                       f"binary does not — and this also leaves the checker "
                       f"resolution below unchecked. Until it is readable the "
                       f"gate is UNDECIDED: gate_active is null and ok is "
                       f"false (pc-937e).",
                       f"chmod +r {sh_path(configured + '/pre-commit')}")
            except OSError:
                hook_text = ""
            # IDENTIFIED ON CODE, NOT COMMENTS (pc-709d, round-6 lane
            # E1-F4). The substring test ran over the whole hook text, so a
            # hand-rolled hook whose COMMENT said it does not use PECIA_CLI
            # resolution was put through the three-way chain and doctor
            # reported "every ordinary commit is refused" while commits
            # succeeded — a false red violating the claim sentence the
            # comment above states. Shell comments (a `#` opening the line
            # or preceded by whitespace) are stripped before the test; a
            # `$PECIA_CLI` expansion, assignment, or string in live code
            # still identifies the chain.
            hook_code = "\n".join(re.sub(r"(^|\s)#.*", r"\1", ln)
                                  for ln in hook_text.splitlines())
            if "PECIA_CLI" in hook_code:
                env_cli = os.environ.get("PECIA_CLI")
                on_path = which_from_root("pecia", top)
                # THE RESOLVER DOCTOR CANNOT MODEL (pc-7acd, round-12 lane
                # E1-F2, v2.17). Up to v2.16 the shipped hook resolved
                # `pecia` with `command -v`, whose answer includes shell
                # functions, aliases and builtins and which prints a matched
                # FUNCTION's bare name — which the hook's slashless rebase
                # then turned into `$top/pecia`, a path that does not exist,
                # so the gate refused every ordinary commit while doctor,
                # resolving the same environment through a PATH search that
                # can never see a function, reported ok: true.
                #
                # The shipped hook now asks `type -P`, which is the PATH
                # search `which_from_root` models, so the divergence is gone
                # at the source. A hook copied before that change still has
                # it, and doctor must not certify a posture on a model it
                # knows is lossy there — so it ASKS THE SHELL (one fixed
                # command, never text read out of the hook) whether the name
                # is one of the things the old resolver would match.
                #
                # REPORTED ONLY WHEN THE DIVERGENCE IS LIVE. With no such
                # name in this environment the old resolver and the model
                # agree, doctor's answer is right, and a warning would be
                # lint about the hook's spelling rather than a statement
                # about posture — which is the false-red risk pc-709d names
                # and the reason this is not a ninth level.
                if (not env_cli and re.search(r"\bcommand\s+-v\s+pecia\b", hook_code)
                        and not re.search(r"\btype\s+-P\s+pecia\b", hook_code)):
                    kind = shell_name_kind("pecia")
                    if kind in {"function", "alias", "builtin", "keyword"}:
                        report("error", "D012",
                               f"the active hook resolves its pecia checker "
                               f"with `command -v pecia`, and in this "
                               f"environment `pecia` is a shell {kind} — "
                               f"`command -v` matches it and prints its bare "
                               f"name, which the hook rebases to "
                               f"{top / 'pecia'}, a path that does not exist, "
                               f"so the gate fails closed and every ordinary "
                               f"commit is refused at 'no pecia CLI found'. "
                               f"A PATH search cannot see a shell {kind}, so "
                               f"the resolution doctor models is not the one "
                               f"this hook performs.",
                               "update the hook to the shipped resolver "
                               "(`type -P pecia`, v2.17), which searches PATH "
                               "for the executable file the hook can actually "
                               "run — or unset the shell function")
                if env_cli:
                    # RESOLVED WHERE THE HOOK RESOLVES IT (pc-2f19, round-5
                    # lane E1-F1, v2.10). git runs hooks from the work-tree
                    # root, so a relative PECIA_CLI is root-relative to the
                    # HOOK — doctor used to test it against its own
                    # invocation directory, reading ok:true from a
                    # subdirectory over a gate refusing every commit (the
                    # pc-87d8 class one layer over: the chain was tested,
                    # against the wrong directory).
                    resolved = Path(env_cli)
                    if not resolved.is_absolute():
                        resolved = top / resolved
                elif on_path:
                    resolved = Path(on_path)
                else:
                    resolved = top / "pecia_cli.py"
                if not resolved.is_file():
                    report("error", "D012",
                           f"the active hook resolves no pecia checker — "
                           f"$PECIA_CLI is {'set to a missing file (a relative value resolves from the repository root, where git runs hooks)' if env_cli else 'unset'}, "
                           f"`pecia` is not on PATH, and {resolved} does not "
                           f"exist, so the gate fails closed and every "
                           f"ordinary commit is refused at 'no pecia CLI "
                           f"found' while posture read clean.",
                           "set PECIA_CLI to the checker, put `pecia` on "
                           "PATH, or place pecia_cli.py at the repository "
                           "root")
                elif resolved.suffix == ".py" and not _is_openable(resolved):
                    # THE QUESTION IS THE RUNTIME'S, NOT is_file()'s (pc-acaf,
                    # round-7 lane E1-F2). The hook runs a .py checker as
                    # `python3 "$cli"`, so python3 must OPEN it — and D012
                    # asked only is_file(). A checker at mode 000 therefore
                    # read ok:true, errors 0 while every ledger-bearing commit
                    # died "can't open file … Permission denied", the missing
                    # checker control firing correctly one mode bit away. This
                    # extends the pc-9663 rule from WHICH ORACLE a detector
                    # asks to WHICH QUESTION: the detector must ask the
                    # question the guarded runtime asks. Asked by trying the
                    # open rather than via os.access(), which answers for the
                    # real uid and would disagree with the runtime under
                    # setuid or an ACL — the point is to ask python3's own
                    # question, not a lookalike.
                    report("error", "D012",
                           f"the active hook resolves {resolved} as its pecia "
                           f"checker, but the file cannot be opened for "
                           f"reading — the hook runs a .py checker through "
                           f"python3, which must read it, so every ordinary "
                           f"commit dies 'Permission denied' while posture "
                           f"read clean.",
                           f"chmod +r {sh_path(resolved)}, or point PECIA_CLI at a "
                           f"checker this user can read")
                elif resolved.suffix != ".py" and not os.access(resolved, os.X_OK):
                    # THE EXECUTION MODE IS THE HOOK'S (pc-b418, round-5
                    # lane E1-F2, same commit). The template runs a non-.py
                    # checker DIRECTLY (only *.py goes through python3), so
                    # an existing mode-644 file dies 'Permission denied'
                    # into the staged-ledger refusal at every commit while
                    # is_file() read clean — false green in the direction
                    # automation trusts, the pc-aff4/pc-87d8 class at the
                    # checker instead of the hook.
                    report("error", "D012",
                           f"the active hook resolves {resolved} as its "
                           f"pecia checker, but the file is not executable "
                           f"and is not a .py the hook would run via "
                           f"python3 — the hook executes it directly, so "
                           f"every ordinary commit dies 'Permission denied' "
                           f"while posture read clean.",
                           "chmod +x the checker, point PECIA_CLI at a .py "
                           "file, or put an executable `pecia` on PATH")
                elif resolved.suffix != ".py" and not _is_openable(resolved):
                    # SIBLING OF pc-acaf, SAME COMMIT, same undecidability as
                    # the unreadable hook above: the template execs a non-.py
                    # checker directly, so an interpreted wrapper at mode 111
                    # dies 126 "Permission denied" at every commit while a
                    # self-contained binary runs. Doctor has no read access
                    # with which to tell them apart, so it reports the
                    # condition it verified and not the outcome it did not.
                    report("warning", "D012",
                           f"the active hook resolves {resolved} as its pecia "
                           f"checker and executes it directly; it is "
                           f"executable but NOT readable, so doctor cannot "
                           f"tell whether the hook can run it — an interpreted "
                           f"wrapper (a `#!` script) dies 'Permission denied' "
                           f"at every commit, a self-contained binary does "
                           f"not.",
                           f"chmod +r {sh_path(resolved)} so the posture is decidable")
                else:
                    # THE PROGRAM NAMES ARE DERIVED, NOT LISTED (pc-a2da,
                    # round-9 lane E1-F1). v2.13 stated the environment rule
                    # as a universal — "every program name the guarded
                    # invocation depends on must resolve on the PATH the hook
                    # inherits" — and the implementation enumerated ONE name
                    # (`python3`) for ONE checker kind. Both halves of the
                    # sequence the record measured were therefore invisible:
                    # an extensionless `pecia` on PATH whose own shebang is
                    # `#!/usr/bin/env -S uv run --script` with uv absent, and
                    # the hook's own `#!/usr/bin/env bash` with bash absent —
                    # each reading ok:true, 0 errors, 0 warnings while every
                    # ordinary commit died, and each cleared by adding ONLY
                    # the missing program to the same PATH. This is a
                    # SPEC/IMPLEMENTATION DISAGREEMENT rather than a sixth
                    # under-specification, so the fix is to derive the set
                    # from the template the detector stands in for: the
                    # interpreter chain of the hook that git executes, plus
                    # the invocation the hook performs (`python3 "$cli"` for
                    # a .py checker, the checker's own shebang chain when the
                    # hook execs it directly).
                    #
                    # SCOPE, stated so the next level is not mistaken for
                    # this one: what is derived is the INTERPRETER CHAIN of
                    # the two files that get executed. The POSIX utilities
                    # the hook body calls (git, mktemp, sed) are not
                    # enumerated — a missing `git` is not a state a git hook
                    # can be reported in, and guessing command words out of
                    # shell text is the false-red risk pc-709d names.
                    needs: list[tuple[str, str, str]] = [
                        (kind, name, "the hook's own interpreter")
                        for kind, name in shebang_requirements(hook)]
                    if resolved.suffix == ".py":
                        needs.append(("path", "python3",
                                      f"the hook runs a .py checker as "
                                      f"`python3 {resolved.name}`"))
                    else:
                        needs += [(kind, name,
                                   f"the hook execs {resolved.name} directly "
                                   f"and its shebang names this")
                                  for kind, name in shebang_requirements(resolved)]
                    for kind, name, why in needs:
                        if kind == "computed":
                            # THE NAME IS DECIDED AT RUN TIME (pc-fc1e). An
                            # `env -S` string may build the program name out
                            # of `${VAR}`, and doctor's environment — which
                            # stands in for the hook's — does not carry that
                            # variable. Resolving it to the literal text
                            # would be the false red this record is about,
                            # and skipping it silently would hide a posture
                            # nothing else reads. So it reports what it
                            # verified: it could not decide.
                            report("warning", "D012",
                                   f"the active hook resolves {resolved} as "
                                   f"its pecia checker, and the interpreter "
                                   f"it names is built at run time from "
                                   f"{name} — {why}, and doctor's own "
                                   f"environment does not carry that "
                                   f"variable, so whether the program "
                                   f"resolves cannot be decided here.",
                                   f"set {name} in the environment the hook "
                                   f"inherits, or name the interpreter "
                                   f"directly in the shebang")
                            continue
                        if kind == "path":
                            if which_from_root(name, top):
                                continue
                            report("error", "D012",
                                   f"the active hook resolves {resolved} as "
                                   f"its pecia checker, and {name} is on no "
                                   f"directory of this PATH — {why}, so every "
                                   f"ordinary commit dies "
                                   f"'{name}: command not found' (or `env: "
                                   f"{name}: No such file or directory`) while "
                                   f"posture read clean.",
                                   f"install {name} or put it on the PATH the "
                                   f"hook inherits, or point PECIA_CLI at a "
                                   f"checker whose interpreter is present")
                        elif not os.access(name, os.X_OK):
                            report("error", "D012",
                                   f"the active hook resolves {resolved} as "
                                   f"its pecia checker, and {name} is not an "
                                   f"executable file — {why}, and the kernel "
                                   f"execs that path directly, so every "
                                   f"ordinary commit dies while posture read "
                                   f"clean.",
                                   f"install the interpreter at {sh_path(name)}, or "
                                   f"point the shebang at one that exists")
        # Two ways the shipped hook can be dead, with different fixes. The
        # cross-tree case (D008) is the one that reads as noise when reported
        # as D004: the gate IS running, so nothing looks broken, but it is
        # another checkout's copy of the file — which tracks whatever branch
        # THAT tree has checked out. A worktree developing a hook fix is
        # therefore gated by the older hook it is trying to replace, and the
        # fix is never exercised where it is written.
        for extra in shipped:
            shipped_dir = (top / extra).resolve()
            if active_dir.resolve() == shipped_dir:
                continue
            if path_is_inside(active_dir, top):
                report("warning", "D004",
                       f"repo also ships {extra}/pre-commit, which is not the "
                       f"configured hooks path — one of them is dead code.")
            else:
                scope = ("--worktree " if git_out("config", "extensions.worktreeConfig") == "true"
                         else "")
                report("warning", "D008",
                       f"core.hooksPath resolves OUTSIDE this working tree "
                       f"({active_dir}) — the hook that runs is that checkout's "
                       f"copy and tracks whatever branch IT has checked out, "
                       f"not this one. This tree's own {extra}/pre-commit "
                       f"never runs.",
                       f"git config {scope}core.hooksPath {sh_path(extra)}")

    if PECIA_DIR.exists():
        if not lock_is_ignored():
            report("warning", "D005",
                   ".pecia/.lock is not git-ignored — it becomes permanent "
                   "untracked noise, and noise trains readers to skim the "
                   "worktree-activity report that catches swept files.",
                   "pecia doctor --fix (or re-run: pecia init)")
            fixes.append("ignore .pecia/.lock")
        # D006 INVERTED at v2 (pc-b8a6). It used to warn when merge=union was
        # ABSENT. init now strips that attribute, so doctor was telling the
        # operator to undo what init had just done — run one then the other and
        # they contradict. Under one timeline the snapshot is a projection that
        # is never merged, and a union attribute on it would silently splice two
        # branches' projections back together, which is the alternative timeline
        # rule 5 forbids. The check is kept and reversed rather than deleted: a
        # silent reappearance of the attribute would restore the merge path.
        # PARSED, not substring-matched (pc-69b4). The original test was
        # `".pecia/work.jsonl merge=union" in attrs.read_text()`, which reads
        # the whole file including comments — so a .gitattributes whose
        # comment explained why the attribute is deliberately NOT declared
        # made doctor report that it WAS. Found on 2026-09-01 by the first
        # .gitattributes written since the file was deleted at pc-de27, which
        # is also the first input this inverted check ever had. The parse
        # lives in merge_union_declared, shared with init's remover and
        # --fix's credit check (pc-9663).
        if merge_union_declared(ROOT / ".gitattributes"):
            report("warning", "D006",
                   "the snapshot still declares merge=union — a v1 attribute. The "
                   "snapshot is a generated projection under v2 and is never merged; "
                   "a union here splices two branches' projections into a timeline "
                   "that never existed.",
                   "pecia doctor --fix (or re-run: pecia init)")
            fixes.append("remove merge=union")
        # Deliberately LEDGER_PATH, not snapshot_path() (pc-74da): doctor
        # reports the REPOSITORY's posture, and the custody question is about
        # the tracked .pecia/ projection; a pinned scratch store is not
        # custody and has no git to be tracked by.
        if LEDGER_PATH.exists() and git_out("ls-files", "--error-unmatch",
                                            ".pecia/work.jsonl") is None:
            report("warning", "D007",
                   "the ledger is not tracked by git — an uncommitted ledger is "
                   "not custody; nobody else can see or review it.",
                   "git add .pecia/work.jsonl")
        # D011 (v2.7, pc-f446): the config the vocabulary depends on was
        # outside custody with no signal — D007 covered the ledger only.
        # The adopter hook validates the STAGED ledger against the STAGED
        # config; an untracked config substitutes empty in the index, so a
        # record legal under the live vocabulary (extra_types, planned) is
        # refused at every commit while posture read wholly clean. Loud
        # downstream, invisible here — until this.
        if CONFIG_PATH.exists() and git_out("ls-files", "--error-unmatch",
                                            ".pecia/config.yaml") is None:
            report("warning", "D011",
                   "the config (.pecia/config.yaml) is not tracked by git — "
                   "commit gates validate the staged ledger against the "
                   "STAGED config, which substitutes empty, so a record "
                   "legal under the live vocabulary is refused at commit "
                   "while the live check stays green.",
                   "git add .pecia/config.yaml")

    # D009 (pc-5f0f): the fresh-clone trap. `git clone` never fetches
    # refs/pecia/log — it lives outside refs/heads/*, refs/remotes/* — so a
    # clone with a remote configured and no local timeline is NOT a repo
    # that has never used pecia; it is the ordinary shape of a fresh clone
    # of an already-bootstrapped one, and `pecia sync` is how it hydrates.
    # Independent of the `.pecia/` checks above: the log lives under
    # --git-common-dir, not the working tree. Advisory only, and
    # unconditional on the remote actually publishing anything — doctor
    # reports POSTURE, not correctness (rule 1 again), so this does not
    # `ls-remote` to confirm refs/pecia/log exists there before advising
    # sync; sync's own message is clear if it does not, and doctor stays a
    # command that never touches the network.
    ledger_target = log_path()
    if ledger_target is not None and not ledger_target.exists() and git_out("remote"):
        report("error", "D009",
               "no local pecia timeline, and this clone has a remote "
               "configured — this is the fresh-clone state `pecia sync` "
               "exists to hydrate. Do not run `pecia migrate` here: it "
               "builds a LOCAL chain from this clone's own history (which a "
               "plain `git clone` does not include — refs/pecia/log is "
               "never fetched) and can silently diverge from what is "
               "already published.",
               "pecia sync")

    if args.fix:
        # --FIX ANSWERS EVEN WHEN IT AUTOMATES NOTHING (v2.8, pc-1121). The
        # guard here was `if args.fix and fixes:`, so when every finding had
        # no automated fix (D010 — no gate anywhere, the adopter's case),
        # --fix fell through to the plain report: same findings, nothing
        # changed, no `fixed:` acknowledgment, exit 0 — and automation
        # reading the output treated the unguarded repository as fixed. The
        # D010 warning/ok-at-exit-0 posture itself is the declared design
        # (a legitimate first minute of adoption); what was missing is the
        # explicit account of what --fix did and did not do. `fixed` may
        # now be empty and `unfixed` names every finding left for a human,
        # whose recipes are in the findings printed above.
        # FIXED MEANS IT HAPPENED (v2.9, pc-daec). cmd_init's return was
        # DISCARDED here, so over a malformed timeline — where init
        # correctly REFUSES (the pc-0ff7 guard) — --fix appended 're-ran
        # init' to `fixed`, dropped the still-present D005 from `unfixed`,
        # and exited 0: the very accounting pc-1121 added reported an
        # action that did not happen. A fix is `fixed` only on exit 0;
        # a refusal is named in `refused` and its findings stay `unfixed`;
        # and a --fix that could not do what it set out to do exits 1.
        #
        # CREDITED WHEN THE CONDITION CLEARED, NEVER WHEN THE FIXER EXITED 0
        # (pc-9663, round-5 lane C-F2, v2.10). The letter of pc-daec was
        # satisfied while its headline was violated: init exited 0 having
        # removed only the exact legacy line, the variant attribute the
        # detector fires on stayed active, and D006 was dropped from
        # `unfixed` anyway. Each automated credit now re-runs its
        # finding's own DETECTOR after the fixer; a condition still
        # standing behind an exit-0 fixer is named in `refused` and its
        # finding stays on the books.
        #
        # AND THE WRITE IS READ BACK (pc-ef4c, round-5 lane E1-F3, same
        # commit): git_out collapsed a failed `git config core.hooksPath`
        # to None, so the attempted fix appeared in neither `fixed` nor
        # `refused` and the command exited 0 with D001 standing.
        applied = []
        refused = []
        automated: set[str] = set()
        finding_codes = {f["code"] for f in findings}
        if fixes:
            if shipped and not configured:
                code, _, err = git_run("config", "core.hooksPath", shipped[0])
                if code == 0 and git_out("config", "core.hooksPath") == shipped[0]:
                    applied.append(f"core.hooksPath={shipped[0]}")
                    automated.add("D001")
                else:
                    refused.append(
                        f"core.hooksPath write refused "
                        f"({err or 'git config failed'}) — D001 stands")
            if PECIA_DIR.exists():
                args_init = argparse.Namespace()
                # init's own output is captured and passed through unchanged,
                # so the account below can say what init ACTUALLY emitted
                # and where (pc-c810), rather than what one branch emits.
                # Captured as JSON whatever this run prints (v3.5), because
                # the account reads it; then printed in this run's form.
                init_out, init_err = io.StringIO(), io.StringIO()
                mode = OUTPUT["json"]
                OUTPUT["json"] = True
                try:
                    with contextlib.redirect_stdout(init_out), \
                            contextlib.redirect_stderr(init_err):
                        rc = cmd_init(args_init)  # idempotent: ignore rule, and STRIPS merge=union
                finally:
                    OUTPUT["json"] = mode
                replay_json(init_out.getvalue(), init_err.getvalue())
                if rc == 0:
                    cleared: list[str] = []
                    if lock_is_ignored():
                        automated.add("D005")
                        cleared.append("ignore rule in place")
                    elif "D005" in finding_codes:
                        refused.append("init exited 0 and .pecia/.lock is "
                                       "still not ignored — not credited "
                                       "(D005 stands)")
                    if not merge_union_declared(ROOT / ".gitattributes"):
                        automated.add("D006")
                        cleared.append("no v1 merge attribute declared")
                    elif "D006" in finding_codes:
                        refused.append("init exited 0 and the merge=union "
                                       "attribute is still declared — not "
                                       "credited (D006 stands)")
                    applied.append("re-ran init"
                                   + (f" ({'; '.join(cleared)})" if cleared
                                      else ""))
                else:
                    # A REFUSAL SAYS WHERE TO LOOK, AND HAS TO BE RIGHT
                    # (v2.16, pc-8fca). This said the refusal was on stderr
                    # while both refusal objects — init's E001 finding and
                    # its `initialized: false` note — went to stdout, so a
                    # caller that followed the sentence read an empty stream.
                    # v2.13 made a refusal's named remedy part of its
                    # contract; where it says to look is the same rule.
                    #
                    # AND IT HAS TO BE RIGHT ON EVERY BRANCH (pc-c810, round-13
                    # lane E1-F3). pc-8fca's repair measured the
                    # malformed-timeline refusal, where both objects are on
                    # stdout, and installed one unconditional sentence. The
                    # fresh-clone refusal emits no `initialized` object at
                    # all; its only refusal is a fatal on stderr, so the
                    # sentence pointed at the wrong stream and named an
                    # object that never existed. The account is now derived
                    # from what init emitted, and carries a fatal's reason.
                    refused.append("init refused ("
                                   + init_refusal_account(init_out.getvalue(),
                                                          init_err.getvalue())
                                   + ") — nothing about the ignore rule or "
                                   "merge attribute changed")
        unfixed = sorted(finding_codes - automated)
        for f in findings:
            emit(f)
        # THE EXIT CODE AND THE POSTURE ARE DIFFERENT QUESTIONS (v2.16,
        # pc-b8e2). Claim 34 said "a --fix that could not do what it set out
        # to do exits 1" and the code answered something narrower: a fixer
        # that ATTEMPTED an action and failed. On the adopter's case — a
        # ledger and no gate anywhere — --fix reports `fixed: []`,
        # `unfixed: ["D010"]` and exits 0, because it attempted nothing and
        # nothing it attempted failed.
        #
        # THE CLAIM IS NARROWED TO THAT, NOT THE CODE WIDENED TO THE CLAIM,
        # and the reason is written here so the call can be reviewed. This
        # command's exit 0 means "it ran and reported", which is rule 1's own
        # sentence — exit 0 means well-formed, never true — and D010's
        # warning severity is a deliberate decision (pc-1121, pc-1eba): a
        # ledger without a gate is a legitimate first minute of adoption, and
        # an error there would make `init` be followed by a red doctor. What
        # WAS missing is a machine-readable posture answer on this surface,
        # which is the same defect `ok` carried on the plain one (pc-8411).
        # `posture_clean` is that field, and it is what automation should
        # read.
        # AFTER the fixes, not before: each automated credit re-ran its own
        # detector (pc-9663), so an empty `unfixed` means the conditions
        # cleared rather than that the fixer exited 0.
        result = {"fixed": applied, "unfixed": unfixed,
                  "posture_clean": not unfixed and not refused,
                  "rerun": "pecia doctor",
                  "note": "Fixes cover configuration only. `unfixed` names the "
                          "findings whose remedy needs a human; each Fix: recipe "
                          "is printed in its finding above. The exit code says "
                          "whether what this command ATTEMPTED took (v2.16); "
                          "`posture_clean` says whether anything is left, and "
                          "is the field to read — a --fix that attempts "
                          "nothing and repairs nothing exits 0."}
        if refused:
            result["refused"] = refused
        emit(result)
        return 1 if refused else 0

    for f in findings:
        emit(f)
    errors = sum(1 for f in findings if f["severity"] == "error")
    # `ok` ANSWERS DOCTOR'S OWN QUESTION (v2.16, pc-8411). It used to mean
    # "no error-severity D-code", which the register narrowed nowhere — so on
    # a repository whose own D010 says "no write gate is active in this
    # clone, nothing checks a record before it is committed", the
    # machine-readable field a caller reads said `ok: true` in the same
    # output. Claim 33's subject is whether the gates are ACTIVE, so that is
    # what `ok` reports, with `gate_active` beside it so the reason is
    # readable rather than inferred.
    #
    # THE EXIT CODE IS DELIBERATELY NOT MOVED WITH IT. D010 is a warning
    # because an unguarded ledger is also a legitimate first minute of
    # adoption, and an error would make `init` be followed by a red doctor.
    # So `ok` and the exit code answer different questions here, which is
    # rule 1's own shape: exit 0 means the command ran and reported, never
    # that the posture is good.
    gate_ok = gate_active is True or not ledger_present
    emit({"ok": errors == 0 and gate_ok, "errors": errors,
          "warnings": len(findings) - errors, "gate_active": gate_active,
          "hooks_path": configured, "ships_hooks": shipped,
          "note": "doctor reports whether gates are ACTIVE, never whether they "
                  "are correct. An active gate can still be wrong; see rule 1. "
                  "`ok` is that posture answer and NOT the exit code: an "
                  "unguarded ledger is ok: false at exit 0 (v2.16)."})
    return 1 if errors else 0


def cmd_add(args: argparse.Namespace) -> int:
    with ledger_lock():
        return _add_locked(args)


def _add_locked(args: argparse.Namespace) -> int:
    records, load_findings, _ = load_raw()
    for f in load_findings:
        if f["code"] in ("E000", "E013"):
            return cannot_run(f["message"])
    created = today()
    edges = empty_edges()
    for key in LIST_EDGES:
        edges[key] = list(getattr(args, key, None) or [])
    for key in SCALAR_EDGES:
        value = getattr(args, key, None)
        # AUTHORED INPUT IS WRITTEN AS AUTHORED, AND THE GATE DECIDES
        # (pc-798a, round-4 lane C-F2). `if value:` silently collapsed an
        # explicitly empty argument to null at exit 0 — authored input
        # discarded where claim 26 promises a write-path refusal — while
        # `edit` preserved the same value and was correctly refused (E001).
        # None means the option was not given; anything given, the empty
        # string included, reaches the write gate and is refused there,
        # the same move edit makes.
        if value is not None:
            edges[key] = value
    rec: dict[str, Any] = {
        "id": mint_id(args.type, args.title, created, set(group_revisions(records))),
        "rev": 1, "type": args.type, "title": args.title, "status": "open",
        "priority": args.priority, "created": created, "updated": created,
        "edges": edges, "disposition": None, "evidence": "unknown",
        "owner": args.owner, "labels": list(args.label or []), "body": args.body or "",
    }
    if args.target:
        rec["target"] = args.target
    if getattr(args, "context", None):
        rec["context"] = args.context
    rec.update(provenance_anchor())
    abort = gate_or_brand(args, records, rec, None)
    if abort is not None:
        return abort
    append_record(rec)
    emit(project("written", rec))
    return 0


def _revise(args: argparse.Namespace, mutate) -> int:
    with ledger_lock():
        return _revise_locked(args, mutate)


def _revise_locked(args: argparse.Namespace, mutate) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, records = got
    head = heads.get(args.id)
    if head is None:
        return cannot_run(f"record {args.id} not found")
    rec = json.loads(canonical(head))  # deep copy
    rec.pop("forced", None)  # the brand is per-revision, never inherited
    rec.pop("anchor", None)  # so is the anchor: each revision names ITS commit
    rec.pop("anchor_dirty", None)
    mutate(rec)
    rec["rev"] = head["rev"] + 1
    rec["updated"] = today()
    rec.update(provenance_anchor())
    if unchanged(args, head, rec):
        emit(unchanged_echo(head))
        return 0
    abort = gate_or_brand(args, records, rec, head)
    if abort is not None:
        return abort
    append_record(rec)
    emit(project("written", rec))
    return 0


def unchanged(args: argparse.Namespace, head: dict[str, Any],
              rec: dict[str, Any]) -> bool:
    """Whether this revision changes nothing, and so is not written (v3.8,
    pc-9e70b7933815).

    A REVISION THAT CHANGES NOTHING IS NOT WRITTEN. `edit` with a value
    equal to the current one, or with no change at all, appended a revision
    whose derived touched set was empty, at exit 0. sync already skips such a
    revision (claim 35), but only in the unpublished suffix; once published
    it was permanent. A FORCED one is still written: its brand records a use
    of the escape hatch, which audit enumerates (pc-dacc)."""
    return not getattr(args, "force", False) and not diff_fields(head, rec)


def unchanged_echo(head: dict[str, Any]) -> dict[str, Any]:
    """What a write that changed nothing reports: the record as it stands."""
    return {**project("written", head), "unchanged": True}


# -- --also-closes: the population mechanism for `retires` (v1.13) -----------
#
# The edge exists to be WRITTEN, and PLAN.md's measured unpopulated-edge
# problem says a vocabulary nobody populates buys nothing. So the edge is
# written because it is the cheapest way to close a group of records, not
# because a rule tells an agent to — substrate-not-prompt (catalog v2.6 GP8)
# applied to an edge rather than to a gate.
#
# It has to be ATOMIC and it has to be ORDERED. The write gate refuses a
# write that introduces a new error, so a revision making the retirer
# terminal while a target is still open is refused by E012 — correctly. The
# targets must therefore close FIRST, inside the same lock, and the whole
# batch must be refused together: a half-applied group would leave the
# ledger in exactly the state the gate exists to prevent.


def _merge_retires(rec: dict[str, Any], targets: list[str] | None) -> None:
    if not targets:
        return
    edges = rec.setdefault("edges", empty_edges())
    edges["retires"] = sorted(set(edges.get("retires") or []) | set(targets))


def _retire_target_mutate(retirer: str, status: str, disposition: str,
                          evidence: str | None):
    def mutate(rec: dict[str, Any]) -> None:
        rec["status"] = status
        rec["disposition"] = f"Retired by {retirer}: {disposition}"
        # Fill an evidence gap, never overwrite custody. A target that already
        # carries its own structural evidence has a better claim on its own
        # closure than the retirer's blanket one; a target sitting at
        # "unknown" would fail E007 on a defect and refuse the batch.
        if evidence is not None and not evidence_is_structural(rec.get("evidence"), load_config()):
            rec["evidence"] = evidence
    return mutate


def _also_closes_plan(args: argparse.Namespace, retirer_mutate):
    """Build the (id, mutate) sequence for a --also-closes batch: targets
    first, retirer last. Returns an exit code instead of a plan when the
    request is incoherent — checked against heads read under the lock."""
    def build(heads: dict[str, dict[str, Any]]):
        rid = args.id
        head = heads.get(rid)
        if head is None:
            return cannot_run(f"record {rid} not found")
        targets = list(dict.fromkeys(args.also_closes))
        if rid in targets:
            return cannot_run("--also-closes names the record being closed — "
                              "a record cannot retire itself")
        missing = [t for t in targets if t not in heads]
        if missing:
            return cannot_run(
                f"--also-closes names {len(missing)} record(s) this ledger does "
                f"not carry; --also-closes CLOSES its targets, so every one must "
                f"exist. (`--retires` alone accepts a `planned:` id.)")
        # The status the retirer will hold once this batch lands. `close`
        # always supplies one; `edit` falls back to the head's, which is what
        # makes the RETROACTIVE case work — discovering after Y is done that
        # Y's work retired X is the common case, and a plain
        # `edit Y --retires X` is refused by E012 while X is open.
        final = getattr(args, "status", None) or head.get("status")
        if final not in TERMINAL_STATUSES:
            return cannot_run(
                "--also-closes needs the retiring record to end up terminal: use "
                "`close --also-closes`, or `edit --also-closes` on a record that "
                "is already terminal. To record the edge without closing anything, "
                "use --retires.")
        disposition = getattr(args, "disposition", None) or head.get("disposition")
        if not (isinstance(disposition, str) and disposition.strip()):
            return cannot_run("--also-closes gives each retired record the "
                              "retirer's own disposition, and this one has none "
                              "— pass --disposition")
        plan = [(t, _retire_target_mutate(rid, final, disposition,
                                          getattr(args, "evidence", None)))
                for t in targets
                if heads[t].get("status") not in TERMINAL_STATUSES]
        # An already-terminal target gets no new revision — it was closed by
        # hand, which is the very history this edge exists to make computable
        # — but the edge is still written by the retirer's own mutate below.
        plan.append((rid, retirer_mutate))
        return plan
    return build


def _write_batch(args: argparse.Namespace, build_plan) -> int:
    with ledger_lock():
        return _write_batch_locked(args, build_plan)


def _write_batch_locked(args: argparse.Namespace, build_plan) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, records = got
    plan = build_plan(heads)
    if isinstance(plan, int):
        return plan
    pending, written, shown = list(records), [], []
    for rid, mutate in plan:
        head = heads[rid]
        rec = json.loads(canonical(head))   # deep copy
        rec.pop("forced", None)             # the brand is per-revision
        rec.pop("anchor", None)             # and so is the anchor
        rec.pop("anchor_dirty", None)
        mutate(rec)
        rec["rev"] = head["rev"] + 1
        rec["updated"] = today()
        rec.update(provenance_anchor())
        if unchanged(args, head, rec):      # v3.8: nothing to write
            shown.append(unchanged_echo(head))
            continue
        # Gated against the ledger PLUS the candidates already accepted in
        # this batch, which is what makes the order inside the plan mean
        # something: the retirer is checked against a ledger in which its
        # targets are already closed.
        abort = gate_or_brand(args, pending, rec, head)
        if abort is not None:
            return abort                    # nothing appended — all or nothing
        pending.append(rec)
        written.append(rec)
        shown.append(project("written", rec))
    for rec in written:
        append_record(rec)
    # A batch emits a LIST, in write order. Callers pass --also-closes
    # deliberately; a single-object echo would have to pick one of several
    # written revisions to describe. An item that changed nothing is listed
    # where it stands in the plan, marked unchanged (v3.8).
    emit(shown)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    def mutate(rec: dict[str, Any]) -> None:
        for field in ("title", "status", "body", "owner", "evidence",
                      "disposition", "target"):
            value = getattr(args, field, None)
            if value is not None:
                rec[field] = value
        if args.priority is not None:
            rec["priority"] = args.priority
        if getattr(args, "context", None) is not None:
            if args.context == "none":
                rec.pop("context", None)
            else:
                rec["context"] = args.context
        if getattr(args, "ratify", False):
            # The ratifying identity is the invoker, never --owner: --owner
            # edits the record's authorship, and conflating the two would
            # let one flag both write a decision and countersign it.
            rec["ratified_by"] = default_owner()
            rec["ratified"] = today()
        edges = rec.setdefault("edges", empty_edges())
        for key in LIST_EDGES:
            value = getattr(args, key, None)
            if value is not None:
                # `--blocks none` CLEARS, matching the scalar edges' vocabulary
                # (pc-35e6). Without it a list edge could be replaced but never
                # emptied: 'none' was refused by E003 as a nonexistent target,
                # '' likewise, --force would have written the literal string as
                # a target rather than clearing, and --no-edges drops the
                # scalars too — so a record could not express "blocks nothing,
                # but was still discovered from something". Hit twice in one
                # session, both times while correcting an edge direction, and
                # the only recovery was to drop the record and recreate it.
                #
                # A record id can never be the literal "none" (ID_RE requires
                # the pc- prefix), so this steals nothing from the id space.
                edges[key] = [] if list(value) == ["none"] else list(value)
        for key in SCALAR_EDGES:
            value = getattr(args, key, None)
            if value is not None:
                edges[key] = None if value == "none" else value
        if args.no_edges:
            edges["no_edges"] = True
        if args.label is not None:
            rec["labels"] = list(args.label)
        _merge_retires(rec, args.also_closes)
    if args.also_closes:
        return _write_batch(args, _also_closes_plan(args, mutate))
    return _revise(args, mutate)


def cmd_close(args: argparse.Namespace) -> int:
    def mutate(rec: dict[str, Any]) -> None:
        rec["status"] = args.status
        rec["disposition"] = args.disposition
        if args.evidence is not None:
            rec["evidence"] = args.evidence
        _merge_retires(rec, args.also_closes)
    if args.also_closes:
        return _write_batch(args, _also_closes_plan(args, mutate))
    return _revise(args, mutate)


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config)) if args.config else load_config()
    if args.ledger:
        # An explicitly named file: bare records, no timeline invariants. This
        # is how an adapter or a reviewer interrogates a snapshot they were
        # handed, and E013/E014/E015 are meaningless there by construction.
        ledger = Path(args.ledger)
        if not ledger.exists():
            return cannot_run(f"no ledger at {ledger}")
        records, parse_findings, record_lines = load_raw(ledger)
        findings = run_checks(records, parse_findings, cfg,
                              record_lines=record_lines)
    else:
        entries, parse_findings = load_entries()
        if any(f["code"] == "E000" for f in parse_findings):
            return cannot_run(parse_findings[0]["message"])
        records = [e["rec"] for e in entries]
        findings = run_checks(records, parse_findings, cfg,
                              record_lines=list(range(1, len(records) + 1)))
        # read_log returns at its FIRST error, so an error finding here means
        # `entries` is a prefix and not the chain (v2.16, pc-1667).
        findings.extend(timeline_checks(
            entries,
            log_complete=not any(f["severity"] == "error"
                                 for f in parse_findings)))
    for f in findings:
        emit(f)
    if any(f["severity"] == "error" for f in findings):
        return 1
    emit({"ok": True, "note": RULE1, "records": len(records),
          "warnings": sum(1 for f in findings if f["severity"] == "warning")})
    return 0


def compute_blockers(heads: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """id -> open blockers. A blocks B means B waits on A; parent counts for done-rollup,
    and an open child blocks its parent milestone's completion, not the reverse.

    `retires` (v1.13) is the same shape as both: the record HOLDING the edge
    blocks its TARGETS. An open retirer therefore holds the record it claims
    to retire out of `ready` and `next`, which is the forward signal pc-4d19
    named as gap (1) — `next` no longer offers work whose resolution is
    somebody else's already-scheduled job. When every retirer goes terminal
    the target becomes ready again, and E012 fires if nobody then closes it.

    Note the arc runs retirer -> target and NOT the reverse. pc-4d19 rev 2
    read it the other way ("Y cannot be done while the X in Y.retires is
    non-terminal"). That direction is right as a consistency invariant, which
    is what E012 enforces, but wrong as a scheduling arc: with several
    records retiring one target it deadlocks every one of them behind a
    target that only their own closure can resolve."""
    blocked_by: dict[str, list[str]] = {rid: [] for rid in heads}
    for rid, head in heads.items():
        if head.get("status") in TERMINAL_STATUSES:
            continue
        edges = head.get("edges") or {}
        for key in LIST_EDGES:
            for target in edges.get(key) or []:
                if target in blocked_by:
                    blocked_by[target].append(rid)
        parent = edges.get("parent")
        if parent in blocked_by:
            blocked_by[parent].append(rid)  # open child blocks parent completion
    return {rid: sorted(set(v)) for rid, v in blocked_by.items()}


def open_heads(heads: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in heads.values() if h.get("status") == "open"]


def cmd_ready(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    blocked_by = compute_blockers(heads)
    ready = [h for h in open_heads(heads) if not blocked_by.get(h["id"])]
    ready.sort(key=lambda h: (h.get("priority", 2),
                              chronological_key(h.get("created", "")), h["id"]))
    emit([project("ready", h) for h in ready])
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    blocked_by = compute_blockers(heads)
    out = []
    for h in sorted(open_heads(heads), key=lambda h: h["id"]):
        blockers = blocked_by.get(h["id"], [])
        if blockers:
            questions = [b for b in blockers if heads[b].get("type") == "question"]
            out.append({**project("blocked", h),
                        "blockers": [safe_id(b) for b in blockers
                                     if b not in questions],
                        "awaiting_answers": [safe_id(b) for b in questions]})
    emit(out)
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    blocked_by = compute_blockers(heads)
    ready = [h for h in open_heads(heads)
             if not blocked_by.get(h["id"]) and h.get("priority", 2) <= 3]
    ready.sort(key=lambda h: (h.get("priority", 2),
                              chronological_key(h.get("created", "")), h["id"]))
    emit([project("next", h) for h in ready[: args.limit]])
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """A record as stored — every field, verbatim (v3.3, pc-ded91385e31a).

    v1.10 withheld prose from every command so that record text never reached
    a reading agent through pecia. Every repo that keeps its tickets here has
    readers with file access, so the rule contained nothing: it sent readers
    around pecia to the raw files instead. This is the supported read. With
    --history, every entry that revised the record, in log order, each with
    its `touched` set. Refuses where the queries refuse: a history it cannot
    schedule against is one it cannot say what a record currently is in."""
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    if args.id not in heads:
        return cannot_run(f"record {args.id} not found")
    if not args.history:
        emit(heads[args.id])
        return 0
    entries, _ = load_entries()
    emit([e for e in entries if e["rec"].get("id") == args.id])
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    nodes = [project("graph", h) for _, h in sorted(heads.items())]
    edges_out = []
    for rid, head in sorted(heads.items()):
        edges = _projected_edges(head)
        for key in LIST_EDGES:
            for target in edges.get(key) or []:
                edges_out.append({"from": safe_id(rid), "to": target, "kind": key})
        for key in SCALAR_EDGES:
            if edges.get(key):
                edges_out.append({"from": safe_id(rid), "to": edges[key], "kind": key})
    if args.format == "mermaid":
        lines = ["graph TD"]
        for node in nodes:
            lines.append(f'  {mermaid_id(node["id"])}'
                         f'["{mermaid_safe_id_display(node["id"])}: '
                         f'{mermaid_label(node["title"], 40)}"]')
        for e in edges_out:
            lines.append(f'  {mermaid_id(e["from"])} -->|{e["kind"]}| {mermaid_id(e["to"])}')
        out_line("\n".join(lines))
    else:
        emit({"nodes": nodes, "edges": edges_out})
    return 0


def milestone_is_overdue(head: dict[str, Any], cutoff_today: str) -> bool:
    """A milestone whose target is past and whose status is non-terminal
    (rule 2: derived state is computed, never stored — pc-5640). Shared by
    `audit` and `gantt` so the finding and its distinct rendering cannot
    drift apart, the same reasoning audit_findings() gives for sharing with
    `board`. Plain string comparison against a date-only cutoff, unguarded —
    matching every other date-cutoff check in this file (priority-rot,
    stale-in-progress), not a new pattern.

    TYPE-SAFE ON THE TARGET (pc-5706, round-13 fix sitting). `bool(...)` and
    `<` were the unguarded pair: a NON-STRING target is truthy and then
    `42 < "2026-09-19"` raised TypeError, which took `audit` and `board` —
    neither of which runs the E018 projection scan in front of this — to a
    fatal E000 at exit 2. Overdue is DERIVED, and a value that is not a date
    derives nothing: the predicate declines rather than raises. That is not
    the malformation going unreported. `check` refuses the record with E001
    at exit 1, and `board` prints those errors itself; `gantt` refuses it
    with E018 before either renderer runs. The structural verdict stays with
    the structural gate, which is rule 1's own division."""
    target = head.get("target")
    return (head.get("type") == "milestone"
            and head.get("status") not in TERMINAL_STATUSES
            and isinstance(target, str) and bool(target)
            and target < cutoff_today)


def gantt_mermaid_lines(heads: dict[str, dict[str, Any]], cfg: dict[str, Any]) -> list[str]:
    """The portable projection: Mermaid gantt source, for a renderer that isn't
    this terminal (docs, GitHub, a future harness pipeline). Pulled out of
    cmd_gantt as pure text-in/text-out so --mermaid and the ASCII default
    can't drift on which milestones or which state (done/crit) they agree on
    — both read the same heads through the same milestone_is_overdue call."""
    milestones = [h for h in heads.values() if h.get("type") == "milestone"]
    cutoff_today = date.today().isoformat()
    # Config is repo-local and declared trusted (it is not merge=union, and
    # anyone who can write it can write this file) — but "trusted" should not
    # also mean "may emit a control character".
    title = mermaid_label(cfg.get("project_name") or ROOT.name, TITLE_CAP)
    lines = ["gantt", "  dateFormat YYYY-MM-DD", f"  title {title} milestones",
             "  %% dates are truncated to the day: this chart's dateFormat is "
             "YYYY-MM-DD (spec v1.12 allows more precision in the ledger)"]
    # An empty chart is not "no milestones are late" — it is "nothing was
    # rendered." A projection that renders successfully while conveying
    # nothing reads as green; it must declare its own emptiness instead.
    if not milestones:
        lines.append(f"  %% no milestone records in this ledger "
                     f"({len(heads)} records, 0 of type milestone) — nothing to chart")
    for m in sorted(milestones, key=lambda h: (h.get("target") or "9999", h["id"])):
        row = project("gantt", m)
        label = mermaid_label(row["title"], 60)
        if not row["target"]:
            lines.append(f"  %% {row['id']} {label} — no target date")
            continue
        # done/crit are mutually exclusive by construction: overdue requires
        # non-terminal (milestone_is_overdue), done requires terminal.
        state = ("done, " if m.get("status") in TERMINAL_STATUSES
                 else "crit, " if milestone_is_overdue(m, cutoff_today) else "")
        lines.append(f"  {gantt_task_label(label)} "
                     f"({mermaid_safe_id_display(row['id'])}) "
                     f":{state}{mermaid_id(row['id'])}, "
                     f"{date_part(row['created'])}, {date_part(row['target'])}")
    return lines


def gantt_status_text(b: "Board", h: dict[str, Any], today_d: date,
                      cutoff_today: str) -> str:
    """The PLAIN status text for one dated milestone row: `✓ done`,
    `✗ overdue Nd`, `due today`, or `Nd left`.

    Split out so the row's width budget is derived from the string that will
    actually be rendered rather than from a constant someone estimated
    (pc-0ef6 codex pass, 2026-08-23). It returns unpainted text on purpose:
    the caller measures it with `visible_len` and paints at the point of use,
    so the measurement can never be taken over a string carrying SGR escapes
    that the width arithmetic would then count as columns.

    One definition, two readers — the loop below and the `widest_status`
    computation above it. That is the whole point: a second copy of this
    formatting is a second thing to drift, and the drift is silent because it
    only shows up as an off-by-N overflow at one specific width."""
    if h.get("status") in TERMINAL_STATUSES:
        return f"{b.g['ok']} done"
    target_d = date.fromisoformat(date_part(h["target"]))
    if milestone_is_overdue(h, cutoff_today):
        return f"{b.g['bad']} overdue {(today_d - target_d).days}d"
    remaining = (target_d - today_d).days
    return "due today" if remaining == 0 else f"{remaining}d left"


def gantt_track(b: "Board", width: int, segments: list[tuple[int, int, str, tuple[str, ...]]]) -> str:
    """Render one bar: `segments` are disjoint (start, end, glyph, style)
    ranges over [0, width); gaps between them are blank. Colouring per
    segment rather than per character keeps a no-colour render identical
    text (Board.paint is a no-op there), so this is the one code path for
    both — not an ANSI version and a plain one that could drift apart."""
    out: list[str] = []
    pos = 0
    for start, end, glyph, style in segments:
        if start > pos:
            out.append(" " * (start - pos))
        out.append(b.paint(glyph * (end - start), *style))
        pos = end
    if pos < width:
        out.append(" " * (width - pos))
    return "".join(out)


def gantt_ascii(heads: dict[str, dict[str, Any]], cfg: dict[str, Any],
                args: argparse.Namespace) -> int:
    """The default: a shared-axis ASCII gantt for a terminal, drawn with the
    same zero-dependency Board glyphs `board` uses. `gantt --mermaid` is the
    one for a pipeline; nobody reading a terminal wants Mermaid source."""
    stream = sys.stdout
    piped = not getattr(stream, "isatty", lambda: False)()
    color = board_supports_color(stream, False if args.no_color else None)
    width = args.width or (BOARD_PIPED_WIDTH if piped
                           else shutil.get_terminal_size((100, 24)).columns)
    b = Board(max(BOARD_MIN_WIDTH, min(width, BOARD_MAX_WIDTH)), color,
              board_supports_unicode(stream))
    name = safe_text(cfg.get("project_name") or ROOT.name, TITLE_CAP)

    milestones = [h for h in heads.values() if h.get("type") == "milestone"]
    if not milestones:
        b.rule(f"{name} milestones",
               f"no milestone records in this ledger ({len(heads)} records, "
               f"0 of type milestone) — nothing to chart")
        board_print(b, stream)
        return 0

    cutoff_today = date.today().isoformat()
    today_d = date.today()
    dated = sorted((h for h in milestones if h.get("target")),
                   key=lambda h: (h["target"], h["id"]))
    undated = sorted((h for h in milestones if not h.get("target")),
                     key=lambda h: (h.get("priority", 2), h["id"]))
    sep = b.g["sep"]
    note = (f"{len(dated)} dated {sep} {len(undated)} undated {sep} today {cutoff_today}"
            if undated else f"{len(dated)} {sep} today {cutoff_today}")
    b.rule(f"{name} milestones", note)

    if dated:
        id_width, type_width = board_columns(b, dated)
        starts = [date.fromisoformat(date_part(h["created"])) for h in dated]
        ends = [date.fromisoformat(date_part(h["target"])) for h in dated]
        domain_start = min(starts)
        span = max(1, (max(ends + [today_d]) - domain_start).days)
        # Everything around the track that isn't the track: 8 indent +
        # 10+10 dates + 2 brackets + 2 spacing + 2 spacing = 34 fixed, PLUS
        # the widest status text actually being rendered.
        #
        # THE STATUS WIDTH IS MEASURED, NOT ASSUMED (pc-0ef6 codex pass,
        # 2026-08-23). This was a flat 50, whose comment claimed "16
        # worst-case status text ("overdue 9999d" et al) — measured, not
        # guessed". It was guessed: the day count is unbounded, because a
        # target date is unbounded. A checker-clean milestone dated
        # 0001-01-01 renders "✗ overdue 739849d" — 17 columns, not 16 — and
        # produced a 61-column row on a width-60 board. "Measured" against a
        # worst case someone imagined is the same hand-counted constant this
        # file keeps being bitten by; measure the strings that exist instead.
        status_notes = {h["id"]: gantt_status_text(b, h, today_d, cutoff_today)
                        for h in dated}
        widest_status = max((visible_len(s) for s in status_notes.values()),
                            default=0)
        track_width = max(10, min(40, b.width - 34 - widest_status))

        def col(d: date) -> int:
            offset = max(0, min((d - domain_start).days, span))
            return round(offset / span * track_width)

        for h in dated:
            created_d = date.fromisoformat(date_part(h["created"]))
            target_d = date.fromisoformat(date_part(h["target"]))
            start_col = min(col(created_d), track_width - 1)
            end_col = max(start_col + 1, min(col(target_d), track_width))
            if h.get("status") in TERMINAL_STATUSES:
                kind = "done"
            elif milestone_is_overdue(h, cutoff_today):
                kind = "overdue"
            else:
                kind = "open"

            if kind == "done":
                segments = [(start_col, end_col, b.g["full"], ("green",))]
                glyph_key, style = "ok", "green"
            elif kind == "overdue":
                overrun_col = max(end_col, min(col(today_d), track_width))
                segments = [(start_col, end_col, b.g["full"], ("byellow",)),
                            (end_col, overrun_col, b.g["full"], ("bred",))]
                glyph_key, style = "bad", "bred"
            else:
                elapsed_col = max(start_col, min(col(today_d), end_col))
                segments = [(start_col, elapsed_col, b.g["full"], ("bcyan",)),
                            (elapsed_col, end_col, b.g["empty"], ("grey",))]
                glyph_key, style = "open", "grey"

            b.add(board_row(b, h, glyph_key, id_width, type_width))
            track = gantt_track(b, track_width, segments)
            bar = (f"        {date_part(h['created'])} {b.g['pipe']}{track}"
                   f"{b.g['pipe']} {date_part(h['target'])}")
            plain = status_notes[h["id"]]
            # Spill rather than clip, exactly as `rule` and `fit` do: a day
            # count is a NUMBER, and a truncated number is a false one. The
            # track floor (10 columns) can still leave no room for a very wide
            # status, so the status takes its own line instead of overflowing.
            if visible_len(bar) + 2 + visible_len(plain) <= b.width:
                b.add(f"{bar}  {b.paint(plain, style)}")
            else:
                b.add(bar)
                b.add(" " * 8 + b.paint(plain, style))

    if undated:
        b.add()
        b.add("   " + b.paint("no target date", "grey"))
        id_width, type_width = board_columns(b, undated)
        for h in undated:
            b.add(board_row(b, h, "open", id_width, type_width))

    board_print(b, stream)
    return 0


def cmd_gantt(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, _ = got
    cfg = load_config()
    # E018 (v2.8, pc-a5f8): gantt CONSTRUCTS calendar days from `created`
    # and `target`, and E001 checks dates by SHAPE alone (v1.12: whether a
    # date is real is rule-1 territory, not the checker's) — so a
    # check-clean 9999-99-99 milestone, reachable through the ordinary
    # write path, died here as a fatal E000 ValueError in the ASCII mode
    # and flowed into `--mermaid` output as an impossible dateFormat value:
    # a crash and a lie, both on certified state. A projection refuses
    # structurally, BEFORE either rendering — a finding naming the record
    # and field at exit 1, so no chart is half-drawn and nothing impossible
    # is emitted for a downstream renderer to choke on.
    unprojectable = []
    for rid, head in sorted(heads.items()):
        if head.get("type") != "milestone":
            continue
        for field in ("created", "target"):
            # PRESENCE, NOT TRUTHINESS (pc-5706, round-12 lane E2-F1). This
            # scan opened `value = head.get(field)` / `if not value or not
            # isinstance(value, str): continue`, and that guard discarded
            # exactly the states the branch below was written for. An
            # EMPTY-STRING target is falsy, so both renderers charted it as
            # "no target date" at exit 0 — reading a stored bad value as an
            # absent one, while `check` calls it PRESENT and invalid
            # ("target, when present, must be a YYYY-MM-DD string"). A
            # NON-STRING target is truthy, so it flowed past into
            # date.fromisoformat and into `<` against a date string, and
            # both renderers died E000 at exit 2 on certified-as-present
            # state. A cleared optional field is ABSENCE, never null or ""
            # (PRESENCE_FIELDS, v2.6), so PRESENCE is the honest test and
            # anything present that is not a placeable date is this
            # projection's refusal to make.
            if field not in head:
                # `target` is optional (rule 3) and an absent one is the
                # undated milestone both renderers already chart as such.
                # `created` is required — E001 refuses a record without it —
                # and gantt_ascii indexes h["created"] directly, so its
                # absence was a KeyError E000 rather than a chart (v2.17).
                if field == "target":
                    continue
                unprojectable.append(finding(
                    "error", "E018", rid,
                    f"created is ABSENT — it is a required field, `pecia "
                    f"check` refuses this record with E001, and the chart "
                    f"reads it directly, so there is no day to start the "
                    f"bar from. It reached the projection through a write "
                    f"that bypassed the gate or an import that never ran "
                    f"it; restore the created date (v2.17)"))
                continue
            value = head[field]
            # THE MESSAGE SAYS WHICH STATE THIS IS (pc-3942, round-8 lane
            # E2-F1). E018 exists for a value that IS date-shaped and
            # names no calendar day — `check` passes it at exit 0, which
            # is what makes the projection the only place it can be
            # caught. A value that is not date-shaped at all is a
            # DIFFERENT state: `check` refuses it with E001, and it
            # reaches here only through a write that bypassed the gate or
            # an import that never ran it. Both got the same sentence,
            # whose text asserted that the value is "date-shaped but not
            # a calendar day" and that "the structural gates accept it" —
            # both false of the second state, and the second half told
            # the reader the checker accepted something the checker
            # rejects. The verdict and the remedy were right throughout;
            # this is the diagnostic catching up with them.
            if isinstance(value, str) and DATE_RE.fullmatch(value):
                try:
                    date.fromisoformat(date_part(value))
                except ValueError:
                    unprojectable.append(finding(
                        "error", "E018", rid,
                        f"{field} {render_value(date_part(value))} is date-shaped but "
                        f"not a calendar day — the structural gates accept "
                        f"it (E001 is shape-only, v1.12) and no chart can "
                        f"place it; repair the {field} (v2.8)"))
                continue
            # Not date-shaped at all, which now reaches here for every way
            # of being so: a non-date string, the empty string, and a value
            # that is not a string (v2.17). The type is named because
            # `''` and `42` are indistinguishable from their repr alone
            # once safe_text has stringified them.
            unprojectable.append(finding(
                "error", "E018", rid,
                f"{field} {render_value(safe_text(value, DATE_CAP))} "
                f"({json_type(value)}) is not date-shaped, so no chart "
                f"can place it — and unlike the shape-valid case this is "
                f"NOT state the structural gates accept: `pecia check` "
                f"refuses this record with E001, which is where to start. "
                f"It reached the projection through a write that bypassed "
                f"the gate or an import that never ran it; repair the "
                f"{field} (v2.13)"))
    if unprojectable:
        for f in unprojectable:
            emit(f)
        return 1
    if args.mermaid:
        out_line("\n".join(gantt_mermaid_lines(heads, cfg)))
        return 0
    return gantt_ascii(heads, cfg, args)


# D12 (pc-0afd): the day the retires edge landed (v1.13). A record closed
# before it CANNOT have carried the edge its disposition lacks, so its
# prose-only linkage is a fact about history, not rot.
RETIRES_LANDED = "2026-08-11"


def audit_findings(heads: dict[str, dict[str, Any]], all_records: list[dict[str, Any]],
                   cfg: dict[str, Any], sample: int = 0,
                   resolve: bool = True, historical: bool = False) -> list[dict[str, Any]]:
    """The advisory anti-rot surface, as data.

    Shared by `audit` and `board` so the human projection cannot drift from
    the machine one — the drift class this repo keeps catching in prose.

    `resolve=False` is board's calling convention (D9, pc-5422, closing
    pc-cb50): a human projection must be safe to run, and a configured
    resolver is an arbitrary command executed with the caller's privileges.
    board therefore marks each declared foreign reference `not attempted`
    and points at `audit`, which is what resolution is for. This is the
    ONLY branch on the flag — everything else the two surfaces show stays
    shared, which is the drift guarantee this function exists to give."""
    stale_days = int(cfg.get("stale_days", 7))
    rot_days = int(cfg.get("rot_days", 14))
    cutoff_stale = (date.today() - timedelta(days=stale_days)).isoformat()
    cutoff_rot = (date.today() - timedelta(days=rot_days)).isoformat()
    cutoff_today = date.today().isoformat()
    out: list[dict[str, Any]] = []
    titles: dict[str, list[str]] = {}
    blocked_by = compute_blockers(heads)
    min_chars = int(cfg.get("min_disposition_chars", 20))
    known = set(heads)
    # Linkage for the prose-only scan is UNDIRECTED, and it has to be. The
    # relation is recorded once, on one end; which end is a representation
    # choice the finding has no business caring about. Read directionally it
    # fires on every record `--also-closes` writes — the target's disposition
    # says "Retired by <retirer>" while the `retires` edge lives on the
    # retirer — so the sugar would manufacture the finding it exists to
    # prevent. Found by this change's own control test.
    linkage: dict[str, set[str]] = {rid: set() for rid in heads}
    for rid, h in heads.items():
        e = h.get("edges") or {}
        targets = {t for k in LIST_EDGES for t in (e.get(k) or []) if isinstance(t, str)}
        targets |= {e[k] for k in SCALAR_EDGES if isinstance(e.get(k), str)}
        for t in targets:
            linkage.setdefault(rid, set()).add(t)
            linkage.setdefault(t, set()).add(rid)
    # D12's operational definition (VP12): a record is grandfathered by the
    # date of its FIRST terminal revision, never by the head's `updated` —
    # a later custody edit to a closed record must not un-grandfather it.
    first_terminal: dict[str, str] = {}
    for rec in all_records:
        rec_id = rec.get("id")
        if rec.get("status") in TERMINAL_STATUSES and isinstance(rec_id, str):
            when = rec.get("updated") or ""
            if when and (rec_id not in first_terminal or when < first_terminal[rec_id]):
                first_terminal[rec_id] = when
    grandfathered_count = 0
    for rid, h in sorted(heads.items()):
        edges = h.get("edges") or {}
        has_edge = any(edges.get(k) for k in EDGE_KEYS)
        if (h.get("type") in {"task", "defect"} and h.get("status") not in TERMINAL_STATUSES
                and not has_edge and not edges.get("no_edges")):
            out.append(audit_finding("untriaged", rid))
        if h.get("status") == "in-progress" and h.get("updated", "") < cutoff_stale:
            out.append(audit_finding("stale-in-progress", rid, since=h.get("updated")))
        if (h.get("type") == "defect" and h.get("status") not in TERMINAL_STATUSES
                and h.get("evidence") == "unknown" and h.get("created", "") < cutoff_rot):
            out.append(audit_finding("aging-unknown-evidence", rid))
        if (h.get("status") == "open" and h.get("priority", 2) <= 1
                and h.get("created", "") < cutoff_rot):
            out.append(audit_finding("priority-rot", rid, priority=h.get("priority"),
                                     created=h.get("created")))
        if milestone_is_overdue(h, cutoff_today):
            out.append(audit_finding("overdue-milestone", rid,
                                     target=h.get("target"), status=h.get("status")))
        # v2.5 (pc-813f, closing pc-ff8f). Machine authorship is read off the
        # vendor-qualified owner convention (AGENTS.md): a `:` in the owner
        # string. Terminal decisions only — an open decision has decided
        # nothing yet, so there is nothing to ratify.
        if (h.get("type") == "decision" and h.get("status") in TERMINAL_STATUSES
                and ":" in (h.get("owner") or "") and not h.get("ratified_by")):
            out.append(audit_finding("unratified-decision", rid,
                                     owner=h.get("owner")))
        if h.get("status") in TERMINAL_STATUSES:
            for blocker in blocked_by.get(rid, []):
                out.append(audit_finding("closed-while-blocked", rid, blocker=blocker))
            disp = h.get("disposition")
            if isinstance(disp, str):
                # The metric says how short; the text says what it failed to
                # say, which is what the reader fixes (v3.3, pc-ded91385e31a).
                chars, words = len(disp.strip()), len(disp.strip().split())
                if chars < min_chars or words < 3:
                    out.append(audit_finding("inadequate-disposition", rid,
                                             disposition_chars=chars,
                                             disposition_words=words,
                                             min_chars=min_chars,
                                             disposition=disp))
        # Prose-only linkage: the disposition names records this one has no
        # edge to. Not restricted to terminal records — `edit --disposition`
        # can set one on an open record, and the gap is the same there.
        named, unresolved = ids_named_in(h.get("disposition"), known)
        unlinked = [i for i in named
                    if i != rid and i not in linkage.get(rid, set())]
        if unlinked:
            closed_pre_retires = (h.get("status") in TERMINAL_STATUSES
                                  and first_terminal.get(rid, "") != ""
                                  and first_terminal[rid] < RETIRES_LANDED)
            if closed_pre_retires and not historical:
                grandfathered_count += 1
            else:
                out.append(audit_finding("prose-only-linkage", rid,
                                         ids=", ".join(unlinked),
                                         unresolved=", ".join(unresolved) or "none"))
        titles.setdefault(h.get("title", "").casefold().strip(), []).append(rid)
    for title, ids in sorted(titles.items()):
        if title and len(ids) > 1:
            out.append(audit_finding("possible-duplicates", ids[0],
                                     ids=", ".join(sorted(ids))))
    if grandfathered_count:
        out.append(audit_finding("historical-prose-only-linkage", None,
                                 count=grandfathered_count,
                                 cutoff=RETIRES_LANDED))
    # Escape-hatch enumeration (spec/pecia.als T10): every forced revision in
    # HISTORY, not just heads — the brand is custody, and custody is append-only.
    for rec in all_records:
        if rec.get("forced") is True:
            # `owner` is bounded, not withheld: who forced the write is the
            # entire point of the finding. Bounded means the string cannot
            # break its frame — it does not mean the name is true. A forced
            # revision claiming owner "release-manager" is exactly as
            # trustworthy as the record it brands.
            out.append(audit_finding("forced-write", rec.get("id"),
                                     rev=rec.get("rev"), owner=rec.get("owner"),
                                     updated=rec.get("updated")))
    # Historical custody violations (v1.4, review-3 F11): E006/E007 are
    # state checks on heads; a repaired head is legitimately clean, but the
    # violating revision stays visible here forever — repaired, not laundered.
    for rec in all_records:
        head = heads.get(rec.get("id"))
        if head is not None and head.get("rev") == rec.get("rev"):
            continue
        if rec.get("status") in TERMINAL_STATUSES and not (rec.get("disposition") or "").strip():
            out.append(audit_finding("historical-custody-violation", rec.get("id"),
                                     rev=rec.get("rev"),
                                     violation="terminal without disposition"))
        if (rec.get("type") == "defect" and rec.get("status") == "done"
                and not evidence_is_structural(rec.get("evidence"), cfg)):
            out.append(audit_finding("historical-custody-violation", rec.get("id"),
                                     rev=rec.get("rev"),
                                     violation="done defect without structural evidence"))
    # Foreign-reference resolution (v1.7, pc-4d1e). This is the truth half of
    # the split: `check` certified the scheme is declared, `audit` asks the
    # declared verifier whether the referent is actually there. Advisory by
    # construction — an unreachable sibling system is not a defect in this
    # ledger, and must never redden a pre-commit gate.
    declared = resolvers(cfg)
    for rid, h in sorted(heads.items()):
        ref = parse_foreign_ref(h.get("evidence"))
        if not ref or ref[0] not in declared:
            continue  # undeclared schemes are E011's job, not a second report
        scheme, target = ref
        if not resolve:
            # `field` says WHICH reference is unattempted (pc-5893): this
            # loop reads `evidence`, the context loop below reads `context`,
            # and the shared template used to hardcode "evidence" for both.
            out.append(audit_finding("reference-not-attempted", rid,
                                     field="evidence", scheme=scheme))
            continue
        ok, reason, exit_code, stderr = resolve_reference(declared[scheme], target)
        if not ok:
            out.append(audit_finding("unresolvable-reference", rid, scheme=scheme,
                                     target=target, reason=reason,
                                     exit="n/a" if exit_code is None else exit_code,
                                     stderr=stderr))
    # Context references resolve through the same registry (v2.5, pc-813f,
    # closing pc-7ab3), under the same advisory-by-construction rule and the
    # same board exemption: a missing orientation document is a fact about
    # the tree, never grounds to redden a gate or to execute from `board`.
    for rid, h in sorted(heads.items()):
        ref = parse_foreign_ref(h.get("context"))
        if not ref or ref[0] not in declared:
            continue  # an undeclared scheme is E011's job, not a second report
        scheme, target = ref
        if not resolve:
            out.append(audit_finding("reference-not-attempted", rid,
                                     field="context", scheme=scheme))
            continue
        ok, reason, exit_code, stderr = resolve_reference(declared[scheme], target)
        if not ok:
            out.append(audit_finding("unresolvable-context", rid, scheme=scheme,
                                     target=target, reason=reason,
                                     exit="n/a" if exit_code is None else exit_code,
                                     stderr=stderr))

    # Evidence-command resolution (v1.8, pc-2251). The truth half of E007's
    # split, and the home of every host read that used to live in `check`:
    # the shape was certified off the ledger, and here we ask this machine
    # whether the command is actually runnable. Advisory by construction — a
    # command missing from THIS checkout is a fact about the checkout, and
    # must never redden a pre-commit gate on a ledger that is fine.
    # Self-addressed imperatives in terminal dispositions (pc-89a9).
    for rid, h in sorted(heads.items()):
        if h.get("status") not in TERMINAL_STATUSES:
            continue
        disp = h.get("disposition")
        if not isinstance(disp, str):
            continue
        m = IMPERATIVE_RE.search(disp)
        if not m:
            continue
        cited = False
        for path in sorted(tracked_test_paths()):
            try:
                # Sibling of pc-34b5's shape, fixed in the same commit: the
                # strict read_text() meant one non-UTF-8 test file crashed
                # `audit` (and `board`, which calls audit_findings) as fatal
                # E000 — UnicodeDecodeError is a ValueError, so the OSError
                # net below never caught it. The scan is a substring test;
                # surrogateescape preserves exactly the id-visibility
                # question being asked.
                if rid in Path(path).read_bytes().decode(
                        "utf-8", errors="surrogateescape"):
                    cited = True
                    break
            except OSError:
                continue
        if not cited:
            out.append(audit_finding("unverified-imperative", rid,
                                     phrase=m.group(0).lower(),
                                     reason="no test file names this record"))

    for rid, h in sorted(heads.items()):
        token = evidence_command_head(h.get("evidence"), cfg)
        if token is None:
            continue
        # D7 (pc-3c0e): `check` and this finding must agree on what a legal
        # head is (VP12), and check's definition — the spec's — accepts a
        # unittest node id and tolerates a trailing shell separator. So the
        # FILE half is what gets resolved: strip a trailing `;`, then take
        # the path before `::`. The class name after `::` is deliberately
        # NOT verified — a deleted test class resolves green here until the
        # evidence-runner task (pc-f844) lands, which is where truth beyond
        # existence belongs.
        resolvable = token.rstrip(";").split("::", 1)[0]
        if shutil.which(resolvable) is None and not (ROOT / resolvable).exists():
            out.append(audit_finding("unresolvable-evidence", rid,
                                     evidence_kind=evidence_kind(h, cfg),
                                     reason="does not resolve: neither on PATH nor "
                                            "a path in this tree — the evidence "
                                            "cannot be run here",
                                     evidence=h.get("evidence")))

    if sample:
        closed = sorted((h for h in heads.values() if h.get("status") in TERMINAL_STATUSES),
                        key=lambda h: hashlib.sha256(h["id"].encode()).hexdigest())
        for h in closed[:sample]:
            # What the reader verifies, quoted: the disposition's claim and the
            # evidence offered for it (v3.3, pc-ded91385e31a).
            disp = h.get("disposition")
            out.append(audit_finding("truth-audit-sample", h["id"], rev=h.get("rev"),
                                     disposition_chars=_chars(disp),
                                     disposition_words=_words(disp),
                                     evidence_kind=evidence_kind(h, cfg),
                                     disposition=disp, evidence=h.get("evidence")))
    return out


def cmd_audit(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, all_records = got
    out = audit_findings(heads, all_records, load_config(), args.sample,
                         historical=args.historical)
    emit({"advisory": True, "note": "audit findings never block; they surface frontier-vs-rot",
          "findings": out})
    return 0


# -- board: the human projection (zero-dependency ANSI) ----------------------
#
# `board` is a projection, like `gantt` and `graph --format mermaid`, and so
# emits its own format rather than JSON. The machine contract is unchanged:
# every *query* (ready/blocked/next/audit/check) still emits JSON.
#
# It is read-only by construction — it opens no write path, so it cannot
# become a second way to mutate the ledger (PLAN.md, second-ledger drift).
#
# Zero dependencies is a design constraint, not thrift: the standalone claim
# is load-bearing, and hand-rolled SGR survives the M5 Rust-port question
# where a rendering library would be thrown away.

BOARD_PIPED_WIDTH = 80
# THE CLAMP IS NAMED SO THE HELP CAN BE GENERATED FROM IT (v2.16, pc-fa6c).
# `--width` was documented as an override with no floor stated anywhere, and
# both renderers silently raised anything below 60 — `--width 20` emitted
# lines of 60, three times the request. The floor is real (the frame's own
# fixed columns do not fit below it) and the ceiling is real (a projection
# wider than this stops being readable), so what was wrong is that neither
# was written down. They are written down by being PRINTED: the help text
# below interpolates these, which is round 11's rule that a number beside
# machinery is stale the moment the machinery moves.
BOARD_MIN_WIDTH = 60
BOARD_MAX_WIDTH = 160
SGR = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
       "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
       "bred": "91", "bgreen": "92", "byellow": "93", "bblue": "94",
       "bmagenta": "95", "bcyan": "96"}
PRIORITY_STYLE = {0: ("bred", "bold"), 1: ("byellow",), 2: ("bcyan",), 3: ("grey",)}
TYPE_STYLE = {"defect": ("red",), "task": ("blue",), "question": ("magenta",),
              "decision": ("green",), "milestone": ("yellow",)}
STATUS_STYLE = {"open": ("byellow",), "in-progress": ("bcyan", "bold"),
                "done": ("green",), "dropped": ("grey",), "superseded": ("magenta",)}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;.*?\x1b\\")
UNI_GLYPHS = {"h": "─", "rule": "═", "tee": "├", "end": "└", "pipe": "│",
              "full": "█", "empty": "░", "open": "○", "ready": "●",
              "active": "◐", "ok": "✓", "bad": "✗", "sep": "·"}
ASCII_GLYPHS = {"h": "-", "rule": "=", "tee": "|-", "end": "`-", "pipe": "|",
                "full": "#", "empty": ".", "open": "o", "ready": "*",
                "active": "@", "ok": "ok", "bad": "X", "sep": "-"}


def char_cols(ch: str) -> int:
    """Terminal columns one character occupies: 0, 1, or 2."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_len(text: str) -> int:
    """Width as the terminal sees it — escape sequences occupy no columns.

    COLUMNS, NOT CODE POINTS (pc-0ef6 codex pass, 2026-08-23). This was
    `len(ANSI_RE.sub("", text))`, which is right only for the Latin-1-ish
    subset. A CJK ideograph occupies TWO terminal columns, and a combining
    mark occupies none, so `len` under-counts one and over-counts the other
    and every width budget derived from it was wrong by that margin: a
    `project_name` of thirty `界` measured 60 and rendered 90 columns on a
    width-60 board.

    `safe_text` already strips Cc/Cf/Cs, so zero-width *format* characters
    never reach here; combining marks (Mn) are not stripped and are the live
    zero-width case. Wide/Fullwidth East Asian is the live double-width case.
    This deliberately does not attempt grapheme clustering or emoji ZWJ
    sequences — that needs a real Unicode segmentation table, which this
    zero-dependency file does not have. What it buys is that the common
    cases stop lying; it is not a claim of full correctness."""
    return sum(char_cols(c) for c in ANSI_RE.sub("", text))


def board_supports_color(stream: Any, override: bool | None = None) -> bool:
    if override is not None:
        return override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def board_supports_unicode(stream: Any) -> bool:
    return (getattr(stream, "encoding", None) or "").lower().replace("-", "").startswith("utf")


class Board:
    """Accumulates styled lines. Every style call is a no-op when colour is
    off, so the plain rendering is the same layout, not a second one."""

    def __init__(self, width: int, color: bool, unicode_ok: bool) -> None:
        self.width = width
        self.color = color
        self.g = UNI_GLYPHS if unicode_ok else ASCII_GLYPHS
        self.lines: list[str] = []

    def paint(self, text: str, *names: str) -> str:
        codes = [SGR[n] for n in names if n in SGR]
        if not self.color or not codes or not text:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"

    def link(self, url: str, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def rule(self, label: str, note: str = "", heavy: bool = False) -> None:
        """A section rule: `── label ───────── note`.

        THE BUDGET IS MEASURED OFF THE COMPOSED LINE, never off a frame
        constant (codex pass, 2026-08-23). The first repair here carried
        `RULE_FRAME = 7` / `RULE_FRAME_BARE = 5`, derived by hand from the
        format string — and both survived mutation, because the hand
        derivation had missed that `render()` rstrips every line, so the
        tail's trailing space costs nothing and the true frame is 6, not 7.
        The constants were a column conservative, harmlessly; that they could
        be wrong by a column *and* have no test able to see it is the finding.

        A constant that restates what the format string already says is a
        second copy, and this file's whole history of overflow is copies
        drifting apart (`fit`: "every overflow this board has had was a format
        string and a parallel column tally"). So there is no constant now:
        compose the line, measure it, and yield if it does not fit."""
        glyph = self.g["rule"] if heavy else self.g["h"]

        # NO TRAILING SPACE AFTER THE NOTE (codex pass, 2026-08-23). The tail
        # was `f" {nt} "`, and `composed` rstripped that final space while the
        # PAINTED line could not: `paint` puts a reset code after it, so
        # `render`'s rstrip sees `\x1b[0m`, not whitespace. The measurement was
        # therefore one column short of the truth whenever colour was on, and
        # a width-60 board emitted a 61-column header. Removing the space
        # rather than teaching two places to strip it keeps ONE definition of
        # the line: plain and painted now differ only by escape sequences,
        # which is the invariant `composed` claims.
        def composed(lab: str, nt: str) -> str:
            """Exactly what `add` below will append, minus the styling —
            `paint` is a no-op without colour and `visible_len` strips SGR, so
            this measures the real rendered line, in both colour modes."""
            tail = f" {nt}" if nt else ""
            fill = max(1, self.width - (visible_len(lab) + 4) - visible_len(tail))
            return f"{glyph * 2} {lab} " + glyph * fill + tail

        spill = ""
        if note and visible_len(composed(label, note)) > self.width:
            spill, note = note, ""
        over = visible_len(composed(label, note)) - self.width
        if over > 0:  # only the label can still be too wide
            label = budget_clip(label, max(1, visible_len(label) - over))

        tail = f" {note}" if note else ""
        fill = max(1, self.width - (visible_len(label) + 4) - visible_len(tail))
        self.add(self.paint(glyph * 2 + " ", "grey")
                 + self.paint(label, "bold")
                 + self.paint(" " + glyph * fill, "grey")
                 + (self.paint(tail, "grey") if tail else ""))
        for line in self._spill(spill):
            self.add(self.paint(line, "grey"))

    def _spill(self, note: str) -> list[str]:
        """A note too wide to sit beside its label moves to its own line rather
        than being cut — `fit`'s rule for a wide id column, applied to the rule
        line ("below `floor` remaining columns the text moves to its own line
        instead").

        WHY IT SPILLS INSTEAD OF CLIPPING (pc-0ef6 retro-verification,
        2026-08-22). `fill` was `max(1, width - head - tail)`: the floor
        guarantees a fill glyph but clips nothing, so any label+note pair wider
        than the board rendered a line LONGER than the width asked for. The
        first repair here clipped the note — and the suite immediately caught
        why that is wrong: `EmptyProjectionsDeclareThemselves` exists because a
        projection that renders successfully while conveying nothing reads as
        green, and the note it checks for IS that declaration
        ("… — nothing to chart"). Truncating it to `…` destroys precisely the
        signal that class was built to protect. A note is supplementary in
        LAYOUT and sometimes load-bearing in MEANING; only the layout may
        yield. Spilling honours the budget and keeps every word."""
        if not note:
            return []
        indent = " " * 3
        budget = max(1, self.width - len(indent))
        # textwrap counts code points, so a wrapped line of CJK still doubles
        # its column count. Wrap first, then column-fit each result — a second
        # pass rather than a smarter wrapper, because textwrap's algorithm is
        # what keeps word boundaries and this file has no Unicode line-break
        # table to replace it with.
        return [indent + budget_clip(ln, budget)
                for ln in textwrap.wrap(safe_text(note), budget)]

    def render(self) -> str:
        return "\n".join(line.rstrip() for line in self.lines)


def board_print(b: "Board", stream: Any) -> None:
    """Render and print, lossy-but-total on encoding: a report must not be the
    thing that fails. Shared by `board` and `gantt`'s ASCII default so a
    legacy-encoding terminal degrades the same way for both — record titles
    are ledger content and routinely carry em-dashes."""
    out_line(b.render())


def clip(text: str, width: int) -> str:
    """Truncate plain text to width, marking the cut. Never called on styled
    text — measuring a truncated escape sequence is how box drawing breaks.

    The board's choke point for the output boundary (pc-cdb8): safe_text runs
    first, so no ledger string can drive the reader's terminal. That mattered
    more here than in the JSON commands — an ANSI escape in a title reached a
    tty directly, and `visible_len` would then have mis-measured its own
    frame."""
    text = safe_text(text)
    if width <= 1 or visible_len(text) <= width:
        return text
    return take_cols(text, width - 1) + "…"


def take_cols(text: str, cols: int) -> str:
    """The longest prefix of `text` occupying at most `cols` terminal columns.

    Slicing by index is what `clip` used to do, and it is wrong for the same
    reason `len` was (see `visible_len`): `text[:n]` counts code points, so a
    prefix of CJK text takes twice the columns intended. A double-width
    character that would straddle the boundary is dropped rather than split —
    there is no half-column to render it in."""
    out, used = [], 0
    for ch in text:
        w = char_cols(ch)
        if used + w > cols:
            break
        out.append(ch)
        used += w
    return "".join(out)


def budget_clip(text: str, width: int) -> str:
    """`clip`, made TOTAL at every width. `clip` deliberately returns the text
    unchanged at `width <= 1` — a one-column cell has nothing useful to say and
    its callers all have a floor under them. `Board.rule` has no such floor (a
    caller may pass any `--width`), so a truncation that silently declines to
    truncate would reintroduce exactly the overflow it is there to prevent.
    Split out rather than folded into `clip` so the pc-cdb8 choke point keeps
    its measured behaviour.

    (Named `Board._rule_budget` here until 2026-08-23 — a method that had been
    renamed mid-fix, leaving a docstring pointing at nothing. The same dead
    name reached the ledger and claims.yaml and was caught there by the codex
    pass, not here.)"""
    text = safe_text(text)
    if width <= 0:
        return ""
    if visible_len(text) <= width:
        return text
    return take_cols(text, width - 1) + "…" if width > 1 else "…"


def fit(b: Board, prefix: str, text: str, suffix: str = "", floor: int = 10,
        style: tuple[str, ...] = ()) -> str:
    """Compose `prefix + text + suffix` inside the board's width, clipping only
    the free text. The budget is measured off the rendered prefix and suffix,
    never hand-counted: every overflow this board has had was a format string
    and a parallel column tally drifting apart (the `p{N}` gutter went
    uncounted, and an audit row budgeted a constant where an id's width
    belonged). Below `floor` remaining columns the text moves to its own line
    instead: a wide id column is the one thing that can eat a whole row, and
    an id is typed back into `pecia edit`, so it is never what gets cut."""
    room = b.width - visible_len(prefix) - visible_len(suffix)
    # Styling happens after the cut, never before: `clip` must not be handed a
    # string whose escape sequences it would count as columns and truncate.
    if room < floor:
        indent = " " * 8
        room = b.width - len(indent) - visible_len(suffix)
        return (prefix.rstrip() + "\n" + indent
                + b.paint(clip(text, max(1, room)), *style) + suffix)
    return prefix + b.paint(clip(text, max(floor, room)), *style) + suffix


def board_wrapped(b: Board, prefix: str, parts: list[str], sep: str) -> None:
    """Emit `prefix` then `parts` joined by `sep`, breaking between parts so no
    line exceeds the width, with continuations aligned under the first part.
    These rows are counts — every part is data, so the budget is met by
    wrapping and never by dropping one."""
    indent = " " * visible_len(prefix)
    line, first = prefix, True
    for part in parts:
        if not first and visible_len(line) + visible_len(sep + part) > b.width:
            b.add(line)
            line = indent + part
        else:
            line += part if first else sep + part
        first = False
    b.add(line)


def pad_to(b: Board, text: str, width: int, *style: str) -> str:
    """Style the CONTENT; pad OUTSIDE the styling.

    `b.paint(x.ljust(w))` puts the padding INSIDE the SGR span, so the string
    ends with a reset code rather than whitespace — and `rstrip`, which both
    `fit`'s continuation path and `Board.render` rely on to reclaim trailing
    layout, then silently reclaims nothing. The result is a defect that only
    exists on a real terminal: piped output has no SGR, so `rstrip` works and
    every width test in this repo passes. Every width test in this repo is
    piped. Found by the codex pass 2026-08-23, which drove a colour TTY and
    saw an ordinary `pc-short` dependency row padded to 95 columns at
    `--width 60`.

    Columns, not code points (`visible_len`), for the same reason as
    everywhere else here."""
    return b.paint(text, *style) + " " * max(0, width - visible_len(text))


def column_width(values: Any) -> int:
    """The widest of `values` in TERMINAL COLUMNS.

    Every shared column in this file was `max(len(...))`. `len` is code
    points, so a CJK `type` or id measured half its rendered width and the
    column it sized was too narrow by that much — a checker-clean record
    with a 30-ideograph type (declared via `extra_types`) pushed `board`
    to 73 columns at `--width 60`. Same defect class as `visible_len`
    itself had; fixed there and not at the call sites, until now."""
    return max((visible_len(v) for v in values), default=1)


def board_columns(b: Board, rows: Any) -> tuple[int, int]:
    """The shared (id, type) column widths, allocated TOGETHER.

    Jointly, because they compete for one budget and the tie-break between
    them is a real decision rather than an implementation detail: the TYPE
    yields, because a type is display vocabulary and is clipped to its column,
    while an id is never cut (`fit`: an id is typed back into `pecia edit`).

    Two separate caps could not express that, and trying produced exactly the
    bug this replaces — `board_type_width` reserved a single column for the id
    because `type_width` is computed first, so with a real 7-column id every
    row ran 6 over. Allocating in one place also removes the ordering hazard
    that the codex pass probed as D5: there is no longer a width computed from
    another width that might not be computed yet.

    Both are capped against the same MEASURED furniture (rendering the prefix
    empty rather than counting the format string). A row whose own id exceeds
    the shared cap keeps it whole — `ljust` below a string's length is a no-op
    — and yields its title to `fit`'s continuation line, which is the
    documented long-id exception."""
    ids = [safe_id(r.get("id", "")) for r in rows]
    types = [safe_text(r.get("type", ""), VOCAB_CAP) for r in rows]
    fixed = visible_len(board_row_prefix(b, "", 0, "", 0, 2))
    room = max(2, b.width - fixed)
    id_w = max(1, min(column_width(ids), room - 1))
    type_w = max(1, min(column_width(types), room - id_w))
    return id_w, type_w


def board_row_prefix(b: Board, rid: str, id_width: int, rtype: str,
                     type_width: int, priority: int, glyph_key: str = "open") -> str:
    """The fixed furniture of a board row, up to the title. Extracted so its
    width can be MEASURED by `board_columns` instead of re-counted.

    Padding goes through `pad_to`, i.e. OUTSIDE the SGR span — see `pad_to`
    for why `paint(x.ljust(w))` is a colour-only overflow bug."""
    gutter = b.paint(f"p{priority}", *PRIORITY_STYLE.get(priority, ("bcyan",)))
    mark = b.paint(b.g[glyph_key], *PRIORITY_STYLE.get(priority, ("bcyan",)))
    # The TYPE is clipped to its column; the ID is not. That asymmetry is the
    # point (codex pass, 2026-08-23): an id is typed back into `pecia edit`, so
    # cutting it produces a string that still looks like an id and is wrong —
    # `fit` says so and this file has honoured it since pc-6c8b. A type is
    # display vocabulary, already length-bounded by VOCAB_CAP upstream, and
    # nothing is retyped from it. Before this, a checker-clean record whose
    # `type` was 30 CJK ideographs (legal via `extra_types`) rendered 60
    # columns against a column measured at 30 and pushed `board` to 73 at
    # `--width 60`.
    return (f"   {gutter} {mark} "
            + pad_to(b, rid, id_width, "bold") + " "
            # budget_clip, NOT clip: `clip` returns the text unchanged at
            # width <= 1, and a squeezed board really does allocate a
            # one-column type column (a 53-character id leaves nothing else).
            # `clip` there rendered the type in full and put the row 3 columns
            # over — the precise quirk `budget_clip` was split out for, missed
            # at the one call site that reaches it.
            + pad_to(b, budget_clip(rtype, type_width), type_width,
                     *TYPE_STYLE.get(rtype, ()))
            + "  ")


def board_row(b: Board, head: dict[str, Any], glyph_key: str, id_width: int,
              type_width: int) -> str:
    row = project("board", head)
    priority = row["priority"] if row["priority"] is not None else 2
    rid, rtype = row["id"] or "?", row["type"] or "?"
    # main's measured width budget (pc-6c8b), fed the PROJECTED title: `fit`
    # routes it through `clip`, which is the board's safe_text choke point.
    return fit(b, board_row_prefix(b, rid, id_width, rtype, type_width,
                                   priority, glyph_key), row["title"])


def board_distribution(b: Board, heads: dict[str, dict[str, Any]]) -> None:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for h in heads.values():
        # Vocabulary values become legend labels on a tty, and a record whose
        # `type` is out of vocabulary still reaches here (require_heads gates
        # on soundness, not on E001) — so these are bounded like any other
        # ledger string.
        status = safe_text(h.get("status", "?"), VOCAB_CAP) or "?"
        rtype = safe_text(h.get("type", "?"), VOCAB_CAP) or "?"
        by_status[status] = by_status.get(status, 0) + 1
        by_type[rtype] = by_type.get(rtype, 0) + 1
    total = max(1, sum(by_status.values()))
    sep = b.paint(f" {b.g['sep']} ", "grey")
    order = sorted(by_status, key=lambda s: (-by_status[s], s))
    legend = [b.paint(f"{by_status[s]} {s}", *STATUS_STYLE.get(s, ())) for s in order]
    lead = f"   {b.paint('status', 'grey')}  "
    # The bar is decoration and the counts are data, so the bar yields: size it
    # from what the legend actually needs rather than a fixed allowance, and
    # drop it below the width where it would stop reading as a proportion. A
    # legend that still does not fit wraps; it never loses a status.
    room = (b.width - visible_len(lead) - 2
            - sum(visible_len(x) for x in legend) - visible_len(sep) * (len(legend) - 1))
    bar_width = min(40, room) if room >= 10 else 0
    bar = ""
    if bar_width:
        # Largest-remainder so the bar's cell count is exactly bar_width and no
        # non-empty status silently rounds away to nothing.
        exact = {s: by_status[s] * bar_width / total for s in by_status}
        cells = {s: max(1, int(exact[s])) for s in by_status}
        while sum(cells.values()) > bar_width:
            cells[max(cells, key=lambda s: (cells[s], s))] -= 1
        for status in order:
            bar += b.paint(b.g["full"] * cells[status], *STATUS_STYLE.get(status, ()))
        bar += "  "
    board_wrapped(b, lead + bar, legend, sep)
    # Each part is CLIPPED BEFORE PAINTING (codex pass, 2026-08-23).
    # `board_wrapped` breaks BETWEEN parts, so a single part wider than the
    # board cannot be wrapped and was emitted whole — a checker-clean 30-
    # ideograph `type` (legal via `extra_types`) rendered a 73-column legend
    # line on a 60-column board. Clipping here rather than inside
    # `board_wrapped` because these parts arrive painted, and `clip` must
    # never be handed styled text: it would count the escape sequences as
    # columns and cut inside one. Same rule as the row's type column — a type
    # is display vocabulary and may be clipped; only an id may not.
    type_room = max(1, b.width - visible_len(f"   type    "))
    types = [b.paint(clip(f"{t} {by_type[t]}", type_room), *TYPE_STYLE.get(t, ()))
             for t in sorted(by_type, key=lambda t: (-by_type[t], t))]
    board_wrapped(b, f"   {b.paint('type', 'grey')}    ", types, sep)


def board_section(b: Board, label: str, rows: list[dict[str, Any]], glyph: str,
                  empty_note: str, note: str | None = None) -> None:
    b.add()
    if not rows:
        # Same rule as the gantt projection: an empty section states that it is
        # empty. A section that renders blank reads as "fine", which is a claim.
        b.rule(label, empty_note)
        return
    b.rule(label, note or f"{len(rows)}")
    id_width, type_width = board_columns(b, rows)
    for r in rows:
        b.add(board_row(b, r, glyph, id_width, type_width))


def _snapshot_display() -> str:
    """The snapshot path as board caption text: repo-relative in the ordinary
    case, absolute for a pinned store outside the tree (pc-74da) — a caption
    must never be the thing that crashes the report."""
    sp = snapshot_path()
    try:
        return str(sp.relative_to(ROOT))
    except ValueError:
        return str(sp)


def cmd_board(args: argparse.Namespace) -> int:
    got = require_heads()
    if isinstance(got, int):
        return got
    heads, all_records = got
    cfg = load_config()
    stream = sys.stdout
    piped = not getattr(stream, "isatty", lambda: False)()
    color = board_supports_color(stream, False if args.no_color else None)
    # Piped output is fixed-width so the projection is byte-reproducible;
    # terminal width is a property of the viewer, never of the ledger.
    width = args.width or (BOARD_PIPED_WIDTH if piped
                           else shutil.get_terminal_size((100, 24)).columns)
    b = Board(max(BOARD_MIN_WIDTH, min(width, BOARD_MAX_WIDTH)), color,
              board_supports_unicode(stream))

    findings = run_checks(all_records, [], cfg)
    errors = [f for f in findings if f["severity"] == "error"]
    blocked_by = compute_blockers(heads)
    ready = sorted((h for h in open_heads(heads) if not blocked_by.get(h["id"])),
                   key=lambda h: (h.get("priority", 2),
                                  chronological_key(h.get("created", "")), h["id"]))
    blocked = sorted((h for h in open_heads(heads) if blocked_by.get(h["id"])),
                     key=lambda h: (h.get("priority", 2), h["id"]))
    active = sorted((h for h in heads.values() if h.get("status") == "in-progress"),
                    key=lambda h: (h.get("priority", 2), h["id"]))

    name = safe_text(cfg.get("project_name") or ROOT.name, TITLE_CAP)
    state = (b.paint(f"{b.g['ok']} check clean", "bgreen") if not errors
             else b.paint(f"{b.g['bad']} check {len(errors)} error(s)", "bred"))
    plural = "record" if len(heads) == 1 else "records"
    b.rule(name, f"{len(heads)} {plural} {b.g['sep']} {state}", heavy=True)
    # Rule 1 is the caption the whole report is read under, so on a narrow
    # terminal it moves to its own line rather than running off the edge.
    board_wrapped(b, "   ",
                  [b.paint(b.link("file://" + str(snapshot_path()),
                                  _snapshot_display()), "grey"),
                   b.paint(RULE1, "dim")], "  ")
    b.add()
    board_distribution(b, heads)

    # `ready`, not `next` — they differ, and conflating them would misreport.
    # `next` additionally caps at priority <= 3 and truncates to --limit, so a
    # p4 record is ready but never "next".
    capped = sum(1 for h in ready if h.get("priority", 2) > 3)
    note = f"{len(ready)} {b.g['sep']} total order"
    if capped:
        note += f" {b.g['sep']} {capped} below `next` cut-off"
    board_section(b, "ready", ready, "ready",
                  "nothing ready — every open record is blocked", note)
    board_section(b, "in progress", active, "active", "nobody is working on anything")

    b.add()
    if not blocked:
        b.rule("blocked", "nothing is blocked")
    else:
        b.rule("blocked", f"{len(blocked)}")
        id_width, type_width = board_columns(b, blocked)
        for h in blocked:
            b.add(board_row(b, h, "open", id_width, type_width))
            ids = blocked_by.get(h["id"], [])
            questions = [i for i in ids if heads.get(i, {}).get("type") == "question"]
            others = [i for i in ids if i not in questions]
            groups = [(lbl, [safe_id(i) for i in items]) for lbl, items in
                      (("blocked by", others), ("awaiting", questions)) if items]
            label_width = column_width([lbl for lbl, _ in groups])
            # THE FIFTH SHARED COLUMN (codex pass, 2026-08-23). pc-87dd capped
            # the four `id_width` sites and missed this one, which is the same
            # defect in the dependency sub-rows: one long blocker id padded
            # every OTHER dependency line, and `prefix.rstrip()` could not
            # reclaim it because the padding sat inside a bold SGR span. Capped
            # against the same measured overhead, and padded via `pad_to`.
            dep_stem = "        " + b.g["tee"] + " " + " " * label_width + "  "
            dep_width = min(column_width([d for _, items in groups for d in items]),
                            max(1, b.width - visible_len(dep_stem)))
            deps = {safe_id(i): i for i in ids}
            for gi, (lbl, items) in enumerate(groups):
                last_group = gi == len(groups) - 1
                for ii, dep in enumerate(items):
                    last = last_group and ii == len(items) - 1
                    stem = b.g["end"] if last else b.g["tee"]
                    dep_head = heads.get(deps.get(dep, dep), {})
                    dep_status = safe_text(dep_head.get("status", "?"), VOCAB_CAP) or "?"
                    shown = (lbl if ii == 0 else "").ljust(label_width)
                    b.add(fit(b, "        " + b.paint(f"{stem} {shown}  ", "grey")
                              + pad_to(b, dep, dep_width, "bold") + "  ",
                              dep_head.get("title", "?"),
                              " " + b.paint(f"({dep_status})",
                                            *STATUS_STYLE.get(dep_status, ()))))

    b.add()
    audit = audit_findings(heads, all_records, cfg, resolve=False)
    if not audit:
        b.rule("audit", "no advisory findings")
    else:
        b.rule("audit", f"{len(audit)} advisory {b.g['sep']} never blocking")
        kind_width = column_width([f["kind"] for f in audit])
        for f in sorted(audit, key=lambda f: (f["kind"], f.get("id") or "")):
            b.add(fit(b, f"   {b.paint(f['kind'].ljust(kind_width), 'yellow')}  "
                      + b.paint(f.get("id") or "-", "bold") + "  ",
                      f.get("note", ""), floor=20, style=("grey",)))
    b.add()
    board_print(b, stream)
    return 1 if errors else 0


# `compact` IS GONE at v2, deliberately and without a replacement.
#
# It rewrote the working-tree ledger to resolved heads, and its safety argument
# was that git history retained everything else. Under one append-only log the
# log IS the history, so compaction is a timeline rewrite — a force-push by
# another name, and the exact operation rule 5 exists to make unavailable. It
# is removed rather than kept behind a --force flag: an audited path to rewrite
# history is still a path, and nothing in this repo needs one. Its two Alloy
# theorems (CompactPreservesCleanAndQueries, CompactIdempotent) and its trap
# (BranchedCompactMergeIsClean) go in the same change — VP4's destructive half.
#
# What compaction was actually FOR — a greppable working file — is served by
# the snapshot, regenerated on every write.
#
# CORRECTED (pc-9b3e): an earlier version of this comment claimed the snapshot
# carries "exactly the resolved heads", and used that as the argument that
# deleting compact lost nothing. It is false — write_snapshot writes every
# entry's record, so the snapshot is the full history and grows without bound.
# The claim was written in the same commit that deleted compact, which is the
# worst place for a false one. Whether the snapshot SHOULD be head-only is a
# live question: heads are what a reader wants, and the full history is what
# makes the projection checkable against the log byte-for-byte (E015).


PECIA_REF = "refs/pecia/log"


def git_run(*args: str, stdin: str | bytes | None = None) -> tuple[int, str, str]:
    try:
        payload = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        done = subprocess.run(["git", *args], cwd=str(ROOT),
                              input=payload, capture_output=True, timeout=60)
        # Git prints paths and remote names as bytes. fsdecode preserves their
        # identity when a caller passes one back to Git under a legacy locale.
        return (done.returncode, os.fsdecode(done.stdout).strip(),
                done.stderr.decode("utf-8", errors="replace").strip())
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def ref_head() -> tuple[str | None, str | None]:
    """The local publication register's head, as (commit, error).

    (sha, None) — the ref resolves. (None, None) — genuinely unborn.
    (None, why) — the ref EXISTS but cannot be read.

    The third state is pc-a6d3 (round-5 lane B1-F1): rev-parse's failure
    status was discarded, so a present, unreadable refs/pecia/log was
    reported as unborn and sync's offline branch answered 'nothing
    published locally' over a register that was something — fail-closed,
    but the wrong branch with misleading remediation. A register that
    cannot be read is UNKNOWN, never absent (claim 5; the read-failure
    half of the pc-39f6/pc-9e4d seam). rev-parse --verify --quiet exits 1
    for absent and unreadable alike, so the discriminator is for-each-ref:
    it lists an absent ref as silence and warns 'ignoring broken ref' on
    stderr for one it cannot read."""
    code, out, _ = git_run("rev-parse", "--verify", "--quiet", PECIA_REF)
    if code == 0 and out:
        return out, None
    code, listed, err = git_run("for-each-ref", PECIA_REF)
    if code != 0:
        return None, err or "git for-each-ref failed"
    if err.strip():
        return None, err.strip()
    if listed.strip():
        return None, f"{PECIA_REF} is listed but does not resolve to a commit"
    return None, None


def local_ref_readback(expected: str) -> str | None:
    """VP20 for the LOCAL half of the register (v2.8, pc-4d57). The remote
    push has read its target back since pc-c613, and format-v2.md 5 says the
    implementation 're-reads refs/pecia/log after every publish' — an
    unqualified sentence, while every local `update-ref` was trusted by exit
    code: a write that succeeds against the wrong location succeeds. Returns
    None when the ref reads back as `expected`, else what it actually reads
    (for the refusal message)."""
    got, err = ref_head()
    if err is not None:
        return f"<unreadable: {err}>"
    return None if got == expected else (got or "<unborn>")


def git_blob(ref_path: str) -> tuple[int, str, str]:
    """`git show` of a blob, stdout UNSTRIPPED (v2.7, pc-13f6). git_run
    strips stdout, which silently normalized a published blob's trailing
    blank lines before published_blob_lines could see them — the exact
    repair-in-transport that function exists to refuse.

    Decoded with surrogateescape, not strictly (pc-0a2f): text=True raised
    UnicodeDecodeError inside subprocess for an invalid-UTF-8 blob — a fatal
    E000 at exit 2 before the line-level gate in published_blob_lines could
    name the damaged line. Undecodable bytes survive the decode as lone
    surrogates and are refused THERE, with the line named."""
    try:
        done = subprocess.run(["git", "show", ref_path], cwd=str(ROOT),
                              capture_output=True, timeout=60)
        return (done.returncode,
                done.stdout.decode("utf-8", errors="surrogateescape"),
                done.stderr.decode("utf-8", errors="replace").strip())
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def published_blob_lines(blob: str) -> tuple[list[str] | None, str]:
    """The published log's physical lines, or (None, why) when the blob is
    E001-invalid at the line level.

    v2.7 (pc-13f6): every consumer of a published log.jsonl blob — sync's
    hydrate and re-chain, publish's prefix guard, migrate's published-prefix
    check — used `[l for l in blob.splitlines() if l.strip()]`, a filter that
    silently REPAIRED a blank-line-carrying blob in transport: sync hydrated
    a normalized local log while the ref kept pointing at a physically
    different, contract-violating blob, and the prefix guards compared
    against a fiction. The format contract is one entry per line with no
    blank lines (E001, pc-e7f0), and an invalid published artifact is
    refused loudly, never normalized."""
    lines = source_lines(blob)
    for n, ln in enumerate(lines, start=1):
        if not ln.strip():
            return None, (f"published log line {n} is blank — the published "
                          f"blob is E001-invalid (one entry per line, no "
                          f"blank lines; format-v2.md, pc-e7f0)")
        # pc-0a2f (round-4 lane B1-F4): git_blob decoded strictly, so a blob
        # whose line 2 held the byte 0xff killed sync and publish as fatal
        # E000 UnicodeDecodeError before this gate could NAME the line. The
        # blob now arrives surrogateescape-decoded, and undecodable bytes
        # are detected here, per line, where the diagnostic contract lives.
        try:
            ln.encode("utf-8")
        except UnicodeEncodeError:
            return None, (f"published log line {n} is not valid UTF-8 — the "
                          f"published blob is E001-invalid at the line level "
                          f"(one UTF-8 JSON entry per line; format-v2.md, "
                          f"pc-0a2f)")
        # v2.8 (pc-1362): the docstring above promised "E001-invalid at the
        # line level" and the code detected only blankness — so publish
        # called an unparseable line a FORK and migrate a DIVERGENT
        # RECONSTRUCTION, steering the operator to reconciliation when the
        # repair is one named line. The refusals held; the diagnostic
        # contract (claim: a loud refusal NAMING THE LINE) is what this
        # implements.
        try:
            parsed = strict_json_loads(ln)
        except json.JSONDecodeError as exc:
            return None, (f"published log line {n} does not parse ({exc}) — "
                          f"the published blob is E001-invalid at the line "
                          f"level (one JSON entry per line; format-v2.md, "
                          f"pc-1362)")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("rec"), dict):
            return None, (f"published log line {n} is not an entry object "
                          f"carrying a rec — the published blob is "
                          f"E001-invalid at the line level (format-v2.md, "
                          f"pc-1362)")
    return lines, ""


def published_heads(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Head revision per record id over a timeline's entries."""
    heads: dict[str, int] = {}
    for e in entries:
        r = e["rec"] if isinstance(e.get("rec"), dict) else {}
        rid, rev = r.get("id"), r.get("rev")
        if isinstance(rid, str) and strict_int(rev):  # `pc-d89f`
            if rev > heads.get(rid, 0):
                heads[rid] = rev
    return heads


def published_revisions(entries: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    """Canonical CONTENT per (record id, revision) over a timeline's entries.

    pc-9f60: the head revision NUMBER is not the thing a register publishes —
    it publishes record-revisions, and two stores can write different content
    at the same number. This is the identity the rewind guard compares."""
    seen: dict[tuple[str, int], str] = {}
    for e in entries:
        r = e["rec"] if isinstance(e.get("rec"), dict) else {}
        rid, rev = r.get("id"), r.get("rev")
        if isinstance(rid, str) and strict_int(rev):  # `pc-d89f`
            seen[(rid, rev)] = canonical(r)
    return seen


def remote_selection_refusal(requested: str | None, remotes: str) -> str | None:
    """Why this `--remote NAME` cannot be honoured, or None (pc-719e).

    `publish` and `sync` both took their no-remote branch BEFORE reading
    `args.remote`, so an explicitly named remote was discarded without a
    word: `sync --remote definitely-not-configured` exited 0 `synced: true`
    against the local register and the name appeared nowhere. The fallback
    is the declared v2.6 design and stays; what may not happen is an
    explicit selection being dropped in silence.

    One rule for both commands and both branches: a name that is not a
    configured remote is refused and NAMED. That also answers what the
    reproduction observed beside the finding — a push to a nonexistent
    remote reporting "the remote advanced — run `pecia sync`", which is
    false of a remote that does not exist."""
    if not requested:
        return None
    configured = [r for r in remotes.split("\n") if r.strip()]
    if requested in configured:
        return None
    if not configured:
        return (f"--remote {requested} was requested and this repository has "
                f"NO remotes configured — the local publication register is "
                f"the whole timeline here (format-v2.md 3.1), so the name "
                f"cannot be honoured. Nothing about it was assumed")
    return (f"--remote {requested} names no configured remote (configured: "
            f"{', '.join(configured)}) — the name is refused, never silently "
            f"replaced by another remote")


def register_rewind_refusal(register_before: str | None, remote_commit: str,
                            about_to_write: list[dict[str, Any]],
                            carried_locally: list[dict[str, Any]]) -> str | None:
    """Why moving the local register to `remote_commit` would unpublish a
    landed revision, or None.

    pc-1bb6 (round-8 lane B1-F2): sync resolved `remote_commit` from the
    fetched remote and moved the local register to it under a CAS against
    the value it READ, with no test of what that value published. So when a
    concurrent publish won the local register and its remote half failed —
    the exact state the CAS refusal describes, and the state whose remedy
    that refusal names — the prescribed retry exited 0 `synced: true` while
    rewinding refs/pecia/log from the winner back to origin's older commit,
    and the next publish wrote an origin from which the winner's record was
    absent. A refusal's named remedy is part of its contract.

    The test is CONTENT, not bare ancestry, because the ordinary recovery
    IS a divergence: `publish` moves the local register before it pushes,
    so a push refused by an advanced remote leaves the register on a commit
    the remote does not contain, and that is precisely when sync must run.
    What may never happen is LOSS: every record the register publishes, at
    the revision it publishes, must be carried by the timeline this sync is
    about to write — which is what the next publish will deliver. A
    divergent register whose content survives the re-chain passes; a
    register holding a record (or a revision) the new timeline drops is
    refused.

    A REVISION IS ITS CONTENT, NOT ITS NUMBER (pc-9f60, round-9 lane B1-F2).
    The comparison was `published_heads` against `published_heads` — head
    revision NUMBER per id — so two stores writing different content at the
    same number satisfied it: store A won the register with pc-X rev 2 and
    failed its remote half, store B wrote its own rev 2, and B's sync exited
    0 having rewound the register past A's publication, which reached no
    remote and no register afterwards. The claim states the universal as
    LOSS; a number cannot express it.

    So the guard compares (id, rev) -> canonical content, and asks of each
    revision the register publishes: is it carried by the timeline this sync
    would write, OR by the LOCAL timeline the re-chain derives from? The
    second clause is what keeps the ordinary recovery legal — a re-chain
    renumbers the local suffix, so the register's own rev 2 legitimately
    becomes rev 3 and its content is not at that number any more. What it
    does NOT cover is content this store has never held, which is exactly
    the other store's publication in the case above."""
    if not register_before or register_before == remote_commit:
        return None
    code, blob, err = git_blob(f"{register_before}:log.jsonl")
    if code != 0:
        # pc-39f6's rule, on this seam: a failed read is an UNKNOWN
        # register, not an empty one, so the guard cannot answer and an
        # unanswerable guard refuses rather than waving the move through.
        return (f"refusing to move {PECIA_REF}: could not read what it "
                f"currently publishes ({register_before}:log.jsonl): "
                f"{err or 'git show failed'} — a failed read is an unknown "
                f"register, not an empty one, so the rewind guard cannot "
                f"run. Nothing was written; repair the read and sync again")
    lines, why = published_blob_lines(blob)
    if lines is None:
        return (f"refusing to move {PECIA_REF}: {why} — the register's own "
                f"blob must be repaired, not silently normalized in "
                f"comparison. Nothing was written")
    register_entries = [strict_json_loads(ln) for ln in lines]
    held = published_revisions(register_entries)
    arriving = published_revisions(about_to_write)
    local_held = published_revisions(carried_locally)
    arriving_heads = published_heads(about_to_write)
    for rid, rev in sorted(held):
        content = held[(rid, rev)]
        if arriving.get((rid, rev)) == content or local_held.get((rid, rev)) == content:
            continue
        have = arriving_heads.get(rid)
        if have is None:
            lost = f"carries no {rid}"
        elif (rid, rev) not in arriving:
            lost = f"stops at {rid} rev {have}"
        else:
            lost = (f"carries a DIFFERENT {rid} rev {rev} — same number, other "
                    f"content, and this store has never held the published one")
        return (f"refusing to move {PECIA_REF} to {remote_commit[:10]}: "
                f"the register publishes {rid} rev {rev} and the "
                f"timeline this sync would write {lost} — the move would "
                f"unpublish a landed revision, which is the rewind the "
                f"CAS refusal's own remedy used to perform (pc-1bb6). "
                f"Nothing was written; a publication that won the local "
                f"register has not reached the remote — run `pecia "
                f"publish` to deliver it, then sync again")
    return None


def advance_register(register_before: str | None, remote_commit: str,
                     about_to_write: list[dict[str, Any]],
                     carried_locally: list[dict[str, Any]]) -> str | None:
    """Take the local publication register for `remote_commit`, or say why not.

    One helper for both of sync's register writes (pc-b652): the rewind
    guard, the CAS against the single read, and the VP20 read-back, in that
    order. Called from write_log_validated's `before_commit` hook, so every
    refusal here is ordered BEFORE the rename and leaves the store
    byte-identical. `carried_locally` is the local timeline this sync read —
    what the re-chain derives from, and the second half of the rewind
    guard's question (pc-9f60)."""
    rewind = register_rewind_refusal(register_before, remote_commit,
                                     about_to_write, carried_locally)
    if rewind is not None:
        return rewind
    code, _, ref_err = git_run("update-ref", PECIA_REF, remote_commit,
                               register_before if register_before else "")
    # VP20 ON BOTH BRANCHES (v2.16, pc-2390). The read-back sat after the
    # nonzero-status return, so it could only ever catch a writer lying about
    # SUCCESS. A writer that performs the write and THEN exits nonzero was
    # believed on its exit code alone, and this refusal then told the operator
    # three things that were false at once: that the register had not moved,
    # that this command had not moved it, and that a concurrent publisher —
    # which need not exist — had. Claim 5 states the rule with no branch.
    readback = local_ref_readback(remote_commit)
    said = f"exit {code}" + (f": {ref_err}" if ref_err else ", no message")
    if code != 0:
        if readback is None:
            return (f"local register CAS reported failure ({said}) and "
                    f"{PECIA_REF} reads back as exactly the value this sync "
                    f"asked it to take — THE REGISTER DID MOVE, and this "
                    f"command moved it. The log was not written, because this "
                    f"refusal is ordered before the rename, so the store is "
                    f"now behind its own register: that state is recoverable "
                    f"and `pecia sync` is its recovery (v2.12, pc-d373). The "
                    f"writer misreported its own outcome, which is the thing "
                    f"to look at (VP20, pc-2390)")
        unmoved = readback == (register_before or "<unborn>")
        why = ("so the CAS failed for a reason of its own rather than to a "
               "competing writer" if unmoved else
               "so another writer moved it while sync was running and stays "
               "the winner (first publish wins, format-v2.md 5)")
        return (f"local register CAS refused ({said}) — {PECIA_REF} now "
                f"reads {readback}, {why}. Nothing was rewound and nothing "
                f"was written — the local log and its snapshot are "
                f"byte-identical to before (v2.12); run `pecia sync` again to "
                f"reconcile against what the register now publishes")
    if readback is not None:
        return (f"update-ref reported success and {PECIA_REF} reads back as "
                f"{readback} — the local register did not take the write, so "
                f"the log was not written either; the next publish would "
                f"build on a stale parent (VP20, pc-4d57)")
    return None


def timeline_errors(entries: list[dict[str, Any]], cfg: dict[str, Any],
                    check_snapshot: bool = True) -> list[dict[str, Any]]:
    """EVERY error-level finding for a candidate timeline — graph invariants
    included, not just the chain ones.

    This exists because `publish` and `sync` each grew their own partial idea
    of "is this clean", and both were wrong in the same direction: they ran
    timeline_checks (E013/E014/E015) and never run_checks (E003/E004/...).
    v2 trap 3 is precisely that hazard — a re-chain validated by field
    disjointness alone reproduces the cross-branch cycle, because two writers
    can each add one edge to DIFFERENT records, never touch a shared field,
    and compose into an E004 cycle. The model pinned it, pc-0033's disposition
    said to route re-chaining through the full checker, and the code shipped
    without it (off-lineage pass, lanes C/E/A-prime, 2026-08-12).

    One function, two callers: the way that defect got in was two callers each
    deciding for themselves what clean meant."""
    records = [e["rec"] for e in entries]
    findings = run_checks(records, [], cfg,
                          record_lines=list(range(1, len(records) + 1)))
    findings += timeline_checks(entries, check_snapshot)
    return [f for f in findings if f["severity"] == "error"]


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish the timeline to refs/pecia/log, then to the remote.

    The ref is a COMMIT chain, not a blob: fast-forward-ness is defined by
    commit ancestry, so only a commit can serve as the compare-and-swap
    register. A non-fast-forward push is REFUSED by the remote, and that
    refusal is the serialization point — there is no third outcome and no
    merge (spec/format-v2.md 5).

    Delivery is confirmed by READING THE REF BACK, never by the writer's
    exit code — the remote via ls-remote (pc-c613) and, since v2.8
    (pc-4d57), the local register via rev-parse too. VP20, measured twice in
    one day in a sibling repo: a write that succeeds against the wrong
    location succeeds, and a write reported as done with no read-back is
    the exact signature.

    The chain head is reported (v3.3, pc-ded91385e31a), so two clones can be
    compared by what they published; pc-72c8 withheld it only as an oracle
    for prose that is no longer withheld."""
    target = log_path()
    if target is None:
        return cannot_run("not inside a git repository")
    # Validate the generation we will publish. A second read after validation
    # can include a concurrent append that no checker saw (pc-2f9f89d79f67).
    try:
        log_bytes = target.read_bytes()
    except FileNotFoundError:
        _, findings = load_entries()
        return cannot_run(findings[0]["message"] if findings else
                          "timeline disappeared before publication — retry")
    entries, findings = read_log(target, raw_bytes=log_bytes)
    if findings:
        return cannot_run(findings[0]["message"])
    if not entries:
        return cannot_run("timeline is empty — nothing to publish")
    bad = timeline_errors(entries, load_config())
    if bad:
        return cannot_run(f"refusing to publish an unclean timeline: {bad[0]['message']}")

    log_text = log_bytes.decode("utf-8")  # a clean read_log accepted every line
    code, blob, err = git_run("hash-object", "-w", "--stdin", stdin=log_bytes)
    if code != 0:
        return cannot_run(f"could not write the log blob: {err}")
    code, tree, err = git_run("mktree", stdin=f"100644 blob {blob}\tlog.jsonl\n")
    if code != 0:
        return cannot_run(f"could not build the tree: {err}")
    parent, parent_err = ref_head()
    if parent_err is not None:
        # Sibling of pc-a6d3, same commit: publish used to fail closed one
        # step later (the genesis CAS refuses because the ref exists), with
        # the CAS's message rather than the truth — the register is present
        # and unreadable, so the prefix guard cannot even locate its input.
        return cannot_run(
            f"refusing to publish: the local publication register "
            f"{PECIA_REF} exists but cannot be read: {parent_err} — a "
            f"register that cannot be read is unknown, not empty, so the "
            f"fork refusal cannot run. Nothing was published; repair the "
            f"read and publish again")
    # ANCESTRY IS NOT CONTENT. A fast-forward proves the new commit descends
    # from the old one; it proves nothing about the log blob it carries, so a
    # publish could advance the ref while swapping in an unrelated timeline.
    # Bind them: whatever the parent commit published must be a PREFIX of what
    # we are publishing now (pc-c613).
    if parent:
        code, prior, prior_err = git_blob(f"{parent}:log.jsonl")
        # A FAILED READ IS AN UNKNOWN REGISTER, NOT AN EMPTY ONE (pc-39f6).
        # This guard used to run only `if code == 0`, so exactly when its
        # input was unavailable the fork refusal was skipped and a second
        # store's publish REPLACED the published timeline at exit 0. The
        # gate's input failing to arrive means the gate cannot answer, and
        # an unanswerable gate refuses — it never waves through.
        if code != 0:
            return cannot_run(
                f"refusing to publish: could not read the published prefix "
                f"({parent}:log.jsonl): {prior_err or 'git show failed'} — a "
                f"failed read is an unknown register, not an empty one, so "
                f"the fork refusal cannot run. Nothing was published; repair "
                f"the read and publish again")
        prior_lines, why = published_blob_lines(prior)
        if prior_lines is None:
            # The sibling of pc-13f6's shape: comparing against a
            # normalized fiction would let a fork hide behind the blank
            # line that was silently dropped.
            return cannot_run(
                f"refusing to publish: {why} — the ref's current blob "
                f"must be repaired, not silently normalized in comparison")
        # source_lines, not splitlines (pc-d96d, same commit): a local line
        # carrying a literal U+2028 is ONE physical line, and splitting it
        # here would misalign the prefix comparison into a false fork. The
        # blank filter is retained shape only — read_log above already
        # refused any blank line before this point.
        mine_lines = [l for l in source_lines(log_text) if l.strip()]
        if mine_lines[:len(prior_lines)] != prior_lines:
            return cannot_run(
                f"refusing to publish: the ref currently holds {len(prior_lines)} entries "
                f"that are not a prefix of this timeline's {len(mine_lines)}. This is a "
                f"fork, not an append — run `pecia sync`")
    msg = f"pecia timeline: {len(entries)} entries"
    argv = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
    code, commit, err = git_run(*argv)
    if code != 0:
        return cannot_run(f"could not commit the tree: {err}")
    # GENESIS IS A CAS TOO. With no parent the old-value argument was omitted
    # entirely, so two publishers racing from empty both succeeded and the
    # second silently won — last-writer-wins at exactly the moment the timeline
    # is established. An empty old-value asserts "must not exist" (pc-c613).
    code, _, err = git_run("update-ref", PECIA_REF, commit, parent if parent else "")
    # The same asymmetry as the remote half below and as sync's register
    # write (v2.16, pc-bc87, pc-2390): the read-back guarded only the
    # zero-status branch, so a writer that took the value and then exited
    # nonzero was believed on its exit code.
    readback = local_ref_readback(commit)
    if code != 0 and readback is None:
        return cannot_run(
            f"local ref CAS reported failure ({err}) and {PECIA_REF} reads "
            f"back as the commit just built — the register DID take the "
            f"write. Nothing was delivered to a remote, and re-running "
            f"`pecia publish` is safe (the prefix guard will see its own "
            f"work); the writer misreporting is what to look at (VP20)")
    if code != 0:
        return cannot_run(f"local ref CAS refused: {err}")
    if readback is not None:
        return cannot_run(
            f"update-ref reported success and {PECIA_REF} reads back as "
            f"{readback} — the local register did not take the write. "
            f"Delivery is confirmed by reading the target, never by the "
            f"writer's exit code (VP20, pc-4d57)")

    result: dict[str, Any] = {"published_local": True, "entries": len(entries),
                              "head": log_head_hash(entries),
                              "ref": PECIA_REF, "rule": RULE1}
    # A FAILED ENUMERATION IS NOT "NO REMOTE" (pc-30aa, round-4 lane B1-F2).
    # Empty stdout was read as 'no remote configured' even when `git remote`
    # itself failed, so publish exited 0 having delivered nowhere — the VP20
    # read-back guards the transports it knows about, and a discovery
    # failure removed the remote from the plan silently. The local
    # publication above DID land; what exits 1 here is delivery.
    code, remotes, remote_err = git_run("remote")
    if code != 0:
        result["published_remote"] = False
        result["refused"] = safe_text(
            f"could not enumerate remotes: {remote_err or 'git remote failed'}",
            MESSAGE_CAP)
        result["next"] = ("remote discovery failed — a failure is unknown, "
                          "not 'no remote'; fix git and publish again")
        emit(result)
        return 1
    unknown = remote_selection_refusal(args.remote, remotes)
    if unknown is not None:
        # The local half HAS landed, so this is not cannot_run: the publish
        # happened and the delivery did not (pc-719e).
        result["published_remote"] = False
        result["refused"] = unknown
        result["next"] = ("name a configured remote, or run `pecia publish` "
                          "with no --remote to use the first one")
        emit(result)
        return 1
    if not remotes:
        result["remote"] = "none — the local timeline IS the timeline (format-v2.md 3.1)"
        emit(result)
        return 0
    remote = args.remote or remotes.split("\n")[0]
    push_code, _, push_err = git_run("push", remote, f"{PECIA_REF}:{PECIA_REF}")
    # VP20 ON BOTH BRANCHES (v2.16, pc-bc87). The read-back used to sit AFTER
    # `if push_code != 0: ... return 1`, so it could only ever confirm a
    # success the writer had already reported. A push that writes the ref and
    # then exits nonzero was believed on its exit code alone, and publish
    # reported `published_remote: false` with the remedy "the remote advanced
    # — run `pecia sync`" for a state that did not exist. Claim 5 states the
    # rule with no branch: delivery is decided by reading the ref, never by
    # the writer's exit code, and that has to hold in the direction where the
    # writer claims failure as much as where it claims success.
    ls_code, lsr, ls_err = git_run("ls-remote", remote, PECIA_REF)
    landed = lsr.split()[0] if lsr else None
    if ls_code != 0:
        # An unreadable target is UNKNOWN, not undelivered (the pc-39f6
        # shape): say so rather than picking whichever of the two the
        # writer's status happens to suggest.
        result["published_remote"] = "unknown"
        result["refused"] = safe_text(
            f"could not read {PECIA_REF} back from {remote}: "
            f"{ls_err or 'git ls-remote failed'}"
            + (f"; `git push` also exited {push_code}: {push_err}"
               if push_code != 0 else ""), MESSAGE_CAP)
        result["next"] = ("delivery is unknown until the ref is read — repair "
                          "the read and publish again; a second publish of an "
                          "already-delivered timeline is a no-op")
        emit(result)
        return 1
    if landed == commit and push_code != 0:
        # The delivery ARRIVED and the writer said otherwise.
        result["published_remote"] = True
        result["remote"] = remote
        result["read_back_matches"] = True
        result["writer_reported_failure"] = safe_text(
            f"`git push` exited {push_code}: {push_err}", MESSAGE_CAP)
        result["next"] = (
            "the timeline IS published — the remote ref reads back as the "
            "commit just built — but the push reported failure while "
            "delivering it. There is nothing here to re-run and nothing for "
            "`pecia sync` to reconcile; the push tooling is what to look at")
        emit(result)
        return 1
    if landed != commit:
        # A REFUSAL'S WORDS ARE TRUE OF THE VALUE IT REFUSED (v2.13, pc-3942):
        # the old `next` asserted "the remote advanced" whatever the remote
        # actually held.
        if landed is None:
            why = (f"{remote} has no {PECIA_REF} — nothing was delivered, and "
                   f"the push is what failed")
        elif landed == parent:
            why = (f"{remote} still holds the timeline this publish built on "
                   f"— nothing was delivered, and the push is what failed")
        else:
            why = ("the remote advanced — run `pecia sync`, then publish again")
        result["published_remote"] = False
        result["remote_head"] = safe_id(landed) if landed else None
        if push_code != 0:
            result["refused"] = safe_text(push_err, MESSAGE_CAP)
        else:
            result["refused"] = safe_text(
                f"push reported success and {PECIA_REF} on {remote} reads back "
                f"as {render_value(landed)} — delivery is confirmed by reading the target, "
                f"never by the writer's exit code (VP20)", MESSAGE_CAP)
        result["next"] = why
        emit(result)
        return 1
    result["published_remote"] = True
    result["remote"] = remote
    result["read_back_matches"] = True
    emit(result)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Reconcile a refused push by re-chaining, never by merging.

    Fetch the remote timeline, find the common prefix by entry hash, and
    re-chain the local-only suffix onto the remote head. Appends to different
    records commute, so that is mechanical and lossless. Appends to the SAME
    record's same FIELD do not commute: those are surfaced to the writer with
    both revisions named, never auto-rebased, because replaying an intent onto
    changed state is a lost update (format-v2.md 4.1, 5)."""
    target = log_path()
    if target is None:
        return cannot_run("not inside a git repository")
    # THE REGISTER IS READ ONCE, AND EVERY WRITE TO IT IS A CAS AGAINST THAT
    # READ (pc-9e4d, round-4 lane B1-F1). Both register writes below used a
    # bare `update-ref` with no expected-old value, so a publish landing
    # between sync's read and its write was silently overwritten — the ref
    # moved BACKWARD past a successful publication, and both commands exited
    # 0. The pc-4d57 read-back could not catch it: it confirmed the value
    # sync wrote, which was exactly the laundered state. publish has been a
    # CAS since pc-c613; sync's writes get the same register semantics, with
    # the expectation pinned to this single read so a move at ANY later
    # point refuses instead of rewinding. One layer deeper on the same seam
    # as pc-acd4.
    register_before, register_err = ref_head()
    if register_err is not None:
        # pc-a6d3: the one local register reader the round-5 fix wave did
        # not touch. An unreadable ref used to fall through as None and the
        # offline branch answered "refs/pecia/log is unborn — there is
        # nothing to sync against", which is false exactly there.
        return cannot_run(
            f"the local publication register {PECIA_REF} exists but cannot "
            f"be read: {register_err} — a register that cannot be read is "
            f"unknown, never unborn or empty (claim 5, format-v2.md 5); "
            f"repair the read (the loose ref under .git/refs/pecia/) and "
            f"sync again")
    # Sibling of publish's pc-30aa fix, same commit: an enumeration failure
    # used to fall through to the OFFLINE branch, so sync exited 0
    # synced:true against the local register while the configured origin
    # sat ahead — false success on the exact command whose job is catching
    # up. A discovery failure is unknown, never 'no remote'.
    code, remotes, remote_err = git_run("remote")
    if code != 0:
        return cannot_run(
            f"could not enumerate remotes: {remote_err or 'git remote failed'} "
            f"— a discovery failure is unknown, not 'no remote'; syncing "
            f"against the local register instead could report success while "
            f"this clone stays behind the configured origin")
    unknown = remote_selection_refusal(args.remote, remotes)
    if unknown is not None:
        # Ordered before the branch that used to swallow it (pc-719e):
        # nothing has been fetched or written, so this is a cannot-run.
        return cannot_run(unknown)
    if remotes:
        remote = args.remote or remotes.split("\n")[0]
        code, _, err = git_run("fetch", remote, f"+{PECIA_REF}:refs/pecia/remote")
        if code != 0:
            return cannot_run(f"fetch failed: {err}")
        source_ref = "refs/pecia/remote"
    elif register_before is not None:
        # THE OFFLINE RECOVERY EXISTS (pc-099b, v2.6). With no remote, the
        # LOCAL refs/pecia/log is the publication register a refused publish
        # raced against — two stores under PECIA_LOG_DIR, or a rebuilt one —
        # and the prescribed recovery for that refusal is `pecia sync`. It
        # used to answer "no remote configured — there is nothing to sync
        # against", which was false exactly there: the published layer is
        # something to sync against wherever it lives.
        source_ref = PECIA_REF
    else:
        return cannot_run("no remote configured and nothing published locally "
                          "(refs/pecia/log is unborn) — there is nothing to "
                          "sync against")
    code, blob, err = git_blob(f"{source_ref}:log.jsonl")
    if code != 0:
        return cannot_run(f"published ref {source_ref} carries no log.jsonl: {err}")
    # The local ref must ADVANCE to what we just fetched, or the next publish
    # builds a commit that does not descend from the remote head and the CAS
    # refuses it forever. Found by the two-clone test: sync reported success,
    # the re-chained entries were correct, and publish stayed refused — the
    # content was reconciled while the ref that gates delivery was not.
    # (In the offline mode the register IS refs/pecia/log, so the update-ref
    # below re-asserts the value read above — a no-op only while nothing
    # races; a concurrent publish makes the CAS refuse, per pc-9e4d.)
    code, remote_commit, _ = git_run("rev-parse", "--verify", "--quiet",
                                     source_ref)
    if not remote_commit:
        return cannot_run("fetched ref does not resolve to a commit")

    with ledger_lock():
        local, findings = load_entries()
        # A MISSING local log is not an error here — it is the hydrate case,
        # and it is the normal state of a fresh clone. CI checks out a repo
        # with no .git/pecia/, so without this `sync` could not be the way a
        # clone obtains the timeline and every fresh checkout would be a
        # cannot-run. Any OTHER finding (a broken chain) still refuses.
        if findings and all(f["code"] == "E000" and "no timeline yet" in f["message"]
                            for f in findings):
            local, findings = [], []
        if findings:
            return cannot_run(findings[0]["message"])
        # THE LOCAL TIMELINE IS CHECKED BEFORE IT IS CONSUMED (v2.8, pc-acd4).
        # read_log above validates the CHAIN (hashes, prev, seq); it says
        # nothing about the CAS or the graph, so a forged rev-3-after-rev-1
        # suffix that `check` refuses (E008 + E014, "the CAS could not have
        # admitted this") was consumed here and re-chained into a clean rev 2
        # that KEPT the forged mutation — exit 0, synced: true, the recovery
        # path laundering exactly what the write path refuses. The remote
        # side already gets this treatment (pc-66b6) and so does the rebuilt
        # result (trap 3); the local suffix was the last consumer trusting
        # chain validity alone. check_snapshot=False as everywhere in sync:
        # the snapshot witness has its own guard (pc-c6b8, below).
        local_bad = timeline_errors(local, load_config(), check_snapshot=False)
        if local_bad:
            return cannot_run(
                f"local timeline is not clean: {local_bad[0]['message']} — "
                f"sync re-chains only revisions the CAS could have admitted; "
                f"run `pecia check`, repair the local log, and sync again")
        their_lines, why = published_blob_lines(blob)
        if their_lines is None:
            return cannot_run(
                f"{why} — sync refuses to normalize an invalid published "
                f"artifact in transport; repair the published timeline first")
        theirs = []
        for n, ln in enumerate(their_lines, start=1):
            try:
                theirs.append(strict_json_loads(ln))
            except json.JSONDecodeError as exc:
                return cannot_run(
                    f"published log line {n} does not parse: {exc} — the "
                    f"published blob is E001-invalid; refusing it")
        common = 0
        while (common < len(local) and common < len(theirs)
               and canonical(local[common]) == canonical(theirs[common])):
            common += 1
        # A VERIFIED SNAPSHOT IS ADOPTED WHERE THE TIMELINE STOPS SHORT OF IT
        # (v3.6, pc-4f84768dbed1). A commit carrying records nobody published
        # has a snapshot ahead of the published timeline, and a fresh clone of
        # it had nothing it could adopt: the published timeline would erase
        # the snapshot's extra entries (E015), and migrate's rebuild from
        # history diverges wherever history holds conflicting orders. The
        # snapshot is itself a verified copy of the timeline through its head.
        # Where this log and the published timeline are both prefixes of it,
        # it is the timeline this sync writes: nothing either holds is lost,
        # and the writer's entries arrive with the writer's own hashes. The
        # same rule restores a log that lost entries the snapshot still holds.
        copy = verified_snapshot_chain()
        if (copy is not None and len(copy) > max(len(local), len(theirs))
                and chain_prefix(local, copy) and chain_prefix(theirs, copy)):
            if args.take_landed:
                return cannot_run(
                    f"--take-landed named {', '.join(dict.fromkeys(args.take_landed))} "
                    f"and this sync has no local-only revisions to discard — "
                    f"the snapshot's verified timeline is being adopted whole. "
                    f"Nothing was written; run `pecia sync` without the flag")
            gone = mark_violation(local)
            if gone and not holds_marked_entry(copy):
                return cannot_run(f"sync refuses: {gone} (E019), and the "
                                  f"snapshot's timeline does not hold the "
                                  f"marked entry either. {MARK_REMEDY}")
            adopt_refusal: str | None = None

            def take_register_for_copy(parsed: list[dict[str, Any]]) -> str | None:
                nonlocal adopt_refusal
                adopt_refusal = advance_register(register_before, remote_commit,
                                                 parsed, local)
                return adopt_refusal

            entries, refusal = write_log_validated(
                target, copy,
                also=lambda parsed: next(
                    (f"the snapshot's timeline is not clean: {f['message']}"
                     for f in timeline_errors(parsed, load_config(),
                                              check_snapshot=False)), None),
                before_commit=take_register_for_copy,
                project=True)
            if adopt_refusal is not None:
                return cannot_run(adopt_refusal)
            if refusal is not None:
                return cannot_run(
                    f"refusing to adopt the snapshot's timeline: {refusal} — "
                    f"the local log was never written")
            emit({"synced": True, "common_prefix": common, "rechained": 0,
                  "fast_forwarded": max(0, len(theirs) - len(local)),
                  "from_snapshot": len(copy) - max(len(local), len(theirs)),
                  "head": log_head_hash(entries), "rule": RULE1})
            return 0
        mine = local[common:]
        if not mine and args.take_landed:
            # The sibling of pc-719e on this command's other argument: an
            # explicit --take-landed cannot be consulted on the hydrate
            # branch, and an argument that cannot be consulted is refused,
            # never discarded in silence.
            return cannot_run(
                f"--take-landed named {', '.join(dict.fromkeys(args.take_landed))} "
                f"and this sync has no local-only revisions to discard — the "
                f"published timeline is being adopted whole. Nothing was "
                f"written; run `pecia sync` without the flag")
        if not mine:
            # A DETECTED E015 IS EVIDENCE (v2.8, pc-c6b8): a no-op sync used
            # to regenerate the snapshot over the truncation witness. When
            # the published chain CONTAINS the recorded head, this hydrate IS
            # the recovery and proceeds; when it does not, refusing here is
            # what keeps the witness alive.
            witness = snapshot_truncation_witness(local, theirs)
            if witness:
                return cannot_run(f"sync refuses to regenerate the snapshot: "
                                  f"{witness}{copy_parts_note(copy, local, theirs)}")
            # The same rule for the log's mark (v3.3): adopting a timeline
            # that holds the marked entry IS the recovery; one that does not
            # would move the mark past the loss.
            gone = mark_violation(local)
            if gone and not holds_marked_entry(theirs):
                return cannot_run(f"sync refuses: {gone} (E019), and the "
                                  f"published timeline does not hold the "
                                  f"marked entry either. {MARK_REMEDY}")
            # VALIDATE, THEN RENAME (pc-d373). Both refusals below were
            # write-then-restore: the local log was overwritten and then put
            # back from `local`, a rollback that has to run and be right.
            # Nothing is written now unless the staged bytes pass BOTH
            # checks, so "local log left untouched" is a property of the
            # ordering rather than of a restore.
            #
            # The findings were DISCARDED here, so a fresh clone hydrating a
            # chain-broken remote reported success and kept the break (pc-7b30).
            # VP20's shape: the command answered "did the transfer happen",
            # never "did a valid timeline arrive". And read_log is chain
            # validity only (hashes, `prev` pointers) — pc-66b6: a chain-valid,
            # semantically dirty remote (a forged `touched` set, a dangling
            # edge) still hydrated as "synced: true", discovered only by a
            # SUBSEQUENT `pecia check`, so the full checker runs on the staged
            # entries too, before the rename.
            #
            # THE REGISTER IS TAKEN BEFORE THE RENAME (pc-b652). It used to
            # be taken after write_snapshot, so a concurrent publication
            # landing in that window refused at exit 2 over a store whose
            # log AND snapshot had already changed — outside the v2.12
            # declaration this command's own pre-rename refusals satisfy.
            register_refusal: str | None = None

            def take_register(parsed: list[dict[str, Any]]) -> str | None:
                nonlocal register_refusal
                register_refusal = advance_register(register_before,
                                                    remote_commit, parsed,
                                                    local)
                return register_refusal

            entries, refusal = write_log_validated(
                target, theirs,
                also=lambda parsed: next(
                    (f"remote timeline is not clean: {f['message']}"
                     for f in timeline_errors(parsed, load_config(),
                                              check_snapshot=False)), None),
                before_commit=take_register,
                project=True)
            if register_refusal is not None:
                return cannot_run(register_refusal)
            if refusal is not None:
                return cannot_run(
                    f"refusing to hydrate: {refusal} — the published timeline "
                    f"is not a valid timeline to adopt; the local log was "
                    f"never written")
            emit({"synced": True, "common_prefix": common, "rechained": 0,
                  "fast_forwarded": len(theirs) - common,
                  "head": log_head_hash(entries), "rule": RULE1})
            return 0

        # THE PUBLISHED TIMELINE IS FULLY VALIDATED BEFORE THE RE-CHAIN
        # ARITHMETIC TOUCHES IT (pc-7114, round-4 lane B1-F3). The
        # derivation below compares and increments revisions off `theirs`,
        # so a published rev of "two" hit `"two" > 1` and died as fatal
        # E000 TypeError at exit 2 — check's structured E001/E008/E014
        # verdict on the same bytes never surfaced. pc-1362's fix named
        # unparseable LINES; malformed VALUES crashed one layer deeper.
        # Nothing was ever written (the losing log stayed byte-identical),
        # so this converts the crash into the promised refusal, not a
        # behavior change. The hydrate branch above keeps its own
        # write-then-verify-then-restore treatment (pc-66b6).
        # ITS CHAIN FIRST, AND BY NAME (pc-d373, round-7 lane B2-F3).
        # timeline_errors checks the records and the graph; it does NOT check
        # seq or prev, and nothing else looked at the published chain on this
        # branch — so a published line declaring `seq: 9` passed here, was
        # re-chained, WRITTEN, and only then refused by read_log's E013 with
        # no rollback, leaving a previously clean local store at seq [9, 2]
        # and red under E013+E015. The chain is validated before the
        # arithmetic touches it, and the refusal names the published timeline
        # rather than blaming the local re-chain for its damage (claim
        # v2-storage: an invalid published timeline is named and refused
        # BEFORE hydration or re-chain).
        their_chain = log_refusal(theirs, target)
        if their_chain is not None:
            return cannot_run(
                f"published timeline's chain is not valid: {their_chain} — "
                f"refusing to re-chain onto it; repair the published timeline "
                f"first. Nothing was written")
        their_bad = timeline_errors(theirs, load_config(), check_snapshot=False)
        if their_bad:
            return cannot_run(
                f"published timeline is not clean: {their_bad[0]['message']} — "
                f"refusing to re-chain onto it; repair the published timeline "
                f"first (run `pecia check` against it for the full findings)")

        # `touched` IS RECOMPUTED FOR BOTH SIDES, never read off the entry.
        # The conflict test decides whether two appends commute, so trusting a
        # field a peer wrote hands that decision to the peer — a forged or
        # merely stale set silently drops an edit and launders the entry past
        # E014, which exists precisely to refuse an authored `touched`
        # (pc-eeac). format-v2.md 4.1 calls the derivation the reason
        # GP8/BP4's fabrication result does not apply here; it only holds if
        # every consumer re-derives.
        def derived_touched(entries: list[dict[str, Any]], upto: int,
                            entry: dict[str, Any]) -> list[str]:
            rid = entry["rec"].get("id")
            before = None
            for prior in entries[:upto]:
                r = prior["rec"]
                if r.get("id") == rid:
                    if before is None or r.get("rev", 0) > before.get("rev", 0):
                        before = r
            return diff_fields(before, entry["rec"])

        rebuilt = list(theirs)
        conflicts: list[dict[str, Any]] = []
        skipped_noops: list[str] = []
        already_landed: list[str] = []
        # THE REMEDY THE CONFLICT REFUSAL NAMES (pc-ef5b, round-9 lane B1-F4).
        # The refusal said "re-read the record and re-apply your intent", and
        # following it verbatim does not resolve: the re-applied edit is a NEW
        # local revision on top of the losing one, both touch the field, and
        # the next sync refuses naming BOTH. The missing step is discarding
        # the losing local-only revisions, which no command could do — so the
        # writer names the id here and this command does it, in the open: the
        # discarded revisions are listed with their revs and fields in the
        # result, and an id named with no such conflict is refused rather
        # than accepted as a no-op flag.
        take_landed = list(dict.fromkeys(getattr(args, "take_landed", []) or []))
        discarded: list[dict[str, Any]] = []
        for e in mine:
            rec = e["rec"]
            rid = rec.get("id")
            idx = local.index(e)
            landed_idx = [i for i, t in enumerate(theirs)
                          if i >= common and t["rec"].get("id") == rid]
            per_landed = [(theirs[i]["rec"].get("rev"),
                           set(derived_touched(theirs, i, theirs[i])))
                          for i in landed_idx]
            theirs_touched = {f for _, touched in per_landed for f in touched}
            mine_touched = set(derived_touched(local, idx, e))
            # A REVISION THAT LANDED AS IT STANDS IS ALREADY TRUE REMOTELY
            # (v3.6, pc-4f84768dbed1). A clone that adopted a snapshot holds
            # its writer's unpublished revisions verbatim; when the writer
            # re-chains them onto a later publish and publishes, they land
            # with the same id, rev and content. The conflict test saw the
            # same field on both sides and refused, and its remedy said to
            # discard them and re-apply an intent that had already landed. An
            # identical revision loses nothing and needs nothing re-chained.
            # A creation keeps its own rule below (pc-e499): identical, it
            # is a no-op; different, an id collision.
            if (any(p["rec"].get("id") == rid for p in local[:idx])
                    and any(canonical(theirs[i]["rec"]) == canonical(rec)
                            for i in landed_idx)):
                already_landed.append(rid)
                continue
            overlap = sorted(touched_conflicts(theirs_touched, mine_touched))
            if overlap:
                # `landed_rev` NAMES THE REVISION THAT CONFLICTED (pc-7e5c,
                # round-6 lane B1-F1; contract declared at v2.11). This used
                # to report landed[-1] — the latest landed revision of the
                # id — so with landed rev 2 touching title and rev 3
                # touching owner, a losing concurrent title edit pointed the
                # writer at rev 3, which never touched the field. The
                # pc-1d45 shape: surface accounting with its contract stated
                # nowhere. Contract: the LATEST landed revision whose
                # touched set conflicts with yours — the revision whose
                # edit you must read to reconcile.
                touching = [rev for rev, touched in per_landed
                            if touched_conflicts(touched, mine_touched)]
                if rid in take_landed:
                    discarded.append({"id": rid, "fields": overlap,
                                      "your_rev": rec.get("rev"),
                                      "landed_rev": touching[-1]})
                    continue
                conflicts.append({"id": rid, "fields": overlap,
                                  "your_rev": rec.get("rev"),
                                  "landed_rev": touching[-1]})
                continue
            heads = {}
            for t in rebuilt:
                r = t["rec"]
                if isinstance(r.get("id"), str) and isinstance(r.get("rev"), int):
                    if r["id"] not in heads or r["rev"] > heads[r["id"]]["rev"]:
                        heads[r["id"]] = r
            base = heads.get(rid)
            has_local_base = any(p["rec"].get("id") == rid for p in local[:idx])
            if not mine_touched and base is not None:
                # pc-315f: a revision that changes nothing has an empty touched
                # set, and an empty set intersects nothing, so it was re-chained
                # unconditionally — appending a rev that says the same thing as
                # its predecessor and leaving the log unclean. A no-op does not
                # need re-chaining onto anything; it is already true remotely.
                # EXCEPT a branded one (pc-dacc): a forced revision whose only
                # content is the brand is custody of an escape-hatch use, and
                # audit's forced-write finding enumerates it from the log —
                # skipping it here erased the bypass event before the brand
                # preservation below could carry it. It falls through and
                # re-chains as an empty-touched revision, which the local
                # write path already produces for exactly this case.
                # EXCEPT a CREATION (pc-e499, v2.7): an empty touched set means
                # "no change" only where a local predecessor exists to be
                # unchanged FROM. A rev-1 creation derives touched [] by
                # construction, so the old rule classified a DIVERGENT creation
                # sharing a published id as already-true-remotely and dropped
                # the losing intent under a success report — on exactly the
                # recovery path the CAS refusal directs users to. A creation's
                # content IS its intent: identical to the remote head it is a
                # true no-op; different, it is a conflict the writer must see.
                #
                # THE TWO EXCEPTIONS ARE ORDERED (pc-23e6, round-5 lane C-F1):
                # this whole block used to be guarded by `not rec.get("forced")`,
                # so the pc-dacc custody exception was tested BEFORE the
                # pc-e499 creation comparison and a FORCED divergent creation
                # bypassed the conflict surface entirely — re-chained from the
                # winning record, transferred no fields, and landed as a
                # brand-only revision at exit 0 with the losing title and
                # owner gone. The brand is custody of an escape-hatch use,
                # never an exemption from the conflict test: the creation
                # comparison now runs regardless of the brand, and only a
                # branded entry whose content is already true remotely falls
                # through to re-chain as custody.
                if has_local_base:
                    if not rec.get("forced"):
                        skipped_noops.append(rid)
                        continue
                    # branded no-op revision: falls through, custody (pc-dacc)
                else:
                    divergence = diff_fields(base, rec)
                    if divergence:
                        # NAMED AS WHAT IT IS (pc-1c65). A divergent creation
                        # sharing a published id is two clones MINTING one id
                        # for unrelated records, not a concurrent edit of one
                        # record — there is no field to reconcile. It carried
                        # the same-field remedy, and following that discards a
                        # distinct piece of work and then edits the landed
                        # record with its title. `--take-landed` is refused
                        # for it below rather than honoured: the local record
                        # needs a new id, not a discard.
                        conflicts.append({"id": rid, "kind": "id-collision",
                                          "fields": divergence,
                                          "your_rev": rec.get("rev"),
                                          "landed_rev": base.get("rev")})
                        continue
                    if not rec.get("forced"):
                        skipped_noops.append(rid)
                        continue
                    # branded identical creation: falls through, custody —
                    # the content is already true remotely, the brand is not
            revised = json.loads(canonical(rec))
            if base is not None:
                revised = json.loads(canonical(base))
                for f in sorted(mine_touched):
                    transfer_field(revised, rec, f)
                revised["rev"] = heads[rid]["rev"] + 1
            # pc-f7fc: the force brand is PER-REVISION and never inherited
            # (spec v1.3) — audit enumerates escape-hatch uses from the ledger
            # alone, so a brand acquired by inheritance is a false accusation
            # and one lost in a re-chain is dirt with no provenance. Theorem V7
            # (BlameContainment) is the property at stake. It comes from the
            # writer's own entry, never from the base being re-chained onto.
            # The anchor (v2.5) travels the same way: it is custody of what
            # the WRITER wrote against, so the re-chained revision carries the
            # writer's anchor, never the base's.
            revised.pop("forced", None)
            if rec.get("forced"):
                revised["forced"] = rec["forced"]
            revised.pop("anchor", None)
            revised.pop("anchor_dirty", None)
            for key in ("anchor", "anchor_dirty"):
                if key in rec:
                    revised[key] = rec[key]
            rebuilt.append(make_entry(rebuilt, revised))

        # An id named by --take-landed that had no same-field conflict is a
        # REFUSAL, not a no-op: the flag discards revisions, and a flag that
        # silently matched nothing would be indistinguishable from one that
        # discarded the wrong thing. Ordered before the conflict report so a
        # mistyped id is named even when other ids do conflict.
        unused = [rid for rid in take_landed
                  if rid not in {d["id"] for d in discarded}]
        if unused:
            # An id in `conflicts` as an ID COLLISION is not a mistyped id, and
            # saying "take the ids from its conflicts list" would send the
            # writer back to a list it is already in (pc-1c65). The flag has no
            # meaning there: there is no losing REVISION to discard, only a
            # distinct record that needs a new id.
            colliding = [c["id"] for c in conflicts
                         if c.get("kind") == "id-collision" and c["id"] in unused]
            if colliding:
                return cannot_run(
                    f"--take-landed named {', '.join(colliding)}, which is an ID "
                    f"COLLISION, not a same-field conflict — two clones minted "
                    f"that id for unrelated records. The flag discards a losing "
                    f"REVISION and there is none here; discarding would drop a "
                    f"distinct record. Nothing was written. Re-create your record "
                    f"with `pecia add`, which mints a fresh id (pc-1c65)")
            return cannot_run(
                f"--take-landed named {', '.join(unused)}, which has no "
                f"same-field conflict in this sync — nothing was written. "
                f"Run `pecia sync` first and take the ids from its "
                f"`conflicts` list (pc-ef5b)")
        if conflicts:
            # A REFUSAL REPORTS WHAT IT DID, NOT WHAT IT WOULD HAVE DONE
            # (pc-42c5, round-12 lane B1-F1). This branch is the ATOMIC
            # refusal — another conflict remained, so nothing was written and
            # the log is byte-identical — and it emitted the ids the writer
            # named under `discarded`, a field whose whole meaning is "these
            # revisions are gone". Following the note one id at a time gave
            # `"discarded": [ONE]` with the log unchanged, then
            # `"discarded": [TWO]` with the log unchanged. Same shape as the
            # round-11 pair pc-bc87/pc-2390: a report describing the branch
            # the writer WANTED rather than the branch the code TOOK. The
            # key names the hypothetical as hypothetical; the success branch
            # below still reports `discarded`, because there it happened.
            #
            # AND THE REMEDY CONVERGES (same record, second half). It said
            # "Resolve each id with `pecia sync --take-landed <id>`", and
            # following that literally never terminates: each invocation
            # refuses again, naming the other id. The writer must name EVERY
            # conflicting id in ONE invocation, which the note did not say
            # and now spells out as a command that can be retyped. Bounded
            # like every other participant list (v2.16): the flags are
            # rendered under a budget that states how many it did not name,
            # so a long list cannot eat the sentence that follows it.
            #
            # BUT A BUDGET'S ELISION IS NEVER ARGV (pc-4b34, round-13 lane
            # B1-F1). At thirty conflicts the capped flags ended in "… and 9
            # more of 30 (…digest)", composed INSIDE the backticks, so the
            # command the note printed died at argparse. The elision rule was
            # written for findings, whose tail is read; a command's is run.
            # The note now prints the command only when it fits whole, and
            # otherwise says so and names `remedy_argv`, which is always
            # complete — the same completeness `conflicts` already had.
            collisions = [c for c in conflicts if c.get("kind") == "id-collision"]
            editable = [c for c in conflicts if c.get("kind") != "id-collision"]
            editable_ids = [c["id"] for c in editable]
            flags = capped_seq(editable_ids,
                               render=lambda i: f"--take-landed {i}", sep=" ")
            whole = " ".join(f"--take-landed {i}" for i in editable_ids)
            remedy = (f"`pecia sync {flags}`" if flags == whole else
                      f"`pecia sync` with `--take-landed <id>` for each of the "
                      f"{len(editable_ids)} ids — too many for this note, so the "
                      f"complete command is `remedy_argv` (the arguments after "
                      f"`pecia`)")
            emit({"synced": False, "common_prefix": common,
                  "conflicts": conflicts,
                  **({"remedy_argv": ["sync"] + [a for i in editable_ids
                                                 for a in ("--take-landed", i)]}
                     if editable else {}),
                  **({"would_discard": discarded} if discarded else {}),
                  "note": collision_note(collisions) + ("" if not editable else
                          "same-record same-field appends do not commute — nothing was "
                          "written, and this refusal is atomic: any id you named with "
                          "--take-landed is reported under `would_discard`, which is "
                          "what WOULD have been dropped had the sync completed, and is "
                          "still present. Re-reading the record and re-applying your "
                          "intent does NOT resolve this on its own: the re-applied edit "
                          "is a new local revision on top of the losing one and the next "
                          "sync refuses naming both (pc-ef5b). Name EVERY conflicting id "
                          "in ONE invocation — resolving them one at a time never "
                          f"terminates, because each run refuses on the ones you left "
                          f"out (pc-42c5): {remedy}, which discards your "
                          "local-only revisions of each and names them under `discarded` "
                          "in the result, and then re-apply your intent with "
                          "`pecia edit <id>`")})
            return 1
        # THE RE-CHAIN IS RE-CHECKED IN FULL, not merely re-read. Field
        # disjointness says two appends do not collide; it says nothing about
        # whether their COMPOSITION is clean, and trap 3 is the case where it
        # is not — disjoint records, disjoint fields, an E004 cycle in the
        # union. Nothing is written unless the whole result passes.
        bad = timeline_errors(rebuilt, load_config(), check_snapshot=False)
        if bad:
            emit({"synced": False, "common_prefix": common,
                  "rechained_would_be": len(mine),
                  "refused": [f["code"] for f in bad],
                  "note": "re-chaining these appends onto the remote timeline produces an "
                          "unclean ledger — disjoint fields do not imply a clean composition "
                          "(v2 trap 3). Nothing was written. Resolve the named findings and "
                          "sync again"})
            return 1
        # Same witness rule as the hydrate branch (v2.8, pc-c6b8): a re-chain
        # that does not restore the truncated entries may not erase their
        # last witness either.
        witness = snapshot_truncation_witness(local, rebuilt)
        if witness:
            return cannot_run(f"sync refuses to regenerate the snapshot: "
                              f"{witness}{copy_parts_note(copy, local, theirs)}")
        gone = mark_violation(local)
        if gone and not holds_marked_entry(rebuilt):
            return cannot_run(f"sync refuses: {gone} (E019), and the re-chained "
                              f"timeline does not hold the marked entry either. "
                              f"{MARK_REMEDY}")
        # VALIDATE, THEN RENAME (pc-d373). This wrote the rebuilt log onto the
        # live store and then refused with NO rollback at all — the one of the
        # three writers that did not even restore.
        #
        # Same ordering as the hydrate branch (pc-b652): the register is
        # taken from inside the staged write, before the rename. pc-b4b3's
        # rule — the update-ref return is never discarded, and the ref is
        # read back — lives in advance_register now, one implementation for
        # both branches.
        register_refusal: str | None = None

        def take_register(parsed: list[dict[str, Any]]) -> str | None:
            nonlocal register_refusal
            register_refusal = advance_register(register_before,
                                                remote_commit, parsed, local)
            return register_refusal

        entries, refusal = write_log_validated(target, rebuilt,
                                               before_commit=take_register,
                                               project=True)
        if register_refusal is not None:
            return cannot_run(register_refusal)
        if refusal is not None:
            return cannot_run(f"re-chained log does not read back cleanly: "
                              f"{refusal} — nothing was written; the local "
                              f"log is unchanged")
        # `rechained` COUNTS WRITES, NOT EXAMINATIONS (pc-1d45, round-5
        # lane B1-F2, v2.10). len(mine) counted the examined local suffix,
        # so one actual append beside one identical-creation skip reported
        # rechained 2, skipped_noops 1 — the success surface overstating
        # the write next to the field that says part of it never happened.
        # The contract is declared at v2.10: rechained = entries actually
        # appended beyond the common prefix; skipped_noops = examined
        # no-ops; DISCARDED = revisions --take-landed dropped, named one by
        # one (pc-ef5b); already_landed = revisions that landed as they stand
        # (v3.6) — the four sum to the suffix examined, and a discard never
        # happens without the writer naming the id.
        emit({"synced": True, "common_prefix": common,
              "rechained": len(rebuilt) - len(theirs),
              "skipped_noops": len(skipped_noops),
              **({"already_landed": len(already_landed)} if already_landed else {}),
              **({"discarded": discarded} if discarded else {}),
              "entries": len(entries), "head": log_head_hash(entries), "rule": RULE1})
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pecia",
        description=f"pecia — deterministic work ledger. RULE 1: {RULE1}",
        epilog="Exit codes: 0 clean (warnings allowed) / 1 error findings / 2 cannot-run. "
               "Output is for people; --json prints JSON, one document per "
               "line, for scripts (graph --format mermaid emits Mermaid "
               "source); board and gantt are human views only. "
               "Spec: spec/format-v2.md")
    parser.add_argument("--version", action="version", version=f"pecia {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true",
                       help="print JSON, one document per line, for scripts "
                            "(the default output is for people)")

    p = sub.add_parser("init", help="create the timeline and its snapshot projection — "
                       "in .pecia/, or in the pinned store when PECIA_LOG_DIR is set "
                       "(and strip any v1 merge attribute)")
    add_json_flag(p); p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", help="create a record (only type and title required — rule 3)")
    p.add_argument("--type", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--priority", type=int, default=2, choices=range(0, 5))
    p.add_argument("--owner", default=default_owner())
    p.add_argument("--body", default="")
    p.add_argument("--target", help="milestone target date YYYY-MM-DD")
    p.add_argument("--context",
                   help="a <scheme>:<target> reference into a shared orientation "
                        "document (v2.5), e.g. doc:docs/orientation.md#anchor; "
                        "the scheme must be declared in resolvers")
    p.add_argument("--blocks", action="append")
    p.add_argument("--retires", action="append",
                   help="an id this record's work resolves: closing this record closes it")
    for key in SCALAR_EDGES:
        p.add_argument(f"--{key.replace('_', '-')}", dest=key)
    p.add_argument("--label", action="append")
    p.add_argument("--force", action="store_true",
                   help="bypass the write gate; brands the revision forced:true (checker still sees everything)")
    add_json_flag(p); p.set_defaults(func=cmd_add)

    p = sub.add_parser("edit", help="append a new revision with changes")
    p.add_argument("id")
    p.add_argument("--title"); p.add_argument("--status"); p.add_argument("--body")
    p.add_argument("--owner"); p.add_argument("--evidence"); p.add_argument("--disposition")
    p.add_argument("--target"); p.add_argument("--priority", type=int, choices=range(0, 5))
    p.add_argument("--context",
                   help="a <scheme>:<target> reference into a shared orientation "
                        "document (v2.5); 'none' clears it")
    p.add_argument("--ratify", action="store_true",
                   help="mark this decision record accepted by the human: stamps "
                        "ratified_by (PECIA_OWNER or the login user) and today's "
                        "date in a new revision (v2.5). Decision records only; "
                        "never required at creation (rule 3)")
    p.add_argument("--blocks", action="append",
                   help="id; repeat to add several. `--blocks none` clears the list")
    p.add_argument("--retires", action="append",
                   help="replace the retires list. Refused by E012 if this record is "
                        "already terminal and a target is not — use --also-closes for that")
    p.add_argument("--also-closes", action="append", dest="also_closes",
                   help="add to retires AND close the target in the same atomic write, "
                        "inheriting this record's disposition. Requires this record to be "
                        "terminal already (the retroactive case); emits a JSON list")
    for key in SCALAR_EDGES:
        p.add_argument(f"--{key.replace('_', '-')}", dest=key, help="id, or 'none' to clear")
    p.add_argument("--no-edges", action="store_true", dest="no_edges",
                   help="declare this record intentionally has no edges")
    p.add_argument("--label", action="append")
    p.add_argument("--force", action="store_true",
                   help="bypass the write gate; brands the revision forced:true (checker still sees everything)")
    add_json_flag(p); p.set_defaults(func=cmd_edit)

    p = sub.add_parser("close", help="append a terminal revision (disposition required)")
    p.add_argument("id")
    p.add_argument("--disposition", required=True)
    p.add_argument("--status", choices=sorted(TERMINAL_STATUSES), default="done")
    p.add_argument("--evidence")
    p.add_argument("--also-closes", action="append", dest="also_closes",
                   help="an id this closure also retires: adds the retires edge AND closes "
                        "the target in the same atomic write, giving it this disposition. "
                        "Emits a JSON list, in write order (targets first)")
    p.add_argument("--force", action="store_true",
                   help="bypass the write gate; brands the revision forced:true (checker still sees everything)")
    add_json_flag(p); p.set_defaults(func=cmd_close)

    p = sub.add_parser("check", help=f"structural checker (E001-E009, E011-E017). {RULE1}")
    p.add_argument("--ledger", help="path override (default .pecia/work.jsonl)")
    p.add_argument("--config", help="config override, so a staged ledger is checked against its staged config, never a worktree one")
    add_json_flag(p); p.set_defaults(func=cmd_check)

    for name, fn, extra in (("ready", cmd_ready, None), ("blocked", cmd_blocked, None),
                            ("graph", cmd_graph, "format")):
        p = sub.add_parser(name)
        if extra == "format":
            p.add_argument("--format", choices=["json", "mermaid"],
                           help="json or mermaid; without it graph prints "
                                "for people, or JSON with --json")
            # v2.8 (pc-fc28): an explicit --format wins over --json, so
            # `--format mermaid --json` emits Mermaid, and the flag's help
            # says so rather than contradicting it.
            p.add_argument("--json", action="store_true",
                           help="print JSON for scripts; an explicit --format "
                                "wins (the default output is for people)")
        else:
            add_json_flag(p)
        p.set_defaults(func=fn)

    p = sub.add_parser("show", help="one record as stored, every field (v3.3)")
    p.add_argument("id")
    p.add_argument("--history", action="store_true",
                   help="every entry that revised it, in log order, with its touched set")
    add_json_flag(p); p.set_defaults(func=cmd_show)

    p = sub.add_parser("gantt", help="milestone timeline (ASCII by default; "
                       "--mermaid for the portable projection)")
    p.add_argument("--mermaid", action="store_true",
                   help="emit Mermaid gantt source instead — for docs/CI, not a terminal")
    p.add_argument("--width", type=int,
                   help=f"override terminal width; the value is clamped to "
                        f"{BOARD_MIN_WIDTH}-{BOARD_MAX_WIDTH} columns, because the "
                        f"frame does not fit below {BOARD_MIN_WIDTH} and stops being "
                        f"readable above {BOARD_MAX_WIDTH} (piped output is fixed at "
                        f"{BOARD_PIPED_WIDTH})")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour; NO_COLOR is honoured too")
    p.set_defaults(func=cmd_gantt)

    p = sub.add_parser("next", help="ready work, priorities 0-3, total order")
    p.add_argument("--limit", type=int, default=10)
    add_json_flag(p); p.set_defaults(func=cmd_next)

    p = sub.add_parser("audit", help="advisory anti-rot surface + sampled truth audit")
    p.add_argument("--sample", type=int, default=0,
                   help="deterministic sample of closed records for human truth-audit")
    p.add_argument("--historical", action="store_true",
                   help="list prose-only-linkage findings on records closed "
                        "before the retires edge landed (v1.13); by default "
                        "they are one counted line (D12)")
    add_json_flag(p); p.set_defaults(func=cmd_audit)

    p = sub.add_parser("board", help="read-only human projection (ANSI; not JSON, like gantt)")
    p.add_argument("--width", type=int,
                   help=f"override terminal width; the value is clamped to "
                        f"{BOARD_MIN_WIDTH}-{BOARD_MAX_WIDTH} columns, because the "
                        f"frame does not fit below {BOARD_MIN_WIDTH} and stops being "
                        f"readable above {BOARD_MAX_WIDTH} (piped output is fixed at "
                        f"{BOARD_PIPED_WIDTH})")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour; NO_COLOR is honoured too")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("doctor", help="is the enforcement ACTIVE? (posture, never correctness)")
    p.add_argument("--fix", action="store_true",
                   help="apply the configuration fixes (hooks path, ignore rule, v1 merge attribute removal)")
    add_json_flag(p); p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("publish", help="publish the timeline to refs/pecia/log and the remote")
    p.add_argument("--remote", help="remote name (default: the first configured)")
    add_json_flag(p); p.set_defaults(func=cmd_publish)

    p = sub.add_parser("sync", help="re-chain onto a remote timeline that advanced first")
    p.add_argument("--remote", help="remote name (default: the first configured)")
    p.add_argument("--take-landed", action="append", default=[], metavar="ID",
                   help="resolve a same-field conflict on ID by taking the "
                        "LANDED revision: your local-only revisions of it are "
                        "discarded, each named with its rev and fields in the "
                        "result. Repeat for several ids. Refused if ID has no "
                        "such conflict (pc-ef5b)")
    add_json_flag(p); p.set_defaults(func=cmd_sync)

    p = sub.add_parser("snapshot", help="regenerate .pecia/work.jsonl from the log")
    add_json_flag(p); p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("migrate", help="reconstruct the v2 single timeline from all refs (pc-5c7c)")
    p.add_argument("--dry-run", action="store_true",
                   help="enumerate what would be written without writing it")
    p.add_argument("--force", action="store_true",
                   help="rewrite an existing log — a timeline rewrite, never routine")
    p.add_argument("--force-drop", action="store_true",
                   help="accept erasing entries that exist only in the log (pc-824a)")
    p.add_argument("--remote", help="remote name to check the published timeline "
                   "against when no local ref exists (default: the first configured)")
    add_json_flag(p); p.set_defaults(func=cmd_migrate)

    return parser


def main() -> int:
    # On POSIX, Python decodes argv with the process locale. The CLI format is
    # UTF-8; recover the original argument bytes before interpreting values.
    argv = [os.fsencode(arg).decode("utf-8", errors="surrogateescape")
            for arg in sys.argv[1:]]
    args = build_parser().parse_args(argv)
    # Paths and Git remote names are OS objects, not record text. Restore the
    # filesystem-decoded form so Path and subprocess pass the original bytes
    # back to the OS under a legacy locale.
    for name in ("ledger", "config", "remote"):
        value = getattr(args, name, None)
        if isinstance(value, str):
            setattr(args, name, os.fsdecode(value.encode("utf-8", "surrogateescape")))
    OUTPUT["json"] = (bool(getattr(args, "json", False))
                      or getattr(args, "format", None) == "json")
    OUTPUT["command"] = args.command
    OUTPUT["history"] = bool(getattr(args, "history", False))
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        return cannot_run(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
