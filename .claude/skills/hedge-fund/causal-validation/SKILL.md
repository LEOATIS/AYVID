---
name: hedge-fund/causal-validation
description: Validate that a proposed supply-chain causal edge actually holds against observational return data. Used by the off-hot-path causal refinery and by the live mapper agent as a sanity check.
version: 1.0.0
owner: ayvid
upstream: ayvid-native
prompts:
  - system.md
  - user.md
inputs:
  - source_entity
  - target_entity
  - proposed_edge_type
  - llm_rationale
  - observed_return_correlation
  - conditional_independence_p_value
outputs:
  - EdgeValidation (verdict, confidence_update, supporting_evidence, rejection_reason)
---

# Causal edge validation

The LLM edge-proposer inserts new supply-chain edges into Neo4j at a
baseline confidence of 0.20. The weekly refinery runs conditional-
independence tests on observational returns and asks this skill to
adjudicate whether the data supports the proposed edge.

This is the gate that prevents the LLM from smuggling fabricated
relationships into the live traversal layer (which filters at
`min_edge_confidence=0.6`).

## Discipline rules

- The verdict is one of {CONFIRM, REJECT, INCONCLUSIVE}.
- CONFIRM requires (a) p-value of conditional independence < 0.05 AND
  (b) the LLM rationale must name a real-world mechanism (regulatory,
  commercial, technical) that maps to the observed correlation.
- REJECT requires either (a) p-value > 0.20 with adequate sample size,
  or (b) the LLM rationale is unfalsifiable / circular.
- INCONCLUSIVE is the legitimate verdict when sample size is < 60
  observations or the regime distribution is too skewed.
- The confidence update is bounded to ±0.15 per refinery cycle so a
  single noisy signal cannot flip an edge.
