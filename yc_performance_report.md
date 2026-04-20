# AYVID Performance Report

*Generated: 2026-04-20 20:45 UTC*

## Executive Summary

| Metric | Value |
|--------|-------|
| Period | 2018-06-01 → 2025-03-31 (20 scenarios) |
| Initial Equity | $1,000,000 |
| Final Equity | $1,013,679 |
| Total Return | +1.37% |
| Annualized Return | +3.81% |
| Sharpe Ratio | 0.56 |
| Max Drawdown | 9.0% |
| Win Rate | 54% |
| Profit Factor | 1.36 |
| Total Trades | 468 (F&O: 49, Equity: 419) |

## Risk Metrics

| Metric | Value |
|--------|-------|
| Sortino Ratio | 3.67 |
| Calmar Ratio | 24.21 |
| Max Consecutive Losses | 9 |
| Max Consecutive Wins | 15 |
| VaR (5%, daily) | -1.841% |
| Best Day | +18.37% |
| Worst Day | -2.86% |

## Alpha Decomposition

| Source | PnL | Return | Contribution |
|--------|-----|--------|-------------|
| Equity | $+2,232 | +0.22% | 1% |
| F&O | $+242,277 | +24.23% | 99% |
| **Graph Intelligence** | **$+272,316** | **+27.23%** | 223 trades |

## R-Multiple Distribution

| Metric | Value |
|--------|-------|
| Count | 49 |
| Mean R | 1.62 |
| Median R | -1.00 |
| 25th / 75th | -1.00 / -1.00 |
| % Positive | 24.5% |

## Strategy Attribution

| Strategy | PnL | Trades | Win Rate |
|----------|-----|--------|----------|
| shock_directed_put_z | $+244,948 | 3 | 67% |
| supply_chain_beneficiary | $+64,383 | 127 | 59% |
| shock_directed_put_es | $+22,989 | 4 | 0% |
| shock_directed_call_nq | $+177 | 10 | 20% |
| idx_decline_put_hsi | $-1,788 | 2 | 50% |
| shock_directed_put_nq | $-4,833 | 6 | 17% |
| shock_directed_call_es | $-19,216 | 24 | 17% |
| deep_value_investment | $-26,019 | 243 | 54% |
| supply_chain_oversold_dip | $-36,132 | 49 | 47% |

## Per-Index F&O Summary

| Index | Name | Country | PnL | W/L | Win Rate |
|-------|------|---------|-----|-----|----------|
| ^FTSE | FTSE 100 Future | GB | $+244,948 | 2/1 | 67% |
| ^GSPC | E-mini S&P 500 | US | $+3,773 | 6/22 | 21% |
| ^HSI | Hang Seng Future | HK | $-1,788 | 1/1 | 50% |
| ^IXIC | E-mini NASDAQ 100 | US | $-4,655 | 3/13 | 19% |

## Aggregate Performance (All Scenarios)

| Metric | Value |
|--------|-------|
| Scenarios | 20 |
| Avg Return | +1.37% |
| Best Return | +24.01% |
| Worst Return | -8.39% |
| Avg Sharpe | 0.56 |
| Min/Max Sharpe | -1.42 / 4.76 |
| Worst Drawdown | 9.0% |
| Total Trades | 468 |
| Pooled Win Rate | 54.3% |
| Pooled Profit Factor | 1.36 |

## Per-Scenario Breakdown

| Scenario | Return% | Sharpe | Max DD% | Win Rate | Trades |
|----------|---------|--------|---------|----------|--------|
| US-China Trade War Escalation | -2.74% | -1.42 | 3.6% | 55% | 31 |
| Q4 2018 Powell Tightening Selloff | +0.44% | 0.38 | 1.6% | 57% | 14 |
| Boeing 737 MAX Grounding | +0.67% | 1.26 | 0.9% | 40% | 5 |
| COVID-19 Market Crash | -4.29% | -1.19 | 9.0% | 56% | 25 |
| Post-COVID Tech Megacap Rally | +3.16% | 0.96 | 2.2% | 62% | 48 |
| Suez Canal Blockage | +0.81% | 4.76 | 0.1% | 100% | 2 |
| ARK / Hyper-Growth Selloff | +0.41% | 0.18 | 2.4% | 56% | 16 |
| China Big-Tech Regulatory Crackdown | +3.91% | 1.16 | 2.5% | 65% | 31 |
| 2022 Fed Hike Cycle / Tech Bear | -8.39% | -1.29 | 8.4% | 38% | 66 |
| Russia-Ukraine Invasion | -0.18% | -0.41 | 0.6% | 60% | 5 |
| Semiconductor Inventory Correction | -1.90% | -0.37 | 6.1% | 52% | 56 |
| UK Mini-Budget / Gilt Crisis | -0.09% | -0.58 | 0.4% | 50% | 2 |
| ChatGPT / Generative-AI Rally | +5.50% | 1.49 | 3.0% | 60% | 62 |
| SVB / Regional Bank Crisis | +24.01% | 3.03 | 0.7% | 50% | 8 |
| Hamas-Israel War | -0.01% | -0.03 | 0.2% | 50% | 2 |
| Red Sea / Houthi Shipping Attacks | +0.86% | 0.85 | 1.4% | 78% | 9 |
| NVIDIA Q4 Blowout / AI Capex Phase 2 | +2.14% | 0.76 | 2.8% | 58% | 53 |
| Yen Carry Trade Unwind | +0.34% | 0.32 | 3.4% | 56% | 9 |
| Trump 2.0 Election Rally | -0.14% | -0.17 | 1.2% | 33% | 9 |
| DeepSeek R1 / China AI Shock | +2.84% | 1.62 | 1.4% | 40% | 15 |

