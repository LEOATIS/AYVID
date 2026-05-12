---
name: financial-analysis/dcf-model
description: Build a simple DCF as a fundamental sanity check on shock-driven theses — inputs are revenue trajectory, margin assumptions, WACC, terminal growth; output is a fair-value range that AYVID's analyst agent compares against the shock-driven entry price.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (financial-analysis/dcf-model)
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - revenue_history (5y)
  - revenue_forecast (5y)
  - margin_assumptions
  - wacc
  - terminal_growth
  - shares_outstanding
  - net_debt
outputs:
  - DCFOutput (enterprise_value, equity_value, fair_value_per_share_range, sensitivity_table)
---

# DCF model (fundamental anchor)

AYVID's primary edge is shock-driven and causal. But every shock thesis
benefits from a fundamental sanity check: if the thesis implies fair
value is 60% above current price, but the DCF range is anchored 20%
below, the position should be sized down.

This skill is the lightweight anchor — not a full sell-side DCF. It's
a 5-year revenue, margin, WACC, terminal-growth model. The output is
a fair-value range, not a point estimate. The analyst agent compares
that range to the shock-driven thesis entry price and adjusts
conviction.

## Discipline rules

- Always emit a range (low, mid, high) via ±100bp WACC and
  ±50bp terminal growth.
- Sensitivity table is mandatory: WACC × terminal growth grid.
- If revenue growth assumption > 95th percentile of sector historical,
  flag it explicitly and reduce conviction.
- Mid-case fair value is what the analyst compares to entry price.
