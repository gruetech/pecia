#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""README prose tripwire — the shopfront as a checkable projection (pc-73f5).

Usage:  dev/prose-check.py [--root DIR]   (default: repo root; pre-commit
        passes a staging dir holding the STAGED blobs, because git commits
        the index and a gate that reads the worktree checks the wrong thing
        — the F7/F13 class, same as claims-check.)

Exit 0 means WELL-FORMED, never TRUE. This checks that README prose declares
which claim it projects and does not outrun that claim's tier. It cannot
check whether the claim itself is honest — that is claims-check's problem —
and it emphatically cannot read English. Everything here rests on prose
DECLARING its binding, which is the only version of this gate that is
mechanical rather than aspirational.

WHY THIS EXISTS. claims.yaml is canonical and prose is projection, but until
now nothing enforced the second half: the README drifted for a week and was
caught by eye (pc-c07b). AGENTS.md requires projection; this makes the
requirement bite.

THE TWO MECHANISMS, and each maps to one of the defects that motivated it:

  BINDING + TIER FIDELITY.  A `<!-- claims: <id> -->` marker binds the
      paragraph above it to claims.yaml entries. Ids must resolve. A bound
      block may not use a tier word that contradicts the bound tier, and a
      block bound to a SOFT tier (asserted / aspirational) must SAY SO —
      naming the tier, hedging explicitly, or pointing at the open record —
      SENTENCE BY SENTENCE (pc-3cd4): one recognized hedge used to satisfy
      the rule for the whole block, so arbitrary unrelated capability prose
      entered a marked block undetected as long as a hedge appeared
      somewhere in it. Every sentence now earns its own pass: a hedge, a
      tier word the block is bound to, a record id, or a verbatim quotation
      of the bound claim.
      Kill: README's "hooks that let a harness veto untracked work", a flat
      capability sentence projecting `claude-harness-wiring`, whose own text
      says the veto is a shape check never validated against the ledger.

  REFERRAL INTEGRITY.  A `<!-- referral: FILE "subject" -->` marker says the
      README sends a reader elsewhere for something. The target file must
      exist AND mention the subject AND carry a locator on a nearby line.
      Kill: "see the practice catalog cited in PLAN.md" — after the private-
      path scrub, PLAN.md names the rubric and gives no address, so the
      pointer dangles. The scrub was correct; the dangling pointer is its
      uncosted side effect, and this is what notices next time.

ANTI-VACUITY (VP4). Every rule above must be able to turn RED, and a gate
whose corpus contains no marker would pass by having nothing to check. So a
README with zero bindings is itself a failure: see `no bindings` below.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from claims_yaml import load as load_register  # noqa: E402

ROOT = HERE.parent

#: Tiers whose prose must not read as a settled capability. `proved`,
#: `tested` and `machine-checked` are earned and may be stated flatly.
SOFT_TIERS = {"asserted", "aspirational"}

#: Words that, appearing in a bound block, are read as tier talk. Matched
#: against the tier's HEAD (machine-checked(scope) -> machine-checked) so the
#: scope parenthetical does not have to be repeated in prose.
TIER_WORDS = ("proved", "proven", "machine-checked", "tested", "asserted",
              "aspirational")

#: Prose spelling -> the tier it names. `proven` is not a tier; it is how
#: English writes one, and README said it while the checker looked only for
#: `proved` (pc-884c).
TIER_ALIASES = {"proven": "proved"}

#: An explicit hedge. A block bound to a soft tier satisfies the rule by
#: naming the tier, by carrying one of these, or by citing a record id.
HEDGES = ("not read-only", "no adversarial pass", "is not a verdict",
          "shape check", "never validated", "not yet", "does not",
          "deliberately", "claims.yaml", "asserted")

#: A copula phrase assigning a tier word — "X stands at `tested`", "X is
#: `asserted`". The attribution unit for pc-444b: inside a multi-claim
#: marker the union rule below cannot see tier words assigned to the WRONG
#: claim (swapped tiers pass, both words being in the union), so a copula
#: match is additionally checked against the tiers of the bound claims its
#: own subject clause names. A declared heuristic: it catches the copula
#: signature, not every conceivable attribution.
COPULA_TIER = re.compile(
    r"(?:stands?\s+at|is|are)\s+(proved|proven|machine-checked|tested|"
    r"asserted|aspirational)(?![\w-])", re.I)
