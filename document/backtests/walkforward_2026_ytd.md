# AYVID Walk-Forward Out-of-Sample Report

*Generated: 2026-04-20 17:47 UTC*
*Min conviction filter: 0.55*

Out-of-sample sweep across the entire historical span. Every window is treated identically — no cherry-picked shocks, no in-sample overfitting. PnL is post-cost using the IBKR Tiered transaction model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2026-01-01 → 2026-04-20 |
| Window length | 2 months |
| Step | 1 months |
| Initial equity per window | $1,000,000 |
| Windows | 3 valid / 3 total |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per window | -0.28% |
| Median return per window | -0.83% |
| Std dev of returns | 1.57% |
| 10th / 25th / 75th / 90th pct | -1.36% / -1.16% / +0.33% / +1.03% |
| % positive windows | 33.3% |
| Avg Sharpe | -0.11 |
| Median Sharpe | -0.25 |
| % Sharpe > 1.0 | 0.0% |
| Worst single-window drawdown | 5.61% |
| Avg drawdown | 5.16% |
| Pooled trades | 304 |
| Pooled win rate | 53.3% |
| Avg cost drag per window | nan% |

## Compounded Aggregate (chained windows)

| Metric | Value |
|--------|-------|
| Total compounded return | -0.85% |
| CAGR | -2.81% |

## Per-Window Results

| Window | Return | Sharpe | Max DD | Win Rate | Trades | Cost Drag |
|--------|--------|--------|--------|----------|--------|-----------|
| 2026-01-01 → 2026-03-01 | -1.49% | -0.87 | 4.45% | 60% | 81 | 0.214% |
| 2026-02-01 → 2026-04-01 | -0.83% | -0.25 | 5.42% | 52% | 134 | nan% |
| 2026-03-01 → 2026-04-20 | +1.49% | 0.79 | 5.61% | 49% | 89 | 0.226% |

---
*AYVID Walk-Forward — out-of-sample, post-cost*
