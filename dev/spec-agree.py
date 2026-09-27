#!/usr/bin/env python3
"""Spec/implementation agreement check — pc-ae3a.

WHAT THIS ANSWERS. `spec/format-v2.md` is 3,455 lines: 418 of base text and
seventeen dated amendment sittings below it. The amendments restate meaning in
their own sections rather than editing the base, so the base has been moved out
from under itself — base section 8, "the checker contract", names 14 of the 30
codes the tool actually emits and not one of the twelve D-codes. A reader who
takes the base at its word builds the wrong thing. This derives the contract
TWICE, from two sources that do not consult each other, and reports where the
two derivations disagree.

pc-a546's fix shape named two options and only the first was taken: a normative
supersession rule (2026-08-18), which that record itself says "just licenses the
drift". This is the second — GP28 satisfied by an agreement check rather than by
argument.

THE TWO DERIVATIONS, AND WHY THEY ARE INDEPENDENT

  D1, document side. Reads `spec/format-v2.md` and nothing else. The live code
  set is computed from the `<!-- vocab: -->` allocation markers (mint/amend/
  delete/renumber), which is the same substrate `dev/vocab-check.py` reads —
  sharing a LOCATOR is not sharing a derivation, since what is being compared
  is the contract each side states, not where the text sits.

  D2, artifact side. Reads `spec/record.schema.json`, `pecia_cli.py` and
  `tests/test_pecia.py`. It deliberately does NOT read `spec/vocabulary.json`,
  though that file is the most convenient summary available: vocabulary.json is
  GENERATED FROM THE SPEC's markers, so using it here would put the document on
  both sides of the comparison and the check would agree with itself. It is
  reported separately, as a third reference point, never as evidence.

WHAT IT REFUSES TO DECIDE. A code named in the tests is not thereby documented:
the suite asserts E010's ABSENCE (`assertNotIn`), which is the correct null arm
for a code deleted at v2 along with the state it reported, and a bare mention
scan files it as drift. Presence and absence assertions are separated, and a
code seen only in an absence assertion is not counted as tested-present. Rule 1
applies here as everywhere: exit 0 means the two derivations agree ON WHAT WAS
COMPARED, never that either one is true.

Usage:
    dev/spec-agree.py [--json] [--spec PATH] [--verbose]

Exit 0 agreement (notes allowed) / 1 disagreements / 2 cannot-run.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MARKER = re.compile(r"<!--\s*vocab:\s*(\{.*?\})\s*-->")
H2 = re.compile(r"^##\s+(.*)$")
H3 = re.compile(r"^###\s+(.*)$")
AMENDMENT = re.compile(r"^##\s+v(\d+)\.(\d+)\s+amendments", re.IGNORECASE)
CODE = re.compile(r"\b(E\d{3}|D\d{3})\b")


def _display(path: Path) -> str:
    """Render a path for the report without assuming it lives under the repo.

    The fixture paths a red-case feeds in do not, and `relative_to` RAISES on
    that rather than returning the absolute form — so the first fixture run of
    this check died with a traceback and exit 1, which reads from the outside
    exactly like "drift found". That is the crash class the spec closed at v2.7
    and again at v2.11, reappearing in the tool written to audit the spec.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def die(msg: str) -> None:
    print(json.dumps({"ok": False, "cannot_run": msg}), file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------- D1
def derive_document(spec: Path) -> dict:
    """Everything here comes from the spec document and nothing else."""
    if not spec.is_file():
        die(f"spec not found: {spec}")
    lines = spec.read_text(encoding="utf-8").split("\n")

    h2 = h3 = None
    amendments_start = None
    context: list[tuple[str | None, str | None]] = []
    for i, line in enumerate(lines, 1):
        m2 = H2.match(line)
        if m2:
            h2, h3 = m2.group(1).strip(), None
            if AMENDMENT.match(line) and amendments_start is None:
                amendments_start = i
        m3 = H3.match(line)
        if m3:
            h3 = m3.group(1).strip()
        context.append((h2, h3))
    base_end = (amendments_start or len(lines) + 1) - 1

    events = []
    for i, line in enumerate(lines, 1):
        m = MARKER.search(line)
        if not m:
            continue
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            die(f"unparseable vocab marker at {spec}:{i}: {exc}")
        d.update(line_no=i, in_base=i <= base_end,
                 sitting=context[i - 1][0], section=context[i - 1][1] or context[i - 1][0])
        events.append(d)

    # Replay the allocation events in document order. A code is live if it was
    # minted (or amended, which implies it exists) and not deleted; a renumber
    # carries its history to the new code.
    live: "OrderedDict[str, list[dict]]" = OrderedDict()
    for e in events:
        code, action = e.get("code"), e.get("action")
        if action == "delete":
            live.pop(code, None)
        elif action == "renumber":
            moved = live.pop(code, [])
            target = e.get("to")
            if target:
                live.setdefault(target, []).extend(moved + [e])
        elif action in ("mint", "amend"):
            live.setdefault(code, []).append(e)

    citations: "OrderedDict[str, list[dict]]" = OrderedDict()
    for code in sorted(live):
        seen, out = set(), []
        for e in sorted(live[code], key=lambda x: x["line_no"]):
            key = (e.get("at"), e["section"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"at": e.get("at"), "section": e["section"],
                        "line": e["line_no"], "by": e.get("by"),
                        "action": e.get("action"), "in_base": e["in_base"]})
        citations[code] = out

    # Every `###` section below the base, with the codes its TITLE names and the
    # codes its body allocates. Title-scoped deliberately: a section naming a
    # code in passing is not amending it, and scoring body mentions produces the
    # false positives that train a reader to ignore the finding.
    sections: list[dict] = []
    current: dict | None = None
    for i, line in enumerate(lines, 1):
        if H2.match(line):
            if current:
                current["end"] = i - 1
                sections.append(current)
                current = None
        m3 = H3.match(line)
        if m3:
            if current:
                current["end"] = i - 1
                sections.append(current)
            current = {"sitting": context[i - 1][0], "title": m3.group(1).strip(),
                       "start": i, "end": None}
    if current:
        current["end"] = len(lines)
        sections.append(current)

    amendment_sections = []
    for s in sections:
        if s["start"] <= base_end:
            continue
        body = "\n".join(lines[s["start"] - 1:s["end"]])
        allocated = set()
        for m in MARKER.finditer(body):
            try:
                allocated.add(json.loads(m.group(1)).get("code"))
            except json.JSONDecodeError:
                pass
        amendment_sections.append({
            "sitting": s["sitting"], "title": s["title"],
            "start": s["start"], "end": s["end"],
            "lines": s["end"] - s["start"] + 1,
            "codes_in_title": sorted(set(CODE.findall(s["title"]))),
            "codes_allocated": sorted(c for c in allocated if c),
        })

    def named_section(prefix: str) -> tuple[str, int, int]:
        start = None
        for i, line in enumerate(lines, 1):
            if line.startswith("## ") and line[3:].strip().startswith(prefix):
                start = i
            elif start and line.startswith("## ") and i > start:
                return "\n".join(lines[start - 1:i - 1]), start, i - 1
        return ("\n".join(lines[start - 1:]), start, len(lines)) if start else ("", 0, 0)

    record_text, rec_a, rec_b = named_section("7. Record")
    invariant_text, inv_a, inv_b = named_section("8. Invariants")

    # Codes the base invariants section names in prose, with or without a marker.
    base_prose_codes = sorted(set(CODE.findall(invariant_text)))

    return {
        "source": _display(spec),
        "total_lines": len(lines),
        "base_end": base_end,
        "amendment_sittings": sorted({e["sitting"] for e in events if not e["in_base"]}),
        "live_codes": sorted(live),
        "citations": citations,
        "base_invariants_prose_codes": base_prose_codes,
        "base_invariants_span": [inv_a, inv_b],
        "record_span": [rec_a, rec_b],
        "record_defers_to_v1": bool(
            re.search(r"v1'?s record,?\s*\*{0,2}unchanged", record_text, re.I)),
        "record_fields_named": sorted(set(re.findall(r"`([a-z][a-z0-9_]{2,})`", record_text))),
        "amendment_sections": amendment_sections,
    }


# --------------------------------------------------------------------- D2
def _literal(tree: ast.Module, name: str):
    """Read a module-level constant without importing the module.

    Importing pecia_cli would run its argument parser wiring and resolve the
    store, which is a side effect this check has no business causing.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == name:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    if isinstance(node.value, ast.Call) and node.value.args:
                        try:
                            return ast.literal_eval(node.value.args[0])
                        except ValueError:
                            return None
                    return None
    return None


def _resolve_test_assertions(tests: Path) -> tuple[set, set, set]:
    """Which codes does the suite assert PRESENT, which ABSENT, which can't it say?

    A bare mention scan is not good enough in either direction, and both
    directions were caught here before this check was trusted. `assertNotIn`
    marks a code the suite requires to be ABSENT — E010 was deleted at v2 along
    with the state it reported, and its absence assertion is the correct null
    arm, not drift. And a literal is not always in the call: D007 is asserted
    through `for expected in ("D005", "D007"): assertIn(expected, codes)`, which
    a line scan reads as untested. So the call is resolved on the syntax tree,
    with loop bindings followed one level.

    Anything that resolves into neither bucket is returned UNDECIDED rather than
    guessed at. A check that cannot tell says so; that is rule 1 applied to this
    check's own output rather than only to the tool it audits.
    """
    source = tests.read_text(encoding="utf-8")
    present: set[str] = set()
    absent: set[str] = set()

    class Resolver(ast.NodeVisitor):
        def __init__(self) -> None:
            self.bindings: list[dict[str, set[str]]] = []

        def visit_For(self, node: ast.For) -> None:
            bound: dict[str, set[str]] = {}
            if isinstance(node.target, ast.Name):
                try:
                    values = ast.literal_eval(node.iter)
                except (ValueError, TypeError, SyntaxError):
                    values = ()
                codes = {v for v in (values or ())
                         if isinstance(v, str) and CODE.fullmatch(v)}
                if codes:
                    bound[node.target.id] = codes
            self.bindings.append(bound)
            self.generic_visit(node)
            self.bindings.pop()

        def _codes_of(self, arg: ast.expr) -> set[str]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return {arg.value} if CODE.fullmatch(arg.value) else set()
            if isinstance(arg, ast.Name):
                for scope in reversed(self.bindings):
                    if arg.id in scope:
                        return set(scope[arg.id])
            return set()

        def visit_Call(self, node: ast.Call) -> None:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name in ("assertIn", "assertNotIn"):
                found: set[str] = set()
                for arg in node.args:
                    found |= self._codes_of(arg)
                (present if name == "assertIn" else absent).update(found)
            self.generic_visit(node)

    Resolver().visit(ast.parse(source))
    mentioned = set(CODE.findall(source))
    return present, absent, mentioned - present - absent


def v1_record_baseline(spec_v1: Path) -> dict:
    """The v1 record, from v1's own canonical example — not from a restatement.

    Base section 7 of v2 says the record is "v1's record, unchanged" and sends
    the reader to format-v1.md. Taking that sentence at its word is exactly what
    this comparison must do: the baseline is read from the document being
    deferred TO, so the finding is about the two documents and not about which
    field names section 7 happens to set in backticks.
    """
    if not spec_v1.is_file():
        return {}
    text = spec_v1.read_text(encoding="utf-8")
    m = re.search(r"^## Record\s*\n+```json\n(.*?)\n```", text, re.S | re.M)
    if not m:
        return {}
    try:
        example = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return {"fields": sorted(example), "edges": sorted(example.get("edges") or {})}


def derive_artifacts(cli: Path, schema: Path, tests: Path) -> dict:
    for p in (cli, schema, tests):
        if not p.is_file():
            die(f"artifact not found: {p}")
    src = cli.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # A code is EMITTED if it appears as a string literal in the implementation.
    emitted = sorted({m.group(1) for m in re.finditer(r'["\'](E\d{3}|D\d{3})["\']', src)})

    present, absent, undecided = _resolve_test_assertions(tests)

    sch = json.loads(schema.read_text(encoding="utf-8"))
    props = sch.get("properties", {})

    return {
        "emitted_codes": emitted,
        "tested_present": sorted(present),
        "tested_absent_only": sorted(absent - present),
        "tested_undecided": sorted(undecided),
        "schema_version": sch.get("x-pecia-format-version") or sch.get("version"),
        "schema_properties": sorted(props),
        "schema_edge_properties": sorted((props.get("edges") or {}).get("properties", {})),
        "schema_required": sorted(sch.get("required", [])),
        "schema_optional": sorted(set(props) - set(sch.get("required", []))),
        "schema_additional_properties": sch.get("additionalProperties"),
        "cli_required_fields": _literal(tree, "REQUIRED_FIELDS") or [],
        "cli_touched_fields": _literal(tree, "TOUCHED_FIELDS") or [],
        "cli_presence_fields": sorted(_literal(tree, "PRESENCE_FIELDS") or []),
        "cli_per_revision_fields": sorted(_literal(tree, "PER_REVISION_FIELDS") or []),
        "cli_scalar_edges": _literal(tree, "SCALAR_EDGES") or [],
        "cli_list_edges": _literal(tree, "LIST_EDGES") or [],
        "cli_statuses": _literal(tree, "CORE_STATUSES") or [],
        "cli_types": _literal(tree, "CORE_TYPES") or [],
    }


# ------------------------------------------------------------- disagreements
def compare(d1: dict, d2: dict, v1: dict) -> list[dict]:
    out: list[dict] = []

    def finding(kind, severity, message, **ev):
        out.append({"kind": kind, "severity": severity, "message": message,
                    "evidence": ev})

    impl_codes = set(d2["emitted_codes"])
    # A code the base names in prose and the tool emits is live even where no
    # marker allocates it — E005 and E009 are v1 codes v2 never had to re-state,
    # so excluding them would flatter the base section's coverage by shrinking
    # the denominator rather than by covering anything.
    doc_codes = set(d1["live_codes"]) | (set(d1["base_invariants_prose_codes"]) & impl_codes)

    # Emitted and stated nowhere in this document. This is the check's primary
    # finding, and it was computed over an EMPTY DENOMINATOR in the first draft:
    # the set was taken from `doc_codes`, which by construction contains only
    # codes the document already states, so the finding could never fire. Its
    # kill test is what found that — the recurring defect of this repository,
    # reproduced inside the check written to detect drift.
    undocumented = sorted(impl_codes - doc_codes)
    if undocumented:
        finding("code-undocumented", "error",
                "emitted by the implementation and stated nowhere in this spec",
                codes=undocumented)

    # Stated in prose, emitted, and allocated by no marker. Distinct from the
    # above: the contract exists, but only in the document this one defers to,
    # so a derivation driven by the markers omits the code in silence.
    unallocated = sorted(c for c in (doc_codes & set(d1["base_invariants_prose_codes"]))
                         if c not in d1["citations"])
    if unallocated:
        finding("code-has-no-allocation", "warning",
                "named in the base invariants prose and emitted by the tool, but "
                "carrying no machine-readable allocation anywhere in this spec — the "
                "reader is sent to format-v1.md for the contract without being told "
                "so, and a generated view of this document silently omits it",
                codes=unallocated)

    only_doc = sorted(doc_codes - impl_codes)
    if only_doc:
        finding("code-unimplemented", "error",
                "stated in the spec and emitted by nothing in the implementation",
                codes=only_doc)

    # Three different states, deliberately not collapsed into "untested".
    undecided = sorted(impl_codes & set(d2["tested_undecided"]))
    if undecided:
        finding("test-status-undecided", "note",
                "mentioned in the suite, but this check could not resolve the mention "
                "into a presence or an absence assertion — reported undecided rather "
                "than counted either way",
                codes=undecided)
    absence_only = sorted(impl_codes & set(d2["tested_absent_only"]))
    if absence_only:
        finding("code-tested-negative-only", "warning",
                "emitted by the implementation, and the suite asserts only that it does "
                "NOT appear — no test demonstrates the condition firing",
                codes=absence_only)
    unmentioned = sorted(impl_codes - set(d2["tested_present"])
                         - set(d2["tested_absent_only"]) - set(d2["tested_undecided"]))
    if unmentioned:
        finding("code-untested", "warning",
                "emitted by the implementation and named by no test at all",
                codes=unmentioned)

    # The base invariants section against the live vocabulary.
    base_prose = set(d1["base_invariants_prose_codes"])
    uncovered = sorted(doc_codes - base_prose)
    if uncovered:
        finding("base-section-incomplete", "error",
                f"the base invariants section names {len(doc_codes & base_prose)} of "
                f"{len(doc_codes)} live codes; these are defined only in amendments "
                f"below it, so the section that calls itself the checker contract is "
                f"not one",
                missing_from_base=uncovered,
                base_span=d1["base_invariants_span"])

    # An amendment that narrows a code without marking it. v2.15's own amendment
    # (pc-d935) says "an amendment that moves a meaning restates it where the
    # machine reads it"; these are the sittings where that did not happen, so the
    # index the spec keeps to prevent this class of drift is itself incomplete —
    # and any view generated from the markers omits these narrowings in silence.
    unmarked = [s for s in d1["amendment_sections"]
                if set(s["codes_in_title"]) - set(s["codes_allocated"])]
    if unmarked:
        finding("amendment-narrows-without-marking", "error",
                "an amendment section whose title names a code carries no allocation "
                "marker for it — the narrowing is normative and invisible to every "
                "generated view of this document",
                count=len(unmarked),
                sections=[{"sitting": s["sitting"].split("(")[0].strip(),
                           "line": s["start"],
                           "unmarked": sorted(set(s["codes_in_title"])
                                              - set(s["codes_allocated"])),
                           "title": s["title"]} for s in unmarked])

    # How much of the amendment text a marker-driven view would omit entirely.
    unmarked_all = [s for s in d1["amendment_sections"] if not s["codes_allocated"]]
    if unmarked_all:
        total = sum(s["lines"] for s in d1["amendment_sections"]) or 1
        omitted = sum(s["lines"] for s in unmarked_all)
        finding("amendment-coverage", "note",
                "amendment sections carrying no allocation marker at all — normative "
                "text a marker-driven generator cannot see, reported so the index is "
                "never mistaken for the contract",
                sections_without_marker=len(unmarked_all),
                sections_total=len(d1["amendment_sections"]),
                lines_without_marker=omitted, lines_total=total,
                share=f"{omitted * 100 // total}%")

    # The record contract, against the document section 7 defers to.
    if d1["record_defers_to_v1"] and v1:
        novel = sorted(set(d2["schema_properties"]) - set(v1["fields"]))
        schema_edges = sorted(d2["schema_edge_properties"])
        novel_edges = sorted(set(schema_edges) - set(v1["edges"]))
        if novel or novel_edges:
            finding("record-section-stale", "error",
                    "the base record section says the record is v1's \"unchanged\" — "
                    "\"same required fields, same types, statuses, priorities, edges, "
                    "disposition and evidence semantics\" — and sends the reader to "
                    "format-v1.md. The shipped schema disagrees on two counts, so an "
                    "implementer who follows that sentence builds the wrong record",
                    v1_fields=len(v1["fields"]), schema_fields=len(d2["schema_properties"]),
                    fields_postdating_v1=novel,
                    v1_edges=len(v1["edges"]), schema_edges=len(schema_edges),
                    edges_postdating_v1=novel_edges,
                    record_span=d1["record_span"])

    # Schema against the implementation's own field vocabulary.
    if sorted(d2["cli_required_fields"]) != d2["schema_required"]:
        finding("required-fields-disagree", "error",
                "the schema's required set and the implementation's REQUIRED_FIELDS "
                "are not the same set",
                schema_required=d2["schema_required"],
                cli_required=sorted(d2["cli_required_fields"]))

    undeclared = sorted(set(d2["cli_touched_fields"]) - set(d2["schema_properties"]))
    if undeclared:
        finding("touched-field-undeclared", "error",
                "the implementation treats these as conflict units, but the schema "
                "declares no property for them",
                fields=undeclared)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", default=str(ROOT / "spec/format-v2.md"))
    ap.add_argument("--cli", default=str(ROOT / "pecia_cli.py"))
    ap.add_argument("--schema", default=str(ROOT / "spec/record.schema.json"))
    ap.add_argument("--tests", default=str(ROOT / "tests/test_pecia.py"))
    ap.add_argument("--baseline", default=str(ROOT / "spec/format-v1.md"),
                    help="the document section 7 defers to")
    ap.add_argument("--json", action="store_true", help="full derivations, not the summary")
    args = ap.parse_args()

    d1 = derive_document(Path(args.spec))
    d2 = derive_artifacts(Path(args.cli), Path(args.schema), Path(args.tests))
    v1 = v1_record_baseline(Path(args.baseline))
    findings = compare(d1, d2, v1)

    errors = sum(1 for f in findings if f["severity"] == "error")
    if args.json:
        print(json.dumps({"d1": d1, "d2": d2, "v1_baseline": v1, "findings": findings}, indent=1,
                         ensure_ascii=False, default=str))
    else:
        for f in findings:
            print(json.dumps(f, ensure_ascii=False))
        print(json.dumps({
            "ok": errors == 0,
            "errors": errors,
            "warnings": len(findings) - errors,
            "d1_live_codes": len(d1["live_codes"]),
            "d2_emitted_codes": len(d2["emitted_codes"]),
            "note": 'Exit 0 means the two derivations agree ON WHAT WAS COMPARED, '
                    'never that either is true.',
        }, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    # Totality at the entry point, not per site (v2.11, pc-34b5/pc-d502): any
    # escape becomes a cannot-run, because exit 1 means "the two derivations
    # disagree" and a crash has established no such thing.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        print(json.dumps({"ok": False,
                          "cannot_run": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        raise SystemExit(2) from exc
