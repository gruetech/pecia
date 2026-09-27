#!/usr/bin/env bash
# Formal gate for pecia: bounded model-check of the ledger algebra.
#
# THREE files, three EXPECTATIONS, all enforced:
#   spec/peciaV2.als               — theorems: every check must PASS
#     (filename = module name, so the trap/seed files can `open` it — pc-0c76)
#   spec/pecia-v2-traps.als        — real traps: every check must FAIL
#                                    (a trap that stops failing = model drift)
#   spec/pecia-v2-seeded-kills.als — null arm: every check must FAIL
#                                    (a seed that passes = vacuous model)
#
# The v1 files ran alongside v2 from pc-3bbe until pc-0033, because a model
# drifted from its implementation is one of VP4(a)'s three ways a formal gate
# goes vacuously green — and until pc-0033 the Python still implemented v1.
# They were deleted in the commit that made their algebra unreachable, which
# is the same rule read the other way round. See the note above expect_pass.
#
# Tier: machine-checked(scope) — exhaustive up to the scopes in the .als
# files, silent beyond them. Exit 0 means the model checked, never that
# the Python implementation is correct; binding is via fixture tests.
#
# Harness vendored from ~/repos/axiom/spec (D78-pinned Alloy 6.2.0).
#
# Exit codes: 0 all expectations met / 1 an expectation violated / 2 setup.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="$REPO/spec"

# --static: run ONLY the structural companion checks (no JDK, no jar, no
# model-checking) and exit. Exists so the pc-5fba regression class — a
# companion quietly re-growing a private copy of the judged vocabulary —
# is testable from the ordinary suite, which must not require Java.
STATIC_ONLY=0
[ "${1:-}" = "--static" ] && STATIC_ONLY=1

# Declarations as "kind name" pairs — comments stripped first, and the
# match spanning newlines (pc-cb505, round-6 lane C-F1): the previous
# single-line extraction required keyword and identifier on one physical
# line, while Alloy permits whitespace between them — so `pred` on one
# line and `privateCleanLines[...]` on the next evaded the closed set AND
# the full gate (the pinned solver parses the split form; the scorer ran
# it and the counts stayed green). Every structural refusal below reads
# from this one extraction so no sibling check keeps the single-line
# hole. Comment stripping is what the anchor used to approximate: without
# it, a `//`, `--` or block comment mentioning `pred foo` would be
# refused as a declaration — the false-accusation shape, inverted.
#
# THE LEXICAL QUESTION IS ASKED ONCE (pc-6196, round-10 lane C-F1). The
# stripping here was two regexes that know nothing about string literals, so
# a `--` or `*/` inside one truncated the file and a `pred foo` inside one
# was extracted as a declaration. dev/alloy_lex.py blanks comment text and
# string CONTENTS in one pass, preserving length and line breaks, and every
# check in this file now reads through it — including the brace counter that
# was the finding.
decl_pairs() {
  PYTHONPATH="$REPO/dev" python3 - "$1" <<'PY'
import re, sys
from alloy_lex import blank_noncode
text = open(sys.argv[1], encoding="utf-8", errors="surrogateescape").read()
text = blank_noncode(text)
for m in re.finditer(r'\b(sig|pred|fun|enum)\s+([A-Za-z_][A-Za-z0-9_]*)', text):
    print(m.group(1), m.group(2))
PY
}

# Does `pred <name>` in <file> CALL <callee>? (v2.17, pc-dad7.) Read through
# the same lexer everything else here reads through, and out of that
# predicate's OWN brace-balanced body — the pc-d405 rule, because a callee
# named anywhere else in the file (a comment, a neighbouring assert) is not
# the predicate composing it. Exit 0 = calls it.
pred_calls() {
  PYTHONPATH="$REPO/dev" python3 - "$1" "$2" "$3" <<'PY'
import re, sys
from alloy_lex import blank_noncode
text = blank_noncode(open(sys.argv[1], encoding="utf-8",
                          errors="surrogateescape").read())
name, callee = sys.argv[2], sys.argv[3]
head = re.search(rf'\bpred\s+{re.escape(name)}\s*(?:\[[^\]]*\])?\s*\{{', text)
if head is None:
    sys.exit(2)          # cannot read the subject: refuse, never assume
depth, start = 1, head.end()
body = None
for i in range(start, len(text)):
    if text[i] == '{':
        depth += 1
    elif text[i] == '}':
        depth -= 1
        if depth == 0:
            body = text[start:i]
            break
if body is None:
    sys.exit(2)
sys.exit(0 if re.search(rf'\b{re.escape(callee)}\s*\[', body) else 1)
PY
}

