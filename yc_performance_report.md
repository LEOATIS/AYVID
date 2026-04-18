# AYVID Performance Report

*Generated: 2026-04-16 20:14 UTC*

## Executive Summary

| Metric | Value |
|--------|-------|
| Period | 2018-06-01 → 2022-06-30 (5 scenarios) |
| Initial Equity | $1,000,000 |
| Final Equity | $1,055,793 |
| Total Return | +5.58% |
| Annualized Return | +18.36% |
| Sharpe Ratio | 0.24 |
| Max Drawdown | 17.4% |
| Win Rate | 43% |
| Profit Factor | 1.21 |
| Total Trades | 509 (F&O: 217, Equity: 292) |

## Risk Metrics

| Metric | Value |
|--------|-------|
| Sortino Ratio | 1.38 |
| Calmar Ratio | 4.08 |
| Max Consecutive Losses | 24 |
| Max Consecutive Wins | 13 |
| VaR (5%, daily) | -1.956% |
| Best Day | +19.90% |
| Worst Day | -3.85% |

## Alpha Decomposition

| Source | PnL | Return | Contribution |
|--------|-----|--------|-------------|
| Equity | $-205,828 | -20.58% | -70% |
| F&O | $+501,040 | +50.10% | 170% |
| **Graph Intelligence** | **$+271,954** | **+27.20%** | 437 trades |

## R-Multiple Distribution

| Metric | Value |
|--------|-------|
| Count | 217 |
| Mean R | 0.79 |
| Median R | -1.00 |
| 25th / 75th | -1.00 / 1.42 |
| % Positive | 26.7% |

## Strategy Attribution

| Strategy | PnL | Trades | Win Rate |
|----------|-----|--------|----------|
| shock_directed_put_kospi | $+364,470 | 30 | 37% |
| shock_directed_put_z | $+165,106 | 19 | 47% |
| shock_directed_put_ym | $+34,618 | 18 | 28% |
| deep_value_investment | $+23,258 | 72 | 74% |
| shock_directed_put_fesx | $+11,514 | 5 | 20% |
| shock_directed_put_es | $+6,164 | 67 | 21% |
| shock_directed_put_nq | $+2,077 | 46 | 22% |
| shock_directed_put_hsi | $-82,910 | 32 | 12% |
| supply_chain_oversold_dip | $-106,696 | 80 | 44% |
| supply_chain_beneficiary | $-122,389 | 140 | 49% |

## Per-Index F&O Summary

| Index | Name | Country | PnL | W/L | Win Rate |
|-------|------|---------|-----|-----|----------|
| ^KS11 | KOSPI 200 Future | KR | $+364,470 | 11/19 | 37% |
| ^FTSE | FTSE 100 Future | GB | $+165,106 | 9/10 | 47% |
| ^DJI | E-mini Dow | US | $+34,618 | 6/12 | 33% |
| ^STOXX50E | EURO STOXX 50 Future | EU | $+11,514 | 1/4 | 20% |
| ^GSPC | E-mini S&P 500 | US | $+6,164 | 15/52 | 22% |
| ^IXIC | E-mini NASDAQ 100 | US | $+2,077 | 11/35 | 24% |
| ^HSI | Hang Seng Future | HK | $-82,910 | 5/27 | 16% |

## Aggregate Performance (All Scenarios)

| Metric | Value |
|--------|-------|
| Scenarios | 5 |
| Avg Return | +5.58% |
| Best Return | +21.78% |
| Worst Return | -5.16% |
| Avg Sharpe | 0.24 |
| Min/Max Sharpe | -1.65 / 1.78 |
| Worst Drawdown | 17.4% |
| Total Trades | 509 |
| Pooled Win Rate | 42.8% |
| Pooled Profit Factor | 1.21 |

## Per-Scenario Breakdown

| Scenario | Return% | Sharpe | Max DD% | Win Rate | Trades |
|----------|---------|--------|---------|----------|--------|
| Suez Canal Blockage | -0.23% | -1.65 | 0.7% | 40% | 5 |
| Semiconductor Shortage | +21.78% | 1.19 | 17.4% | 42% | 203 |
| Russia-Ukraine Invasion | +0.65% | 0.34 | 3.9% | 51% | 41 |
| COVID-19 Market Crash | +10.84% | 1.78 | 8.5% | 45% | 123 |
| US-China Trade War Escalation | -5.16% | -0.45 | 12.0% | 39% | 137 |

## AI-Native vs Baseline

| Scenario | AI Return | Baseline | Alpha | AI Sharpe | Base Sharpe |
|----------|-----------|----------|-------|-----------|-------------|
| suez_canal_2021 | -0.23% | -0.31% | **+0.09%** | -1.65 | -1.33 |
| semiconductor_2022 | +21.78% | -13.85% | **+35.64%** | 1.19 | -2.59 |
| russia_ukraine_2022 | +0.65% | -3.52% | **+4.17%** | 0.34 | -2.05 |
| covid_crash_2020 | +10.84% | +3.89% | **+6.95%** | 1.78 | 0.72 |
| us_china_tariff_2018 | -5.16% | -10.56% | **+5.40%** | -0.45 | -2.54 |

> AI-native strategy outperforms baseline by +10.45% return and +1.80 Sharpe on average across 5 scenarios.

## AYVID vs Buy-and-Hold

| Scenario | AYVID | Buy & Hold | Alpha | AYVID DD | B&H DD |
|----------|-------|------------|-------|----------|--------|
| Suez Canal Blockage | -0.23% | +3.38% | **-3.61%** | 0.7% | 6.8% |
| Semiconductor Shortage | +21.78% | -38.96% | **+60.74%** | 17.4% | 41.7% |
| Russia-Ukraine Invasion | +0.65% | +17.51% | **-16.86%** | 3.9% | 12.1% |
| COVID-19 Market Crash | +10.84% | -3.61% | **+14.45%** | 8.5% | 41.7% |
| US-China Trade War Escalation | -5.16% | -21.14% | **+15.98%** | 12.0% | 33.5% |

> AYVID generated +14.14% alpha over equal-weight buy-and-hold across 5 historical shock scenarios.

---
*AYVID AI-Native Quantitative Hedge Fund — v2.0*
