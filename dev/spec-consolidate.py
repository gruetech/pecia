#!/usr/bin/env python3
"""Generate the consolidated v2 contract — pc-ae3a.

WHY THIS EXISTS. `spec/format-v2.md` is 418 lines of base text with seventeen
dated amendment sittings below it. The amendments restate meaning in their own
sections rather than editing the base, which is the convention this repository
chose deliberately ("amendments get dated sections, never silent edits") and
which is right for a document that has to keep the record of what thirteen
adversarial review rounds found. What it is NOT right for is being the document
an implementation is written against: base section 8 calls itself "the checker
contract" and names 14 of the 30 codes the tool emits (`pc-0e2a`), and base
section 7 says the record is v1's "unchanged" while the shipped schema carries
seven fields and two edges v1 never had (`pc-c38d`).

So this generates a second view of the same material, organised for an
implementer rather than for the record. format-v2.md stays canonical and
unedited; this document is a projection of it and is regenerated, never
authored — the same relationship `spec/vocabulary.json` has to the markers, and
`.pecia/work.jsonl` has to the log.

WHY IT IS POSITION-DRIVEN, NOT MARKER-DRIVEN. The obvious design reads the
`<!-- vocab: -->` markers and assembles the document per code. That design was
built and abandoned: `pc-4f6a` measured the index and it does not cover the
text. Eight amendment sections name a code in their title and mark nothing, and
58 of 102 sections carry no marker at all — 52% of the amendment lines,
including the crash-class closure, validate-then-rename, and the projection's
place in the commit point. A marker-driven generator omits all of it IN SILENCE,
which is a worse failure than the drift it was built to fix. Every section is
therefore carried by POSITION, in document order, and the markers are a
secondary index that this document reports the incompleteness of rather than
relying on.

WHAT IT DOES NOT DO. It does not rewrite, summarise or reconcile anything: the
normative text is reproduced verbatim, and where two sittings say different
things the reader sees both, in order, because a disagreement between sittings
carries information and deduplicating it away destroys that information
(`pc-ae3a`'s own instruction). Agreement between this document's inputs and the
implementation is `dev/spec-agree.py`'s question, not this script's.

Usage:
    dev/spec-consolidate.py [--write] [--check] [--out PATH]

Exit 0 clean / 1 stale (with --check) / 2 cannot-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec" / "format-v2.md"
SCHEMA = ROOT / "spec" / "record.schema.json"
REGISTRY = ROOT / "spec" / "vocabulary.json"
OUT = ROOT / "spec" / "format-v2-consolidated.md"

MARKER = re.compile(r"<!--\s*vocab:\s*(\{.*?\})\s*-->")
H2 = re.compile(r"^##\s+(.*)$")
H3 = re.compile(r"^###\s+(.*)$")
AMENDMENT = re.compile(r"^##\s+v(\d+)\.(\d+)\s+amendments", re.IGNORECASE)
CODE = re.compile(r"\b(E\d{3}|D\d{3})\b")


def _display(path: Path) -> str:
    """Render a path for the document without assuming it lives under the repo.

    `relative_to` RAISES rather than falling back, so a fixture path outside the
    tree killed the first run of this generator — the same crash class the spec
    closed at v2.7 and v2.11 and the same one `dev/spec-agree.py` hit. Entry-point
    totality caught it (exit 2 with a JSON cannot-run, not a traceback), which is
    what that amendment buys; it does not excuse the assumption.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def die(msg: str) -> None:
    print(json.dumps({"ok": False, "cannot_run": msg}), file=sys.stderr)
    raise SystemExit(2)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:72] or "section"


