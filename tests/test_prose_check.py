#!/usr/bin/env python3
"""Kills and controls for the README prose gate — pc-73f5.

Every test drives `dev/prose-check.py` as a subprocess over a fixture root,
which is why the script takes `--root`: pre-commit must feed it STAGED blobs,
and a gate that can only read the worktree passes a staged contradiction
masked by an unstaged repair (the F7/F13 class this repo has shipped twice).

WHAT THE CONTROLS ARE FOR. This repo has shipped three gates that were green
over an empty denominator (pc-47d6, pc-085a, pc-cb50). The failure mode here
is specific and worse than an empty corpus: this gate can only see prose that
DECLARES its binding, so a README with the markers quietly removed is
indistinguishable from a README with nothing wrong. `test_kill_unmarked_...`
is that arm — an unmarked corpus must be a failure, not a pass.

THE TWO KILLS THIS GATE WAS BUILT FROM are real defects found by eye in the
2026-08-31 v1-prep run, reproduced here as fixtures so they stay caught:
  * a lossy summary of an `asserted` claim stated as a settled capability
    (the harness veto, whose claim says "shape check only");
  * a referral to a document that names the subject but, after the private-
    path scrub, gives no address for it.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "dev" / "prose-check.py"

CLAIMS = """\
schema_version: 1
audit: 2026-07-25
claims:
- id: soft-capability
  claim: The hooks veto a task citing no ledger id (shape check only, never
    validated against the real ledger).
  tier: asserted
  notes: fixture
- id: hard-capability
  claim: The write gate refuses a malformed record at commit time.
  tier: tested
  evidence: python3 -m unittest
  verified: 2026-08-31
  notes: fixture
