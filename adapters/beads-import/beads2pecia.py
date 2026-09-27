#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""beads2pecia — convert a beads JSONL export into pecia records.

The reference adapter (decision pc-8a27: adapters live outside the core).
Read ADAPTERS.md for the contract this program targets; the short form:
emit one JSON object per line matching spec/record.schema.json, validate
with `pecia check --ledger out.jsonl`, and remember that exit 0 means
"well-formed," never "true."

Source format (verified against steveyegge/beads @ main, 2026-07-31:
docs/reference/json-schema.md, docs/core-concepts/dependencies.md,
internal/types/types.go): `bd export --json` emits JSONL discriminated by
`_type` ("issue" / "memory"), with an optional `{"_schema":"beads-jsonl/1"}`
header that readers skip. Viewer exports (the repo-root issues.jsonl) omit
`_type`; lines without it are treated as issues.

Mapping (every lossy step is DECLARED, in the record body or the stderr
summary — nothing is silently dropped):

  id           pc-<source id, chars outside [A-Za-z0-9.-] replaced by '.'>.
               Deterministic, so re-running over an unchanged export is
               byte-identical. Re-mapping a CHANGED export collides at
               rev 1 by design — the checker's E002 catches it, an error
               since v2 absorbed E010's divergent half; landing changes in
               a live ledger is the label-keyed reconcile ADAPTERS.md
               describes, never a re-append.
  rev          1 (single revision per issue; beads has no revision counter)
  issue_type   bug -> defect; task -> task; epic -> milestone;
               feature/chore/other -> task + label beads:type:<original>
  status       open -> open; in_progress/hooked -> in-progress;
               blocked -> open (pecia computes blocked from edges; label
               beads:status:blocked); deferred -> open + priority 4 +
               label beads:status:deferred (original priority declared in
               body); closed -> done; pinned -> skipped (persistent
               context marker, not a work item)
  closed       disposition carries close_reason verbatim (or declares the
               source recorded none); evidence is a provenance command:
               `python3 <this script> --verify-closed <export> <source id>`,
               exit 0
               iff the source export records the issue closed. That is the
               strongest claim an importer can honestly make — it licenses
               "closed in source," never "the work is true" (rule 1).
               beads does not distinguish done from dropped; closed maps to
               done with the reason preserved for a human pass.
  dependencies blocks -> `blocks` edge ON THE BLOCKER (beads stores the
               dep on the dependent; pecia stores the edge on the blocker
               — the adapter inverts). parent-child -> `parent` on the
               child. discovered-from/caused-by/validates/supersedes ->
               the matching scalar edge, same direction. Scalar collisions:
               lowest source id wins, the rest are declared in the body.
               related/tracks/conditional-blocks/waits-for have no exact
               pecia semantics -> declared in the body, never edges
               (conditional-blocks means "runs only if the target fails";
               mapping it to blocks would overclaim). Targets missing from
               the export -> declared in the body, no edge (a dangling
               edge is E003).
  dropped      comments (count declared in body), memory records, the
               _schema header, pinned/ephemeral/template records — all
               counted in the stderr summary.

Exit: 0 imported clean / 1 some issue lines could not become records
(malformed; each named on stderr) / 2 cannot-run. The summary JSON always
lands on stderr; records go to --out (default stdout).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ID_SAFE = re.compile(r"[^A-Za-z0-9.-]")
CUSTODY_TYPES = {"discovered-from": "discovered_from", "caused-by": "caused_by",
                 "validates": "validates", "supersedes": "supersedes"}
STATUS_MAP = {"open": "open", "in_progress": "in-progress", "hooked": "in-progress",
              "blocked": "open", "deferred": "open", "closed": "done"}
TYPE_MAP = {"bug": "defect", "task": "task", "epic": "milestone"}


def pecia_id(source_id: str) -> str:
    token = ID_SAFE.sub(".", source_id)
    if not token or not token[0].isalnum():
        token = "x" + token
    return f"pc-{token}"


def iso_date(value: str | None, fallback: str = "") -> str:
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return fallback