#: Where a subject clause starts: sentence and clause punctuation, plus the
#: em-dash. Commas are deliberately NOT boundaries (a list subject names
#: several claims); the previous copula match bounds instead, so "X is
#: `asserted`, and Y is `tested`" attributes each word to its own clause.
CLAUSE_BOUNDARY = re.compile(r"[.!?:;]|—")

CLAIM_MARK = re.compile(r"<!--\s*claims:\s*([^>]+?)\s*-->")
REFERRAL_MARK = re.compile(r'<!--\s*referral:\s*(\S+)\s+"([^"]+)"\s*-->')
RECORD_REF = re.compile(r"\bpc-[0-9a-f]{4}\b")
#: A locator is an address a reader can act on: a path, a URL, a claims
#: foreign reference, or a record id. A bare NAME is exactly what the
#: dangling-pointer kill is about, so a name does not count.
LOCATOR = re.compile(r"(\S+/\S+|https?://|\bclaims:[\w-]+|\bpc-[0-9a-f]{4}\b)")

failures = 0


def fail(msg: str) -> None:
    global failures
    failures += 1
    print(f"  ✗ {msg}", file=sys.stderr)


def tier_head(tier: str) -> str:
    return tier.split("(", 1)[0].strip()


def normalize(text: str) -> str:
    """Markdown-insensitive form, for comparing prose against claim text.

    Strips the decoration a README adds to a sentence it is quoting —
    blockquote carets, emphasis, backticks, list bullets, line wrapping — so
    that "is this the claim, verbatim?" is a question about words and not
    about formatting.
    """
    stripped = re.sub(r"^[\s>*\-]+", "", text, flags=re.M)
    stripped = stripped.replace("*", "").replace("`", "").replace("_", "")
    return re.sub(r"\s+", " ", stripped).strip().lower()


def split_sentences(text: str) -> list[str]:
    """Whitespace-flattened sentences, split at .!? before a space. The unit
    the soft-tier rule is scoped to (pc-3cd4): a hedge covers its own
    sentence and nothing else. Deliberately naive — a README that wants a
    sentence read as one writes it as one; an abbreviation split too eagerly
    fails toward a louder gate, never a quieter one."""
    flat = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]


def sentence_carries_its_pass(sentence: str, soft: list[dict],
                              bound_words: list[str]) -> bool:
    """One sentence of a soft-tier block: hedged, tier-marked, record-cited,
    or a verbatim quotation of a bound claim (trailing punctuation aside)."""
    lowered = sentence.lower()
    if any(h in lowered for h in HEDGES):
        return True
    if RECORD_REF.search(sentence):
        return True
    if any(t in lowered for t in SOFT_TIERS):
        return True
    for word in bound_words:
        if re.search(rf"(?<!not )(?<![\w-]){re.escape(word)}(?![\w-])",
                     sentence, re.I):
            return True
    core = normalize(sentence).rstrip(".!?").strip()
    return bool(core) and any(core in normalize(str(c.get("claim", "")))
                              for c in soft)


def check_tier_attribution(line_no: int, prose: str, bound: list[dict]) -> None:
    """(a2) Per-clause tier attribution (pc-444b, round-5 lane D-F2).

    The union rule in (a) checks every tier word in the block against the
    union of tiers over every claim the marker binds — so in a multi-claim
    marker, swapping the tier words of an `asserted` and a `machine-checked`
    claim passed at exit 0: both words are in the union, and bound prose
    outran its tier in the gate's own terms. Each copula match is now also
    checked against the tiers of the bound claims its own subject clause
    names (a claim id names itself verbatim or with hyphens read as spaces;
    prose may inflect the name, so containment is substring). A subject
    naming no bound claim keeps the union fallback — this gate still cannot
    read English; it bounds what the union can launder."""
    flat = normalize(prose)
    last_end = 0
    for m in COPULA_TIER.finditer(flat):
        start = last_end
        for b in CLAUSE_BOUNDARY.finditer(flat, 0, m.start()):
            start = max(start, b.end())
        subject = flat[start:m.start()]
        last_end = m.end()
        word = TIER_ALIASES.get(m.group(1).lower(), m.group(1).lower())
        for c in bound:
            cid = str(c.get("id", "")).lower()
            if cid and (cid in subject or cid.replace("-", " ") in subject):
                have = tier_head(str(c.get("tier", "")))
                if word != have:
                    fail(f"README:{line_no} assigns tier {m.group(1)!r} to "
                         f"{c.get('id')}, which the register holds at "
                         f"{have!r} — a tier word checks against the claim "
                         f"its own clause names, not the union of the "
                         f"marker's claims (pc-444b)")


