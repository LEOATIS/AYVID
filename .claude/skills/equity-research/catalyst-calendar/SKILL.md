---
name: equity-research/catalyst-calendar
description: Build a 30-day forward catalyst calendar for the active universe — earnings, fed prints, regulatory dates, product launches, supply-chain milestones. Feeds the strategist and the pre-earnings preview.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/catalyst-calendar)
prompts:
  - system.md
  - user.md
inputs:
  - universe_tickers
  - lookback_window_days
  - lookahead_window_days
  - calendar_seed (manual operator inputs)
outputs:
  - CatalystCalendar (events: list[CatalystEvent], priority_dates)
---

# Catalyst calendar

Produces a 30-day forward-looking catalyst calendar. Sources:

1. Earnings dates (Aiera MCP, FactSet MCP, or scraped fallback).
2. Fed / ECB / BOJ / BOE meetings (hardcoded calendar).
3. Regulatory milestones (operator-seeded).
4. Product launches (operator-seeded + parsed from filings).
5. Supply-chain milestones derived from Neo4j edge metadata.

Output is consumed by:
- The strategist (which times entries away from event uncertainty when
  the position isn't an event play).
- The earnings-preview skill (which gates which tickers get a preview).
- The console UI calendar widget.

## Discipline rules

- Every event has a typed `catalyst_type` enum.
- Events have a `confidence` (operator-seeded events default 1.0;
  parsed events default 0.7).
- `priority_dates` is the ≤5 highest-impact upcoming dates ranked by
  expected vol-impact across the universe.
