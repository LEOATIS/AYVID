# AYVID Walk-Forward Out-of-Sample Report

*Generated: 2026-04-21 19:30 UTC*
*Min conviction filter: 0.55*

Out-of-sample sweep across the entire historical span. Every window is treated identically — no cherry-picked shocks, no in-sample overfitting. PnL is post-cost using the IBKR Tiered transaction model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2026-01-01 → 2026-04-21 |
| Window length | 1 months |
| Step | 1 months |
| Initial equity per window | $1,000,000 |
| Windows | 4 valid / 4 total |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per window | -1.09% |
| Median return per window | -0.81% |
| Std dev of returns | 1.19% |
| 10th / 25th / 75th / 90th pct | -2.24% / -1.50% / -0.41% / -0.16% |
| % positive windows | 0.0% |
| Avg Sharpe | 0.00 |
| Median Sharpe | 0.00 |
| % Sharpe > 1.0 | 0.0% |
| Worst single-window drawdown | 0.00% |
| Avg drawdown | 0.00% |
| Pooled trades | 25 |
| Pooled win rate | 28.0% |
| Avg cost drag per window | 0.272% |

## Compounded Aggregate (chained windows)

| Metric | Value |
|--------|-------|
| Total compounded return | -4.32% |
| CAGR | -13.63% |

## Per-Window Results

| Window | Return | Sharpe | Max DD | Win Rate | Trades | Cost Drag |
|--------|--------|--------|--------|----------|--------|-----------|
| 2026-01-01 → 2026-02-01 | -2.74% | 0.00 | 0.00% | 31% | 16 | 0.829% |
| 2026-02-01 → 2026-03-01 | -1.08% | 0.00 | 0.00% | 25% | 8 | 0.247% |
| 2026-03-01 → 2026-04-01 | -0.54% | 0.00 | 0.00% | 0% | 1 | 0.014% |
| 2026-04-01 → 2026-04-21 | +0.00% | 0.00 | 0.00% | 0% | 0 | 0.000% |

---
*AYVID Walk-Forward — out-of-sample, post-cost*
