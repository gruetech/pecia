#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""report — render a deterministic HTML status report from a pecia ledger.

OUT OF CORE, and deliberately so. `pecia board` is the in-core read-only
projection; this is a second projection that happens to emit HTML, and it
lives beside the adapters (decision pc-8a27, adapters-outside-core) rather
than inside pecia_cli.py. It is NOT claim-bearing: nothing in claims.yaml
asserts anything about it, and rule 1 applies with full force — a report
that renders cleanly is well-formed, never true.

It is a candidate for promotion into `pecia report` when M4 (pc-24fa)
unblocks. Two of M4's blockers are this program's problems:
  pc-cdb8  ledger prose reaching readers unquoted. HTML makes it worse
           (injection, not just instruction), so EVERY string taken from
           the ledger goes through html.escape(), and mermaid labels are
           additionally stripped of characters that break the grammar.
  pc-5640  overdue milestones as a handled state. Handled here by
           comparing target dates against --as-of and labelling them,
           never by erroring.

DETERMINISM CONTRACT
--------------------
Same inputs -> byte-identical output. Verify the way ADAPTERS.md rule 4
verifies adapters: run twice, `cmp`. Specifically:

  * No wall-clock anywhere. --as-of is REQUIRED and is the only "now".
  * Graph queries (ready/blocked/next/audit/graph) are delegated to the
    real CLI, so this program cannot drift from `pecia next`. They were
    measured cwd-independent.
  * `gantt` titles itself from the staging directory's basename, so the
    staging directory takes its name from --name. It is created under the
    system temp dir, never inside a repo -- staging beside the canonical
    ledger leaves untracked residue next to a canonical file (pc-a93c).
  * `check` IS cwd-sensitive: spec v1.8 makes a path evidence iff git
    tracks it, resolved against cwd. An imported ledger whose evidence
    cites the adapter by relative path only checks clean from the pecia
    work tree. --check-cwd makes that explicit and the value is stamped
    into the report rather than left implicit. This is pc-f44a's class,
    cross-repo.
  * Resolution (highest rev per id) is recomputed locally, because the
    CLI exposes no JSON for status/type counts. Blocking semantics are
    NOT recomputed -- an earlier draft did, and disagreed with the CLI on
    three milestones because `parent` rollup blocks too. Delegate the
    graph; cross-check only what is computed here. Mismatch exits 2.

USAGE
-----
  dev/report.py --ledger L --name N --as-of YYYY-MM-DD --out F [options]

