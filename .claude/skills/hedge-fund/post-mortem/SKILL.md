---
name: hedge-fund/post-mortem
description: Generate the post-trade rationale entry for the outcomes log — what happened, why, and what (if anything) the priors file should learn. Used by the auditor agent on every closed trade.
version: 1.0.0
owner: ayvid
upstream: ayvid-native
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - strategy_name
  - regime
  - entry_thesis
  - invalidation_level
  - actual_exit_reason
  - realized_R
  - holding_days
  - cost_drag
outputs:
  - TradePostMortem (verdict, what_worked, what_failed, priors_update_suggestion)
---

# Post-trade rationale

Every closed trade — backtest or live — lands in
`calibration/trade_outcomes.jsonl`. This skill produces the
human-readable rationale entry that accompanies the row.

It is the input to the operator's weekly review and (via the priors
rebuilder) the LLM-suggested input to the auto-block / auto-unblock
decisions.

## Discipline rules

- The verdict is one of {THESIS_PLAYED_OUT, THESIS_FAILED, NOISE,
  COST_DRAG_DOMINATED, INVALIDATION_TRIPPED, TIMED_OUT}.
- The `priors_update_suggestion` is advisory only — it cannot directly
  mutate the priors file. The rebuilder reads it as one signal among
  many (n ≥ 5, sharpe ≤ −0.5 is the binding auto-block rule).
- Specific numbers > flowery prose. Cite the actual realized_R, the
  cost_drag, the holding_days.
- If the verdict is NOISE (small win/loss inside the cost envelope),
  explicitly say so — do not over-fit a narrative.