"""


class ProseGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "claims.yaml").write_text(CLAIMS)

    def run_gate(self, readme: str, **docs: str) -> subprocess.CompletedProcess[str]:
        (self.root / "README.md").write_text(readme)
        for name, text in docs.items():
            (self.root / f"{name}.md").write_text(text)
        # THE GATE IS RUN AS A FILE, not as `sys.executable <path>` (pc-9f02).
        # prose-check.py carries a PEP-723 header declaring pyyaml and a
        # `uv run --script` shebang that provisions it. Handing the path to
        # the test's own interpreter bypasses both, so from a bare `git clone`
        # — no pyproject, no lockfile, no venv — 23 tests failed with
        # ModuleNotFoundError while passing here on an untracked .venv nothing
        # in the repo builds. That is VP19 exactly: the suite was reproducible
        # on the one host it was written on. Executing the file is also how
        # dev/gates.py already invokes every checker, so this is the repo
        # agreeing with itself rather than a special case for tests.
        return subprocess.run([str(GATE), "--root", str(self.root)],
                              capture_output=True, text=True, check=False)

    # ---- kill 1: a lossy summary of a soft claim, stated flat ----------

    def test_kill_flat_capability_projecting_an_asserted_claim(self) -> None:
        """The README:36 defect, as a fixture.

        The prose drops exactly the qualifier that makes the claim honest
        ("shape check only"), which is what makes it a projection failure
        rather than a paraphrase."""
        res = self.run_gate("# t\n\n- Hooks let a harness veto untracked work.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("flat capability", res.stderr)
        self.assertIn("soft-capability", res.stderr)

    def test_control_same_claim_hedged_passes(self) -> None:
        res = self.run_gate("# t\n\n- Hooks veto a task citing no ledger id. This is a\n"
                            "  shape check only, never validated against the ledger.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_verbatim_quotation_of_a_soft_claim_passes(self) -> None:
        """An exact quote cannot outrun the claim — the projection is lossless.

        This is the exemption that keeps the epigraph legal, and it must not
        become a loophole: the kill above is a LOSSY summary of the same
        claim and is not a substring, so it stays red."""
        res = self.run_gate(
            "# t\n\n> The hooks veto a task citing no ledger id (shape check only,\n"
            "> never validated against the real ledger).\n"
            "<!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_hard_tier_may_be_stated_flatly(self) -> None:
        res = self.run_gate("# t\n\n- The write gate refuses bad records.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    # ---- kill 1a: swapped tiers inside a multi-claim marker (pc-444b) ----

    def test_kill_swapped_tiers_in_a_multi_claim_marker_are_caught(self) -> None:
        """pc-444b (round-5 lane D-F2): the tier comparison ran against the
        UNION of tiers over every claim the marker binds, so swapping the
        tier words of an asserted and a tested claim passed at exit 0 —
        bound prose outrunning its tier in the gate's own output-note
        terms. A tier word now checks against the claim its own clause
        names."""
        res = self.run_gate(
            "# t\n\n"
            "The soft capability stands at `tested`. The hard capability\n"
            "is `asserted`.\n"
            "<!-- claims: soft-capability, hard-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout + res.stderr)
        self.assertIn("assigns tier 'tested' to soft-capability", res.stderr)
        self.assertIn("assigns tier 'asserted' to hard-capability", res.stderr)

    def test_control_correctly_attributed_tiers_pass(self) -> None:
        """The discriminating control: the same two clauses with each tier
        word on its own claim."""
        res = self.run_gate(
            "# t\n\n"
            "The soft capability stands at `asserted`. The hard capability\n"
            "is `tested`.\n"
            "<!-- claims: soft-capability, hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_unattributed_tier_word_keeps_the_union_rule(self) -> None:
        """A clause naming no bound claim falls back to the union check —
        the gate still cannot read English, and an honest block may state a
        tier the marker binds without a copula naming the claim."""
        res = self.run_gate(
            "# t\n\n"
            "Parts of this are `tested`; the soft capability is `asserted`\n"
            "still.\n"
            "<!-- claims: soft-capability, hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    # ---- kill 1b: a hedge may not launder its neighbours (pc-3cd4) -------

    def test_kill_hedge_elsewhere_in_the_block_does_not_launder(self) -> None:
        """The round-1 reproduction (lane D-F8): a marked block carrying an
        invented capability passed as long as one recognized hedge appeared
        SOMEWHERE in it. Hedges are sentence-scoped now, so the invented
        sentence fails on its own account while the hedged one stands."""
        res = self.run_gate(
            "# t\n\n- Pecia cures every disease. The veto is a shape check\n"
            "  only, never validated against the real ledger.\n"
            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("flat capability", res.stderr)
        self.assertIn("sentence-scoped", res.stderr)

    def test_control_each_sentence_carrying_its_own_pass_is_green(self) -> None:
        """The discriminating control: the same two-sentence shape where the
        capability sentence is a verbatim quotation of the bound claim and
        the second carries the hedge stays green — the kill measures the
        invented sentence, not the block shape."""
        res = self.run_gate(
            "# t\n\n- Hooks veto a task citing no ledger id. This is a\n"
            "  shape check only, never validated against the ledger.\n"
            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_kill_invented_capability_quoting_nothing_is_red_even_alone(self) -> None:
        res = self.run_gate(
            "# t\n\n- Pecia cures every disease.\n"
            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)

    # ---- kill 2: a referral whose target gives a name, not an address ----

    def test_kill_referral_to_a_name_without_a_locator(self) -> None:
        """The README:31 defect, as a fixture — the scrub's uncosted side effect."""
        res = self.run_gate(
            "# t\n\n- Measured, not asserted — see the practice catalog cited in PLAN.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: PLAN.md "practice catalog" -->\n',
            PLAN="Method rubric: the practice catalog — apply and cite.\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("no locator", res.stderr)

    def test_control_referral_with_a_locator_passes(self) -> None:
        res = self.run_gate(
            "# t\n\n- See the practice catalog cited in PLAN.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: PLAN.md "practice catalog" -->\n',
            PLAN="Method rubric: the practice catalog — see claims:hard-capability.\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_locator_elsewhere_in_the_subjects_paragraph_passes(self) -> None:
        """A citation is a sentence, not a line — the address may sit a line
        below the name it belongs to."""
        res = self.run_gate(
            "# t\n\n- See the practice catalog cited in PLAN.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: PLAN.md "practice catalog" -->\n',
            PLAN="Method rubric: the practice catalog — apply and cite.\n"
                 "The measurement is quoted in spec/format-v1.md.\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_kill_locator_in_a_different_paragraph_does_not_count(self) -> None:
        """The window is paragraph-bounded, or every referral into a document
        containing any path anywhere would pass — the vacuity this gate
        exists to prevent."""
        res = self.run_gate(
            "# t\n\n- See the practice catalog cited in PLAN.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: PLAN.md "practice catalog" -->\n',
            PLAN="Method rubric: the practice catalog — apply and cite.\n"
                 "\n"
                 "Unrelated paragraph mentioning spec/format-v1.md.\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("no locator", res.stderr)

    def test_kill_referral_subject_absent_from_target(self) -> None:
        res = self.run_gate(
            "# t\n\n- See the rubric in PLAN.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: PLAN.md "practice catalog" -->\n',
            PLAN="Nothing relevant here.\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("never mentions it", res.stderr)

    def test_kill_referral_to_a_missing_document(self) -> None:
        res = self.run_gate(
            "# t\n\n- See NOPE.md.\n"
            "  <!-- claims: hard-capability -->\n"
            '  <!-- referral: NOPE.md "anything" -->\n')
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("does not exist", res.stderr)

    # ---- binding integrity ---------------------------------------------

    def test_kill_binding_to_an_undeclared_claim(self) -> None:
        res = self.run_gate("# t\n\n- Something.\n  <!-- claims: no-such-claim -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("does not declare", res.stderr)

    def test_kill_tier_word_contradicting_the_bound_claim(self) -> None:
        res = self.run_gate("# t\n\n- The veto stands at `tested`.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("stand at", res.stderr)

    def test_kill_unbackticked_tier_word_is_caught(self) -> None:
        """pc-884c arm 2 — the hole.

        The rule matched `` `word` `` only, so the IDENTICAL overclaim was RED
        with backticks and GREEN without. Typography is not what makes a
        sentence a tier claim."""
        res = self.run_gate("# t\n\n- The veto is proved and adversarially validated.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("stand at", res.stderr)

    def test_kill_backticked_tier_word_still_caught(self) -> None:
        """arm 1 — the behaviour that already worked must survive the widening."""
        res = self.run_gate("# t\n\n- The veto stands at `proved`.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("stand at", res.stderr,
                      msg="must fail on TIER FIDELITY, not merely on the "
                          "soft-tier hedge rule happening to fire too")

    def test_kill_proven_is_the_same_claim_as_proved(self) -> None:
        """pc-884c #2. `proven` is not a tier, it is how English writes one —
        and README used it while the checker looked only for `proved`."""
        res = self.run_gate("# t\n\n- A discipline already proven in the sibling repos.\n"
                            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("proven", res.stderr)

    def test_control_a_negated_tier_word_is_not_a_tier_claim(self) -> None:
        """The false positive the widening would otherwise create, and it is
        live: README says "measured, not asserted" in a block bound to a
        tested claim. Matching that would fail the honest sentence."""
        res = self.run_gate("# t\n\n- Rules enforced structurally hold. This is\n"
                            "  measured, not asserted.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_tier_word_inside_a_longer_word_is_not_a_match(self) -> None:
        """`untested` and `well-tested` are not the tier `tested`."""
        res = self.run_gate("# t\n\n- The gate is well-proved and not unproved.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_control_prose_naming_its_own_bound_tier_passes(self) -> None:
        res = self.run_gate("# t\n\n- The write gate stands at tested.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    # ---- anti-vacuity (VP4): the gate must be able to turn RED ----------

    def test_kill_unmarked_readme_is_a_failure_not_a_pass(self) -> None:
        """The denominator arm.

        This gate sees only prose that declares a binding, so a README whose
        markers were deleted would otherwise be its quietest possible pass —
        the exact shape of the three empty-denominator gates this repo has
        already shipped."""
        res = self.run_gate("# t\n\n- Hooks let a harness veto untracked work.\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("no bindings", res.stderr)

    def test_kill_marker_binding_no_prose(self) -> None:
        res = self.run_gate("# t\n\nSomething.\n\n<!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("binds no prose", res.stderr)

    def test_cannot_run_without_a_claims_ledger(self) -> None:
        (self.root / "claims.yaml").unlink()
        res = self.run_gate("# t\n\n- x\n  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 2, msg=res.stdout)
        self.assertIn("cannot run", res.stderr)

    def test_bullet_boundary_does_not_borrow_a_neighbours_hedge(self) -> None:
        """A hedge in the bullet ABOVE must not launder the bullet below it.

        Block extent is the whole mechanism: if the walk-up ran past the
        bullet start, every flat claim under a hedged one would pass."""
        res = self.run_gate(
            "# t\n\n- The gate is a shape check only, never validated.\n"
            "- Hooks let a harness veto untracked work.\n"
            "  <!-- claims: soft-capability -->\n")
        self.assertEqual(res.returncode, 1, msg=res.stdout)
        self.assertIn("flat capability", res.stderr)


class DuplicateKeyRegister(unittest.TestCase):
    """pc-4b3c's sibling, one gate over. This gate reads claims.yaml with the
    same parser claims-check used, so a register carrying two `claims:`
    blocks would bind README prose to whichever one PyYAML kept and say
    nothing about the other — the same silent last-key-wins drop, here in
    the reader whose whole job is comparing prose against the register.

    A malformed register is cannot-run (exit 2), not a finding about the
    README: nothing is wrong with the prose, and the gate says which file it
    could not read."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "claims.yaml").write_text(CLAIMS)

    def run_gate(self, readme: str) -> subprocess.CompletedProcess[str]:
        (self.root / "README.md").write_text(readme)
        return subprocess.run([str(GATE), "--root", str(self.root)],
                              capture_output=True, text=True, check=False)

    def test_kill_two_top_level_claims_keys_are_refused(self) -> None:
        (self.root / "claims.yaml").write_text(
            "claims:\n- id: soft-capability\n  claim: a phantom block\n"
            "  tier: tested\n" + CLAIMS)
        res = self.run_gate("# t\n\n- The write gate refuses bad records.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 2, msg=res.stdout + res.stderr)
        self.assertIn("malformed", res.stderr)
        self.assertIn("`claims` is given twice", res.stderr)

    def test_kill_a_repeated_key_inside_an_entry_is_refused(self) -> None:
        """The nested half: a second `tier:` above an entry's own decides
        which tier the prose is measured against, and nothing said so."""
        (self.root / "claims.yaml").write_text(
            CLAIMS.replace("- id: soft-capability\n",
                           "- id: soft-capability\n  tier: tested\n", 1))
        res = self.run_gate("# t\n\n- The write gate refuses bad records.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 2, msg=res.stdout + res.stderr)
        self.assertIn("`tier` is given twice", res.stderr)

    def test_control_the_same_register_without_the_duplicate_passes(self) -> None:
        """Discriminating control: only the repeated key differs."""
        res = self.run_gate("# t\n\n- The write gate refuses bad records.\n"
                            "  <!-- claims: hard-capability -->\n")
        self.assertEqual(res.returncode, 0, msg=res.stdout + res.stderr)


class LiveReadme(unittest.TestCase):
    """The shipped README must satisfy the gate it ships."""

    def test_repo_readme_passes(self) -> None:
        res = subprocess.run([str(GATE)], cwd=str(ROOT),
                             capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, msg=res.stderr)

    def test_repo_readme_actually_carries_bindings(self) -> None:
        """Anti-vacuity for the arm above: it must pass over a real corpus."""
        readme = (ROOT / "README.md").read_text()
        self.assertGreaterEqual(readme.count("<!-- claims:"), 5)


class ShippedReadmeBindings(unittest.TestCase):
    """pc-7b4c (round-6 lane D-F1): README capability prose was
    unregistered or bound to claims about different behavior — the
    storage/publication paragraph rode the checker-only
    reference-implementation claim, the deterministic-queries and
    chain-of-custody bullets carried no marker (the depth beyond
    pc-876d), and the output-contract sentence shared its one marker with
    the unrelated asserted claude-harness-wiring claim. prose-check
    cannot read English, so the repaired content-true bindings are pinned
    against the real artifacts (the pc-130e convention)."""

    def region(self, text: str, start: str, end: str) -> str:
        return text[text.index(start):text.index(end)]

    def test_storage_prose_binds_to_a_claim_stating_storage(self) -> None:
        readme = (ROOT / "README.md").read_text()
        claims = (ROOT / "claims.yaml").read_text()
        para = self.region(readme, "pecia is a work ledger",
                           "## Use it in an existing Git repository")
        self.assertIn("v2-storage", para,
                      msg="the storage paragraph must bind to a storage claim")
        entry = re.search(r"- id: v2-storage\n((?:  .*\n)+)", claims)
        self.assertIsNotNone(entry, msg="the v2-storage claim must exist")
        claim_text = re.search(r"  claim: ((?:.|\n)*?)\n  tier:",
                               entry.group(0)).group(1)
        for word in ("PECIA_LOG_DIR", "refs/pecia/log", "compare-and-swap",
                     "projection"):
            self.assertIn(word, claim_text,
                          msg=f"the bound claim must itself state {word!r}")

    def test_every_capability_bullet_carries_a_marker(self) -> None:
        readme = (ROOT / "README.md").read_text()
        queries = self.region(readme, "pecia is a work ledger",
                              "## Use it in an existing Git repository")
        self.assertIn("<!-- claims:", queries)
        self.assertIn("deterministic-queries", queries)
        custody = self.region(readme, "### Python: copy one file",
                              "### Rust: install the CLI")
        self.assertIn("<!-- claims:", custody)
        self.assertIn("v2-storage", custody)

    def test_output_contract_no_longer_shares_the_harness_marker(self) -> None:
        readme = (ROOT / "README.md").read_text()
        agent = self.region(readme, "Commands with machine output",
                            "## What the evidence says")
        self.assertIn("query-prose-containment", agent,
                      msg="the JSON output contract binds to its own claim")
        self.assertNotIn("claude-harness-wiring", agent,
                         msg="the unrelated asserted claim keeps only the "
                             "veto bullet")


class ProjectionPathIsScopedToItsStore(unittest.TestCase):
    """pc-a80b (round-7 lane D-F3): README's storage paragraph and the
    v2-storage claim named pinned stores and then stated, unscoped, that
    `.pecia/work.jsonl` is the projection — while with PECIA_LOG_DIR set
    `init` writes no `.pecia/work.jsonl` at all: the projection follows the
    store (pc-74da), and an externally pinned store puts nothing in the
    repository or its diff. The implementation was right and the prose was
    imprecise, so the kill is on the prose, pinned against the behaviour it
    describes (the pc-130e convention) — prose-check cannot read English.

    The BEHAVIOUR side is not restated here: TwoStoresOneRepo and
    PinnedStoreFindingsNameTheStore own it. What this class refuses is the
    two artifacts drifting apart again.
    """

    def storage_prose(self) -> str:
        readme = (ROOT / "README.md").read_text()
        return readme[readme.index("### Python: copy one file"):
                      readme.index("### Rust: install the CLI")]

    def claim_text(self) -> str:
        claims = (ROOT / "claims.yaml").read_text()
        entry = re.search(r"- id: v2-storage\n((?:  .*\n)+)", claims)
        self.assertIsNotNone(entry, msg="the v2-storage claim must exist")
        return re.search(r"  claim: ((?:.|\n)*?)\n  tier:",
                         entry.group(0)).group(1)

    def test_readme_scopes_the_advertised_projection_path(self) -> None:
        """Every mention of the repository-side path in the storage prose
        sits with the scope that makes it true."""
        prose = self.storage_prose()
        self.assertIn(".pecia/work.jsonl", prose,
                      msg="anti-vacuity: the path must still be advertised, "
                          "or this arm passes by deletion")
        self.assertIn("by default", prose,
                      msg="the advertised path must be scoped to the store "
                          "that actually has it")
        self.assertIn("beside its own log", prose,
                      msg="and the pinned store's projection must be placed")

    def test_the_claim_scopes_it_too(self) -> None:
        """The register is canonical: prose may not be more precise than the
        claim it projects."""
        text = " ".join(self.claim_text().split())
        self.assertIn("For the default store the projection is .pecia/work.jsonl", text)
        self.assertIn("beside its own log", text)
        self.assertIn("nothing is written into the repository", text)

    def test_neither_artifact_states_the_path_unconditionally(self) -> None:
        """The discriminating arm: the defect was not a missing sentence but
        an unconditional one, so scoping added elsewhere would not fix it.
        The pre-fix README read '`.pecia/work.jsonl` is a **generated
        projection** of that log' with no store named."""
        for name, text in (("README", " ".join(self.storage_prose().split())),
                           ("claims.yaml", " ".join(self.claim_text().split()))):
            with self.subTest(artifact=name):
                self.assertNotIn(
                    "`.pecia/work.jsonl` is a **generated projection**", text,
                    msg=f"{name} states the path unconditionally")
                self.assertNotIn(".pecia/work.jsonl is a generated projection",
                                 text,
                                 msg=f"{name} states the path unconditionally")


if __name__ == "__main__":
    unittest.main()
