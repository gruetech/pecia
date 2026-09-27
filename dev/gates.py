#!/usr/bin/env python3
"""The gate runner — the registry in dev/gates.json is executed here, by this.

Usage:
  dev/gates.py --list                     what is registered, and how it runs
  dev/gates.py --audit [--index]          does the registry match the gates on disk?
  dev/gates.py --run [--id ID]            run gates against the WORKTREE
  dev/gates.py --prove [--id ID]          apply each gate's registered disabling
                                          edit and require its red-case arm to FAIL
  dev/gates.py --precommit --staged-root D  run the commit gates against the INDEX

WHY A RUNNER AND NOT A LIST. BP23 in the practice catalog names the failure
this replaces: "a guard registry maintained by the people who installed the
guards errs toward claiming enforcement", evidenced by an audit that corrected
two rows reading as enforced when the hook had never been enabled in any clone.
A registry that DESCRIBES gates can be wrong silently. This one is the only
path by which a gate runs, so an entry that is wrong stops a commit.

Exit codes follow the repo convention: 0 clean, 1 findings, 2 cannot-run.

STDLIB ONLY, plain python3. It runs inside pre-commit, so it may not depend on
uv, a venv, or a package that a fresh clone has not installed yet — the gate
that cannot start is indistinguishable from the gate that passed.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "gates.json"

# The register's own id scan, shared with dev/claims-ref.py (pc-3472). This
# file runs from the hook with `dev/` as sys.path[0], and the import is
# path-independent for any other caller through the same directory.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from claims_ids import claim_ids  # noqa: E402


#: WHAT EACH REQUIRED COMMIT GATE MUST ACTUALLY READ. Pinned here, in code,
#: for the third time this file has had to learn the same lesson: ANYTHING THE
#: REGISTRY CAN SELF-DECLARE, THE REGISTRY CAN SELF-SERVE.
#:
#: Round 5 landed real commits carrying real defects against claims-check and
#: ledger-check — full loss of both — by repointing each gate at a small clean
#: file it supplied and DELETING the real path from `inputs`. Every rule from
#: rounds 1-4 held and none of them fired, because the rule "every declared
#: input must be referenced" takes its list of obligations from the same field
#: the attacker edits. It asked whether the registry was consistent with
#: itself. It was. The gates ran, examined something, read the index — and the
#: something was a decoy.
#:
#: So the canonical subject of each required gate is no longer the registry's
#: to state. The set below is exact in both directions: a required gate must
#: read every path here and NOTHING ELSE from the index, so a decoy cannot be
#: added and a real file cannot be dropped.
REQUIRED_INPUTS = {
    "claims-check": {"claims.yaml"},
    "ledger-check": {".pecia/work.jsonl", ".pecia/config.yaml"},
    "vocab-check": {"spec/format-v1.md", "spec/format-v2.md",
                    "spec/vocabulary.json", "pecia_cli.py"},
    "prose-check": {"README.md", "claims.yaml", "PLAN.md", "spec/format-v1.md"},
}


#: OPTIONS A COMMIT GATE MAY BE GIVEN. This lives in CODE, not in the registry
#: it polices, and that is the entire point: a fourth adversarial round landed
#: a real commit carrying a real defect by appending ONE token to a gate's
#: staged_argv in dev/gates.json — `--write`, a legitimate flag of
#: vocab-check's own, which turns "compare the generated registry byte for
#: byte" into "regenerate it and call it clean". Every earlier rule passed it:
#: it is not a path, not absolute, has no `..`, no {top}, and removes no
#: declared input. The audit knew where arguments POINTED and nothing about
#: what they AUTHORISED.
#:
#: So the vocabulary is closed and it is short. These options SELECT WHAT TO
#: READ. Anything that changes what a checker DOES — --write, --fix, --force,
#: --update, --no-verify — is absent by construction, and widening this set is
#: a code edit that cannot be made from the registry.
SAFE_OPTIONS = {"--root", "--ledger", "--config", "--spec", "--source",
                "--registry", "--schema"}


def top() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return HERE.parent


def load(registry_text: str | None = None) -> dict:
    text = registry_text if registry_text is not None else REGISTRY.read_text()
    return json.loads(text)


# ── the registry's own shape ──────────────────────────────────────────────
#
# SIBLING OF pc-1a93, SAME COMMIT. The claims register certified `claim: true`
# because it checked presence and never type; this register is the same shape
# one file over, and it fails in BOTH directions at once. Measured on the
# shipped registry with one field retyped:
#   `why: true`          → `registry audit: ok`, exit 0. Certified.
#   `id: true`           → TypeError: '<' not supported between str and bool
#   `staged_argv: true`  → TypeError: 'bool' object is not iterable
#   `gates: true`        → TypeError: 'bool' object is not iterable
# — a traceback out of the gate runner the hook calls, which is a crash where
# the contract says a malformed input is a FINDING. So the shape is checked
# ONCE, at load, on every route (audit, precommit, run, list): a malformed
# registry is a cannot-run refusal naming the field, and nothing downstream
# has to defend itself against a type.
#
# Fields are typed if present rather than all required, because the registry
# carries several entry KINDS (a runnable gate, an `inline` hook gate, a
# `ships` declaration) and no field but `id` is common to all of them. An
# unknown key is refused: this vocabulary is closed and small, and an
# unreadable key is where a typo hides as a silently ignored setting.
STR, BOOL, STRS = "string", "boolean", "list of strings"
#: A registered DISABLING MUTATION (pc-ba95): {"path", "from", "to", "why"},
#: every value a string. It is the discriminating case for a red-case arm —
#: the edit that turns the gate off — so that `--prove` can apply it and show
#: the arm failing, instead of the registry asserting a proof it never ran.
MUT = "mutation object {path, from, to, why}"
MUT_KEYS = ("path", "from", "to", "why")
GATE_FIELDS: dict[str, tuple[str, bool]] = {
    # field: (kind, nullable)
    "id": (STR, False), "why": (STR, False), "hook": (STR, False),
    "require_cached": (STR, False), "redcase": (STR, True),
    "redcase_mutation": (MUT, True),
    "claim": (STR, True), "claim_why_not": (STRS, True),
    "precommit": (BOOL, False), "inline": (BOOL, False), "ships": (BOOL, False),
    # `argv` is nullable: an entry that DECLARES a gate without naming a
    # runnable command (a withheld `ships:false` publisher, an `inline` hook
    # gate) carries none, and the audit already reads it as "nothing to run".
    "argv": (STRS, True), "staged_argv": (STRS, True),
    "inputs": (STRS, True), "triggers": (STRS, True),
    "on_fail": (STRS, True), "markers": (STRS, True),
    "precommit_why_not": (STRS, True), "ships_why": (STRS, True),
}


def _kind_error(kind: str, value: object) -> str | None:
    if kind is STR:
        return None if isinstance(value, str) else f"a {STR}"
    if kind is BOOL:
        return None if isinstance(value, bool) else f"a {BOOL}"
    if kind is MUT:
        if (isinstance(value, dict)
                and set(value) == set(MUT_KEYS)
                and all(isinstance(value[k], str) and value[k].strip()
                        for k in MUT_KEYS if k != "to")
                and isinstance(value["to"], str)):
            return None
        return f"a {MUT}"
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return None
    return f"a {STRS}"


def shape_findings(reg: object) -> list[str]:
    """What is structurally wrong with this registry, in reader's terms."""
    out: list[str] = []
    if not isinstance(reg, dict):
        return [f"the registry must be an object, not a {type(reg).__name__}"]

    discovery = reg.get("discovery")
    if not isinstance(discovery, dict):
        out.append(f"`discovery` must be an object, not a "
                   f"{type(discovery).__name__}")
    elif _kind_error(STRS, discovery.get("globs")):
        out.append(f"`discovery.globs` must be a {STRS}, not a "
                   f"{type(discovery.get('globs')).__name__}")

    gates = reg.get("gates")
    if not isinstance(gates, list):
        return out + [f"`gates` must be a list, not a {type(gates).__name__}"]

    for position, g in enumerate(gates, start=1):
        if not isinstance(g, dict):
            out.append(f"gate #{position}: an entry must be an object, not a "
                       f"{type(g).__name__}")
            continue
        gid = g.get("id")
        where = f"gate '{gid}'" if isinstance(gid, str) else f"gate #{position}"
        if not isinstance(gid, str) or not gid.strip():
            out.append(f"{where}: `id` must be a non-empty {STR}, not "
                       f"{gid!r} — it is the name the hook's floor requires "
                       f"by, so an untyped one disables a required gate "
                       f"invisibly")
        for field, value in g.items():
            if field.startswith("$"):
                continue                  # a comment, by this file's convention
            if field not in GATE_FIELDS:
                out.append(f"{where}: undeclared field `{field}` — the entry "
                           f"vocabulary is closed, and an unread key is where "
                           f"a typo hides as a setting nothing applies")
                continue
            kind, nullable = GATE_FIELDS[field]
            if value is None and nullable:
                continue
            wanted = _kind_error(kind, value)
            if wanted:
                out.append(f"{where}: `{field}` must be {wanted}, not "
                           f"{value!r}")
    return out