## AI-Native vs Baseline

| Scenario | AI Return | Baseline | Alpha | AI Sharpe | Base Sharpe |
|----------|-----------|----------|-------|-----------|-------------|
| us_china_tariff_2018 | -2.74% | -2.00% | **-0.74%** | -1.42 | -0.84 |
| powell_pivot_q4_2018 | +0.44% | +0.03% | **+0.41%** | 0.38 | 0.05 |
| boeing_737max_2019 | +0.67% | +0.72% | **-0.05%** | 1.26 | 1.14 |
| covid_crash_2020 | -4.29% | -2.49% | **-1.80%** | -1.19 | -0.62 |
| covid_recovery_2020 | +3.16% | +9.73% | **-6.57%** | 0.96 | 1.75 |
| suez_canal_2021 | +0.81% | +1.43% | **-0.62%** | 4.76 | 3.55 |
| ark_meltdown_2021 | +0.41% | -0.74% | **+1.15%** | 0.18 | -0.29 |
| china_tech_crackdown_2021 | +3.91% | -0.76% | **+4.67%** | 1.16 | -0.16 |
| fed_hike_cycle_2022 | -8.39% | -11.73% | **+3.33%** | -1.29 | -1.64 |
| russia_ukraine_2022 | -0.18% | -0.57% | **+0.39%** | -0.41 | -0.88 |
| semiconductor_2022 | -1.90% | -2.39% | **+0.49%** | -0.37 | -0.32 |
| uk_mini_budget_2022 | -0.09% | +0.07% | **-0.16%** | -0.58 | 0.35 |
| chatgpt_ai_rally_2023 | +5.50% | +4.82% | **+0.67%** | 1.49 | 1.15 |
| svb_collapse_2023 | +24.01% | -0.82% | **+24.83%** | 3.03 | -1.23 |
| hamas_israel_2023 | -0.01% | -0.35% | **+0.34%** | -0.03 | -2.07 |
| red_sea_2023 | +0.86% | +2.90% | **-2.04%** | 0.85 | 1.86 |
| nvidia_blowout_2024 | +2.14% | +7.01% | **-4.87%** | 0.76 | 2.22 |
| yen_carry_unwind_2024 | +0.34% | -0.58% | **+0.93%** | 0.32 | -0.42 |
| trump_election_rally_2024 | -0.14% | +0.61% | **-0.74%** | -0.17 | 0.79 |
| deepseek_jan_2025 | +2.84% | -0.48% | **+3.32%** | 1.62 | -0.37 |

> AI-native strategy outperforms baseline by +1.15% return and +0.36 Sharpe on average across 20 scenarios.

## AYVID vs Buy-and-Hold

| Scenario | AYVID | Buy & Hold | Alpha | AYVID DD | B&H DD |
|----------|-------|------------|-------|----------|--------|
| US-China Trade War Escalation | -2.74% | -19.85% | **+17.11%** | 3.6% | 31.9% |
| Q4 2018 Powell Tightening Selloff | +0.44% | -25.78% | **+26.22%** | 1.6% | 31.7% |
| Boeing 737 MAX Grounding | +0.67% | +2.46% | **-1.79%** | 0.9% | 15.6% |
| COVID-19 Market Crash | -4.29% | -15.44% | **+11.15%** | 9.0% | 38.9% |
| Post-COVID Tech Megacap Rally | +3.16% | +59.88% | **-56.72%** | 2.2% | 15.9% |
| Suez Canal Blockage | +0.81% | +13.81% | **-13.00%** | 0.1% | 7.2% |
| ARK / Hyper-Growth Selloff | +0.41% | -8.63% | **+9.04%** | 2.4% | 24.9% |
| China Big-Tech Regulatory Crackdown | +3.91% | -26.73% | **+30.64%** | 2.5% | 34.1% |
| 2022 Fed Hike Cycle / Tech Bear | -8.39% | -47.51% | **+39.12%** | 8.4% | 49.6% |
| Russia-Ukraine Invasion | -0.18% | +15.43% | **-15.61%** | 0.6% | 13.2% |
| Semiconductor Inventory Correction | -1.90% | -16.02% | **+14.12%** | 6.1% | 34.2% |
| UK Mini-Budget / Gilt Crisis | -0.09% | -1.79% | **+1.70%** | 0.4% | 8.8% |
| ChatGPT / Generative-AI Rally | +5.50% | +49.96% | **-44.46%** | 3.0% | 12.4% |
| SVB / Regional Bank Crisis | +24.01% | -7.60% | **+31.61%** | 0.7% | 15.4% |
| Hamas-Israel War | -0.01% | -2.98% | **+2.97%** | 0.2% | 11.2% |
| Red Sea / Houthi Shipping Attacks | +0.86% | +18.80% | **-17.94%** | 1.4% | 17.4% |
| NVIDIA Q4 Blowout / AI Capex Phase 2 | +2.14% | +36.98% | **-34.84%** | 2.8% | 14.9% |
| Yen Carry Trade Unwind | +0.34% | -11.73% | **+12.07%** | 3.4% | 24.6% |
| Trump 2.0 Election Rally | -0.14% | +16.93% | **-17.07%** | 1.2% | 14.1% |
| DeepSeek R1 / China AI Shock | +2.84% | -5.15% | **+7.99%** | 1.4% | 19.1% |

> AYVID generated +0.12% alpha over equal-weight buy-and-hold across 20 historical shock scenarios.

---
*AYVID AI-Native Quantitative Hedge Fund — v2.0*
