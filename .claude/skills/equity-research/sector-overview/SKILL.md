---
name: equity-research/sector-overview
description: Produce a one-page sector overview — leaders/laggards, multiple compression/expansion, supply-chain stress map, sector-specific catalysts. Used by the strategist to choose between asset-class allocations.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/sector-overview)
prompts:
  - system.md
  - user.md
inputs:
  - sector_code
  - constituents
  - relative_strength_1m
  - multiple_band_1y
  - supply_chain_stress_indicators
  - sector_specific_catalysts
outputs:
  - SectorOverview (snapshot, leaders, laggards, multiple_status, stress_map, key_catalysts)
---

# Sector overview

Weekly, this skill produces a one-page sector overview for each of
AYVID's tracked sectors. The strategist uses it to choose between
asset-class allocations and to detect when a sector's supply-chain
stress is rising before price has rerated.

Inputs come from:
- The multi-factor screener (constituents + relative strength).
- Neo4j (supply-chain stress: edges where confidence has dropped >15%
  in the last 30d).
- The catalyst calendar.

## Discipline rules

- Leaders and laggards are named (≤5 each).
- Multiple status is one of {EXPANDING, STABLE, COMPRESSING} with
  range numbers.
- Stress map names ≤5 supply-chain stress points, each with the edge
  type and the confidence-drop magnitude.