def load_export(path: Path) -> tuple[list[dict], dict[str, int], list[str]]:
    """Return (issues, skip_counts, malformed_messages)."""
    issues: list[dict] = []
    skipped = {"header": 0, "memory": 0, "pinned": 0, "ephemeral": 0,
               "template": 0, "other_type": 0}
    malformed: list[str] = []
    for n, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            malformed.append(f"line {n}: does not parse: {exc}")
            continue
        except RecursionError:
            # pc-2e2f's class, at the import boundary: a foreign line nested
            # past the interpreter's recursion boundary crashed the adapter
            # instead of landing in its declared malformed-line handling.
            # beads owns its format, so there is no pecia bound to cite —
            # the line is unreadable BY THIS TOOL and is reported as such.
            malformed.append(f"line {n}: does not parse: nesting exceeds "
                             f"the interpreter's recursion boundary")
            continue
        if not isinstance(obj, dict):
            malformed.append(f"line {n}: not an object")
            continue
        if "_schema" in obj and "id" not in obj:
            skipped["header"] += 1
            continue
        kind = obj.get("_type", "issue")
        if kind == "memory":
            skipped["memory"] += 1
            continue
        if kind != "issue":
            skipped["other_type"] += 1
            continue
        if obj.get("status") == "pinned":
            skipped["pinned"] += 1
            continue
        if obj.get("ephemeral") is True:
            skipped["ephemeral"] += 1
            continue
        if obj.get("is_template") is True:
            skipped["template"] += 1
            continue
        if not isinstance(obj.get("id"), str) or not obj["id"]:
            malformed.append(f"line {n}: issue without an id")
            continue
        if not iso_date(obj.get("created_at")):
            malformed.append(f"line {n} ({obj['id']}): no parseable created_at")
            continue
        issues.append(obj)
    return issues, skipped, malformed


