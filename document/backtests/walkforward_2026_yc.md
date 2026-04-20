# AYVID Walk-Forward Out-of-Sample Report

*Generated: 2026-04-20 18:46 UTC*
*Min conviction filter: 0.55*

Out-of-sample sweep across the entire historical span. Every window is treated identically — no cherry-picked shocks, no in-sample overfitting. PnL is post-cost using the IBKR Tiered transaction model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2026-01-01 → 2026-04-20 |
| Window length | 1 months |
| Step | 1 months |
| Initial equity per window | $1,000,000 |
| Windows | 3 valid / 3 total |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per window | -7.63% |
| Median return per window | -5.17% |
| Std dev of returns | 7.57% |
| 10th / 25th / 75th / 90th pct | -13.93% / -10.64% / -3.38% / -2.31% |
| % positive windows | 0.0% |
| Avg Sharpe | -4.67 |
| Median Sharpe | -4.15 |
| % Sharpe > 1.0 | 0.0% |
| Worst single-window drawdown | 16.45% |
| Avg drawdown | 8.37% |
| Pooled trades | 303 |
| Pooled win rate | 43.9% |
| Avg cost drag per window | 1.345% |

## Compounded Aggregate (chained windows)

| Metric | Value |
|--------|-------|
| Total compounded return | -21.72% |
| CAGR | -55.98% |

## Per-Window Results

| Window | Return | Sharpe | Max DD | Win Rate | Trades | Cost Drag |
|--------|--------|--------|--------|----------|--------|-----------|
| 2026-01-01 → 2026-02-01 | -16.12% | -8.50 | 16.45% | 36% | 96 | 2.678% |
| 2026-02-01 → 2026-03-01 | -5.17% | -4.15 | 5.45% | 38% | 64 | 0.475% |
| 2026-03-01 → 2026-04-01 | -1.59% | -1.36 | 3.21% | 52% | 143 | 0.882% |

---
*AYVID Walk-Forward — out-of-sample, post-cost*
