---
name: financial-analysis/comps-analysis
description: Build a peer-comps table — multiples (P/E, EV/EBITDA, EV/Sales), growth, margins, leverage. Used to cross-check fair value from the DCF and to surface mis-pricings within a sector for the idea-generation skill.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (financial-analysis/comps-analysis)
prompts:
  - system.md
  - user.md
inputs:
  - target_ticker
  - peer_universe
  - multiples_to_include
  - margin_metrics_to_include
outputs:
  - CompsOutput (peer_table, target_position, implied_fair_value_range, outliers_flagged)
---

# Comps analysis

Cross-check fair value via peer multiples. Two consumers:

1. The analyst (which compares DCF fair value vs. comps-implied fair
   value; large divergence reduces conviction).
2. The idea-generation skill (which scans for sector-relative
   mis-pricings — a peer trading at the 90th pct on EV/EBITDA when
   growth + margins are at the 30th pct is a candidate short).

## Discipline rules

- Peer universe ≥ 4 to be statistically usable. Below, return a
  warning and a wider implied range.
- Implied fair-value range comes from applying the peer median and
  inter-quartile range of the chosen multiple to the target's metric.
- Outliers (target metric > 2 IQR from peer median) flagged
  explicitly.