def expand(argv: list[str], root: Path, staged: Path | None) -> list[str]:
    out = []
    for a in argv:
        a = a.replace("{top}", str(root))
        if staged is not None:
            a = a.replace("{staged}", str(staged))
        out.append(a)
    return out


# ── audit: the registry must match what is on disk ────────────────────────

def git_lines(args: list[str], root: Path) -> list[str]:
    """NUL-delimited (which is what actually suppresses path quoting).

    Plain `git ls-files` octal-escapes and quotes any path with a non-ASCII
    byte, so such a path matched no glob and no trigger: a gate-shaped file
    with an accented name was invisible to discovery, and a staged file with
    one never triggered its gate. Both are silent misses, which is the only
    kind that matters.

    `-z` is what fixes it — NUL-delimited output is never quoted. The
    `core.quotepath=false` below is belt-and-braces and does NOT carry this on
    its own; an ablation showed the non-ASCII arm stays green without it. Said
    plainly because a comment claiming the wrong mechanism is how the next
    person deletes the half that works."""
    r = subprocess.run(["git", "-c", "core.quotepath=false"] + args + ["-z"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.split("\0") if ln.strip()]


def discovered(reg: dict, root: Path, from_index: bool) -> list[str]:
    globs = reg["discovery"]["globs"]
    if from_index:
        pool = git_lines(["ls-files", "--cached"], root)
    else:
        # os.walk, not pathlib.rglob (pc-c10a, round-4 lane D-F5): rglob had
        # no error handling of its own, so whatever the interpreter's pathlib
        # did WAS the behaviour — and under Apple CLT Python 3.9.6 (the lane
        # sandbox) a directory vanishing between listing and descent killed
        # the audit with an unhandled FileNotFoundError on the first attempt,
        # while 3.12's pathlib swallows the OSError (measured both ways by
        # the round-4 scorer). os.walk's default onerror ignores a vanished
        # directory on every interpreter, so the walk's tolerance is now
        # this code's property, not the stdlib-of-the-day's. is_file() keeps
        # broken symlinks out of the pool, as the rglob form did, and never
        # raises — it reports False on any OSError.
        pool = []
        for dirpath, dirnames, filenames in os.walk(root):
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_file():
                    pool.append(str(p.relative_to(root)))
        pool.sort()
    hits = []
    for path in pool:
        if any(fnmatch.fnmatch(path, g) for g in globs):
            hits.append(path)
    return sorted(set(hits))


def registered_paths(reg: dict) -> dict[str, str]:
    """Map repo-relative path -> gate id, for every argv entry naming a repo file."""
    out = {}
    for g in reg["gates"]:
        for argv in (g.get("argv"), g.get("staged_argv")):
            if not argv:
                continue
            for a in argv:
                if a.startswith("{top}/"):
                    out.setdefault(a[len("{top}/"):], g["id"])
    return out


def resolves(dotted: str, root: Path) -> bool:
    """Does `pkg.module.Class.test_name` name a test that actually exists?

    Read with ast rather than imported: the audit runs inside pre-commit, and a
    checker that imports the test suite to validate the registry would make an
    unrelated import error look like a registry fault.
    """
    import ast
    parts = dotted.split(".")
    if len(parts) < 3:
        return False
    *modparts, cls, func = parts
    path = root.joinpath(*modparts).with_suffix(".py")
    if not path.exists():
        return False
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == func for n in node.body)
    return False