Options:
  --narrative FILE      prose embedded verbatim in a labelled block
  --evidence LABEL=FILE captured command output (repeatable). Capture,
                        never shell out live: test suites and foreign
                        checkers are environment-dependent.
  --source-ref TEXT     e.g. "axiomdb @ 5f3a1c2"
  --coverage TEXT       declared import coverage, e.g. "104 of 133 rows"
  --check-cwd DIR       where to run check (default: this repo's root)
  --include-graph       embed the full dependency graph (large ledgers
                        render unreadably; off by default)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CLI = REPO / "pecia_cli.py"
TOKENS = REPO / "design" / "tokens.css"

TERMINAL = {"done", "dropped", "superseded"}
STATUS_ORDER = ["done", "in-progress", "open", "blocked", "dropped", "superseded"]


# --------------------------------------------------------------------- CLI


def run_cli(args: list[str], cwd: Path, log_dir: Path | None = None) -> tuple[int, str]:
    """Run the CLI, pinning the v2 timeline when one was resolved.

    Storage v2 (format-v2.md 3.1) reads the timeline from --git-common-dir,
    NOT from a file in cwd, so staging a snapshot into a scratch directory
    stopped being enough: the CLI answers E000 there. PECIA_LOG_DIR is the
    supported explicit pin and is consulted before git, which is exactly
    what a tool wants -- the staging dir still supplies gantt's title while
    the data comes from the declared log.
    """
    env = dict(os.environ)
    if log_dir is not None:
        env["PECIA_LOG_DIR"] = str(log_dir)
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout


class QueryRefused(Exception):
    """A query did not return data. Never render around it."""


def cli_json(args: list[str], cwd: Path, log_dir: Path | None = None, expect=list):
    """Parse a query's JSON, REFUSING anything that is not data.

    An empty report is the dangerous output here: `ready 0, blocked 0`
    reads as a finished project rather than as a broken tool. This was not
    hypothetical -- when v2 storage landed, the staged-snapshot approach
    started returning a fatal E000 and a report rendered claiming zero
    ready and zero blocked against a ledger with 41 ready. A projection
    that cannot turn red is worse than none (VP4).
    """
    code, out = run_cli([*args, "--json"], cwd, log_dir)
    out = out.strip()
    if not out:
        raise QueryRefused(f"`{' '.join(args)}` produced no output (exit {code})")
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        # check emits one finding per line when it has findings
        parsed = [json.loads(l) for l in out.splitlines() if l.strip()]
    if isinstance(parsed, dict) and parsed.get("severity") in ("fatal", "error"):
        raise QueryRefused(
            f"`{' '.join(args)}` returned {parsed.get('code')}: {parsed.get('message')}"
        )
    if not isinstance(parsed, expect):
        raise QueryRefused(
            f"`{' '.join(args)}` returned {type(parsed).__name__}, expected "
            f"{expect.__name__}: {str(parsed)[:160]}"
        )
    return parsed


def resolve_log_dir(ledger: Path, explicit: str | None) -> Path | None:
    """Find the v2 timeline for the repo that owns this ledger, if any."""
    if explicit:
        return Path(explicit).resolve()
    proc = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(ledger.parent), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    common = (ledger.parent / proc.stdout.strip()).resolve()
    candidate = common / "pecia"
    return candidate if (candidate / "log.jsonl").exists() else None


# ---------------------------------------------------------------- ledger


def source_provenance(repo: Path, watch: str | None) -> str:
    """Compute the source ref rather than trusting a typed string.

    A report is only reproducible from a pinned source, so dirtiness is
    not a footnote -- it is the difference between a real ref and a
    fiction. Detected here so the honest string cannot be forgotten
    (substrate, not prose). This is pc-e039's shape, applied to reports.
    """

    def git(*a: str) -> str:
        p = subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""

    sha = git("rev-parse", "--short", "HEAD") or "unknown"
    dirty_all = [l for l in git("status", "--porcelain").splitlines() if l.strip()]
    ref = f"{repo.name} @ {sha}"
    if watch:
        touched = [l for l in dirty_all if l.split(maxsplit=1)[-1].endswith(watch)]
        if touched:
            return f"{ref} + UNCOMMITTED changes to {watch} — NOT reproducible from {sha}"
    if dirty_all:
        return f"{ref} (worktree dirty: {len(dirty_all)} path(s); watched file clean)"
    return f"{ref} (clean)"


def resolve(path: Path) -> tuple[dict, int]:
    """Highest rev per id wins (spec v1 resolution). Returns (heads, lines).

    E010 makes same-(id, rev)-different-content an error, so the checker
    has already ruled out the ambiguous case by the time we render.
    """
    heads: dict[str, dict] = {}
    lines = 0
    # LF-split, not splitlines() (v2.12, pc-d96d sibling): a JSON string
    # carrying a literal U+2028 is one physical line of the ledger, and
    # splitlines() would shear it into two unparseable halves here.
    for raw in path.read_text().split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        lines += 1
        rec = json.loads(raw)
        cur = heads.get(rec["id"])
        if cur is None or rec.get("rev", 0) >= cur.get("rev", 0):
            heads[rec["id"]] = rec
    return heads, lines


# ------------------------------------------------------------- rendering


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


_MERMAID_STRIP = re.compile(r'[\"\[\]{}()<>|;`\\\n\r]')


def mermaid_label(text: str, limit: int = 46) -> str:
    """Mermaid labels are not HTML-escaped by the renderer, so strip the
    characters that break the grammar or smuggle markup, then truncate."""
    flat = _MERMAID_STRIP.sub(" ", str(text))
    flat = re.sub(r"\s+", " ", flat).strip()
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


def mermaid_id(rid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", rid)


#: Kept in step with pecia_cli.GANTT_DIRECTIVES, and independently, because
#: this file is a consumer of the format and not an importer of the CLI. The
#: agreement is asserted by a test rather than assumed.
GANTT_DIRECTIVES = frozenset({
    "gantt", "title", "section", "dateformat", "axisformat", "tickinterval",
    "includes", "excludes", "todaymarker", "inclusiveenddates", "topaxis",
    "displaymode", "weekday", "acctitle", "accdescr", "click",
})


def gantt_task_label(label: str) -> str:
    """A gantt task label that cannot be read as a chart directive (pc-87e0).

    The character strip above is not enough on the gantt surface: its
    statements are recognized by a KEYWORD at the start of a line, so a
    milestone titled "title ATTACK" rebuilt here as `  title ATTACK :…` is
    read as the chart's title directive. Needed here in its own right and NOT
    merely inherited from the CLI's escape, because `mermaid_label()` above
    strips `"` and `()` — it would remove the CLI's quotes and its id
    parenthetical and re-expose the keyword at line start. Safe to quote for
    the same reason as there: every `"` is already gone, so these two are the
    only ones on the line."""
    first = label.split(" ", 1)[0].lower() if label else ""
    return f'"{label}"' if first in GANTT_DIRECTIVES else label


_GRAPH_NODE_RE = re.compile(r'^(\S+?)\["(.*)"\]$', re.S)
_GRAPH_EDGE_RE = re.compile(r"^(\S+?)\s*-->\s*(?:\|(.*?)\|\s*)?(\S+)$")


def sanitize_graph_line(line: str) -> str:
    """One body line of a `graph` document, rebuilt from sanitized parts.

    pc-ceff: sanitize_mermaid's else branch used to pass graph node/edge
    lines through untouched (esc() strips none of []{}|), so this layer
    covered gantt rows and comments but not the other document the page
    embeds. Mirroring the gantt dispatch was not enough on its own: a
    POISONED line (the pc-8f0a id-injection shape) does not parse as a
    well-formed node or edge at all, so a rewrite keyed on well-formed
    shapes would sanitize exactly the lines that were already safe. Hence
    three arms — rebuild a node, rebuild an edge, and neutralize anything
    else into a comment. The upstream generator emits no other shape, so an
    unrecognized line is by definition not trusted."""
    indent = line[: len(line) - len(line.lstrip())]
    body = line.strip()
    node = _GRAPH_NODE_RE.match(body)
    if node:
        return (f'{indent}{mermaid_id(node.group(1))}'
                f'["{mermaid_label(node.group(2), limit=64)}"]')
    edge = _GRAPH_EDGE_RE.match(body)
    if edge:
        mid = f"|{mermaid_label(edge.group(2), limit=32)}| " if edge.group(2) else ""
        return (f"{indent}{mermaid_id(edge.group(1))} --> "
                f"{mid}{mermaid_id(edge.group(3))}")
    return f"{indent}%% {mermaid_label(body, limit=90)}"


def sanitize_mermaid(src: str) -> str:
    """Neutralize ledger prose inside generated mermaid source.

    HTML-escaping is NOT sufficient here: the mermaid renderer reads the
    element's textContent, which un-escapes entities, so a title carrying
    quotes or brackets reaches the parser intact and can break the diagram
    or smuggle syntax. `pecia gantt` emits record titles verbatim, so the
    label half of each row is sanitized while the structural half (tag,
    dates) is preserved; a `graph` document dispatches per line to
    sanitize_graph_line (pc-ceff). pc-cdb8, in its rendering form.
    """
    lines = src.splitlines()
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    is_graph = bool(first) and first.split()[0].lower() == "graph"
    seen_header = False
    out = []
    for line in lines:
        if line.lstrip().startswith("%%"):
            marker, _, rest = line.partition("%%")
            out.append(f"{marker}%% {mermaid_label(rest, limit=90)}")
        elif is_graph:
            if not seen_header and line.strip() == first:
                out.append(line)  # the generator's own literal, not prose
                seen_header = True
            elif not line.strip():
                out.append(line)
            else:
                out.append(sanitize_graph_line(line))
        elif " :" in line:
            label, sep, tail = line.partition(" :")
            indent = label[: len(label) - len(label.lstrip())]
            out.append(f"{indent}"
                       f"{gantt_task_label(mermaid_label(label, limit=64))}"
                       f"{sep}{tail}")
        else:
            out.append(line)
    return "\n".join(out)


def pill(status: str) -> str:
    cls = status if status in STATUS_ORDER else "dropped"
    return f'<span class="pc-pill status-{esc(cls)}">{esc(status)}</span>'


def paragraphs(text: str) -> str:
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return "\n".join(f"<p>{esc(b)}</p>" for b in blocks) or "<p><em>(empty)</em></p>"


# ------------------------------------------------------------------ main


def build(args) -> str:
    ledger = Path(args.ledger).resolve()
    check_cwd = Path(args.check_cwd).resolve() if args.check_cwd else REPO

    if args.source_repo:
        args.source_ref = source_provenance(
            Path(args.source_repo).resolve(), args.source_file
        )

    heads, total_lines = resolve(ledger)

    # Staging dir named from --name so `gantt`'s directory-derived title is
    # stable -- but placed OUTSIDE any repo. Writing it beside the ledger
    # left untracked residue next to a canonical file, which is the shape
    # of pc-a93c. Graph queries were measured cwd-independent, so the
    # location is free; only the basename matters.
    stage = Path(tempfile.gettempdir()) / f"pecia-report-{args.name}" / args.name
    (stage / ".pecia").mkdir(parents=True, exist_ok=True)
    (stage / ".pecia" / "work.jsonl").write_text(ledger.read_text())

    log_dir = resolve_log_dir(ledger, args.log_dir)
    ready = cli_json(["ready"], stage, log_dir)
    blocked = cli_json(["blocked"], stage, log_dir)
    nxt = cli_json(["next"], stage, log_dir)
    audit = cli_json(["audit"], stage, log_dir, expect=dict)
    # `--mermaid` EXPLICITLY, and the exit code checked (pc-0ef6 consumer
    # regression, found by the codex pass 2026-08-22). This read bare `gantt`
    # and parsed the result as Mermaid. That was correct until v2.2 flipped
    # gantt's DEFAULT to ASCII, after which the Mermaid parser matched nothing
    # and every report silently declared "No milestone record carries a target
    # date" — while exiting 0, over a ledger with three dated milestones.
    #
    # _has_bars' own docstring already records this exact silent-false-empty
    # failure happening once before (pc-44f1, a prefix that could never match).
    # It recurred by a different route, which is the argument for the
    # integration test in tests/test_report.py rather than for being more
    # careful: three comments in tests/test_pecia.py name "dev/report.py's row
    # parser" as the contract under protection, and all three were moved to
    # `--mermaid` while THIS caller was not, so the suite stayed green across
    # the break. A consumer contract asserted only from the producer's side is
    # not asserted.
    gantt_rc, gantt_src = run_cli(["gantt", "--mermaid"], stage, log_dir)
    if gantt_rc != 0:
        sys.stderr.write(
            f"REFUSING: `gantt --mermaid` exited {gantt_rc}.\n"
            "  Rendering would emit a silently empty milestone section.\n")
        raise SystemExit(2)
    graph_src = ""
    if args.include_graph:
        _, graph_src = run_cli(["graph", "--format", "mermaid"], stage, log_dir)

    # Cross-check resolution ONLY. Blocking semantics are the CLI's -- they
    # involve both `blocks` and `parent` rollup, and an earlier version of
    # this program reimplemented them and disagreed on three milestones.
    # The lesson (pc-c360: rebuild as check-diff so the gate cannot drift
    # from the checker) applies to projections too: delegate the graph, and
    # verify only the one thing computed locally, which is head resolution.
    cli_ids = {r["id"] for r in ready} | {b["id"] for b in blocked}
    missing = sorted(cli_ids - set(heads))
    terminal_ready = sorted(
        r["id"] for r in ready if heads.get(r["id"], {}).get("status") in TERMINAL
    )
    if missing or terminal_ready:
        sys.stderr.write(
            "REFUSING: local head resolution disagrees with the CLI.\n"
            + (f"  ids the CLI reports but resolution lost: {missing}\n" if missing else "")
            + (f"  ids the CLI calls ready that resolve terminal: {terminal_ready}\n"
               if terminal_ready else "")
        )
        raise SystemExit(2)

    check_code, check_out = run_cli(["check", "--ledger", str(ledger), "--json"], check_cwd)
    check_findings = [
        json.loads(l) for l in check_out.strip().splitlines() if l.strip().startswith("{")
    ]
    check_summary = next((f for f in check_findings if "ok" in f), None)
    check_errors = [f for f in check_findings if f.get("severity") == "error"]

    statuses = Counter(r.get("status", "unknown") for r in heads.values())
    types = Counter(r.get("type", "unknown") for r in heads.values())

    as_of = date.fromisoformat(args.as_of)
    tokens_css = TOKENS.read_text() if TOKENS.exists() else ""

    # ---- assemble -----------------------------------------------------
    p: list[str] = []
    a = p.append

    a(f"<title>{esc(args.name)} — status, {esc(args.as_of)}</title>")
    a("<style>")
    a(tokens_css)
    a(_LAYOUT_CSS)
    a("</style>")
    a('<div class="page">')

    # header
    a('<header class="report-head">')
    a('<div class="eyebrow"><span>STATUS REPORT</span>'
      f"<span>{esc(args.as_of)}</span>")
    if args.source_ref:
        a(f"<span>{esc(args.source_ref)}</span>")
    a("</div>")
    a(f"<h1>{esc(args.name)}</h1>")
    a('<p class="subtitle">generated projection of a pecia ledger — '
      "exit 0 means well-formed, never true</p>")
    a('<div class="meta-row">')
    a(f"<span>RECORDS <b>{len(heads)}</b></span>")
    a(f"<span>REVISIONS <b>{total_lines}</b></span>")
    if check_summary and check_summary.get("ok"):
        a('<span>CHECK <b class="pc-pill status-done">clean</b></span>')
    else:
        a(f'<span>CHECK <b class="pc-pill status-blocked">'
          f"{len(check_errors)} error(s)</b></span>")
    a(f"<span>READY <b>{len(ready)}</b></span>")
    a(f"<span>BLOCKED <b>{len(blocked)}</b></span>")
    a("</div></header>")

    # coverage / provenance
    a("<section><h2><span class='num'>01</span> Provenance</h2>")
    a('<table class="pc-table"><tbody>')
    a(f"<tr><td>ledger</td><td><code>{esc(ledger)}</code></td></tr>")
    if args.source_ref:
        a(f"<tr><td>source</td><td><code>{esc(args.source_ref)}</code></td></tr>")
    a(f"<tr><td>as-of</td><td><code>{esc(args.as_of)}</code> "
      "(injected; this program reads no clock)</td></tr>")
    a(f"<tr><td>check run from</td><td><code>{esc(check_cwd)}</code> "
      "— evidence validity is cwd-relative (spec v1.8)</td></tr>")
    a("</tbody></table>")
    if args.coverage:
        a('<div class="pc-callout warn"><span class="tag">declared import coverage</span>'
          f"<p>{esc(args.coverage)}</p></div>")
    if check_errors:
        a('<div class="pc-callout warn"><span class="tag">check is not clean</span><p>'
          f"{len(check_errors)} error finding(s). Codes: "
          + esc(", ".join(sorted({f.get('code', '?') for f in check_errors})))
          + "</p></div>")
    a("</section>")

    # stats
    a("<section><h2><span class='num'>02</span> Ledger at a glance</h2>")
    a('<div class="pc-stats">')
    a(f'<div class="pc-stat"><div class="n">{len(heads)}</div>'
      '<div class="l">resolved records</div></div>')
    a(f'<div class="pc-stat"><div class="n">{total_lines}</div>'
      '<div class="l">total revisions</div></div>')
    for st in STATUS_ORDER:
        if statuses.get(st):
            a(f'<div class="pc-stat"><div class="n">{statuses[st]}</div>'
              f'<div class="l">{esc(st)}</div></div>')
    a("</div>")

    total = sum(statuses.values()) or 1
    a('<div class="pc-statusbar">')
    for st in STATUS_ORDER:
        if statuses.get(st):
            pct = statuses[st] / total * 100
            a(f'<span style="width:{pct:.2f}%;background:var(--status-{st})"></span>')
    a("</div>")
    a('<div class="legend">')
    for st in STATUS_ORDER:
        if statuses.get(st):
            a(f'<span><i style="background:var(--status-{st})"></i>'
              f"{esc(st)} — {statuses[st]}</span>")
    a("</div>")

    a('<h3>by type</h3><table class="pc-table"><thead><tr><th>type</th>'
      "<th>count</th></tr></thead><tbody>")
    for ty, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0])):
        a(f"<tr><td>{esc(ty)}</td><td class='num'>{n}</td></tr>")
    a("</tbody></table></section>")

    # ready
    a("<section><h2><span class='num'>03</span> Ready work</h2>")
    a(f"<p>{len(ready)} unblocked; <code>next</code> returns the top "
      f"{len(nxt)} in total order.</p>")
    a('<table class="pc-table"><thead><tr><th>id</th><th>pri</th><th>type</th>'
      "<th>title</th></tr></thead><tbody>")
    for r in nxt:
        a(f"<tr><td class='id'>{esc(r.get('id'))}</td>"
          f"<td class='num'>{esc(r.get('priority'))}</td>"
          f"<td>{esc(r.get('type'))}</td><td>{esc(r.get('title'))}</td></tr>")
    a("</tbody></table>")
    if len(ready) > len(nxt):
        a(f"<p style='font-size:13px;color:var(--ink-soft)'>+{len(ready) - len(nxt)}"
          " more ready below the <code>next</code> cut-off.</p>")
    a("</section>")

    # blocked
    a("<section><h2><span class='num'>04</span> Blocked</h2>")
    if not blocked:
        a("<p>Nothing is blocked.</p>")
    else:
        a('<table class="pc-table"><thead><tr><th>id</th><th>title</th>'
          "<th>blocked by / awaiting</th></tr></thead><tbody>")
        for b in blocked:
            rec = heads.get(b["id"], {})
            deps = "".join(
                f"<div><code>{esc(d)}</code> {esc(heads.get(d, {}).get('title', ''))[:52]}</div>"
                for d in b.get("blockers", [])
            ) + "".join(
                f"<div><em>awaiting</em> <code>{esc(d)}</code> "
                f"{esc(heads.get(d, {}).get('title', ''))[:52]}</div>"
                for d in b.get("awaiting_answers", [])
            )
            a(f"<tr><td class='id'>{esc(b['id'])}</td>"
              f"<td>{esc(rec.get('title', b.get('title', '')))}</td>"
              f"<td>{deps}</td></tr>")
        a("</tbody></table>")
    a("</section>")

    # milestones / gantt
    has_bars = _has_bars(gantt_src)
    a("<section><h2><span class='num'>05</span> Milestones</h2>")
    overdue = _overdue(gantt_src, as_of)
    if overdue:
        a('<div class="pc-callout warn"><span class="tag">overdue against --as-of</span><p>'
          + esc(", ".join(f"{i} (target {d})" for i, d in overdue))
          + "</p></div>")
    if has_bars:
        a('<div class="pc-folio"><pre class="mermaid">')
        a(esc(sanitize_mermaid(gantt_src).strip()))
        a("</pre><div class='pc-folio-cap'>generated by <code>pecia gantt</code>"
          "</div></div>")
    else:
        a("<p>No milestone record carries a target date; "
          "<code>gantt</code> declares itself empty rather than drawing a "
          "decorative chart.</p>")
    a("</section>")

    # audit
    a("<section><h2><span class='num'>06</span> Audit surface</h2>")
    findings = audit.get("findings", []) if isinstance(audit, dict) else []
    a(f"<p>{len(findings)} advisory finding(s). Audit never blocks; it "
      "surfaces frontier-vs-rot.</p>")
    if findings:
        a('<table class="pc-table"><thead><tr><th>kind</th><th>id</th>'
          "<th>note</th></tr></thead><tbody>")
        for f in findings:
            a(f"<tr><td>{esc(f.get('kind'))}</td>"
              f"<td class='id'>{esc(f.get('id'))}</td>"
              f"<td>{esc(f.get('note'))}</td></tr>")
        a("</tbody></table>")
    a("</section>")

    # captured evidence
    if args.evidence:
        a("<section><h2><span class='num'>07</span> Captured evidence</h2>")
        a("<p>Command output captured as an input, not run live — foreign "
          "checkers and test suites are environment-dependent, so embedding "
          "them by capture is what keeps this report reproducible.</p>")
        for label, fp in args.evidence:
            a(f"<h3>{esc(label)}</h3>")
            a('<div class="pc-folio"><pre class="evidence">')
            a(esc(Path(fp).read_text().strip()))
            a("</pre></div>")
        a("</section>")

    # graph
    if args.include_graph and graph_src.strip():
        a("<section><h2><span class='num'>08</span> Dependency graph</h2>")
        a('<div class="pc-folio"><pre class="mermaid">')
        a(esc(sanitize_mermaid(graph_src).strip()))
        a("</pre></div></section>")

    # narrative
    if args.narrative:
        a("<section><h2><span class='num'>09</span> Narrative</h2>")
        a('<div class="pc-callout"><span class="tag">'
          "hand-written · embedded verbatim · not generated</span>"
          "<p>Everything above is computed from the ledger. This section is "
          f"prose from <code>{esc(Path(args.narrative).name)}</code> and "
          "carries no more authority than its author.</p></div>")
        a('<div class="narrative">')
        a(paragraphs(Path(args.narrative).read_text()))
        a("</div></section>")

    a("<footer>")
    a(f"Generated by <code>dev/report.py</code> from <code>{esc(ledger.name)}</code>"
      f" as of {esc(args.as_of)}. Deterministic: same inputs give byte-identical "
      "output. Out of core and not claim-bearing — see the module docstring. "
      "Rule 1 applies: a report that renders is well-formed, never true.")
    a("</footer></div>")
    return "\n".join(p) + "\n"