static_checks() {
  local rc=0
  # --static must fail CLOSED without its extractor: an empty declaration
  # list refuses nothing, which is the pc-219e hole with extra steps.
  if ! command -v python3 >/dev/null; then
    echo "✗ EXPECTATION VIOLATED: python3 is required for the structural companion checks — without the extractor the closed declaration set has an empty denominator (VP4)." >&2
    return 1
  fi
  # AND THE LEXER IS PART OF THE EXTRACTOR (pc-6196, same rule one file
  # over). Every check below reads the .als files through dev/alloy_lex.py,
  # so a tree carrying the gate without it is a gate that cannot answer —
  # and it used to say so as a Python traceback with the checks silently
  # unrun. It refuses by name instead. Found by re-running the round-10
  # reproduction, whose case tree copies the gate and the spec files alone.
  if ! PYTHONPATH="$REPO/dev" python3 -c 'import alloy_lex' >/dev/null 2>&1; then
    echo "✗ EXPECTATION VIOLATED: dev/alloy_lex.py is not importable from $REPO/dev — every structural check reads the .als files through it (pc-6196), and a check that cannot read its subject refuses rather than running blind (VP4)." >&2
    return 1
  fi
  # THE COMPANIONS IMPORT THE SUBJECT (pc-0c76), and may not re-declare it.
  # The original check refused only `enum Field` and `fun diff[` — so the
  # traps file kept a PRIVATE, WEAKER cleanLines (E003/E004 ignoring
  # retires; E005/E008/E012/E016/E017 absent) and trap 3 was judged against
  # a definition later invariant changes silently outgrew (pc-5fba, v2.7).
  # The refusal now covers the whole judged vocabulary: any declaration
  # whose name stems from the clean/head/ids/depGraph family. diffPartial
  # is Seed 3's own mutation and must live ONLY in the seeds file.
  #
  # THE DECLARATION SET IS ALSO CLOSED (v2.8, pc-219e). The family refusal
  # above is a name blacklist, and a blacklist is one rename away from
  # silence: a companion regrowing the judged vocabulary under fresh
  # identifiers (privateMaxima/privateNames/privateArcs/privateValid)
  # passed --static while the traps were judged against a private,
  # driftable copy — exactly the pc-5fba class, avoided by renaming. Each
  # companion therefore enumerates EXACTLY its deliberate weakenings here,
  # and any other sig/pred/fun/enum declaration is refused. Growing a
  # companion is a deliberate edit of these lists, the same rule the
  # expected counts follow (pc-855f). The blacklist stays as the sharper
  # diagnostic for the reserved-name case.
  #
  # AND EACH ENTRY SAYS WHAT IT WEAKENS (v2.17, pc-dad7). The closed set
  # above stops a companion GROWING a private copy under a fresh name; it
  # does not stop one of the enumerated weakenings from being a private
  # copy in the first place. `rechainedBrandFromLanded` was exactly that:
  # it rebuilt the field transfer locally and replaced the brand clause, so
  # every clause it asserted against was the trap file's own, and deleting
  # `out.forced = mine.forced` from peciaV2.rechained left the gate at
  # 16/16, 0/4, 0/3, exit 0 — a trap biting for its own reasons over a
  # module that no longer carried the rule it exists to pin.
  #
  # THE RULE, and it is narrower than "no private predicates" on purpose:
  # a weakening that differs from a shipped construction by ONE CLAUSE must
  # COMPOSE that construction's shared part and replace only the clause.
  # Some constructions legitimately compose nothing — `rechainedBlind` IS
  # the null construction, `reviseOn` and `State` pose a scenario the
  # theorem module does not model — and those say so with an empty second
  # field rather than being defaulted into silence. A blanket ban would
  # have deleted the traps that work.
  #
  # Each entry is `name:composed` (`composed` empty = nothing to compose,
  # stated deliberately). Where `composed` is named, the declaration's own
  # body must call it — checked below, so this registry executes rather
  # than describes (BP23, the same reason dev/gates.json does).
  local allowed_traps="State: rechainedBy: rechainedBlind: reviseOn: rechainedBrandFromLanded:rechainedFields"
  local allowed_seeds="publishNoChainCas: lastInChainIsMaxRev: publishNoRevCas: diffPartial: rechainedByPartial:"
  local allowed name pairs entry composed
  for companion in pecia-v2-traps.als pecia-v2-seeded-kills.als; do
    if ! rg -q '^open peciaV2$' "$SPEC/$companion"; then
      echo "✗ EXPECTATION VIOLATED: spec/$companion does not open peciaV2 — a companion that stops importing the subject is judging a private copy (pc-0c76)." >&2
      rc=1
    fi
    pairs="$(decl_pairs "$SPEC/$companion")"
    if printf '%s\n' "$pairs" | rg -q '^(enum Field|fun diff)$'; then
      echo "✗ EXPECTATION VIOLATED: spec/$companion re-declares Field or diff — the judged vocabulary must be the shipped one, imported (pc-0c76)." >&2
      rc=1
    fi
    if printf '%s\n' "$pairs" | rg -q '^(pred|fun) (e[0-9]+clean|clean|heads|head|ids|depGraph)\w*$'; then
      echo "✗ EXPECTATION VIOLATED: spec/$companion re-declares part of the clean/head/graph vocabulary — the traps are judged against the SHIPPED cleanL family, never a private copy (pc-5fba)." >&2
      rc=1
    fi
    case "$companion" in
      pecia-v2-traps.als) allowed="$allowed_traps" ;;
      *)                  allowed="$allowed_seeds" ;;
    esac
    while IFS= read -r name; do
      [ -z "$name" ] && continue
      entry=""
      for candidate in $allowed; do
        case "$candidate" in "$name":*) entry="$candidate" ;; esac
      done
      if [ -z "$entry" ]; then
        echo "✗ EXPECTATION VIOLATED: spec/$companion declares '$name', which is not in the companion's enumerated declaration set — a companion declares EXACTLY its deliberate weakenings, because any new declaration is one rename away from a private copy of the judged vocabulary (pc-219e, widening pc-5fba). Growing a companion is a deliberate edit of dev/alloy-gate.sh." >&2
        rc=1
      else
        composed="${entry#*:}"
        if [ -n "$composed" ]; then
          pred_calls "$SPEC/$companion" "$name" "$composed"
          case "$?" in
            0) ;;
            1) echo "✗ EXPECTATION VIOLATED: spec/$companion's '$name' is registered as weakening peciaV2.$composed but its body does not CALL it — a weakening that rebuilds the shipped construction instead of composing it asserts against its own clauses, so deleting the rule it exists to pin changes nothing and the gate stays green (pc-dad7). Compose peciaV2.$composed and replace only the clause you weaken." >&2
               rc=1 ;;
            *) echo "✗ EXPECTATION VIOLATED: could not read 'pred $name' out of spec/$companion — the composition check reads that predicate's own body, and a check that cannot find its subject refuses rather than passing (pc-d405, VP4)." >&2
               rc=1 ;;
          esac
        fi
      fi
    done < <(printf '%s\n' "$pairs" | cut -d' ' -f2)
  done
  if decl_pairs "$SPEC/peciaV2.als" | rg -q '^fun diffPartial$'; then
    echo "✗ EXPECTATION VIOLATED: spec/peciaV2.als declares diffPartial — the seeded mutation may only exist in the null arm (pc-0c76)." >&2
    rc=1
  fi
  # THE FIELD TRANSFER IS PINNED TO THE SHIPPED ENUM (pc-219e, lane C's
  # growth-surface observation): rechainedBy repeats one clause per Field
  # member rather than deriving them, so a member the enum gains and the
  # traps file never mentions would leave that field silently unconstrained
  # in every trap. A new member reddens this until the traps grow with it,
  # deliberately.
  #
  # IT CHECKS TRANSFER, NOT OCCURRENCE (pc-7d3f, round-7 lane C-F1). This
  # was `rg -q "\bFX\b"` over the RAW traps file, so a new conflict unit
  # correctly refused for being transferred nowhere passed the moment a
  # COMMENT mentioning it was appended — and the pinned-jar run over that
  # same fixture was 16/16, 0/4, 0/3 at exit 0, so no layer of the gate
  # refused. The same shape decl_pairs already fixed one check over
  # (pc-cb505), one check short of the whole file. Now the traps are
  # comment-stripped and each member must carry its own rechainedBy clause,
  # transferring its own distinct Line field — the property the enum's
  # growth actually endangers. The enum is read from the comment-stripped
  # MODEL for the same reason: a commented-out `enum Field { ... }` would
  # otherwise have supplied the denominator.
  #
  # IT CHECKS CORRESPONDENCE, NOT DISTINCTNESS (pc-d0dd, round-8 lane C-F1).
  # pc-7d3f moved the check from occurrence to transfer and stopped one step
  # short: the test was a BIJECTION between enum members and Line fields and
  # never asked whether FStatus transfers `status`. A traps file with the
  # FStatus and FBlocks transfers SWAPPED keeps the bijection intact, keeps
  # the correspondence wrong, and passed — the pinned jar returning the same
  # counts at exit 0 over the swapped fixture as over the shipped companion,
  # so no layer of the gate refused traps that transfer the wrong fields.
  # The mapping is not the traps file's to declare: `fun diff` in the MODEL
  # says which Line comparison derives each member, and each rechainedBy
  # clause must transfer exactly that field. Distinctness is kept beside it
  # rather than folded in — it is what catches a `diff` whose own mapping
  # collapses two members onto one field, which correspondence alone would
  # then certify.
  if ! PYTHONPATH="$REPO/dev" python3 - "$SPEC/peciaV2.als" "$SPEC/pecia-v2-traps.als" <<'PY'
