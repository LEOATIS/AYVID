"""Tests for ``shared.skills_loader`` and the researchers-prompt shim.

Two responsibilities:

1. The loader can find and parse every SKILL.md under .claude/skills/.
2. The researcher agent's BULL/BEAR/FACILITATOR system + user templates
   are byte-identical to the canonical strings (regression guard against
   accidental drift in either the markdown or the Python shim).
"""

from __future__ import annotations

import pytest

from shared import skills_loader

# The 15 skills we expect to exist in the repo today. Adding a new skill?
# Add its name here so an accidental delete is caught.
_EXPECTED_SKILLS = frozenset(
    {
        # AYVID-native hedge-fund bundle
        "hedge-fund/bull-bear-debate",
        "hedge-fund/shock-thesis",
        "hedge-fund/causal-validation",
        "hedge-fund/kelly-sizing",
        "hedge-fund/post-mortem",
        # Equity-research bundle (adapted from anthropics/financial-services)
        "equity-research/earnings-analysis",
        "equity-research/earnings-preview",
        "equity-research/thesis-tracker",
        "equity-research/catalyst-calendar",
        "equity-research/idea-generation",
        "equity-research/model-update",
        "equity-research/morning-note",
        "equity-research/sector-overview",
        # Financial-analysis core
        "financial-analysis/dcf-model",
        "financial-analysis/comps-analysis",
    }
)


@pytest.fixture(autouse=True)
def _fresh_index() -> None:
    skills_loader.refresh_index()


def test_every_expected_skill_loads() -> None:
    loaded = set(skills_loader.list_skills())
    missing = _EXPECTED_SKILLS - loaded
    assert not missing, f"Missing skill bundles: {sorted(missing)}"


def test_skill_frontmatter_has_required_fields() -> None:
    for name in _EXPECTED_SKILLS:
        skill = skills_loader.load_skill(name)
        assert skill.name == name, f"name mismatch for {name}"
        assert skill.description, f"{name} missing description"
        assert skill.version, f"{name} missing version"


def test_bull_bear_debate_exposes_six_prompts() -> None:
    skill = skills_loader.load_skill("hedge-fund/bull-bear-debate")
    expected = {
        "bull_system",
        "bull_user",
        "bear_system",
        "bear_user",
        "facilitator_system",
        "facilitator_user",
    }
    assert set(skill.prompts) == expected


def test_load_skill_missing_raises() -> None:
    with pytest.raises(KeyError):
        skills_loader.load_skill("does-not-exist/nope")


def test_skill_prompt_missing_raises() -> None:
    skill = skills_loader.load_skill("hedge-fund/bull-bear-debate")
    with pytest.raises(KeyError):
        skill.prompt("not_a_real_prompt")


# ── Byte-identicality regression guard ─────────────────────────────
#
# These canonical strings are the pre-loader-migration values of the
# researchers' Python constants. They are duplicated here on purpose:
# if either (a) the markdown drifts or (b) the loader changes
# whitespace handling, the test fails. The test exists to prevent a
# silent change in the prompts that the researcher agent runs in
# production.


_CANONICAL_BULL_SYSTEM = """\
You are the Bull Advocate in a hedge-fund research debate.

Role
────
Build the strongest possible case for entering a LONG position on the
mapper's thesis. You are NOT a cheerleader — your job is to surface
the concrete catalysts, pricing dislocations, and asymmetric payoffs
that a rigorous PM would cite. You also directly address the strongest
counter-argument (supplied as ``opposing_thesis``) with concession
where valid and refutation where not.

Discipline
──────────
- Cite specific figures, dates, or price levels where possible.
- Never make up data. If you lack a data point, name it as missing
  and adjust your conviction downwards.
- Your ``invalidation_level`` MUST be a testable observation (a price
  move, volume threshold, macro print, or earnings number). Vague
  invalidations like "sentiment sours" are rejected by the facilitator.

Output STRICT JSON matching the ``BullArgument`` schema.
"""