# A gantt bar is `label :tag, start, end` OR `label :state, tag, start, end`
# -- pecia emits the four-field form once a milestone carries a state (e.g.
# `:done, pc_fb43, ...`). Matching only the three-field form made completed
# and overdue milestones invisible to _overdue; found 2026-08-13 when M6
# closed and its bar stopped being counted.
_BAR = re.compile(
    r":\s*(?P<tag>[^,]*),\s*(?:(?P<rid>[^,]*),\s*)?"
    r"(?P<start>\d{4}-\d{2}-\d{2}),\s*(?P<end>\d{4}-\d{2}-\d{2})\s*$"
)


def _has_bars(gantt_src: str) -> bool:
    """True iff gantt emitted at least one real bar.

    An empty projection must DECLARE itself rather than render a
    decorative frame (pc-44f1). The first version of this test compared
    a stripped line against a leading-whitespace prefix, which can never
    match, so every report silently claimed 'no milestone has a target'.
    Matching the bar's own `:tag, start, end` shape is checkable.
    """
    return any(_BAR.search(line) for line in gantt_src.splitlines())


def _overdue(gantt_src: str, as_of: date) -> list[tuple[str, str]]:
    """Overdue is a handled STATE, never an error (pc-5640).

    A TERMINAL milestone is never overdue, however late it finished. That is
    not a detail — it is the exact discriminating control pc-5640 built the
    predicate around ("a milestone that finished AFTER its target fires
    neither the finding nor crit — without it, 'fires on any past target' and
    'fires on overdue' are the same green suite"). This function implemented
    the first of those two and called it the second: it tested `end < as_of`
    and never looked at status, so `pecia audit` reported no overdue
    milestones on this repo's own ledger while the HTML report announced one
    (pc-24fa, done 2026-08-15). Found 2026-08-23 while verifying the
    `--mermaid` fix above.

    This is the drift this very file warns about thirty lines up — "an earlier
    version of this program reimplemented [blocking semantics] and disagreed
    on three milestones", the pc-c360 lesson to delegate rather than re-derive.
    The fix honours it: `:done`/`:crit` status tags come from the SAME
    `milestone_is_overdue` predicate the checker and `audit` use, so read the
    tag rather than recompute the state from dates.
    """
    out = []
    for line in gantt_src.splitlines():
        m = _BAR.search(line)
        if not m:
            continue
        tags = {t.strip() for t in (m.group("tag") or "").split(":") if t.strip()}
        if "done" in tags:
            continue
        rid = (m.group("rid") or m.group("tag") or "?").strip()
        end = m.group("end")
        try:
            if date.fromisoformat(end) < as_of:
                out.append((rid, end))
        except ValueError:
            continue
    return sorted(out)