import re, sys
from alloy_lex import blank_noncode

def code(path):
    """The module as the checks below may read it: comments and string
    contents blanked, offsets intact (pc-6196). Every `{`, `}`, `]` and
    keyword that survives is one the Alloy parser would see too."""
    text = open(path, encoding="utf-8", errors="surrogateescape").read()
    return blank_noncode(text)

model, traps = code(sys.argv[1]), code(sys.argv[2])
bad = []

enum = re.search(r'\benum\s+Field\s*\{([^}]*)\}', model)
members = [m for m in re.split(r'[,\s]+', enum.group(1) if enum else "") if m]
if not members:
    bad.append("could not read the Field enum from spec/peciaV2.als — the "
               "coverage check has an empty denominator (VP4).")

# THE CORRESPONDENCE COMES FROM `fun diff`, in the model (pc-d0dd): one
# clause per member, `(a.<line field> != b.<line field> implies FX else
# none)`, so the model itself says which Line field each conflict unit is
# derived from. The parameter names are read from the signature rather than
# assumed, since they are the model's to choose.
produces = {}
sig = re.search(r'\bfun\s+diff\s*\[\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*'
                r':\s*Line\s*\]\s*:\s*set\s+Field\s*\{([^}]*)\}', model)
if sig is None:
    bad.append("could not read `fun diff` from spec/peciaV2.als — without the "
               "model's own member-to-field mapping the transfer check can "
               "only test distinctness, which a swapped transfer satisfies "
               "(pc-d0dd, VP4).")