def parse(spec: Path) -> dict:
    lines = spec.read_text(encoding="utf-8").split("\n")
    h2 = None
    amendments_start = None
    heads: list[dict] = []
    for i, line in enumerate(lines, 1):
        m2 = H2.match(line)
        if m2:
            h2 = m2.group(1).strip()
            if AMENDMENT.match(line) and amendments_start is None:
                amendments_start = i
            heads.append({"level": 2, "title": h2, "start": i, "sitting": h2})
            continue
        m3 = H3.match(line)
        if m3:
            heads.append({"level": 3, "title": m3.group(1).strip(),
                          "start": i, "sitting": h2})
    if amendments_start is None:
        die(f"{spec}: no amendment sitting found; this document is not v2-shaped")

    for a, b in zip(heads, heads[1:]):
        a["end"] = b["start"] - 1
    if heads:
        heads[-1]["end"] = len(lines)

    for h in heads:
        body = "\n".join(lines[h["start"] - 1:h["end"]])
        allocated, actions = [], []
        for m in MARKER.finditer(body):
            try:
                d = json.loads(m.group(1))
            except json.JSONDecodeError:
                die(f"{spec}:{h['start']}: unparseable vocab marker")
            allocated.append(d.get("code"))
            actions.append(d)
        h.update(
            body=body,
            lines=h["end"] - h["start"] + 1,
            in_base=h["start"] < amendments_start,
            codes_marked=sorted({c for c in allocated if c}),
            codes_in_title=sorted(set(CODE.findall(h["title"]))),
            actions=actions,
            anchor=slug(h["title"]),
        )
    return {"lines": lines, "heads": heads, "amendments_start": amendments_start}


def attribute(heads: list[dict]) -> dict[str, list[dict]]:
    """Which sections bear on each code, and on what authority.

    Three tiers, kept apart on purpose. A MARKER is the document declaring the
    allocation. A TITLE is the section announcing which code it amends without
    declaring it — `pc-4f6a`'s eight, normative and unindexed. A body MENTION is
    neither, and is not attributed at all: scoring mentions was tried, produced
    false positives on every section that cites a neighbouring code in passing,
    and a finding a reader learns to skip is worse than no finding.
    """
    index: dict[str, list[dict]] = {}
    deleted = {d.get("code") for h in heads for d in h["actions"]
               if d.get("action") == "delete"}
    for h in heads:
        for code in h["codes_marked"]:
            if code in deleted:
                continue
            index.setdefault(code, []).append({"section": h, "authority": "marker"})
        for code in h["codes_in_title"]:
            if code in h["codes_marked"] or code in deleted:
                continue
            index.setdefault(code, []).append({"section": h, "authority": "title"})
    for code in index:
        index[code].sort(key=lambda e: e["section"]["start"])
    return index


