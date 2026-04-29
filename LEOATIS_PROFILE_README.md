<h1 align="center">LEOATIS</h1>

<p align="center"><i>Latent Edge. Operational Alpha. Truth In Signal.</i></p>

<p align="center">
  <img alt="Status" src="https://img.shields.io/badge/status-building-brightgreen">
  <img alt="Stack" src="https://img.shields.io/badge/stack-Python%20%7C%20LangGraph%20%7C%20Neo4j%20%7C%20FastAPI%20%7C%20C%2B%2B-blue">
  <img alt="Domain" src="https://img.shields.io/badge/domain-quant%20finance%20%2F%20AI%20agents-black">
  <img alt="Focus" src="https://img.shields.io/badge/focus-0%E2%86%921%20systems-orange">
</p>

---

## About

LEOATIS is where I build **0→1 systems at the intersection of applied AI and capital markets** — full-stack platforms that go from raw data ingestion to live execution, end to end, by one person.

I don't ship demos. I ship production-grade infrastructure with the same primitives institutional desks use: LLM agent swarms, graph databases, C++ watchdogs, real expiry calendars, real broker integrations, and real risk math.

---

## Flagship Project — **[AYVID](https://github.com/LEOATIS/AYVID)**

> **Asymmetric Yield Via Intelligent Decisions** — an AI-native quantitative hedge fund that trades two parallel tracks: index futures & options across 20 global markets, and equity investments across 50+ dynamically-screened stocks.

### The thesis in one line

**Supply chain shocks propagate through hidden dependencies. GPT-4o extracts the entities, Neo4j traces N-th order causal paths, and a 4-agent LangGraph pipeline identifies downstream victims before the market prices them in.**

### Why this is hard

- LLM agent debates are slow (seconds) but markets move in milliseconds.
- Real options trading needs real expiry calendars, real Greeks, real lot math — not toys.
- Broker reconnects, LLM outages, Neo4j timeouts all happen. The system can't die.
- Backtests lie unless you simulate the same shock-interception logic you run live.

### How AYVID solves it

| Challenge | Solution |
|---|---|
| LLM latency during market hours | **Three-tier execution**: Tier 1 pre-computes a `SignalBook` offline → Tier 2 sub-100ms fast path with zero LLM calls → Tier 3 async shock override with streaming LLM |
| Stale signals during breaking news | News-aware **shock interceptor** derives directional bias from event metadata (not lagging price), invalidates cache, writes defensive HOLDs until LLM re-confirms |
| Catastrophic option trade losses | **Hard -1R stop**, mathematically clamped. No F&O trade can lose more than 1× its initial risk. Ever. |
| Weak signal overtrading | **Dynamic conviction** (0.50–0.85) from z-score depth × RSI depth × volume × factor composite. Per-index cooldowns after losses. 50% portfolio F&O cap. |
| Process crashes leaving open positions | Independent **C++ kill switch** reads mmap at 10Hz with seqlock IPC, SIGKILLs Python on drawdown breach |
| Broker disconnects | Idempotency gate + orphan-bracket cancellation + full position-reconciliation sequence on every IB reconnect |
| External API flakiness | Tenacity exponential backoff on every external call + chaos-engineering test suite simulating LLM / IB / OpenBB / Neo4j outages |

### What's actually inside

A **modular monolith**, ~50k lines of Python, one repo, strict dependency inversion.

```
 Data Primitives      →   Graph Intelligence    →   Execution Engine
 OpenBB, Finnhub,         LangGraph 4-agent         Kelly sizing,
 SEC EDGAR, Reddit,       causal pipeline            Black-Scholes,
 GPT-4o NER,              (Strategist → Mapper       real expiry
 universe builder,        → Auditor → Supervisor),   calendars,
 tiered continuous        Bayesian multi-analyst     IB bracket orders,
 ingestion worker         debate, Neo4j + Qdrant     C++ kill switch
                          shock propagation
```

Plus: FastAPI REST+SSE backend · Typer+Rich CLI with 40 commands and a live dashboard · pytest including chaos/resilience suite · Docker Compose with Neo4j + QuestDB.

### Validated post-cost, walk-forward, with a published negative control

Every PnL number in AYVID is **post-cost** (IBKR-Tiered commissions + symbol-aware slippage via `shared/cost_model.py`) and every backtest is **walk-forward** — no cherry-picked windows, no in-sample overfit. Two backtests are published in the repo, both run on the same 2018→2025 cached OHLCV with the same cost model:

| Mode | Compounded 2018→2025 | Trades | Avg cost drag | % Sharpe > 1.0 | Best outcome |
|---|---:|---:|---:|---:|---:|
| Naive (trade every ticker every day on technicals) | **−99.98%** | 16,168 | 7.40% | 0.0% | −5.78% (90th pct) |
| **Shock-driven (graph-surfaced tickers, in-window)** | **+33.43%** | 1,392 | 0.55% | 25.0% | **+23.81% (SVB crisis)** |

Same engine, same data, same cost model — the **133 percentage-point swing** is the measured value of the causal-graph layer. We publish the −99.98% baseline on purpose: it is the expected result of running an event-blind orchestrator at full IBKR cost, and it falsifies the claim that the alpha is "just a clever orchestrator." Full methodology: [`document/backtests/SHOCK_VS_NAIVE_COMPARISON.md`](https://github.com/LEOATIS/AYVID/blob/main/document/backtests/SHOCK_VS_NAIVE_COMPARISON.md).

Honest weakness: the shock-driven profile is **asymmetric long-vol / crisis-alpha** — strong on SVB (+23.8%), DeepSeek (+20.9%), Yen Carry (+4.0%), ChatGPT (+4.2%), Suez (+0.5%); weak on rally scenarios (NVIDIA blowout −7.1%, Post-COVID −4.9%, China crackdown −4.2%, Semi correction −3.0%). Documented, quantified, on the near-term roadmap.

### Also shipped

- **Live paper-trading runner** (IB + shadow modes) emitting `live/equity_curve.jsonl` continuously — `ayvid trade paper-start`.
- **Next.js 15 marketing site + operations console** (`frontend/`) with a live-shock demo screen that plays the whole pipeline end-to-end in 60 seconds.
- **Live REST surface** for the console (`backend/api/v1/endpoints/live_routes.py`) exposing equity curve, runner status, trades, positions, backtest reports, and scenarios.
- **Gemini-CLI-style REPL** (`cli/repl.py`) that wraps the 46-command Typer app with a persistent prompt and live status bar.

### Tech stack

`Python 3.11` · `LangGraph` · `GPT-4o` · `Claude 3.5 Sonnet` · `Neo4j 5 (APOC + GDS)` · `Qdrant` · `QuestDB` · `Hopsworks` · `OpenBB SDK` · `ib_async` · `FastAPI` · `Typer + Rich` · `Pydantic` · `Tenacity` · `C++` (kill switch) · `mmap + seqlock IPC` · `pytest` · `Next.js 15 (App Router, route groups)` · `Tailwind` · `Docker Compose` (three multi-stage production images)

---

## Philosophy

- **Build end to end.** Data ingestion, NER, graph, agents, backend, CLI, REPL, execution, broker, watchdog, frontend, tests — all of it, one person, one repo.
- **No toys.** If it doesn't handle real expiry calendars, real reconnects, real partial fills, real commissions, real slippage, it doesn't count.
- **Alpha is in the details.** −1R clamps, DTE-scaled sizing, per-index cooldowns, seqlock reads, IBKR-Tiered cost calibration. Every detail is load-bearing.
- **Publish the negative control.** The −99.98% naive walk-forward is in the repo because real quant diligence starts with "show me what *doesn't* work."
- **Ship.** Opinions without running code are noise.

---

## Status · April 2026

- **YC S26 applicant.** AI-Native Hedge Funds RFS (Charlie Holtz). On-time deadline **May 4, 2026 · 8 PM PT**; decisions by June 5; batch runs July–September.
- **IB Paper runner live** since 2026-04-19, writing `live/equity_curve.jsonl` every 60 s.
- **Two walk-forward backtests published** post-cost on 2018→2025 data.
- **Live-shock demo screen** shipping at `/console/live-shock` — 6-second loop, ready for the 60-second YC application video.

---

## What's next on LEOATIS

- Symmetric rally-side coverage (close the long-vol asymmetry quantified in the shock-driven report).
- 4+ weeks of unbroken live paper equity curve by submission.
- Swift/iOS front-end for live portfolio + signal monitoring (build target iOS 26).
- Expanded shock corpus with continuous NER on a live news firehose.
- Hopsworks feature-store sync for multi-horizon model retraining.

---

<p align="center">
  <a href="https://github.com/LEOATIS/AYVID"><b>→ Explore AYVID</b></a>
</p>