else:
    a, b, body = sig.group(1), sig.group(2), sig.group(3)
    derives = re.compile(rf'\(\s*{re.escape(a)}\.([A-Za-z_]\w*)\s*!=\s*'
                         rf'{re.escape(b)}\.\1\s+implies\s+([A-Za-z_]\w*)\s+'
                         rf'else\s+none\s*\)')
    for m in derives.finditer(body):
        line_field, member = m.group(1), m.group(2)
        if member in produces:
            bad.append(f"`fun diff` derives {member} from more than one "
                       f"comparison ({produces[member]} and {line_field}) — "
                       f"the mapping the transfer check reads is ambiguous.")
        else:
            produces[member] = line_field
    if members and not produces:
        bad.append("`fun diff` produces no Field member — the correspondence "
                   "check would pass vacuously, so it refuses instead (VP4).")

# THE CLAUSES ARE READ FROM `pred rechainedBy`'S OWN BODY (pc-d405, round-9
# lane C-F1). This scanned the WHOLE comment-stripped module, so
# transfer-shaped text anywhere in the file counted as coverage: delete the
# FStatus clause from rechainedBy and append a separate, unchecked
# `assert UncheckedDecoyTransfer` carrying the same text, and --static exits
# 0 with `pred rechainedBy` mentioning FStatus nowhere — the pinned-jar run
# over that fixture returning 16/16, 0/4, 0/3 at exit 0 as well. Third
# instance of one shape after pc-7d3f (a COMMENT naming a member) and
# pc-d0dd (two transfers SWAPPED): the check kept asking a question of the
# file when the property belongs to one predicate.
def pred_body(text, name):
    """The brace-balanced body of `pred <name>[...] { ... }`, or None.

    `text` is already blanked by dev/alloy_lex.py, which is what makes the
    counting safe (pc-6196, round-10 lane C-F1): this counted `{` and `}`
    with no notion of a string literal, so `some "{"` inside the predicate
    raised the depth and `some "}"` inside a later, unchecked `assert`
    returned it to zero there — the extracted body ran past the real closing
    brace and swallowed the decoy, and the gate counted a transfer that the
    predicate does not make. A brace inside a literal is not a brace, and
    that is a fact about the language, not about this check, so it is
    answered once for the whole file rather than here.

    The parameter list is optional because `pred name { ... }` is legal
    Alloy: the previous anchor required brackets, so the subject could go
    missing through an ordinary edit.
    """
    head = re.search(rf'\bpred\s+{re.escape(name)}\s*(?:\[[^\]]*\])?\s*\{{',
                     text)
    if head is None:
        return None
    depth, start = 1, head.end()
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None