def render(spec: Path, parsed: dict, schema: dict, registry: dict,
           schema_path: Path = SCHEMA, registry_path: Path = REGISTRY) -> str:
    lines, heads = parsed["lines"], parsed["heads"]
    amendments_start = parsed["amendments_start"]
    base_heads = [h for h in heads if h["in_base"] and h["level"] == 2]
    amd_sittings = [h for h in heads if not h["in_base"] and h["level"] == 2]
    amd_sections = [h for h in heads if not h["in_base"] and h["level"] == 3]
    index = attribute(heads)
    # Codes base section 8 keeps by naming them in its "retained from v1" list and
    # says nothing else about. They have no v2 definition to point at, and a table
    # of amendments alone would leave a reader thinking there is none anywhere.
    base_invariants = next((h for h in heads if h["title"].startswith("8. Invariants")), None)
    retained_from_v1 = set()
    if base_invariants:
        m = re.search(r"\*\*Retained from v1, unchanged:\*\*(.+)", base_invariants["body"])
        if m:
            retained_from_v1 = set(CODE.findall(m.group(1)))

    source_digest = hashlib.sha256(spec.read_bytes()).hexdigest()
    meanings = registry.get("meanings", {})
    d_codes = registry.get("d_codes", {})
    all_codes = sorted(set(meanings) | set(d_codes) | set(index),
                       key=lambda c: (c[0], int(c[1:])))

    unmarked = [h for h in amd_sections if not h["codes_marked"]]
    mis = [h for h in amd_sections if set(h["codes_in_title"]) - set(h["codes_marked"])]
    amd_lines = sum(h["lines"] for h in amd_sections) or 1

    o: list[str] = []
    w = o.append

    w("# pecia format v2 — the consolidated contract")
    w("")
    w("**GENERATED — do not edit.** Regenerate with `dev/spec-consolidate.py --write`;")
    w("`dev/spec-consolidate.py --check` fails when this file is stale.")
    w("")
    w(f"- Generated from `{_display(spec)}` "
      f"({len(lines)} lines, sha256 `{source_digest[:16]}…`)")
    w(f"- Record contract from `{_display(schema_path)}`; "
      f"code glosses from `{_display(registry_path)}`")
    w(f"- {len(base_heads)} base sections, {len(amd_sittings)} amendment sittings, "
      f"{len(amd_sections)} amendment sections")
    w("")
    w("## What this document is")
    w("")
    w("A second view of `spec/format-v2.md`, organised for an implementer. The source")
    w("stays canonical and unedited: it is the record of what thirteen adversarial")
    w("review rounds found, and its dated-amendment convention exists so that record")
    w("survives. This projection exists because that convention makes the source a poor")
    w("thing to implement from — its base section 8 calls itself \"the checker contract\"")
    w("and does not state one for most of the codes below (`pc-0e2a`), and its base")
    w("section 7 says the record is v1's \"unchanged\" while the shipped schema carries")
    w("fields and edges v1 never had (`pc-c38d`).")
    w("")
    w("Both counts belong to `dev/spec-agree.py`, which measures them, and are")
    w("deliberately not restated here: a count beside machinery is stale the moment the")
    w("machinery moves (v2.16, `pc-9611`), and this document and that check are")
    w("regenerated at different times.")
    w("")
    w("**Rule 1 applies here as everywhere: well-formed is never true.** This document")
    w("reproduces what the spec says. Whether the implementation does it is the kill")
    w("matrix's question, and whether the two agree is `dev/spec-agree.py`'s.")
    w("")
    w("Nothing here is rewritten, summarised or reconciled. Where two sittings say")
    w("different things you will see both, in document order, because a disagreement")
    w("between sittings carries information that deduplicating it away destroys.")
    w("")
    w("### The index is incomplete, and this document says so rather than hiding it")
    w("")
    w("Sections are carried by POSITION, in document order. The `<!-- vocab: -->`")
    w("markers are a secondary index only, because they do not cover the text")
    w(f"(`pc-4f6a`): {len(unmarked)} of {len(amd_sections)} amendment sections carry no marker at")
    w(f"all — {sum(h['lines'] for h in unmarked)} of {amd_lines} amendment lines, "
      f"{sum(h['lines'] for h in unmarked) * 100 // amd_lines}% — and "
      f"{len(mis)} name a code in")
    w("their title while marking nothing. A marker-driven generator omits all of that in")
    w("silence. Part 3's per-code tables therefore mark each attribution `marker` or")
    w("`title`, and Part 4 carries every section whether indexed or not.")
    w("")
    if mis:
        w("Sections that amend a code without marking it:")
        w("")
        w("| sitting | line | code | section |")
        w("|---|---|---|---|")
        for h in mis:
            missing = ", ".join(sorted(set(h["codes_in_title"]) - set(h["codes_marked"])))
            w(f"| {h['sitting'].split('(')[0].strip()} | {h['start']} | {missing} "
              f"| [{h['title']}](#{h['anchor']}) |")
        w("")

    # ---------------------------------------------------------------- Part 1
    w("---")
    w("")
    w("## Part 1 — The record")
    w("")
    w("Generated from `spec/record.schema.json`, which is the shipped contract and the")
    w("most current statement of the record in the repository. Base section 7 of the")
    w("source says the record is v1's \"unchanged\" and sends the reader to")
    w("`spec/format-v1.md`; that sentence is false as of v2.17 and is the subject of")
    w("`pc-c38d`. The table below is what an implementation must accept.")
    w("")
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    defs = schema.get("$defs", {})

    def type_of(spec_obj: dict) -> str:
        if "$ref" in spec_obj:
            return f"`{spec_obj['$ref'].rsplit('/', 1)[-1]}`"
        if "const" in spec_obj:
            return f"const `{json.dumps(spec_obj['const'])}`"
        if "enum" in spec_obj:
            return "enum"
        t = spec_obj.get("type")
        if isinstance(t, list):
            return " \\| ".join(f"`{x}`" for x in t)
        return f"`{t}`" if t else "—"

    w("| field | required | type | meaning |")
    w("|---|---|---|---|")
    for name in sorted(props, key=lambda n: (n not in required, n)):
        p = props[name]
        if name == "edges":
            desc = "The typed edge object — see below."
        else:
            # A $ref'd field carries its meaning on the definition, not on the
            # property, so read through the reference before giving up on it.
            ref = defs.get(p["$ref"].rsplit("/", 1)[-1], {}) if "$ref" in p else {}
            desc = (p.get("description") or p.get("$comment")
                    or ref.get("description") or ref.get("$comment") or "")
            desc = re.sub(r"\s+", " ", desc.replace("\n", " ")).strip() or "—"
        mark = "**yes**" if name in required else "no"
        w(f"| `{name}` | {mark} | {type_of(p)} | {desc} |")
    w("")
    for name in ("type", "status", "priority"):
        p = props.get(name, {})
        if "enum" in p:
            w(f"- **`{name}`** — one of {', '.join('`' + str(v) + '`' for v in p['enum'])}."
              + (f" {re.sub(r'\\s+', ' ', p.get('$comment', '')).strip()}" if p.get("$comment") else ""))
    w("")
    w("### Edges")
    w("")
    edges = (props.get("edges") or {}).get("properties", {})
    w(f"`edges` is a closed object (`additionalProperties: "
      f"{json.dumps((props.get('edges') or {}).get('additionalProperties'))}`) with "
      f"{len(edges)} keys.")
    w("")
    w("| edge | shape | meaning |")
    w("|---|---|---|")
    for name, p in edges.items():
        desc = re.sub(r"\s+", " ", (p.get("description") or p.get("$comment") or "")).strip()
        shape = "list" if p.get("type") == "array" else type_of(p)
        w(f"| `{name}` | {shape} | {desc} |")
    w("")
    if defs:
        w("### Shared definitions")
        w("")
        for name, d in defs.items():
            desc = re.sub(r"\s+", " ", (d.get("description") or d.get("$comment") or "")).strip()
            pat = f" pattern `{d['pattern']}`" if d.get("pattern") else ""
            w(f"- **`{name}`** — {type_of(d)}{pat}. {desc}")
        w("")

    # ---------------------------------------------------------------- Part 2
    w("---")
    w("")
    w("## Part 2 — The model, as the base states it")
    w("")
    w("Base sections reproduced verbatim and in order. Sections 7 and 8 are carried")
    w("here too, unaltered, WITH their known staleness marked — this document does not")
    w("correct the source, it reports it. Part 1 supersedes section 7 in practice, and")
    w("Part 3 supersedes section 8.")
    w("")
    stale_base = {"7. Record": "pc-c38d", "8. Invariants (the checker contract)": "pc-0e2a"}
    for h in base_heads:
        w(f"<a id=\"{h['anchor']}\"></a>")
        w("")
        note = stale_base.get(h["title"])
        if note:
            w(f"> **STALE ({note}).** Reproduced as the source has it. See "
              f"{'Part 1' if note == 'pc-c38d' else 'Part 3'} for what the implementation "
              f"is actually held to.")
            w("")
        w(h["body"].rstrip())
        w("")

    # ---------------------------------------------------------------- Part 3
    w("---")
    w("")
    w("## Part 3 — The diagnostic vocabulary")
    w("")
    w("One entry per live code. The gloss is the spec's own current one-line meaning,")
    w("from the generated registry. The table beneath it is every section that bears on")
    w("the code, in document order — `marker` where the document declares the")
    w("allocation, `title` where the section announces the code and declares nothing")
    w("(`pc-4f6a`). Follow the links into Part 4 for the normative text.")
    w("")
    for code in all_codes:
        gloss = meanings.get(code) or d_codes.get(code) or "_no gloss in the registry._"
        entries = index.get(code, [])
        w(f"### {code}")
        w("")
        w(gloss if gloss.endswith(".") else gloss + ".")
        w("")
        if not entries:
            w("_No section of `format-v2.md` allocates or announces this code._ Base")
            w("section 8 lists it as retained from v1 and says nothing further, so its")
            w("ENTIRE contract is in `spec/format-v1.md` and nothing in v2 narrows it.")
            w("It carries no allocation marker either, so every generated view of the")
            w("source omits it in silence (`pc-0aaa`).")
            w("")
            continue
        if code in retained_from_v1:
            w("Base section 8 keeps this code by listing it as retained from v1 and says")
            w("nothing further about it, so its ORIGIN definition is in `spec/format-v1.md`.")
            w("Everything below narrows that definition.")
            w("")
        w(f"Stated in {len(entries)} section{'s' if len(entries) != 1 else ''}:")
        w("")
        w("| where | line | authority | section |")
        w("|---|---|---|---|")
        for e in entries:
            s = e["section"]
            where = "base" if s["in_base"] else s["sitting"].split("(")[0].strip()
            w(f"| {where} | {s['start']} | `{e['authority']}` "
              f"| [{s['title']}](#{s['anchor']}) |")
        w("")

    # ---------------------------------------------------------------- Part 4
    w("---")
    w("")
    w("## Part 4 — The amendments in force, in document order")
    w("")
    w("Every amendment section, carried by position. A section appears here whether or")
    w("not any marker indexes it, which is the whole point: half of this text is")
    w("invisible to the index (`pc-4f6a`).")
    w("")
    current_sitting = None
    for h in amd_sections:
        if h["sitting"] != current_sitting:
            current_sitting = h["sitting"]
            w(f"### {current_sitting}")
            w("")
        w(f"<a id=\"{h['anchor']}\"></a>")
        w("")
        tags = []
        if h["codes_marked"]:
            tags.append("marks " + ", ".join(f"`{c}`" for c in h["codes_marked"]))
        unindexed = sorted(set(h["codes_in_title"]) - set(h["codes_marked"]))
        if unindexed:
            tags.append("**amends " + ", ".join(f"`{c}`" for c in unindexed)
                        + " without marking it**")
        if not h["codes_marked"] and not unindexed:
            tags.append("allocates no code")
        w(f"_{h['sitting'].split('(')[0].strip()}, source line {h['start']} — "
          + "; ".join(tags) + "._")
        w("")
        w("\n".join(h["body"].split("\n")[1:]).strip())
        w("")

    w("---")
    w("")
    w("## Appendix — what this document does not carry")
    w("")
    w("- The source's own history beyond the amendment text: the ledger records each")
    w("  sitting cites are in `refs/pecia/log`, not here.")
    w("- `spec/format-v1.md`, which base sections 7 and 9 defer to and which two codes")
    w("  (E005, E009) still depend on entirely (`pc-0aaa`).")
    w("- Any claim that the implementation does what this says. That is the kill")
    w("  matrix's job, and `dev/spec-agree.py` reports where the two derivations part.")
    w("")
    return "\n".join(o).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="write the document")
    ap.add_argument("--check", action="store_true", help="fail if it is stale")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--schema", default=str(SCHEMA))
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    spec, out = Path(args.spec), Path(args.out)
    for p in (spec, Path(args.schema), Path(args.registry)):
        if not p.is_file():
            die(f"input not found: {p}")

    parsed = parse(spec)
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    text = render(spec, parsed, schema, registry,
                  Path(args.schema), Path(args.registry))

    if args.check:
        if not out.is_file():
            print(json.dumps({"ok": False, "stale": True,
                              "reason": f"{out} does not exist"}))
            return 1
        current = out.read_text(encoding="utf-8")
        fresh = current == text
        print(json.dumps({"ok": fresh, "stale": not fresh, "out": str(out),
                          "note": 'Exit 0 means the projection matches its source, '
                                  'never that the source is true.'}))
        return 0 if fresh else 1

    if args.write:
        out.write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "written": str(out),
                          "lines": len(text.split("\n")),
                          "note": 'Exit 0 means "generated," never "true."'}))
        return 0

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - totality at the entry point (v2.11)
        print(json.dumps({"ok": False, "cannot_run": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        raise SystemExit(2) from exc
