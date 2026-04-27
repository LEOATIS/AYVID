# `calibration/` — Empirical Priors for the Supervisor LLM

This directory holds the empirical-prior artefacts that the AYVID
pipeline reads at decision time. Nothing here is hand-edited — every
file is the output of a deterministic calibration or refinery job.

## What lives here

| File | Produced by | Consumed by |
|---|---|---|
| `priors.json` | `backtesting/calibrate.py` / `ayvid data calibrate` — AND auto-rebuilt by `backtesting/trade_outcomes.rebuild_priors_from_outcomes` on every `shock-walkforward --recalibrate` run | `get_param()` in `strategy_orchestrator.py`, Supervisor LLM prompt |
| `trade_outcomes.jsonl` | `backtesting.engine.run_scenario_backtest` (backtest path) and the live runner on position close (live path) | `rebuild_priors_from_outcomes` |
| `edge_proposals.jsonl` | `data_primitives.extractors.edge_proposer` | audit / dashboard only |
| `refined_dag.json` | `graph_intelligence.causal_falsification.refine_dag` / `ayvid graph refine` | audit artefact only; live edges live in Neo4j |

## `priors.json` — strategy × regime → Sharpe-optimal parameters

### Schema

```jsonc
{
  "_generated_at":   "2026-04-21T02:08Z",
  "_min_samples":    5,
  "_schema_version": 1,
  "priors": {
    "<strategy_name>": {
      "<REGIME_KEY>": {
        "max_rsi":             <float, LLM-tunable max RSI for LONG entry>,
        "max_z":               <float, LLM-tunable max z-score for LONG entry>,
        "min_factor_score":    <float>,
        "min_expected_r":      <float>,
        "sharpe":              <per-trade Sharpe (μ/σ)>,
        "ci":                  [<lower 95%>, <upper 95%>],
        "n":                   <# trades in this cell>,
        "win_rate":            <0..1>,
        "trades_per_100bars":  <float>
      },
      "<REGIME_KEY_BLOCKED>": {
        "blocked_by_regime":   true,
        "n":                   <# trades seen>,
        "best_sharpe_seen":    <worst-best across configs; must be ≤ 0>,
        "_block_reason":       "<optional human-authored string — `Batch-*` prefix makes it immortal>"
      }
    }
  }
}
```

### Regime key format

`{TREND_REGIME}[_UP|_DOWN]__{VOLATILITY_REGIME}`

Examples:
- `STRONG_TREND_UP__HIGH_VOL`
- `STRONG_TREND_DOWN__CRISIS`
- `MEAN_REVERTING__NORMAL`
- `CHOPPY__LOW_VOL` (no direction suffix for non-trending regimes)

The direction suffix is attached only when `trend_regime in (STRONG_TREND, WEAK_TREND)`; mean-reverting and choppy markets are direction-agnostic. See `backtesting.calibrate.regime_key` for the canonical composer.

### Strategy names

The outer key is the canonical strategy identifier from `graph_intelligence.agents.strategist.schema.CANONICAL_STRATEGIES`. Examples:

- Equity: `deep_value_investment`, `supply_chain_beneficiary`, `supply_chain_oversold_dip`, `trend_investment`, `mr_equity_bounce`
- Index F&O: `idx_trend_es`, `idx_mr_oversold_call`, `idx_decline_put`, `crisis_index_put`, `shock_directed_put`, `shock_directed_call`, …

A `blocked_by_regime: true` cell means that during calibration, no parameter combination produced a positive-Sharpe result for this `(strategy, regime)` pair with at least `_min_samples` trades. The orchestrator treats these as hard-skip — the Supervisor LLM never even gets the chance to argue otherwise.

### How the pipeline reads this file

**Three-tier fallback** in `graph_intelligence.agents.strategy_orchestrator.get_param()`:

```
Tier 1  ctx.strategy_parameters[key]         — LLM Supervisor emission
Tier 2  priors.json[strategy][regime][key]   — empirical prior
Tier 3  hardcoded default                    — shipped in source
```

The Supervisor LLM receives the relevant `priors.json` cell as prompt context (rendered via `render_priors_block()`) and is instructed by its system prompt to deviate from the prior only with a cited regime-feature delta, subject to the 4× cost hurdle.

## Regenerating `priors.json`

```bash
# Fast smoke run — 16 configs × 3 scenarios, ~2 min.
ayvid data calibrate --mode quick

# Weekly refresh — 36 configs × 8 scenarios, ~25-40 min.
ayvid data calibrate --mode medium --concurrency 4

# Canonical sweep — 400 configs × 20 scenarios, overnight.
ayvid data calibrate --mode full --concurrency 6

# Single-scenario debugging.
ayvid data calibrate --mode medium \
    --scenarios svb_collapse_2023,covid_crash_2020
```

After running, the file mtime changes and the orchestrator's in-process priors cache auto-invalidates on next read — no pipeline restart required.

## Online feedback loop (post-Option-C)

In addition to the offline grid-sweep above, `priors.json` is rebuilt from live
trade outcomes whenever the walkforward is invoked with `--recalibrate`:

```bash
ayvid data shock-walkforward --recalibrate --reset-outcomes
```

Each closed trade writes one row to `calibration/trade_outcomes.jsonl`
(append-only). The rebuilder groups by `(strategy, regime_key)`, refreshes the
stats fields (`n`, `mean_r`, `sharpe`, `win_rate`), and auto-blocks any cell
that crosses `n ≥ _min_samples` AND `sharpe ≤ −0.5`. Human-authored policy
overrides (any `_block_reason` string starting with `Batch-`) are preserved
verbatim — evidence never overrides explicit policy.

## `refined_dag.json` — audit trail of graph falsification

Each `ayvid graph refine` run writes a snapshot of the refined supply-chain edges plus the LLM's rationale trail. The authoritative truth lives in Neo4j (as `r.confidence`, `r.confidence_method`, `r.confidence_p_value`, `r.confidence_updated_at` properties on each `SUPPLIES_TO` edge), and `find_hidden_victims(..., min_edge_confidence=0.6)` filters traversal on that field. This file exists purely for post-hoc inspection of what the refinery decided and why.

## Never edit these by hand

Every downstream component — the orchestrator, the Supervisor LLM prompt, the mapper's path queries — treats these artefacts as canonical. Overwriting with hand-tuned values would contaminate both the backtest baseline and any future calibration diff. If you want to override a specific cell temporarily, emit the corresponding `strategy_parameters` through the Supervisor LLM instead; that path is designed for exactly this kind of on-demand override.