rechained = pred_body(traps, "rechainedBy")
if rechained is None:
    bad.append("could not read `pred rechainedBy` from "
               "spec/pecia-v2-traps.als — the transfer check reads that "
               "predicate's own body, and a check that cannot find its "
               "subject refuses rather than falling back to the file "
               "(pc-d405, VP4).")
    rechained = ""

# One rechainedBy clause: `(FX in t implies out.f = mine.f else out.f = landed.f)`.
clause = re.compile(
    r'\(\s*([A-Za-z_][A-Za-z0-9_]*)\s+in\s+t\s+implies\s+'
    r'out\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*mine\.\2\s+'
    r'else\s+out\.\2\s*=\s*landed\.\2\s*\)')
transfers = {m.group(1): m.group(2) for m in clause.finditer(rechained)}
if members and not transfers:
    bad.append("no rechainedBy transfer clause was found in "
               "`pred rechainedBy` — the coverage check would pass "
               "vacuously, so it refuses instead (VP4).")

seen = {}
for m in members:
    field = transfers.get(m)
    derived = produces.get(m)
    if field is None:
        bad.append(f"Field member {m} is transferred by no rechainedBy clause "
                   f"in spec/pecia-v2-traps.als — a conflict unit the traps do "
                   f"not transfer is silently unconstrained in every trap "
                   f"(pc-219e); merely NAMING it does not transfer it "
                   f"(pc-7d3f).")
        continue
    if sig is not None and derived is None:
        bad.append(f"Field member {m} is in the enum and `fun diff` derives it "
                   f"from no comparison, so nothing in the model says which "
                   f"Line field it stands for and the traps' transfer of it "
                   f"cannot be checked (pc-d0dd).")
    elif derived is not None and field != derived:
        bad.append(f"Field member {m} transfers out.{field} in "
                   f"spec/pecia-v2-traps.als while `fun diff` derives {m} from "
                   f"the {derived} comparison — the traps transfer the WRONG "
                   f"field. A bijection between members and fields is intact "
                   f"under a swap; correspondence is not (pc-d0dd).")
    if field in seen:
        bad.append(f"Field members {seen[field]} and {m} both transfer "
                   f"out.{field} — one of them leaves its own Line field "
                   f"unconstrained in every trap.")
    else:
        seen[field] = m

for line in bad:
    print(f"✗ EXPECTATION VIOLATED: {line}", file=sys.stderr)
sys.exit(1 if bad else 0)
PY
  then
    rc=1
  fi
  return $rc
}

if [ "$STATIC_ONLY" -eq 1 ]; then
  if static_checks; then
    echo "alloy-gate --static: companion structure ok (vocabulary imported, nothing re-declared)"
    exit 0
  fi
  exit 1
fi
ALLOY_JAR="$SPEC/.alloy/alloy.jar"
ALLOY_VERSION="6.2.0"
ALLOY_SHA256="6b8c1cb5bc93bedfc7c61435c4e1ab6e688a242dc702a394628d9a9801edb78d"
ALLOY_URL="https://github.com/AlloyTools/org.alloytools.alloy/releases/download/v${ALLOY_VERSION}/org.alloytools.alloy.dist.jar"