def audit(reg: dict, root: Path, from_index: bool, require: list[str],
          claims: Path | None = None) -> int:
    """`require` is the FLOOR, and it is deliberately NOT read from the
    registry it audits.

    Without it this function verified only that the registry was internally
    CONSISTENT, never that it still enabled anything — so
    `{"discovery":{"globs":[]},"gates":[]}` audited clean, ran nothing, and
    printed `registry audit: ok`. So did flipping all four commit gates to
    precommit:false with a plausible one-line excuse the audit never reads.
    Either edit is one file and one innocuous-looking diff, and it silently
    disables claims-check, ledger-check, vocab-check and prose-check at once.
    That is BP23 reproduced one level up, inside the mechanism written to
    prevent it, and it is why a self-audit cannot be the only audit.

    The floor therefore lives in `dev/hooks/pre-commit`, which passes it in.
    This is GP28's distinction and not an oversight: the required-gate list is
    not a RESTATEMENT to be normalised away, it is an INDEPENDENT observation
    that must agree with the registry, and its disagreement is the signal. The
    cost is that disabling a commit gate now takes two edits in two files
    instead of one. That is the feature.
    """
    findings = []

    # Computed up front: the floor's zero-denominator check below needs it.
    found = discovered(reg, root, from_index)
    known = registered_paths(reg)

    ids = {g["id"] for g in reg["gates"]}
    enabled = {g["id"] for g in reg["gates"] if g.get("precommit")}
    for want in require:
        if want not in ids:
            findings.append(
                f"REQUIRED GATE MISSING: '{want}' is required by the hook but has no "
                f"entry in the registry. Removing a gate takes an edit to BOTH.")
        elif want not in enabled:
            findings.append(
                f"REQUIRED GATE DISABLED: '{want}' is registered but precommit:false, "
                f"and the hook requires it. No `precommit_why_not` prose overrides this.")

    # THE FLOOR MUST CHECK WHAT ACTUALLY CONTROLS EXECUTION, not just that a
    # gate is listed and flagged on. A second adversarial pass defeated the
    # first version of this floor with three single-field edits to gates.json
    # alone — the exact property the floor exists to rule out:
    #   (a) staged_argv's `{staged}/claims.yaml` swapped to `{top}/claims.yaml`,
    #       so the gate read the WORKTREE and passed a staged defect masked by
    #       an unstaged repair — the F7/F13 class, reintroduced through config;
    #   (b) prose-check's `triggers` set to `[]`, so should_run() never matched
    #       and the gate silently never ran;
    #   (c) ledger-check's `require_cached` misspelled, so the ls-files probe
    #       always failed and the gate silently skipped.
    # All three audited clean. A flag that says a gate is on is not evidence
    # that it can run.
    for g in reg["gates"]:
        if not g.get("precommit") or g.get("inline"):
            continue
        gid, argv = g["id"], g.get("staged_argv") or []
        inputs = g.get("inputs") or []

        # (1) THE EXECUTABLE IS BOUND TO THE GATE. The previous version asked
        # only "does SOME token contain {staged}?", which a decoy satisfies:
        # `["true", "{staged}/claims.yaml"]` audited clean and landed a real
        # claims.yaml honesty violation through a live commit. A gate that runs
        # a different program is not that gate.
        worktree_argv = g.get("argv") or []
        if not argv or not worktree_argv or argv[0] != worktree_argv[0]:
            findings.append(
                f"GATE RUNS A DIFFERENT PROGRAM: '{gid}' has staged_argv[0]="
                f"{argv[0] if argv else '(none)'} but argv[0]="
                f"{worktree_argv[0] if worktree_argv else '(none)'}. A commit gate must "
                f"run the same checker the worktree run does.")
        elif not argv[0].startswith("{top}/"):
            findings.append(
                f"GATE IS NOT A REPO FILE: '{gid}' runs {argv[0]}, which is not a "
                f"{{top}}/-rooted path in this repository, so nothing ties it to a "
                f"checker the audit can find on disk.")

        if not inputs:
            findings.append(
                f"GATE EXAMINES NOTHING: '{gid}' is a commit gate declaring no inputs, "
                f"so no staged blob is ever materialised for it to read.")

        # (2) EVERY ARGUMENT AFTER THE EXECUTABLE MUST COME FROM THE INDEX.
        # The old per-input check was a literal substring test for
        # "{top}/" + rel, so one input of a multi-input gate could be redirected
        # to the worktree by dropping it from `inputs`, or by spelling it any
        # other way: a bare relative path resolved against cwd, an absolute
        # path, or {top}/./rel. All three read the worktree; none matched.
        for a in argv[1:]:
            if "{top}" in a:
                findings.append(
                    f"GATE READS THE WORKTREE: '{gid}' passes {a}, which resolves to the "
                    f"worktree copy rather than the staged blob.")
            elif ".." in a.split("/"):
                findings.append(
                    f"GATE ESCAPES THE STAGED TREE: '{gid}' passes {a}, which climbs out "
                    f"of the materialised index copy with '..'.")
            elif os.path.isabs(a):
                findings.append(
                    f"GATE READS AN ABSOLUTE PATH: '{gid}' passes {a}. Absolute paths are "
                    f"not staged blobs.")
            elif not a.startswith("-") and "{staged}" not in a and (root / a).exists():
                findings.append(
                    f"GATE READS THE WORKTREE: '{gid}' passes the bare path '{a}', which "
                    f"resolves against the repo root — the worktree copy, not the index.")

        # (2b) THE ARGUMENT VOCABULARY IS CLOSED. A token is either a path from
        # the index, an option this file permits, or a token the worktree
        # invocation also uses (which binds subcommands like pecia's `check`
        # and stops a gate being re-pointed at a different one). Nothing else.
        for a in argv[1:]:
            if "{staged}" in a:
                continue
            if a.startswith("-"):
                if a not in SAFE_OPTIONS and a not in worktree_argv:
                    findings.append(
                        f"UNPERMITTED OPTION: '{gid}' is given {a}, which is neither in "
                        f"the read-only option vocabulary nor used by its own worktree "
                        f"invocation. An option that changes what a checker DOES has no "
                        f"place in a commit gate.")
            elif a not in worktree_argv:
                findings.append(
                    f"UNPERMITTED ARGUMENT: '{gid}' is given the bare token '{a}', which "
                    f"its worktree invocation does not use — a commit gate must not be "
                    f"pointed at a different subcommand than the one it is audited as.")

        # (2c) THE CANONICAL SUBJECT IS PINNED, NOT DECLARED. Exact in both
        # directions: every pinned path must be read, and no other path may be.
        want = REQUIRED_INPUTS.get(gid)
        if want is not None:
            if any(a == "{staged}" for a in argv):
                got = set(inputs)          # root mode: the whole tree is handed over
                where = "inputs"
            else:
                got = {a.split("{staged}/", 1)[1] for a in argv if "{staged}/" in a}
                where = "staged_argv"
            missing, extra = want - got, got - want
            if missing:
                findings.append(
                    f"CANONICAL INPUT NOT READ: '{gid}' must read {sorted(missing)} from "
                    f"the index, and its {where} does not. Dropping a path from `inputs` "
                    f"does not remove the obligation — the obligation is not the "
                    f"registry's to state.")
            if extra:
                findings.append(
                    f"DECOY INPUT: '{gid}' reads {sorted(extra)}, which is not its "
                    f"canonical subject. A gate pointed at a file the attacker supplies "
                    f"runs, examines something, and proves nothing.")

        # (3) EVERY DECLARED INPUT MUST ACTUALLY BE HANDED TO THE GATE, either
        # named explicitly as {staged}/<input> or covered by a root-mode
        # `{staged}` token (prose-check's `--root {staged}`).
        root_mode = any(a == "{staged}" for a in argv)
        if not root_mode:
            for rel in inputs:
                if not any("{staged}/" + rel in a for a in argv):
                    findings.append(
                        f"INPUT NOT PASSED FROM THE INDEX: '{gid}' declares input '{rel}' "
                        f"but no argument names {{staged}}/{rel}, so the staged copy is "
                        f"materialised and never read.")

        trig = g.get("triggers", None)
        if isinstance(trig, list) and not trig:
            findings.append(
                f"GATE NEVER TRIGGERS: '{gid}' declares an EMPTY trigger list, so it can "
                f"never run. Use null for 'always', not [].")

        req = g.get("require_cached")
        if req and req not in inputs:
            findings.append(
                f"UNCHECKABLE PRECONDITION: '{gid}' requires '{req}' to be staged, but "
                f"that path is not among its declared inputs — a typo here silently "
                f"skips the gate forever.")

        if not g.get("redcase"):
            findings.append(
                f"UNPROVEN COMMIT GATE: '{gid}' blocks commits with no red-case arm "
                f"proving it can turn RED (VP21).")

    # INLINE COMMIT GATES (pc-fff0, v2.7). Seven enforcement blocks live
    # inside dev/hooks/pre-commit itself, outside the discovery globs — and
    # the registry's own promise ("adding or removing a gate requires editing
    # this file, enforced at commit time") was silently false for them:
    # deleting the secret-filename refusal changed neither this audit nor any
    # commit verdict. Each inline block is now REGISTERED, carrying the hook
    # path and one marker per refusal line it prints. Both directions are
    # checked against the hook AS THE COMMIT WILL CONTAIN IT (the index copy
    # under --index, matching every other input): a registered marker gone
    # from the hook is a removed gate; a `✋ pre-commit:` refusal line no
    # registered marker covers is an added, unregistered one.
    inline_gates = [g for g in reg["gates"] if g.get("inline")]
    for hook_rel in sorted({g.get("hook") for g in inline_gates if g.get("hook")}):
        if from_index:
            r = subprocess.run(["git", "show", f":{hook_rel}"], cwd=root,
                               capture_output=True, text=True)
            hook_text = r.stdout if r.returncode == 0 else None
        else:
            hook_path = root / hook_rel
            hook_text = hook_path.read_text() if hook_path.exists() else None
        where = "the index" if from_index else "the worktree"
        if hook_text is None:
            findings.append(
                f"INLINE HOOK MISSING: {hook_rel} is named by registered inline "
                f"gate(s) and is not in {where}.")
            continue
        for g in (g for g in inline_gates if g.get("hook") == hook_rel):
            if not g.get("markers"):
                findings.append(
                    f"INLINE GATE UNANCHORED: '{g['id']}' declares no markers, "
                    f"so its removal from {hook_rel} would be undetectable.")
            if not g.get("redcase"):
                findings.append(
                    f"UNPROVEN COMMIT GATE: '{g['id']}' blocks commits with no "
                    f"red-case arm proving it can turn RED (VP21).")
            gone = [m for m in g.get("markers", []) if m not in hook_text]
            if gone:
                findings.append(
                    f"INLINE GATE REMOVED: '{g['id']}' is registered as an "
                    f"inline block of {hook_rel} and its marker(s) {gone} are "
                    f"not in {where}'s copy. Removing a gate requires editing "
                    f"dev/gates.json — that is the registry's own rule, now "
                    f"enforced for inline blocks too.")
            # PRESENCE IS NOT REACHABILITY (pc-ba95, round-8 lane D-F1).
            # Markers are the refusal's WORDS, and changing a block's
            # condition to `if false` leaves every one of them in place: the
            # audit exited 0, reported the gate among those "proved able to
            # turn RED", and all three of its registered kills failed — the
            # gate being dead. The registry must therefore carry the
            # DISCRIMINATING CASE, not only the arm's name: the edit that
            # turns this gate off. `--prove` applies it and requires the arm
            # to fail; the audit checks the edit still applies here, which
            # anchors the block's GUARD rather than its message, so `if
            # false` is caught at commit time too.
            mut = g.get("redcase_mutation")
            if g.get("redcase") and not mut:
                findings.append(
                    f"UNEXECUTED RED-CASE: inline gate '{g['id']}' names a "
                    f"red-case arm and registers no redcase_mutation, so "
                    f"nothing can apply the disabling edit and show the arm "
                    f"failing. A registered arm is a name; the mutation is "
                    f"the proof (pc-ba95).")
            elif mut:
                if mut["path"] != hook_rel:
                    findings.append(
                        f"MUTATION OFF ITS GATE: inline gate '{g['id']}' "
                        f"registers a disabling edit to {mut['path']}, which "
                        f"is not the hook it lives in ({hook_rel}).")
                elif hook_text.count(mut["from"]) != 1:
                    findings.append(
                        f"INLINE GATE UNREACHABLE OR MOVED: '{g['id']}' "
                        f"registers its guard as {mut['from']!r}, which "
                        f"appears {hook_text.count(mut['from'])} time(s) in "
                        f"{where}'s copy of {hook_rel}. Its markers can all "
                        f"be present while the block never runs, so the "
                        f"guard is anchored too (pc-ba95) — restore it, or "
                        f"re-register the edit that disables this gate.")
        covered = [m for g in inline_gates if g.get("hook") == hook_rel
                   for m in g.get("markers", [])]
        for n, line in enumerate(hook_text.splitlines(), start=1):
            if "✋ pre-commit:" in line and not any(m in line for m in covered):
                findings.append(
                    f"UNREGISTERED INLINE GATE: {hook_rel}:{n} refuses with a "
                    f"message no registered inline gate's marker covers — "
                    f"adding a gate requires a dev/gates.json entry naming "
                    f"its marker.")

    for g in inline_gates:
        if not g.get("hook"):
            findings.append(
                f"INLINE GATE UNANCHORED: '{g['id']}' names no hook file, so "
                f"nothing ties it to a block the audit can find.")

    if not reg.get("discovery", {}).get("globs"):
        findings.append(
            "EMPTY DISCOVERY: the registry declares no discovery globs, so NOTHING can "
            "be found unregistered and the audit is vacuous by construction (VP4).")
    elif require and not found:
        # A non-empty glob list that MATCHES NOTHING is exactly as vacuous as
        # an empty one, and reads as configured. Caught by this check's own
        # green control, which is the argument for writing green controls.
        findings.append(
            "DISCOVERY FINDS NOTHING: the globs are declared but match no file in this "
            "tree, so the unregistered-gate check has an empty denominator and cannot "
            "fire — while the registry requires " + str(len(require)) + " gate(s) to exist.")

    for path in found:
        if path not in known:
            findings.append(
                f"UNREGISTERED GATE: {path} matches a discovery glob but no entry in "
                f"dev/gates.json runs it. Add it — a gate nothing runs is not a gate."
            )

    # When auditing the INDEX, existence means "in the index" — a gate file
    # present in the worktree but not staged is not in the commit, and an
    # entry naming it would be dangling for anyone who clones the result.
    if from_index:
        cached = set(git_lines(["ls-files", "--cached"], root))
        exists = lambda p: p in cached
    else:
        exists = lambda p: (root / p).exists()

    ships_false = {g["id"] for g in reg["gates"] if g.get("ships") is False}
    for path, gid in sorted(known.items()):
        if not exists(path):
            # D4 (pc-8105): a `ships: false` entry names a file deliberately
            # withheld from publication, so in a public clone its absence is
            # the declared state, not a broken registry. Reported as
            # WITHHELD — visible, never a finding — because 'missing' would
            # teach a public reader to delete the entry, which is exactly
            # the shortening a fail-open list invites.
            if gid in ships_false:
                print(f"gate '{gid}': {path} absent — WITHHELD (ships: false), "
                      f"a private gate withheld from publication, not missing")
                continue
            findings.append(
                f"DANGLING ENTRY: gate '{gid}' names {path}, which does not exist. "
                f"Remove the entry or restore the file."
            )

    ids = [g["id"] for g in reg["gates"]]
    for dup in {i for i in ids if ids.count(i) > 1}:
        findings.append(f"DUPLICATE ID: '{dup}' appears more than once.")

    for g in reg["gates"]:
        if g.get("inline"):
            # An inline gate is executed by the hook's own bash block, not by
            # this runner — its runnability question is the marker check above.
            continue
        if g.get("precommit") and not g.get("staged_argv"):
            findings.append(
                f"UNRUNNABLE COMMIT GATE: '{g['id']}' is precommit:true but declares no "
                f"staged_argv, so the hook has nothing to run against the index."
            )
        if not g.get("precommit") and not g.get("precommit_why_not"):
            findings.append(
                f"UNEXPLAINED EXCLUSION: '{g['id']}' is precommit:false with no "
                f"precommit_why_not. Say why, or wire it in."
            )

    # A `redcase` naming a test that does not exist is the exact fault BP23
    # describes — the registry claiming a proof it does not have — so an
    # unresolvable arm IS a finding, while an honestly-null one is not.
    for g in reg["gates"]:
        rc = g.get("redcase")
        if not rc:
            continue
        if not resolves(rc, root):
            # Same rule as the dangling-entry case: a withheld gate's red-case
            # arm is withheld with it (D4), and its absence from a public
            # clone is declared, not phantom.
            if g.get("ships") is False:
                print(f"gate '{g['id']}': red-case arm {rc} absent — WITHHELD "
                      f"(ships: false), withheld with the gate it proves")
                continue
            findings.append(
                f"PHANTOM RED-CASE: gate '{g['id']}' claims its red-case arm is "
                f"{rc}, which does not exist. A claimed proof that is not there is "
                f"worse than a declared gap — set redcase to null or write the test."
            )

    # THE TWO REGISTERS ARE JOINED, AND THE JOIN IS CHECKED (pc-fc37, round-7
    # lane D-F2). README says the state of every project claim lives in
    # claims.yaml "and nowhere else"; the editor-debris commit gate had its
    # state, its red case and its passing tests only HERE and in
    # tests/test_pecia.py, and no checker compared the two registries, so both
    # audited clean over the split for as long as it existed. Six of the seven
    # inline hook gates carried a dedicated claim entry, which is what settles
    # the scope question the finding poses: inline gates are NOT out of scope,
    # so every gate now names the claim it enforces, or declares in
    # `claim_why_not` why it enforces none — and the named claim must be in the
    # ledger. The reverse direction (a claim naming a gate that is gone) is
    # already covered: a deleted gate file is a DANGLING ENTRY and a deleted
    # entry is an UNREGISTERED GATE, both above.
    # The ledger is read only when a gate names something to resolve in it.
    # Not fail-open: a gate with neither `claim` nor `claim_why_not` is
    # refused below whatever the ledger says, so a registry cannot dodge the
    # join by deleting claims.yaml — it would first have to declare its way
    # out, gate by gate, in the file this audit reads and the diff shows.
    names_a_claim = any(g.get("claim") for g in reg["gates"])
    if not names_a_claim:
        claims_text, claims_where = "", "(not consulted)"
    elif claims is not None:
        claims_text = claims.read_text() if claims.exists() else None
        claims_where = str(claims)
    else:
        claims_text, claims_where = None, "the worktree"
        if from_index:
            r = subprocess.run(["git", "show", ":claims.yaml"], cwd=root,
                               capture_output=True, text=True)
            if r.returncode == 0:
                claims_text, claims_where = r.stdout, "the index"
        if claims_text is None:
            # THE SAME WORKTREE FALLBACK THE INPUT MATERIALIZER USES, and for
            # the same reason: a repository that never adopted claims.yaml —
            # or deliberately de-adopted it with --no-verify, which the
            # canonical-deletion gate makes the only way — must still be able
            # to commit. Demanding the ledger here would also duplicate a
            # guard that already exists (that gate, plus doctor's D007/D011),
            # and the first thing it did was redden that gate's own control.
            worktree_claims = root / "claims.yaml"
            claims_text = (worktree_claims.read_text()
                           if worktree_claims.exists() else None)
            if claims_text is not None and from_index:
                claims_where = "the worktree (not in the index)"
    if claims_text is None:
        findings.append(
            f"CLAIMS LEDGER MISSING: claims.yaml is in neither the index nor "
            f"the worktree, and {sum(1 for g in reg['gates'] if g.get('claim'))} "
            f"gate(s) name a claim in it. The registers are joined; a name "
            f"that resolves nowhere is not a registration.")
    else:
        # Read as raw lines, not YAML: this file has no yaml dependency and
        # must not grow one to run inside a hook. WHICH raw lines is the
        # bounded part (pc-3472, round-9 lane D-F1): the ids of the
        # `claims:` SEQUENCE, not every `^- id:` in the file, because a
        # `decoys:` block carrying the name satisfied a file-wide regex
        # while claims-check — which parses `ledger["claims"]` — never saw
        # it. dev/claims_ids.py is that scan, shared with the resolver that
        # had the same shape.
        ledger_ids, why_not = claim_ids(claims_text)
        if names_a_claim and why_not is not None:
            findings.append(
                f"CLAIMS LEDGER UNREADABLE: {why_not} ({claims_where}), so "
                f"the cross-register check would pass vacuously — it "
                f"refuses instead.")
        elif names_a_claim and not ledger_ids:
            findings.append(
                "CLAIMS LEDGER UNREADABLE: the `claims:` sequence carries no "
                "`- id:` entries, so the cross-register check would pass "
                "vacuously — it refuses instead.")
        for g in reg["gates"]:
            cid = g.get("claim")
            if cid:
                if cid not in ledger_ids:
                    findings.append(
                        f"CLAIM NOT IN THE LEDGER: gate '{g['id']}' names claim "
                        f"'{cid}', which claims.yaml does not carry. The gate "
                        f"enforces something the register does not state.")
            elif not g.get("claim_why_not"):
                findings.append(
                    f"GATE OUTSIDE THE REGISTER: gate '{g['id']}' names no "
                    f"claim and declares no reason. A tested gate whose state "
                    f"lives only here is a claim kept outside the register "
                    f"README says holds every claim and nowhere else "
                    f"(pc-fc37) — add a claims.yaml entry, or state "
                    f"claim_why_not.")

    # VP21: report the red-case denominator rather than rounding it up. This is
    # NOT a finding — an unproven gate is a known gap, not a broken registry —
    # but it is printed on every audit so the number cannot quietly rot.
    #
    # THE WORDING SAYS WHAT THIS ROUTE DID (pc-ba95). It used to read "gates
    # proved able to turn RED", over gates whose arms this audit never ran —
    # so a gate disabled as unreachable code was counted among the proved
    # ones while all three of its kills failed. Registration is what the
    # audit checks; execution is `--prove`, and the two are now named apart.
    registered = [g["id"] for g in reg["gates"] if g.get("redcase")]
    executable = [g["id"] for g in reg["gates"]
                  if g.get("redcase") and g.get("redcase_mutation")]
    total = len(reg["gates"])
    print(f"gates: {total} registered, {sum(1 for g in reg['gates'] if g['precommit'])} run at commit")
    print(f"red-case arms REGISTERED: {len(registered)}/{total} "
          f"({', '.join(sorted(registered)) or 'none'}) — registration, not "
          f"execution; `dev/gates.py --prove` runs them against their "
          f"registered disabling edit")
    print(f"red-case arms EXECUTABLE by --prove: {len(executable)}/{total} "
          f"({', '.join(sorted(executable)) or 'none'})")
    unproved = [g["id"] for g in reg["gates"] if not g.get("redcase")]
    if unproved:
        print(f"  NO ARM REGISTERED (not a failure; a gap): {', '.join(sorted(unproved))}")
    unexecutable = sorted(set(registered) - set(executable))
    if unexecutable:
        print(f"  ARM REGISTERED BUT NOT EXECUTABLE (a gap, not a failure — "
              f"no disabling edit registered): {', '.join(unexecutable)}")

    if findings:
        print("", file=sys.stderr)
        for f in findings:
            print(f"✋ gate registry: {f}", file=sys.stderr)
        return 1
    print("registry audit: ok")
    return 0