_CANONICAL_BULL_USER = """\
Ticker: {ticker}
Mapper thesis: {mapper_thesis}
Proposed direction: {mapper_direction}
Regime snapshot: {regime_block}
Shock / causal intel: {shock_context}
Empirical priors for this (strategy, regime): {priors_block}
Opposing thesis (bear): {opposing_thesis}

Build your strongest LONG case. Output JSON only.
"""

_CANONICAL_BEAR_SYSTEM = """\
You are the Bear Advocate in a hedge-fund research debate.

Role
────
Build the strongest possible case AGAINST the mapper's LONG thesis —
either by arguing for a SHORT, or by arguing the risk/reward does
not warrant a trade. You are NOT a permabear — your job is to be the
scepticism a PM needs before committing capital.

Discipline
──────────
- Cite specific risks: crowded positioning, valuation ceilings,
  regulatory tail, macro rollover, technical resistance.
- Address the bull case directly (supplied as ``opposing_thesis``).
- ``invalidation_level`` MUST be testable (e.g. "stock reclaims 200-DMA
  on >1.5× ADV volume"). Vague invalidations are rejected.

Output STRICT JSON matching the ``BearArgument`` schema.
"""

_CANONICAL_BEAR_USER = """\
Ticker: {ticker}
Mapper thesis: {mapper_thesis}
Proposed direction: {mapper_direction}
Regime snapshot: {regime_block}
Shock / causal intel: {shock_context}
Empirical priors for this (strategy, regime): {priors_block}
Opposing thesis (bull): {opposing_thesis}

Build your strongest counter-case. Output JSON only.
"""

_CANONICAL_FACILITATOR_SYSTEM = """\
You are the Debate Facilitator at a hedge fund — a senior PM whose job
is to synthesise a bull/bear debate into a single actionable consensus.

Inputs
──────
You receive the full transcript of a 2-round bull-vs-bear debate plus
the live empirical priors (historical Sharpe per regime for this
strategy). Your role is explicitly NOT to side with one advocate
because they argued better; your role is to weigh their arguments
against:
  1. the empirical priors for this (strategy, regime),
  2. the severity / testability of each side's invalidation levels,
  3. whether the bear's rebuttal successfully refuted the bull, or
     vice versa.

Discipline
──────────
- If both sides made valid points and neither clearly dominated,
  output ``dominant_side=NEITHER``, ``direction=HOLD``, and flag a
  low ``margin`` (< 0.3). This is a legitimate outcome.
- The ``conviction`` field is bounded [0, 1] and represents your
  confidence that the dominant side is right — after, not before,
  the rebuttals. A 0.55 is perfectly acceptable.
- The ``margin`` field answers "how clear was the victory" on a 0-1
  scale. Close debates get low margins regardless of which side won.

Output STRICT JSON matching the ``ResearcherConsensus`` schema.
"""

_CANONICAL_FACILITATOR_USER = """\
Ticker: {ticker}
Mapper's original thesis: {mapper_thesis}
Proposed direction: {mapper_direction}
Regime: {regime_block}
Priors: {priors_block}

═══ DEBATE TRANSCRIPT ═══
Round 1 — Bull:
{r1_bull}

Round 1 — Bear:
{r1_bear}

Round 2 — Bull (with rebuttal):
{r2_bull}

Round 2 — Bear (with rebuttal):
{r2_bear}

═══ END TRANSCRIPT ═══

Adjudicate. Output JSON matching the ResearcherConsensus schema.
"""


def test_bull_system_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.BULL_SYSTEM == _CANONICAL_BULL_SYSTEM


def test_bull_user_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.BULL_USER_TEMPLATE == _CANONICAL_BULL_USER


def test_bear_system_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.BEAR_SYSTEM == _CANONICAL_BEAR_SYSTEM


def test_bear_user_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.BEAR_USER_TEMPLATE == _CANONICAL_BEAR_USER


def test_facilitator_system_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.FACILITATOR_SYSTEM == _CANONICAL_FACILITATOR_SYSTEM


def test_facilitator_user_byte_identical() -> None:
    from graph_intelligence.agents.researchers import prompts

    assert prompts.FACILITATOR_USER_TEMPLATE == _CANONICAL_FACILITATOR_USER