def convert(issues: list[dict], export_arg: str, evidence_prefix: str,
            owner_fallback: str) -> tuple[list[dict], dict[str, int]]:
    counts = {"unmapped_deps": 0, "dangling": 0, "scalar_collisions": 0}
    by_source: dict[str, dict] = {}
    for issue in sorted(issues, key=lambda i: i["id"]):
        if issue["id"] in by_source:
            raise SystemExit(f"duplicate source id in export: {issue['id']}")
        by_source[issue["id"]] = issue
    ids = {src: pecia_id(src) for src in by_source}
    if len(set(ids.values())) != len(ids):
        raise SystemExit("source ids collide after sanitization; refusing to merge")

    records: dict[str, dict] = {}
    declarations: dict[str, list[str]] = {src: [] for src in by_source}

    for src, issue in by_source.items():
        source_status = issue.get("status", "open")
        source_type = issue.get("issue_type", "task")
        priority = issue.get("priority", 2)
        if not isinstance(priority, int) or isinstance(priority, bool):
            priority = 2
        priority = max(0, min(4, priority))
        labels = [f"beads:{src}"] + [l for l in issue.get("labels") or []
                                     if isinstance(l, str)]

        rec_type = TYPE_MAP.get(source_type)
        if rec_type is None:
            rec_type = "task"
            labels.append(f"beads:type:{source_type}")
        status = STATUS_MAP.get(source_status)
        if status is None:
            status = "open"
            labels.append(f"beads:status:{source_status}")
        if source_status == "blocked":
            labels.append("beads:status:blocked")
        if source_status == "deferred":
            labels.append("beads:status:deferred")
            if priority != 4:
                declarations[src].append(
                    f"deferred in source (original priority {priority}); "
                    f"imported at priority 4, excluded from `next`")
            priority = 4

        created = iso_date(issue.get("created_at"))
        updated = iso_date(issue.get("updated_at"), created)
        title = issue.get("title") if isinstance(issue.get("title"), str) else ""
        if not title.strip():
            title = f"(untitled bead {src})"

        body_parts = []
        for field, heading in (("description", None), ("design", "Design"),
                               ("acceptance_criteria", "Acceptance criteria"),
                               ("notes", "Notes")):
            text = issue.get(field)
            if isinstance(text, str) and text.strip():
                body_parts.append(text.strip() if heading is None
                                  else f"{heading}:\n{text.strip()}")
        comment_count = issue.get("comment_count", 0)
        if isinstance(comment_count, int) and comment_count > 0:
            declarations[src].append(f"{comment_count} source comment(s) not imported")

        disposition = None
        evidence: str | None = "unknown"
        if status == "done":
            closed_on = iso_date(issue.get("closed_at"))
            reason = issue.get("close_reason")
            disposition = f"Closed in source: beads {src}"
            if closed_on:
                disposition += f" on {closed_on}"
            if isinstance(reason, str) and reason.strip():
                disposition += f" — {reason.strip()}"
            else:
                disposition += "; the source recorded no close reason"
            disposition += (". Imported by beads2pecia; closure licensed by the "
                            "source export, not re-verified (rule 1).")
            # QUOTED (pc-3351, round-4 lane E1-F4): interpolated unquoted, a
            # source path carrying whitespace ('imports/beads data.jsonl')
            # produced stored evidence a shell splits into four arguments —
            # a certified provenance command that exits 2 at usage. The
            # data-derived tokens are shell-quoted so shlex.split gives back
            # exactly the arguments this adapter verified; the prefix stays
            # as declared (it is the operator's own command spelling).
            evidence = (f"{evidence_prefix} --verify-closed "
                        f"{shlex.quote(export_arg)} {shlex.quote(src)}")

        owner = next((issue.get(k) for k in ("assignee", "owner")
                      if isinstance(issue.get(k), str) and issue.get(k).strip()),
                     owner_fallback)

        records[src] = {
            "id": ids[src], "rev": 1, "type": rec_type, "title": title,
            "status": status, "priority": priority, "created": created,
            "updated": updated,
            "edges": empty_edges(),
            "disposition": disposition, "evidence": evidence, "owner": owner,
            "labels": labels, "body": "\n\n".join(body_parts),
        }

    # Dependency pass. Beads stores every dep on the DEPENDENT (issue_id
    # depends on depends_on_id); pecia's blocks edge lives on the blocker.
    deps: list[tuple[str, str, str]] = []
    for src, issue in by_source.items():
        for dep in issue.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            carrier = dep.get("issue_id") or src
            target = dep.get("depends_on_id")
            dep_type = dep.get("type", "blocks")
            if not isinstance(target, str) or not target:
                continue
            deps.append((carrier, target, dep_type))
    for carrier, target, dep_type in sorted(set(deps)):
        if carrier not in records:
            continue
        if target not in records:
            declarations[carrier].append(
                f"source dependency ({dep_type}) on {target}, which is not in "
                f"this export — edge not written (would dangle, E003)")
            counts["dangling"] += 1
            continue
        if dep_type == "blocks":
            blocks = records[target]["edges"]["blocks"]
            if ids[carrier] not in blocks:
                blocks.append(ids[carrier])
        elif dep_type == "parent-child":
            _set_scalar(records, declarations, counts, carrier, "parent",
                        ids[target], target, dep_type)
        elif dep_type in CUSTODY_TYPES:
            _set_scalar(records, declarations, counts, carrier,
                        CUSTODY_TYPES[dep_type], ids[target], target, dep_type)
        else:
            declarations[carrier].append(
                f"source dependency {dep_type} -> {target}: no exact pecia "
                f"semantics; declared here, not an edge")
            counts["unmapped_deps"] += 1

    for src, notes in declarations.items():
        if notes:
            block = "Declared by import:\n" + "\n".join(f"- {n}" for n in notes)
            body = records[src]["body"]
            records[src]["body"] = f"{body}\n\n{block}" if body else block
    for src, rec in records.items():
        rec["edges"]["blocks"].sort()
        issue = by_source[src]
        prov = (f"Imported from beads {src} ({export_arg}); source status "
                f"{issue.get('status', 'open')}, type {issue.get('issue_type', 'task')}.")
        rec["body"] = f"{rec['body']}\n\n{prov}" if rec["body"] else prov

    return [records[src] for src in sorted(records, key=lambda s: ids[s])], counts


