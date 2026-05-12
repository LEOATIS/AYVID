---
name: equity-research/model-update
description: Update a fundamental model (revenue, margins, capex assumptions) given a new earnings print, guidance change, or material announcement. Output is the assumption delta and the resulting fair-value range.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/model-update)
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - prior_model
  - trigger_event
  - new_data_points
outputs:
  - ModelDelta (assumption_changes, new_fair_value_range, conviction_change)
---

# Model update

When earnings, guidance, or a material announcement lands, this skill
updates the per-ticker fundamental model. It is the lighter-weight
sibling to a full DCF — it changes assumptions, not the whole model.

Used by:
- The analyst agent (which uses the new fair-value range as a sanity
  check on shock-driven thesis pricing).
- The thesis tracker (which gets a fresh fair-value to compare against
  current price).

## Discipline rules

- Each assumption change MUST cite the data point that drove it.
- Fair-value range is a (low, mid, high) tuple — never a single number.
- Conviction change is bounded ±0.15 per update.
