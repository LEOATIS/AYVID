---
name: hedge-fund/bull-bear-debate
description: Bull/Bear/Facilitator trio for hedge-fund research debate — generates opposing theses, then adjudicates them against empirical priors. Used by AYVID's researchers LangGraph node.
version: 1.0.0
owner: ayvid
upstream: ayvid-native
prompts:
  - bull_system.md
  - bull_user.md
  - bear_system.md
  - bear_user.md
  - facilitator_system.md
  - facilitator_user.md
inputs:
  - ticker
  - mapper_thesis
  - mapper_direction
  - regime_block
  - shock_context
  - priors_block
  - opposing_thesis
outputs:
  - BullArgument
  - BearArgument
  - ResearcherConsensus
---

# Bull/Bear/Facilitator Debate

A three-role adversarial-research skill modelled on a hedge-fund PM's
internal pre-trade debate. Two advocates (bull, bear) build the strongest
opposing cases for a thesis the mapper has surfaced; a senior-PM
facilitator adjudicates against empirical priors and returns a single
actionable consensus.

Used by `graph_intelligence/agents/researchers/nodes.py`. Prompts are
re-exported through `shared/skills_loader.py` so the Python module is a
shim and this markdown bundle is the single source of truth.

## Roles

- **Bull advocate** — surfaces concrete catalysts, pricing dislocations,
  asymmetric payoffs; directly addresses the bear's opposing thesis.
- **Bear advocate** — surfaces crowded positioning, valuation ceilings,
  regulatory tail, macro rollover, technical resistance; directly
  addresses the bull's opposing thesis.
- **Facilitator** — weighs both sides against (1) empirical priors for
  this (strategy, regime), (2) severity/testability of invalidation
  levels, (3) whether each rebuttal landed. Output: dominant side,
  direction, conviction, margin.

## Discipline rules

- Cite specific figures, dates, or price levels where possible.
- Never invent data; missing data → name it and reduce conviction.
- `invalidation_level` MUST be a testable observation. Vague
  invalidations are rejected by the facilitator.
- Close debates legitimately produce `dominant_side=NEITHER` with low
  margin — the facilitator does not have to pick a winner.
