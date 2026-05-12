---
name: equity-research/idea-generation
description: Surface long/short ideas from the current information set — universe scan, recent shocks, supply-chain edge updates, screener output. Output is a ranked list of candidate trades for the strategist to evaluate.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/idea-generation)
prompts:
  - system.md
  - user.md
inputs:
  - universe_screener_output
  - recent_shocks (last 7d)
  - recent_edge_updates (Neo4j refinery output)
  - active_positions (so we don't repropose them)
  - max_ideas
outputs:
  - IdeaList (ranked: list[CandidateIdea], rationale_per_idea)
---

# Idea generation

Daily, this skill surfaces a ranked list of candidate trade ideas for
the strategist to evaluate. It is NOT a sizer, NOT a thesis builder,
NOT a debater. It only proposes.

Inputs come from:
1. The multi-factor screener output (`SCREEN_TOP_N` per `shared/config.py`).
2. Last-7d shocks the continuous ingestion worker recorded.
3. The weekly refinery's edge-confidence updates.
4. Active positions list (to avoid reproposing).

Outputs feed into the strategist node, which decides which ideas
actually get a thesis run.

## Discipline rules

- `max_ideas` is a hard cap (default 10).
- Each idea must name at least one input source it was triggered by.
- No idea proposes a ticker that's already an active position.
- Rank ideas by expected R-conviction product, not by sentiment heat.