# ── running ───────────────────────────────────────────────────────────────

def materialise(gate: dict, root: Path, staged_root: Path) -> Path | None:
    """Extract this gate's inputs from the INDEX into a private dir.

    Repo layout is PRESERVED, not flattened. vocab-check's generated registry
    records each allocation as `<basename>:<line>`, so extracting the staged
    blobs under flattened names made the regenerated bytes differ from the
    committed ones and the gate refused its own introducing commit.
    """
    d = staged_root / gate["id"]
    d.mkdir(parents=True, exist_ok=True)
    for rel in gate.get("inputs", []):
        dest = d / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "show", f":{rel}"], cwd=root,
                           capture_output=True)
        if r.returncode == 0:
            dest.write_bytes(r.stdout)
            continue
        # ABSENT FROM THE INDEX MEANS ABSENT FROM THE COMMIT — but there are
        # two ways to be absent and they are not the same event.
        #
        #   in HEAD, not in the index  → THIS COMMIT DELETES IT. Refuse: the
        #       gate's subject is being removed, the worktree copy is not what
        #       lands, and a deletion of a gate input is exactly the change a
        #       human should look at.
        #   in neither                 → the repo simply has no such file. The
        #       gate has no subject and never did; skip it, the way
        #       require_cached already skips. Refusing here would block every
        #       commit in a repo that has no claims.yaml.
        #
        # Collapsing the two is what made the first fix wrong: it refused the
        # second case and broke a legitimate control.
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel}"],
                          cwd=root, capture_output=True).returncode == 0:
            return "deleting"
        # WHY THE OLD FALLBACK WAS WRONG: it copied the worktree file here, on
        # the reasoning that a partial commit should still be checked against
        # what lands. That mistook which files reach this branch. `git show
        # :path` reads the INDEX, and the index holds every tracked file
        # whether or not it was just staged — so a tracked file never arrives
        # here at all. The only way to arrive is to be genuinely absent from
        # the index, which means the commit will not contain it, and
        # validating the worktree copy validates a file about to stop
        # existing. `git rm --cached PLAN.md` landed a commit in which
        # README's referral to PLAN.md dangled while prose-check reported ok —
        # its own `target.exists()` had resolved against the substitute. Same
        # for spec/format-v1.md and vocab-check. No registry edit was
        # involved: the F7/F13 staged-versus-worktree class arriving through
        # the data plane instead of the config.
        return None
    return d