def blocks_with_markers(text: str) -> list[tuple[int, str, str]]:
    """Return (line_no, block_text, marker_line) for each `claims:` marker.

    The bound block is the marker's own line plus every line above it back to
    the previous blank line or list-item boundary. Binding UPWARD is
    deliberate: it lets the marker sit at the end of a bullet without
    interrupting the sentence a reader sees.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if not CLAIM_MARK.search(line):
            continue
        start = i
        while start > 0:
            # Stop AT the bullet's first line: that is this block's top, and
            # walking past it would swallow the bullet above and let its
            # unrelated prose satisfy the hedge rule for this one.
            if re.match(r"\s*[-*]\s", lines[start]):
                break
            if not lines[start - 1].strip():
                break
            start -= 1
        out.append((i + 1, "\n".join(lines[start:i + 1]), line))
    return out


def check_bindings(readme: str, claims: dict) -> None:
    by_id = {c["id"]: c for c in claims.get("claims", [])}
    marked = blocks_with_markers(readme)

    if not marked:
        fail("no bindings: README declares no `<!-- claims: <id> -->` marker, so "
             "every rule in this checker is vacuous. A shopfront that projects "
             "nothing is not a passing state (VP4).")
        return

    for line_no, block, marker in marked:
        ids = [i.strip() for i in CLAIM_MARK.search(marker).group(1).split(",") if i.strip()]
        prose = CLAIM_MARK.sub("", block).strip()
        lowered = prose.lower()

        if not prose:
            # A marker with nothing above it binds no prose, so every rule
            # below would pass by vacuity — the per-block form of the corpus
            # rule in check_bindings' opening guard.
            fail(f"README:{line_no} marker binds no prose (blank line directly "
                 f"above it?) — a binding that checks nothing is not a pass")
            continue

        bound = []
        for cid in ids:
            if cid not in by_id:
                fail(f"README:{line_no} binds to claim {cid!r}, which claims.yaml "
                     f"does not declare ({len(by_id)} ids present)")
                continue
            bound.append(by_id[cid])
        if not bound:
            continue

        tiers = {tier_head(str(c.get("tier", ""))) for c in bound}

        # (a) Tier fidelity — prose may not name a tier it is not bound to.
        for word in TIER_WORDS:
            # Backticks OPTIONAL (pc-884c). The rule read `` `word` `` only,
            # so the identical overclaim was RED with backticks and GREEN
            # without — narrower than the contract stated one line above it,
            # and the typography is not what makes a sentence a tier claim.
            #
            # `not` is excluded because a NEGATED tier word is not a claim to
            # that tier: README's "measured, not asserted" is the live
            # instance, and matching it would make the honest sentence fail.
            named = TIER_ALIASES.get(word, word)
            if named in tiers:
                continue
            if re.search(rf"(?<!not )(?<![\w-]){re.escape(word)}(?![\w-])",
                         prose, re.I):
                fail(f"README:{line_no} says {word!r} but its bound claim(s) "
                     f"{[c['id'] for c in bound]} stand at {sorted(tiers)}")

        # (a2) Per-clause attribution — the union above cannot see a tier
        #      word assigned to the WRONG claim of a multi-claim marker.
        check_tier_attribution(line_no, prose, bound)

        # (b) The soft-tier rule — a claim that is merely asserted may not be
        #     projected as a settled capability.
        soft = [c for c in bound if tier_head(str(c.get("tier", ""))) in SOFT_TIERS]
        if soft:
            # VERBATIM EXEMPTION. This gate exists to catch prose that OUTRUNS
            # its claim, and an exact quotation of the claim's own text cannot:
            # the projection is lossless, so claims.yaml's tier still governs
            # every word a reader sees. Note this is not a convenience escape —
            # it is why the rule is sound. A LOSSY summary is not a substring,
            # so the qualifier-dropping case (the shape-check veto stated as a
            # flat veto) stays caught, which is the whole point.
            #
            # SENTENCE-SCOPED beyond that (pc-3cd4, round-1 lane D-F8). The
            # hedge test ran over the WHOLE block, so one recognized hedge
            # laundered every other sentence: the reproduction replaced a
            # capability phrase with "Pecia cures every disease" and the
            # block still passed on the hedges of its neighbouring sentence.
            # Each sentence now earns its own pass — a hedge, a bound tier
            # word, a record id, or a quotation of the bound claim. This
            # still cannot read English (an invented capability WITH a hedge
            # in its own sentence passes); it bounds what one hedge can
            # cover, which is the mechanical half the gate can own.
            quoted = any(normalize(prose) in normalize(str(c.get("claim", "")))
                         for c in soft)
            if not quoted:
                bound_words = [w for w in TIER_WORDS
                               if TIER_ALIASES.get(w, w) in tiers]
                for sentence in split_sentences(prose):
                    if sentence_carries_its_pass(sentence, soft, bound_words):
                        continue
                    fail(f"README:{line_no} projects {[c['id'] for c in soft]} "
                         f"(tier {sorted({tier_head(str(c.get('tier',''))) for c in soft})}) "
                         f"as a flat capability — a sentence with no hedge, no "
                         f"bound tier word, no record reference and no claim "
                         f"quotation (hedges are sentence-scoped, pc-3cd4): "
                         f"{sentence[:70]!r}")


def check_referrals(readme: str, root: Path) -> None:
    for i, line in enumerate(readme.splitlines(), 1):
        m = REFERRAL_MARK.search(line)
        if not m:
            continue
        target_name, subject = m.group(1), m.group(2)
        target = root / target_name
        if not target.exists():
            fail(f"README:{i} refers the reader to {target_name}, which does not exist")
            continue
        # A citation is a sentence, not a line: look for the address anywhere
        # in the PARAGRAPH that names the subject. Bounded at the blank line
        # deliberately — widen it further and an unrelated path elsewhere in
        # the document would satisfy every referral into it.
        tlines = target.read_text().splitlines()
        hits = [n for n, ln in enumerate(tlines) if subject.lower() in ln.lower()]
        if not hits:
            fail(f"README:{i} refers to {subject!r} in {target_name}, which never "
                 f"mentions it")
            continue
        windows = []
        for n in hits:
            lo = hi = n
            while lo > 0 and tlines[lo - 1].strip():
                lo -= 1
            while hi + 1 < len(tlines) and tlines[hi + 1].strip():
                hi += 1
            windows.append(tlines[lo:hi + 1])
        if not any(LOCATOR.search(ln) for w in windows for ln in w):
            fail(f"README:{i} says {subject!r} is cited in {target_name}, but every "
                 f"line there naming it carries no locator — a name is not an "
                 f"address. Nearest: {tlines[hits[0]].strip()[:70]!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT),
                    help="directory holding README.md, claims.yaml and referral "
                         "targets (pre-commit passes the STAGED blobs)")
    args = ap.parse_args()
    root = Path(args.root)

    readme_path, claims_path = root / "README.md", root / "claims.yaml"
    for p in (readme_path, claims_path):
        if not p.exists():
            print(f"cannot run: no {p.name} under {root}", file=sys.stderr)
            return 2
    # THE SAME REGISTER TO EVERY READER (pc-4b3c). A duplicate key is a
    # malformation here too: PyYAML keeps the last and drops the rest, so a
    # register carrying two `claims:` blocks would bind this gate's prose to
    # one of them and say nothing about the other.
    claims, why_not = load_register(claims_path.read_text())
    if why_not is not None:
        print(f"cannot run: claims.yaml is malformed — {why_not}",
              file=sys.stderr)
        return 2
    claims = claims or {}

    readme = readme_path.read_text()
    check_bindings(readme, claims)
    check_referrals(readme, root)

    if failures:
        print(f"✗ prose-check: {failures} README claim(s) outrun claims.yaml",
              file=sys.stderr)
        return 1
    print('{"ok": true, "note": "Exit 0 means \\"well-formed,\\" never \\"true.\\" '
          'Bindings resolve and no bound prose outruns its tier."}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
