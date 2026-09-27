#!/usr/bin/env python3
"""Vocabulary allocation gate — pc-e9cf.

E-codes and spec amendment levels are hand-assigned. Record ids are not: they
are hash-minted with a nonce precisely so two agents on two branches cannot
collide, a lesson taken from Backlog.md at v0. That lesson was applied once,
to the ids, and never generalised — so the E-code namespace collided for real
(two independent `E012`s) and so did the amendment namespace (two independent
`v1.8`s), both discovered at merge.

WHAT THIS IS, AND THE TWO SHAPES IT IS NOT
------------------------------------------
Two earlier designs for this gate were built and rejected by adversarial
review before either shipped. Both failures are worth stating, because both
looked like tuning problems from the inside and neither was:

1. Infer intent from the prose around a bullet — "does this paragraph say
   `renumbered` or `supersedes`?". Unfixable: the input is a human sentence.
   It produced seven false positives on the untouched specs and still passed
   an unrelated `E016` that merely contained the word "supersedes".

2. Author a registry keyed by code — `{"E016": {...}, "E016": {...}}`.
   Unfixable for a subtler reason: **duplicate JSON keys collapse.** After
   `json.load`, one entry survives, so "no two entries share a code" is true
   by construction and the structure cannot represent the very state it
   exists to detect.

What survives both is this: the spec DECLARES each allocation event in a
machine-readable marker, and allocations are held in an **array**, where two
live `E016` events remain two elements and collide no matter what words
surround them or which record id they cite.

    <!-- vocab: {"action":"mint","code":"E012","at":"v1.13","by":"pc-4d19"} -->

`spec/vocabulary.json` is GENERATED from those markers, never authored. It is
a projection, so it cannot drift from the specs — the drift is impossible
rather than merely checked, which is the difference between GP28 satisfied by
construction and GP28 satisfied by argument.

Usage:
    dev/vocab-check.py [--write] [--spec PATH ...] [--source PATH]
                       [--registry PATH]

Exit 0 clean (warnings allowed) / 1 findings / 2 cannot-run.
Exit 0 means "well-formed," never "true" — rule 1 applies here as everywhere.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MARKER = re.compile(r"<!--\s*vocab:\s*(\{.*?\})\s*-->")
BULLET = re.compile(r"^\s*[-*]\s+\*{0,2}(E\d{3}|D\d{3})\b")
HEADER = re.compile(r"^##+\s+v(\d+)\.(\d+)\s+amendments", re.IGNORECASE)
CODE = re.compile(r"\b([ED]\d{3})\b")
ACTIONS = {"mint", "amend", "delete", "renumber"}

findings: list[dict] = []


def finding(code: str, message: str, **extra) -> None:
    findings.append({"severity": "error", "code": code, "message": message, **extra})


def cannot_run(message: str) -> int:
    print(json.dumps({"severity": "fatal", "code": "V000", "message": message}))
    return 2


# --------------------------------------------------------------- extraction

def parse_markers(text: str, path: str) -> list[dict]:
    """Allocation events, in document order, each tagged with where it came
    from. The owning bullet supplies `meaning` for a mint — authored once, in
    the prose, and merely transcribed here."""
    out: list[dict] = []
    lines = text.splitlines()
    owner_code: str | None = None
    owner_text: list[str] = []
    fenced = False
    for n, line in enumerate(lines, 1):
        # A FENCED BLOCK IS AN EXAMPLE, NOT AN ALLOCATION. The spec has to be
        # able to SHOW the marker syntax while documenting it, and the v2.3
        # amendment does exactly that — its illustrative
        # `{"code":"E012",...}` was read as a real mint on the first run,
        # silently overwriting the genuine one because it happened to carry
        # the same minter. A different `by` would have manufactured a
        # collision out of documentation.
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        b = BULLET.match(line)
        if b:
            owner_code, owner_text = b.group(1), [line]
        elif owner_code and line.strip() and not MARKER.search(line):
            owner_text.append(line)
        for m in MARKER.finditer(line):
            try:
                ev = json.loads(m.group(1))
            except json.JSONDecodeError as exc:
                finding("V001", f"{path}:{n} marker is not valid JSON: {exc}")
                continue
            # TYPED, NOT MERELY PRESENT (pc-1a93 sibling, same commit). Every
            # marker value is a string by construction, and nothing checked:
            # a non-string reached `in ACTIONS`, a set membership test, and
            # `{"at":["v2.8"]}` died as `TypeError: unhashable type: 'list'` —
            # a traceback out of a registered commit gate, where the contract
            # says a malformed input is a FINDING. Checked here, once, before
            # any value is used as a key, compared, or written to the registry.
            if not isinstance(ev, dict):
                finding("V001", f"{path}:{n} marker must be a JSON object, "
                                f"not a {type(ev).__name__}")
                continue
            mistyped = sorted(k for k, v in ev.items() if not isinstance(v, str))
            if mistyped:
                finding("V001", f"{path}:{n} marker field(s) "
                                f"{', '.join(repr(k) for k in mistyped)} must "
                                f"be strings — a marker's values are its "
                                f"vocabulary, never structures")
                continue
            if ev.get("action") not in ACTIONS:
                finding("V001", f"{path}:{n} marker has no valid action "
                                f"(expected one of {sorted(ACTIONS)})")
                continue
            if not CODE.fullmatch(str(ev.get("code", ""))):
                finding("V001", f"{path}:{n} marker has no valid code")
                continue
            ev["_file"], ev["_line"] = path, n
            # A gloss travels with MINT and AMEND alike (pc-439d): capturing
            # it only at mint left the registry's meanings frozen at
            # mint-time while amendments moved the semantics \u2014 E002 still
            # read as v1's merge=union warning after v2 re-founded it as a
            # corruption error. The LATEST gloss wins in the meanings view
            # below.
            if ev["action"] in ("mint", "amend") and owner_code == ev["code"]:
                gloss = " ".join(" ".join(owner_text).split())
                # THE BOLD SPAN IS TAKEN WHOLE (pc-d935's aside, round-10
                # lane A-F1). Two of the nineteen owner bullets open their
                # bold span over the code AND its name \u2014 `- **E016
                # closed-while-blocked** \u2014 \u2026` \u2014 and stripping the code alone
                # left the span's closing `**` inside the served gloss, so
                # E000 and E016 served `cannot-run** \u2014 \u2026` and
                # `closed-while-blocked** \u2014 \u2026` while the other seventeen,
                # whose span covers the code alone, did not. The name is
                # part of the meaning and is kept; only the markup goes.
                named = re.match(r"^[-*]\s+\*\*([ED]\d{3})([^*]*)\*\*\s*", gloss)
                if named:
                    rest = gloss[named.end():]
                    name = re.sub(r"^[—\-:]\s*", "",
                                  named.group(2).strip())
                    # The text that followed the span is left exactly as the
                    # bullet wrote it \u2014 including its em-dash, which is the
                    # separator the name needs. A span covering the code
                    # ALONE has no name to separate, and there the dash is
                    # punctuation between the code and its gloss, dropped
                    # with the code as it always was.
                    if not name:
                        gloss = re.sub(r"^[\u2014\-:]\s*", "", rest)
                    elif rest[:1] in (":", ";", ",", "."):
                        gloss = f"{name}{rest}".strip()
                    else:
                        gloss = f"{name} {rest}".strip()
                else:
                    gloss = re.sub(r"^[-*]\s+\*{0,2}[ED]\d{3}\**\s*", "", gloss)
                    gloss = re.sub(r"^[\u2014\-:]\s*", "", gloss)
                # SERVED WHOLE (pc-8704, round-6 lane A-F3): a [:160] here
                # cut mid-word \u2014 E014 served "presence-awa", E015 "content
                # mat" \u2014 truncating the v2.9/v2.10 qualifications out of
                # the machine-readable register claim 11 says carries the
                # LATEST gloss per live code, with nothing declaring the
                # bound. The gloss is one normalized spec bullet; the spec
                # is the register's source and the register serves what
                # the spec states, entire.
                ev["meaning"] = gloss
            out.append(ev)
    return out


def parse_headers(text: str) -> list[str]:
    return [f"v{m.group(1)}.{m.group(2)}" for line in text.splitlines()
            if (m := HEADER.match(line))]


def emitted_codes(source: str, path: str) -> set[str] | None:
    """Codes the implementation actually emits, by AST — never by grep.

    A grep over the source counts codes named in comments and docstrings, and
    on this repository that population is non-empty and nearly complete: an
    extractor reading the wrong surface would report a healthy non-zero census
    while measuring nothing. Only literal arguments at call sites count.

    Returns None (cannot-run) if any call site builds a code dynamically —
    silence about an unreadable producer is how a gate reports a confident
    wrong answer."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        finding("V002", f"{path} does not parse: {exc}")
        return None
    out: set[str] = set()

    # A bare `"E002"` literal is a code; prose cannot produce one, because a
    # docstring or comment mentioning E002 is a LONGER string and `fullmatch`
    # rejects it. That is what makes this immune to the wrong-surface census.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CODE.fullmatch(node.value):
                out.add(node.value)

    # Dynamic producers are checked ONLY in the code POSITION of the emitters.
    # Scanning every argument flags message f-strings, which is a false
    # cannot-run — and a gate that refuses to run on healthy input is as
    # useless as one that passes on broken input.
    #
    # WHAT COUNTS AS UNREADABLE. The distinction is between an expression that
    # CONSTRUCTS a code at runtime and one that merely CARRIES a code minted
    # elsewhere. Only the first is invisible to the literal scan above:
    #
    #   finding("error", "E002", ...)          literal      — captured
    #   finding(severity, code, ...)           forwarder     — `report`'s wrapper
    #   finding("error", f["code"], ...)       relay         — the write gate
    #                                                          re-emitting a
    #                                                          code run_checks
    #                                                          already produced
    #   finding("error", f"E{n:03d}", ...)     CONSTRUCTED   — unreadable
    #
    # Forwarders and relays are safe because the code they pass began as a
    # literal somewhere the scan already saw. Flagging them was a false
    # cannot-run that made the gate refuse healthy input twice while I built
    # it. The residual bound, stated rather than hidden: a code assembled from
    # parts and stored in a variable before emission would be missed — no
    # such site exists today (34 emit sites, all literal), and introducing one
    # requires a shape a reviewer can see.
    def constructs_a_string(n: ast.AST) -> bool:
        if isinstance(n, (ast.JoinedStr, ast.BinOp)):
            return True
        if isinstance(n, ast.Call):
            attr = getattr(n.func, "attr", None)
            return attr in ("format", "join")
        return False

    bad: list[tuple[int, str]] = []

    def descend(node: ast.AST, params: frozenset[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            params = frozenset(p.arg for p in a.args + a.kwonlyargs + a.posonlyargs)
        if isinstance(node, ast.Call):
            fn = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fn in ("finding", "report") and len(node.args) >= 2:
                if constructs_a_string(node.args[1]):
                    seg = ast.get_source_segment(source, node.args[1]) or "<expr>"
                    bad.append((node.lineno, seg[:60]))
        for child in ast.iter_child_nodes(node):
            descend(child, params)

    descend(tree, frozenset())
    if bad:
        line, seg = bad[0]
        finding("V002", f"{path}:{line} builds a diagnostic code dynamically "
                        f"({seg}) — cannot verify")
        return None
    return out


def declared_d_codes(source: str) -> dict[str, str]:
    """The D_CODES table in pecia_cli.py — D-codes' single authored home."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "D_CODES":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "D_CODES" for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


# ------------------------------------------------------------------- checks

def build_registry(events: list[dict], d_codes: dict[str, str]) -> dict:
    """The generated projection. Allocations are an ARRAY — see the module
    docstring for why a map keyed by code cannot hold a collision.

    `meanings` is the CURRENT-semantics view (pc-439d): for each live code,
    the latest event carrying a gloss wins, so an amendment that moved a
    code's meaning moves the registry too instead of leaving the mint-time
    gloss standing as if current. Still a projection of the specs — the
    specs are canonical for meaning; this file may never be cited against
    them. `d_codes` is passed in as the SPEC-derived view since v2.8
    (pc-085ad): the D-codes are minted in the specs like the E-codes, and
    the implementation's D_CODES table is held to them by V014 — the
    registry no longer transcribes the implementation."""
    allocations = []
    for ev in events:
        entry = {"code": ev["code"], "action": ev["action"], "at": ev["at"]}
        if "by" in ev:
            entry["by"] = ev["by"]
        if "of" in ev:
            entry["of"] = ev["of"]
        if "to" in ev:
            entry["to"] = ev["to"]
        if "meaning" in ev:
            entry["meaning"] = ev["meaning"]
        entry["source"] = f"{ev['_file']}:{ev['_line']}"
        allocations.append(entry)
    live = live_codes(events)
    meanings: dict[str, str] = {}
    for ev in events:
        # E-codes only: the D family's current glosses are the d_codes view
        # below — one projection per family, not two drifting copies.
        if ev["code"] in live and ev["code"].startswith("E") and "meaning" in ev:
            meanings[ev["code"]] = ev["meaning"]
    return {
        "note": "GENERATED by dev/vocab-check.py from <!-- vocab: --> markers "
                "in the specs. Do not edit. The specs are canonical for "
                "meaning — for D-codes too since v2.8 (pc-085ad), with "
                "pecia_cli.py's D_CODES held to the spec by V014; `meanings` "
                "is the latest gloss per live E-code, a projection that may "
                "never be cited against the specs (pc-439d).",
        "allocations": allocations,
        "meanings": dict(sorted(meanings.items())),
        "d_codes": dict(sorted(d_codes.items())),
    }


def live_codes(events: list[dict]) -> set[str]:
    """The live code set, side-effect-free — build_registry's half of what
    allocations() computes. allocations() reports V011/V012 findings as it
    walks, so calling it twice would double-report; this walks the same
    events and judges nothing."""
    live: set[tuple[str, str]] = set()
    for ev in events:
        if ev["action"] == "mint":
            live.add((ev["code"], ev.get("by", "?")))
        elif ev["action"] in ("delete", "renumber"):
            live.discard((ev["code"], ev.get("of")))
    return {code for code, _ in live}


def allocations(events: list[dict]) -> dict[tuple[str, str], dict]:
    """Live allocations, keyed by (code, MINTER) — never by code alone.

    An allocation is a code claimed by a particular minter, and that is the
    whole reason this gate can see a collision at all. Keying by code alone
    conflates two different claims on one number, which is exactly the state
    that must be detectable: `E012` was allocated twice, by `pc-4d19` for an
    expired retirement promise and by `pc-0033` for a chain break, on branches
    neither of which could see the other.

    A `delete` or `renumber` therefore names WHICH allocation it retires, via
    `of` — the predecessor anchor. Without it, v2 renumbering its own draft
    `E012` silently retired v1.13's `E012`, which stands. That is not a
    hypothetical: it is what this function did in its first draft, and the
    gate reported the live code as dead."""
    live: dict[tuple[str, str], dict] = {}
    for ev in events:
        if ev["action"] == "mint":
            live[(ev["code"], ev.get("by", "?"))] = ev
        elif ev["action"] in ("delete", "renumber"):
            target = ev.get("of")
            if target is None:
                finding("V011", f"{ev['_file']}:{ev['_line']} {ev['action']}s "
                                f"{ev['code']} without naming which allocation "
                                f"(`of` is required)", id=ev["code"])
                continue
            if live.pop((ev["code"], target), None) is None:
                finding("V012", f"{ev['_file']}:{ev['_line']} {ev['action']}s "
                                f"{ev['code']} minted by {target}, which is not "
                                f"a live allocation", id=ev["code"])
    return live


def main() -> int:
    ap = argparse.ArgumentParser(prog="vocab-check")
    ap.add_argument("--spec", action="append", default=None)
    ap.add_argument("--source", default=str(ROOT / "pecia_cli.py"))
    ap.add_argument("--registry", default=str(ROOT / "spec" / "vocabulary.json"))
    ap.add_argument("--write", action="store_true",
                    help="regenerate the registry instead of verifying it")
    args = ap.parse_args()

    specs = args.spec or [str(ROOT / "spec" / "format-v1.md"),
                          str(ROOT / "spec" / "format-v2.md")]

    events: list[dict] = []
    headers: list[str] = []
    for s in specs:
        p = Path(s)
        if not p.exists():
            return cannot_run(f"spec not found: {s}")
        text = p.read_text()
        label = Path(s).name
        events += parse_markers(text, label)
        headers += parse_headers(text)

    try:
        source = Path(args.source).read_text()
    except OSError as exc:
        return cannot_run(f"cannot read {args.source}: {exc}")

    emitted = emitted_codes(source, Path(args.source).name)
    if emitted is None:
        # Print WHY. An earlier draft returned 2 here silently, which is a
        # gate that refuses to run and declines to say so — indistinguishable
        # at a glance from a crash, and useless in a hook's output.
        for f in findings:
            print(json.dumps(f, sort_keys=True))
        return cannot_run("cannot determine the emitted code vocabulary; "
                          "see the finding above")
    d_codes = declared_d_codes(source)

    # -- census FIRST: a zero denominator is cannot-run, never clean ---------
    census = {"markers": len(events), "amendment_headers": len(headers),
              "emitted_codes": len(emitted), "declared_d_codes": len(d_codes)}
    # Zero is cannot-run for the populations that MUST exist for the gate to
    # have measured anything. `declared_d_codes` is deliberately NOT among
    # them: a spec fixture with no D-codes is legitimate, and what matters
    # there is AGREEMENT with the emit sites (V009), not presence. Requiring
    # presence made every fixture in the kill matrix unrunnable — a guard
    # strict enough to refuse healthy input is as useless as one that passes
    # broken input.
    for name in ("markers", "amendment_headers", "emitted_codes"):
        if census[name] == 0:
            return cannot_run(f"census {name} is zero — the extractor found "
                              f"nothing, which is unreadable input or a broken "
                              f"reader, never a clean result")

    # 1. DOUBLE-MINT: two allocations of one code live at the same time.
    #    Computed over the allocation array, so it cannot be defeated by the
    #    words around a bullet, by two mints sharing a `pc` id, or — the
    #    failure that killed the previous design — by a keyed map silently
    #    collapsing duplicates at parse time.
    live_allocs = allocations(events)
    by_code: dict[str, list[tuple[str, str]]] = {}
    for (code, minter) in live_allocs:
        by_code.setdefault(code, []).append((code, minter))
    for code, claims in sorted(by_code.items()):
        if len(claims) > 1:
            who = ", ".join(sorted(m for _, m in claims))
            finding("V003", f"{code} has {len(claims)} live allocations "
                            f"({who}) — one code, two claims", id=code)

    # 2. amendment levels are unique
    for lvl in sorted({h for h in headers if headers.count(h) > 1}):
        finding("V004", f"amendment level {lvl} is declared "
                        f"{headers.count(lvl)} times", id=lvl)

    # 3. every level a marker cites exists as a header (or is a base level)
    known = set(headers) | {"v1", "v2"}
    for ev in events:
        if ev.get("at") not in known:
            finding("V005", f"{ev['_file']}:{ev['_line']} cites amendment level "
                            f"{ev.get('at')!r}, which no spec declares", id=ev["code"])

    # 4. spec -> registry: every code named in a spec bullet has a marker.
    #    D bullets included since v2.8 (pc-085ad): the D-codes are minted in
    #    the specs now, so a D bullet without a marker is the same
    #    prose-only allocation V006 exists to refuse.
    marked = {ev["code"] for ev in events}
    for s in specs:
        for n, line in enumerate(Path(s).read_text().splitlines(), 1):
            b = BULLET.match(line)
            if b and b.group(1) not in marked:
                finding("V006", f"{Path(s).name}:{n} defines {b.group(1)} in prose "
                                f"with no vocab marker", id=b.group(1))

    # 5. implementation <-> registry, both directions
    live = {c for c, _ in live_allocs} | set(d_codes)
    for code in sorted(emitted - live):
        finding("V007", f"{code} is emitted by the implementation but is not a "
                        f"live registered code", id=code)
    for code in sorted(live - emitted):
        finding("V008", f"{code} is registered live but the implementation "
                        f"never emits it", id=code)
    for code in sorted(set(d_codes) ^ {c for c in emitted if c.startswith("D")}):
        finding("V009", f"{code} disagrees between D_CODES and its emit sites",
                id=code)

    # 6. THE LATEST ALLOCATION OWNS THE GLOSS (V013, v2.8, pc-c05a). The
    #    meanings view serves the latest gloss per live code — but a mint or
    #    amend marker not owned by a same-code bullet records no gloss, so
    #    combined amendment prose silently produced meaning-less events and
    #    meanings kept serving an OLDER gloss as current, green (18 such
    #    events, 8 live codes at the finding). pc-130e closed one instance
    #    by regenerating one base bullet; this closes the channel: every
    #    live code's latest mint/amend event must own its gloss, so an
    #    amendment that moves semantics restates the meaning where the
    #    machine reads it.
    live_set = live_codes(events)
    latest: dict[str, dict] = {}
    for ev in events:
        if ev["action"] in ("mint", "amend"):
            latest[ev["code"]] = ev
    for code in sorted(live_set):
        ev = latest.get(code)
        if ev is not None and "meaning" not in ev:
            finding("V013",
                    f"{code}'s latest {ev['action']} "
                    f"({ev['_file']}:{ev['_line']}) carries no gloss — the "
                    f"registry would serve an older gloss as current "
                    f"(pc-c05a). Own the marker with a {code} bullet "
                    f"restating the current meaning at the amending level",
                    id=code)

    # 6b. A SERVING GLOSS IS A STATEMENT, NOT A FRAGMENT (V015, v2.9,
    #     pc-fea8). V013 requires the latest event to OWN a gloss; it said
    #     nothing about the gloss being USABLE, so a combined amendment
    #     bullet ("E006/E007 are state checks ...") donated its captured
    #     tail to E006 as the dangling fragment "/E007 are ..." — never
    #     stating E006's own condition — and the registry served it green
    #     (E008 served "= contiguous ..." the same way). The mechanical
    #     signature of a captured fragment is a leading JOINER — the "/" or
    #     "=" that spliced the tail to the token the parser consumed; a
    #     real gloss opens with a word, a path, or a quoted name, never
    #     with a joiner. A declared heuristic: it catches the splice
    #     signature, not every conceivable fragment. Only the SERVING
    #     gloss (the latest event's) is held to it; historical fragments
    #     stay as custody.
    for code in sorted(live_set):
        ev = latest.get(code)
        if (ev is not None and "meaning" in ev
                and ev["meaning"][:1] in "/="):
            finding("V015",
                    f"{code}'s serving gloss begins with "
                    f"{ev['meaning'][:16]!r} — a fragment captured from a "
                    f"combined bullet, not a statement of {code}'s own "
                    f"condition (pc-fea8). Restate the meaning in a bullet "
                    f"{code} owns at the amending level",
                    id=code)

    # 7. D-CODES ARE SPEC-CANONICAL (V014, v2.8, pc-085ad). The registry
    #    declared "the specs are canonical for meaning" while every D-code
    #    meaning was read from pecia_cli.py's D_CODES dict — an
    #    implementation-only change to a D-code's meaning regenerated
    #    cleanly. The D-codes are minted in the specs now; the registry's
    #    d_codes view is generated from the SPEC, and the implementation
    #    table must match it byte-for-byte, so a change to either side
    #    alone is refused.
    spec_d: dict[str, str] = {}
    for ev in events:
        if (ev["code"].startswith("D") and ev["code"] in live_set
                and ev["action"] in ("mint", "amend") and "meaning" in ev):
            spec_d[ev["code"]] = ev["meaning"]
    for code in sorted(set(d_codes) - set(spec_d)):
        finding("V014", f"{code} is declared in D_CODES with no spec-owned "
                        f"meaning — the specs are canonical for D-code "
                        f"meaning (pc-085ad); mint it in the spec", id=code)
    for code in sorted(set(spec_d) - set(d_codes)):
        finding("V014", f"{code} is minted in the specs and absent from "
                        f"D_CODES — the implementation table must mirror "
                        f"the spec (pc-085ad)", id=code)
    for code in sorted(set(spec_d) & set(d_codes)):
        if spec_d[code] != d_codes[code]:
            finding("V014", f"{code}'s meaning differs between its spec "
                            f"bullet and D_CODES — the specs are canonical; "
                            f"change the spec and mirror it in the same "
                            f"commit (pc-085ad)", id=code)

    # -- registry: generate, or verify byte-identical ------------------------
    registry = build_registry(events, spec_d)
    rendered = json.dumps(registry, indent=2, sort_keys=False) + "\n"
    reg_path = Path(args.registry)
    if args.write:
        reg_path.write_text(rendered)
    else:
        if not reg_path.exists():
            finding("V010", f"{reg_path.name} is missing — run --write")
        elif reg_path.read_text() != rendered:
            finding("V010", f"{reg_path.name} is not what the markers generate "
                            f"— it was edited by hand, or the specs moved "
                            f"without regenerating (run --write)")

    for f in findings:
        print(json.dumps(f, sort_keys=True))
    print(json.dumps({"census": census, "ok": not findings,
                      "note": 'Exit 0 means "well-formed," never "true."'},
                     sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