_LAYOUT_CSS = """
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:var(--font-body);
 font-size:16px;line-height:1.55;margin:0;padding:0 24px 80px}
.page{max-width:860px;margin:0 auto}
.report-head{padding:56px 0 28px;border-bottom:2px solid var(--ink)}
.eyebrow{font-family:var(--font-mono);font-size:12.5px;letter-spacing:.08em;
 text-transform:uppercase;color:var(--ink-soft);display:flex;gap:18px;
 flex-wrap:wrap;margin-bottom:10px}
h1{font-family:var(--font-display);font-weight:600;font-size:2.6rem;
 margin:0 0 8px;text-wrap:balance;letter-spacing:-.01em}
.subtitle{font-family:var(--font-display);font-style:italic;
 color:var(--ink-soft);font-size:1.1rem;margin:0}
.meta-row{margin-top:20px;display:flex;gap:26px;flex-wrap:wrap;
 font-family:var(--font-mono);font-size:12.5px;color:var(--ink-soft)}
.meta-row b{color:var(--ink);font-weight:600}
section{padding:38px 0;border-bottom:1px solid var(--rule)}
section:last-of-type{border-bottom:none}
h2{font-family:var(--font-display);font-size:1.45rem;font-weight:600;
 margin:0 0 16px;display:flex;align-items:baseline;gap:12px}
h2 .num{font-family:var(--font-mono);font-size:.75rem;color:var(--accent-soft)}
h3{font-size:1rem;font-weight:700;margin:26px 0 10px;color:var(--accent)}
p{margin:0 0 14px;max-width:68ch}
code{font-family:var(--font-mono);font-size:12.5px;color:var(--accent)}
.pc-stats{margin-top:8px}
.pc-statusbar{margin:14px 0 10px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink-soft)}
.legend i{display:inline-block;width:9px;height:9px;margin-right:6px;
 vertical-align:middle}
.pc-table{margin:6px 0 4px}
pre.mermaid,pre.evidence{background:transparent;margin:0;
 font-family:var(--font-mono);font-size:12px;line-height:1.5;
 white-space:pre;color:var(--ink)}
pre.evidence{white-space:pre-wrap;word-break:break-word}
.narrative{max-width:68ch}
footer{padding:30px 0 0;font-family:var(--font-mono);font-size:11.5px;
 color:var(--ink-soft);line-height:1.6}
"""