def should_run(gate: dict, root: Path, staged_files: list[str]) -> bool:
    req = gate.get("require_cached")
    if req:
        r = subprocess.run(["git", "ls-files", "--cached", "--error-unmatch", req],
                           cwd=root, capture_output=True)
        if r.returncode != 0:
            return False
    triggers = gate.get("triggers")
    if triggers is None:
        return True
    return any(f in triggers for f in staged_files)


def run_one(gate: dict, root: Path, staged: Path | None, mode: str) -> int:
    argv = gate.get("staged_argv") if mode == "precommit" else gate.get("argv")
    if not argv:
        return 0
    cmd = expand(argv, root, staged)
    # A commit gate that hangs holds `git commit` open indefinitely, and the
    # pressure that creates is exactly the "teach everyone to --no-verify"
    # failure this registry argues against elsewhere. Bounded at commit time
    # only; a worktree run may take as long as it takes (the suite is 200s).
    timeout = gate.get("timeout_s", 120) if mode == "precommit" else None
    try:
        r = subprocess.run(cmd, cwd=root, timeout=timeout,
                           stdout=subprocess.DEVNULL if mode == "precommit" else None)
    except subprocess.TimeoutExpired:
        print(f"✋ pre-commit: {gate['id']} exceeded {timeout}s and was killed. "
              f"A gate that hangs is a failed gate.", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"✋ pre-commit: {gate['id']} could not be executed ({e}). "
              f"Is it executable, and is `uv` on PATH?", file=sys.stderr)
        return 1
    return r.returncode