# ── Java: prefer the mise-provided JDK; bare `java` may be a broken stub ──
if command -v mise &>/dev/null && [ -x "$(mise where java 2>/dev/null)/bin/java" ]; then
  JAVA_BIN="$(mise where java)/bin/java"
  JAVAC_BIN="$(mise where java)/bin/javac"
elif command -v java &>/dev/null && java -version &>/dev/null; then
  JAVA_BIN="java"; JAVAC_BIN="javac"
else
  echo "error: no working JDK (try: mise use -g java@21)" >&2
  exit 2
fi

# ── Pinned jar (vendored from axiom's cache; re-download if missing) ──────
verify_jar() {
  if command -v shasum &>/dev/null; then
    echo "${ALLOY_SHA256}  ${ALLOY_JAR}" | shasum -a 256 -c - >/dev/null 2>&1
  else
    echo "${ALLOY_SHA256}  ${ALLOY_JAR}" | sha256sum -c - >/dev/null 2>&1
  fi
}
if [ -f "$ALLOY_JAR" ] && ! verify_jar; then
  echo "alloy.jar checksum mismatch — re-downloading pinned ${ALLOY_VERSION}" >&2
  rm -f "$ALLOY_JAR"
fi
if [ ! -f "$ALLOY_JAR" ]; then
  mkdir -p "$SPEC/.alloy"
  curl -fsSL "$ALLOY_URL" -o "$ALLOY_JAR"
  verify_jar || { echo "error: downloaded jar fails sha256" >&2; exit 2; }
fi

# ── Compile the headless runner if stale ─────────────────────────────────
if [ ! -f "$SPEC/AlloyCheck.class" ] || [ "$SPEC/AlloyCheck.java" -nt "$SPEC/AlloyCheck.class" ]; then
  "$JAVAC_BIN" -cp "$ALLOY_JAR" "$SPEC/AlloyCheck.java" -d "$SPEC"
fi

run_file() { "$JAVA_BIN" -cp "$ALLOY_JAR:$SPEC" AlloyCheck "$1"; }

fail=0

# Expected-failure files must exit EXACTLY 1 (counterexamples found).
# Exit 2 (parse/setup error) is a violation, not a failure-as-expected —
# a crash is not a bite. (This distinction was added after a parse error
# in the seeds file slipped through as "expected failure": the gate's own
# instrument-that-cannot-fail moment, caught 2026-07-29.)

# THE UNIT OF JUDGEMENT IS THE ASSERTION, NOT THE FILE.
# Reading a file's exit code was wrong in two directions and both shipped
# (pc-5a28, pc-855f, found off-lineage 2026-08-12):
#   - an EMPTY theorem model reports Default 1/1 and exits 0, so the theorem
#     arm passed while proving nothing — a fourth way to go vacuously green
#     that VP4(a)'s three do not name;
#   - an expected-failure file exits 1 if ANY check fails, so one healed trap
#     among three was invisible, while this gate's own header claimed a trap
#     that stops failing is treated as failure. That claim was false from the
#     day it was written.
# Both files now declare an EXPECTED COUNT and the realized count must match
# it exactly. A count that drifts is a change to the gate, and has to be a
# deliberate edit here.
results_line() { rg -o 'Results: [0-9]+/[0-9]+ passed' | tail -1; }

expect_pass() {
  local f="$1" want="$2"
  echo "── theorems (must pass, exit 0, expect $want/$want): spec/$f"
  local out rc=0
  out="$(run_file "$SPEC/$f" 2>&1)" || rc=$?
  echo "$out" | rg -o 'Results: .*' | tail -1
  local got; got="$(echo "$out" | results_line)"
  if [ "$rc" -ne 0 ]; then
    echo "✗ EXPECTATION VIOLATED (exit $rc) in $f: a theorem has a counterexample, a run is vacuous, or the file cannot run." >&2
    fail=1
  elif [ "$got" != "Results: $want/$want passed" ]; then
    echo "✗ EXPECTATION VIOLATED in $f: expected 'Results: $want/$want passed', got '${got:-<none>}'." >&2
    echo "  A file that proves FEWER things than declared still exits 0 — that is the empty-model hole (pc-5a28)." >&2
    fail=1
  fi
  echo ""
}