def main() -> int:
    ap = argparse.ArgumentParser(prog="report", description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--name", required=True, help="repo name; also the staging dir")
    ap.add_argument("--as-of", required=True, metavar="YYYY-MM-DD",
                    help="the only 'now' this program has; no clock is read")
    ap.add_argument("--out", required=True)
    ap.add_argument("--narrative")
    ap.add_argument("--evidence", action="append", default=[], metavar="LABEL=FILE")
    ap.add_argument("--source-ref", help="free text; prefer --source-repo")
    ap.add_argument("--source-repo", help="compute the ref (sha + dirtiness) from git")
    ap.add_argument("--source-file", help="path suffix inside --source-repo to watch for dirtiness")
    ap.add_argument("--coverage")
    ap.add_argument("--check-cwd")
    ap.add_argument("--log-dir", help="pin the v2 timeline (PECIA_LOG_DIR); auto-detected otherwise")
    ap.add_argument("--include-graph", action="store_true")
    args = ap.parse_args()

    try:
        date.fromisoformat(args.as_of)
    except ValueError:
        sys.stderr.write("--as-of must be YYYY-MM-DD\n")
        return 2

    parsed = []
    for item in args.evidence:
        if "=" not in item:
            sys.stderr.write(f"--evidence needs LABEL=FILE, got {item!r}\n")
            return 2
        label, _, fp = item.partition("=")
        if not Path(fp).exists():
            sys.stderr.write(f"evidence file not found: {fp}\n")
            return 2
        parsed.append((label, fp))
    args.evidence = parsed

    if not CLI.exists():
        sys.stderr.write(f"cannot find pecia_cli.py at {CLI}\n")
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = build(args)
    except QueryRefused as exc:
        sys.stderr.write(f"REFUSING to render: {exc}\n")
        return 2
    out.write_text(rendered)
    sys.stderr.write(f"wrote {out} ({len(out.read_text())} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