def precommit(reg: dict, root: Path, staged_root: Path) -> int:
    staged_files = git_lines(["diff", "--cached", "--name-only"], root)
    fail = 0
    for gate in reg["gates"]:
        if not gate.get("precommit") or gate.get("inline"):
            # inline gates run inside the hook's own bash blocks; the audit
            # (not this runner) is what binds them to the registry (pc-fff0).
            continue
        if not should_run(gate, root, staged_files):
            continue
        d = materialise(gate, root, staged_root)
        if d is None:
            # No subject in this repo at all — nothing to check, nothing to say.
            continue
        if d == "deleting":
            print(f"✋ pre-commit: {gate['id']} cannot run — one of its inputs is "
                  f"not in the index, so it is not in this commit. A gate that "
                  f"cannot see the committed state does not get to pass.\n"
                  f"   If you meant to remove it, remove it from the worktree too; "
                  f"if you meant to keep it, re-stage it. Bypass: git commit --no-verify",
                  file=sys.stderr)
            fail = 1
            continue
        rc = run_one(gate, root, d, "precommit")
        if rc != 0:
            print(f"✋ pre-commit: {gate['id']} failed on the STAGED tree.", file=sys.stderr)
            for line in gate.get("on_fail", []):
                print(f"   {line}", file=sys.stderr)
            print("   Bypass: git commit --no-verify", file=sys.stderr)
            fail = 1
    return fail