def empty_edges() -> dict:
    """The full edge object, stated once (`pc-4342`).

    Was an inline literal at the one construction site, and so silently went
    a relation behind the format when v1.13 added `retires` — a shape spelled
    in one place is a shape that can be held to the CLI's EDGE_KEYS by a test,
    which is now done.
    """
    return {"blocks": [], "retires": [], "parent": None, "duplicate_of": None,
            "discovered_from": None, "caused_by": None,
            "validates": None, "supersedes": None}


def _set_scalar(records: dict, declarations: dict, counts: dict, carrier: str,
                edge: str, value: str, target: str, dep_type: str) -> None:
    if records[carrier]["edges"][edge] is None:
        records[carrier]["edges"][edge] = value
    else:
        declarations[carrier].append(
            f"source dependency {dep_type} -> {target} lost the scalar "
            f"`{edge}` slot (lowest source id wins); declared here")
        counts["scalar_collisions"] += 1


def cmd_verify_closed(export: Path, source_id: str) -> int:
    """Exit 0 iff the export records source_id with status closed.

    This is the evidence command written onto imported closures: its exit 0
    licenses "closed in source" — nothing more. Keep the export in-repo (or
    re-exportable) or the evidence stops being runnable, which `audit`
    will surface.
    """
    if not export.exists():
        print(json.dumps({"ok": False, "note": f"no export at {export}"}))
        return 2
    for raw in export.read_text().splitlines():
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, RecursionError):
            # RecursionError: the pc-2e2f class — skipping the unreadable
            # line is this scan's declared handling, and crashing is not.
            continue
        if isinstance(obj, dict) and obj.get("id") == source_id:
            closed = obj.get("status") == "closed"
            print(json.dumps({"ok": closed, "id": source_id,
                              "source_status": obj.get("status")}))
            return 0 if closed else 1
    print(json.dumps({"ok": False, "note": f"{source_id} not in {export}"}))
    return 2


def main() -> int:
    # Evidence mode dispatches BEFORE argparse: a flag between two
    # positionals parses differently across Python versions (pc-62f9,
    # caught by CI), and evidence commands must behave identically on
    # whatever interpreter uv resolves. Any argument order works.
    argv = sys.argv[1:]
    if "--verify-closed" in argv:
        rest = [a for a in argv if a != "--verify-closed"]
        if len(rest) != 2:
            print("usage: beads2pecia.py --verify-closed <export> <source-id>",
                  file=sys.stderr)
            return 2
        return cmd_verify_closed(Path(rest[0]), rest[1])

    parser = argparse.ArgumentParser(
        description="Convert a beads JSONL export into pecia records.",
        epilog="Evidence mode: beads2pecia.py --verify-closed <export> "
               "<source-id> — exit 0 iff the export records the issue "
               "closed (licenses 'closed in source', nothing more).")
    parser.add_argument("export", help="beads JSONL export (from `bd export`)")
    parser.add_argument("--out", default=None,
                        help="output path (default stdout)")
    parser.add_argument("--owner-fallback", default="import:beads",
                        help="owner when the source has no assignee/owner")
    parser.add_argument("--evidence-prefix",
                        default="python3 adapters/beads-import/beads2pecia.py",
                        help="how the evidence command runs this script, "
                             "repo-root-relative in the repo you import into; "
                             "python3-prefixed so it runs without uv (pc-5078)")
    args = parser.parse_args(argv)

    export = Path(args.export)
    if not export.exists():
        print(f"no export at {export}", file=sys.stderr)
        return 2

    issues, skipped, malformed = load_export(export)
    records, counts = convert(issues, args.export, args.evidence_prefix,
                              args.owner_fallback)
    lines = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    if args.out:
        Path(args.out).write_text(lines)
    else:
        sys.stdout.write(lines)

    for message in malformed:
        print(f"beads2pecia: skipped malformed issue — {message}", file=sys.stderr)
    print(json.dumps({"issues": len(issues), "records": len(records),
                      "skipped": skipped, "declared": counts,
                      "malformed": len(malformed),
                      "note": "validate: pecia check --ledger <out> (exit 0 "
                              "= well-formed, never true)"},
                     sort_keys=True), file=sys.stderr)
    return 1 if malformed else 0


if __name__ == "__main__":
    sys.exit(main())
