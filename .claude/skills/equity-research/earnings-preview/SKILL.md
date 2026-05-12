---
name: equity-research/earnings-preview
description: Build a pre-earnings positioning note 48-72h before the print — consensus, whisper, IV-implied move, key swing items, recommended pre-positioning (or stay flat).
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/earnings-preview)
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - reporting_date
  - consensus_estimates
  - whisper_numbers
  - iv_implied_move_pct
  - last_4_qtr_actual_vs_consensus
  - prior_setup_notes
outputs:
  - EarningsPreview (positioning_recommendation, swing_items, iv_rich_or_cheap, invalidation_line)
data_sources:
  - mcp:factset (consensus + IV)
  - mcp:aiera (prior-quarter transcripts for tone)
  - mcp:sp_global (whisper + analyst dispersion)
---

# Earnings preview (AYVID profile)

48-72h before the print, the strategist asks for a preview note on
each ticker that:

1. has a Neo4j edge of confidence ≥ 0.6 to a position currently held, OR
2. is itself a held position, OR
3. is on the active watchlist and has IV-implied move > 5%.

The output recommends pre-positioning (long-vol, short-vol, directional,
flat) and a price-based `invalidation_line` that the orchestrator pins
to any pre-earnings trade taken on the recommendation.

## Discipline rules

- If `iv_implied_move > last 4qtr 75th pct realized move`, IV is RICH —
  default recommendation is short-vol (iron condor / credit spread)
  unless there's a specific catalyst justifying the premium.
- If `iv_implied_move < last 4qtr 25th pct realized move`, IV is CHEAP —
  long-vol candidate (long straddle / long strangle).
- Directional pre-positioning requires conviction ≥ 0.65 and a named
  swing item.
- `swing_items` ≤ 3, each named in terms of a specific line item or
  guidance segment.