def run_worktree(reg: dict, root: Path, only: str | None) -> int:
    worst = 0
    for gate in reg["gates"]:
        if only and gate["id"] != only:
            continue
        if not gate.get("argv"):
            continue
        print(f"\n── {gate['id']} — {gate['why']}", flush=True)
        rc = run_one(gate, root, None, "worktree")
        print(f"── {gate['id']}: {'ok' if rc == 0 else f'EXIT {rc}'}", flush=True)
        worst = max(worst, rc)
    if only is None:
        # THE RED CASES ARE RUN, NOT ASSERTED (pc-ba95). A whole-worktree run
        # is where the registry's own claims get tested, and "proved able to
        # turn RED" was the one claim nothing executed. It is a route of its
        # own (`--prove`) so it can be run alone; it is called here so it is
        # not a route nobody runs.
        print("\n── red-case proofs — each gate's registered disabling edit, "
              "applied", flush=True)
        worst = max(worst, prove(reg, root, None))
    return worst


# ── proving ───────────────────────────────────────────────────────────────
#
# pc-ba95 (round-8 lane D-F1): the audit CERTIFIED a gate that had been
# disabled as unreachable code — its condition changed to `if false`, every
# registered marker still present — and went on counting it among the gates
# "proved able to turn RED" while all three of the kills its entry names
# failed. Presence was checked; reachability was not; and the red-case arm was
# asserted without being run. This route runs it: the registered disabling
# edit is applied to a private copy of the tree and the named arm must FAIL
# there, against the control of the same arm passing on the tree as it stands.
# A registered arm is a name. This is the proof.

#: Tracked paths the prover does not need and will not copy. Agent logs and
#: the research corpus are megabytes of transcript, and the vendored solver is
#: a jar; none is read by a gate's red-case arm.
PROVE_SKIP = ("dev/agent/logs/", "research/", "spec/.alloy/")