expect_bite() {
  local f="$1" kind="$2" want="$3"
  echo "── $kind (must fail, exit 1, expect 0/$want): spec/$f"
  local out rc=0
  out="$(run_file "$SPEC/$f" 2>&1)" || rc=$?
  echo "$out" | rg -o 'Results: .*' | tail -1
  local got; got="$(echo "$out" | results_line)"
  if [ "$rc" -ne 1 ]; then
    echo "✗ EXPECTATION VIOLATED (exit $rc) in $f: must produce counterexamples (exit 1)." >&2
    echo "  exit 0 = it stopped biting (fix landed? model drift? vacuous model); exit 2 = cannot-run." >&2
    fail=1
  elif [ "$got" != "Results: 0/$want passed" ]; then
    echo "✗ EXPECTATION VIOLATED in $f: expected 'Results: 0/$want passed', got '${got:-<none>}'." >&2
    echo "  EVERY trap/seed must still bite. A file exits 1 when ANY check fails, so a" >&2
    echo "  single healed one hid behind its siblings until this counted them (pc-855f)." >&2
    fail=1
  fi
  echo ""
}

# v1's three files were DELETED at pc-0033, in the same commit that made their
# algebra unreachable — VP4's destructive half. They modelled union merge,
# divergent-revision resolution and compaction, none of which a single timeline
# can express. Keeping them would have left a permanently-green gate over a
# design the code no longer implements, which reads as assurance and is none.
# Retired: StrictOpsPreserveResolvable, DivergentMergeIsFlagged,
# DisjointEditsMergeClean, CompactPreservesCleanAndQueries, CompactIdempotent,
# BranchedCompactMergeIsClean, CrossBranchEdgeAdditionsMergeClean,
# LastWinsMatchesMaxRev. Their surviving content moved to pecia-v2*.als.
# THE COMPANIONS IMPORT THE SUBJECT (pc-0c76). The trap and seed files were
# full COPIES of the theorem model, so a semantic regression of the shipped
# definitions (diff, Field, the chain rules) left every counterexample
# biting in its private copy while all three counts stayed green — the
# null arm could not see the one drift it exists to catch. They now `open`
# peciaV2 and may declare only what they deliberately weaken; static_checks
# (defined above, also the --static mode) refuses a quiet return to
# copying, widened at pc-5fba to the whole clean/head/graph vocabulary.
static_checks || fail=1

# 11, corrected from 12 at pc-9a63: V10 was V7's formula run twice, so the
# old count asserted proof breadth the file did not have. The count moves
# again only with a deliberate edit here (pc-855f's rule).
# 13 at pc-af81 (round-2 fix): 11 grew by RechainPreservesClearing (the
# pc-76b5 escaped case as a theorem) and the SomeClearingRechain
# non-vacuity run. 9 assertions + 4 runs.
# 14 at pc-2caf (round-3 fix): the planned-id frontier joins the model
# (Config.planned in e003cleanL and writeOk) and SomePlannedFrontier is
# its non-vacuity run. 9 assertions + 5 runs.
# 16 at pc-fc4c (round-5 fix): `rechained` constrains out.rev and
# RechainedEntryPassesTheRevCas (V12) binds the construction's arithmetic
# to the revCas over an actual log — a re-chain revision regression turns
# the model red; SomeRechainedCasStep is V12's non-vacuity run, against a
# multi-revision head. 10 assertions + 6 runs.
# 18 at pc-dad7 (round-13 fix): BrandCustodyThroughRechain (V13) is the
# first CORE assertion to observe the brand transfer — with
# `out.forced = mine.forced` deleted from `rechained` the whole gate stayed
# 16/16, 0/4, 0/3 at exit 0, measured by executing the pinned jar — and
# SomeBrandedRechain is its non-vacuity run, exhibiting the branded-mine /
# unbranded-landed scenario trap 4 poses. 11 assertions + 7 runs.
expect_pass "peciaV2.als" 18
# Traps 4 at pc-af81: BrandSurvivesRechain pins the brand-dropping
# re-chain (pc-f7fc) as a permanent expected counterexample.
expect_bite "pecia-v2-traps.als"        "v2 traps" 4
expect_bite "pecia-v2-seeded-kills.als" "v2 seeded kills / null arm" 3

echo ""
if [ "$fail" -eq 0 ]; then
  echo "alloy-gate: all expectations met (theorems hold; traps and seeds still bite)"
  echo "Exit 0 means the model checked at scope — never that the code is correct."
fi
exit $fail