def prove_tree(root: Path, dest: Path, mutation: dict) -> None:
    """Copy the tracked tree into `dest` and apply one registered edit."""
    for rel in git_lines(["ls-files"], root):
        if rel.startswith(PROVE_SKIP):
            continue
        src, out = root / rel, dest / rel
        if not src.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
    target = dest / mutation["path"]
    text = target.read_text()
    if text.count(mutation["from"]) != 1:
        raise ValueError(
            f"the registered edit's `from` appears {text.count(mutation['from'])} "
            f"time(s) in {mutation['path']} — a mutation that does not apply "
            f"exactly once proves nothing")
    target.write_text(text.replace(mutation["from"], mutation["to"], 1))
    shutil.copystat(root / mutation["path"], target)


def run_arm(arm: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "unittest", arm], cwd=str(cwd),
                          capture_output=True, text=True)


def prove(reg: dict, root: Path, only: str | None) -> int:
    """Execute every registered red-case arm against its disabling edit."""
    import tempfile

    findings: list[str] = []
    ran = 0
    for gate in reg["gates"]:
        if only and gate["id"] != only:
            continue
        arm, mutation = gate.get("redcase"), gate.get("redcase_mutation")
        if not arm or not mutation:
            continue
        ran += 1
        print(f"\n── {gate['id']} — {mutation['why']}", flush=True)
        green = run_arm(arm, root)
        if green.returncode != 0:
            findings.append(
                f"ARM RED ON THE LIVE TREE: '{gate['id']}' names {arm}, which "
                f"fails with nothing mutated — the control for this proof does "
                f"not hold, so its kill would mean nothing.\n"
                f"{green.stderr.strip()[-1200:]}")
            print(f"── {gate['id']}: CONTROL FAILED", flush=True)
            continue
        with tempfile.TemporaryDirectory(prefix=f"prove-{gate['id']}.") as td:
            dest = Path(td) / "tree"
            dest.mkdir()
            try:
                prove_tree(root, dest, mutation)
            except (OSError, ValueError) as exc:
                findings.append(f"MUTATION DID NOT APPLY: '{gate['id']}': {exc}")
                print(f"── {gate['id']}: MUTATION FAILED", flush=True)
                continue
            red = run_arm(arm, dest)
        if red.returncode == 0:
            findings.append(
                f"ARM GREEN UNDER ITS OWN DISABLING EDIT: '{gate['id']}' "
                f"registers {arm} as the proof that it can turn RED, and that "
                f"arm PASSES with the gate disabled by the registered edit "
                f"({mutation['path']}: {mutation['from']!r} → "
                f"{mutation['to']!r}). The gate is not known to be a gate.")
            print(f"── {gate['id']}: NOT PROVED", flush=True)
        else:
            print(f"── {gate['id']}: proved — the arm fails with the gate "
                  f"disabled and passes without the edit", flush=True)
    if not ran:
        print("✋ gate registry: no gate carries both a red-case arm and a "
              "registered disabling edit, so this route would report success "
              "over an empty denominator (VP4).", file=sys.stderr)
        return 1
    if findings:
        print("", file=sys.stderr)
        for f in findings:
            print(f"✋ gate registry: {f}", file=sys.stderr)
        return 1
    print(f"\nred-case arms EXECUTED: {ran} proved able to turn RED")
    return 0


def listing(reg: dict) -> int:
    for g in reg["gates"]:
        mark = "commit" if g.get("precommit") else "      "
        red = "red-case ✔" if g.get("redcase") else "red-case —"
        print(f"[{mark}] {g['id']:<14} {red}  {g['why']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--canonical-paths", action="store_true",
                    help="print every path REQUIRED_INPUTS pins, one per line. The "
                         "hook's staged-deletion guard derives its set from this "
                         "rather than restating it — GP28: one owner, referenced.")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--require", default="",
                    type=lambda s: [x for x in s.replace(",", " ").split() if x],
                    help="gate ids that MUST be registered and precommit:true; "
                         "the floor, passed in by the hook rather than read from "
                         "the registry being audited")
    ap.add_argument("--index", action="store_true",
                    help="audit against the INDEX (what this commit will contain)")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--prove", action="store_true",
                    help="apply each gate's registered disabling edit in a "
                         "private copy of the tree and require its red-case "
                         "arm to FAIL there — execution, where --audit only "
                         "checks registration (pc-ba95)")
    ap.add_argument("--id", default=None)
    ap.add_argument("--precommit", action="store_true")
    ap.add_argument("--staged-root", default=None)
    ap.add_argument("--registry", default=None,
                    help="read the registry from this path instead of dev/gates.json")
    ap.add_argument("--claims", default=None,
                    help="read the claims ledger from this path instead of "
                         "claims.yaml (the audit joins the two registers: "
                         "every gate names the claim it enforces). Same reason "
                         "--registry exists — a hook checking a commit must "
                         "read the register the commit will contain.")
    args = ap.parse_args()

    root = top()
    try:
        text = Path(args.registry).read_text() if args.registry else None
        reg = load(text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"✋ gate registry: cannot read dev/gates.json — {e}", file=sys.stderr)
        return 2

    # The shape is checked before any route reads a field (pc-1a93 sibling).
    # Cannot-run, not a gate failure: the registry is the thing that says what
    # the gates ARE, so a malformed one leaves nothing to report on — and the
    # hook's `if ! gates.py --audit` still refuses the commit.
    malformed = shape_findings(reg)
    if malformed:
        print("✋ gate registry: malformed entries — the registry is checked "
              "for SHAPE, not merely for presence (pc-1a93):", file=sys.stderr)
        for f in malformed:
            print(f"   {f}", file=sys.stderr)
        return 2

    if args.canonical_paths:
        for rel in sorted({r for paths in REQUIRED_INPUTS.values() for r in paths}):
            print(rel)
        return 0
    if args.list:
        return listing(reg)
    if args.audit:
        return audit(reg, root, args.index, args.require,
                     Path(args.claims) if args.claims else None)
    if args.precommit:
        if not args.staged_root:
            print("--precommit requires --staged-root", file=sys.stderr)
            return 2
        return precommit(reg, root, Path(args.staged_root))
    if args.prove:
        return prove(reg, root, args.id)
    if args.run:
        return run_worktree(reg, root, args.id)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
