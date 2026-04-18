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

### Validated on real historical shocks

Back-tested end-to-end on five named shock scenarios — Suez Canal 2021, Semiconductor Shortage 2022, Russia-Ukraine 2022, COVID Crash 2020, US-China Tariff 2018 — with supply-chain shock context injected into the same path used at runtime:

| Metric | Result |
|---|---|
| Annualized Return | **+18.4%** |
| Sortino Ratio | **1.38** |
| Calmar Ratio | **4.08** |
| Graph Intelligence PnL contribution | **+27.2%** across 437 trades |
| Best scenario (Semiconductor 2022) | **+21.8%** @ Sharpe 1.19 |
| COVID Crash 2020 | **+10.8%** @ Sharpe 1.78 |

### Tech stack

`Python 3.11` · `LangGraph` · `GPT-4o` · `Claude 3.5 Sonnet` · `Neo4j 5 (APOC + GDS)` · `Qdrant` · `QuestDB` · `OpenBB SDK` · `ib_async` · `FastAPI` · `Typer + Rich` · `Pydantic` · `Tenacity` · `C++` (kill switch) · `mmap + seqlock IPC` · `pytest` · `Docker Compose`

---

## Philosophy

- **Build end to end.** Data ingestion, model, backend, CLI, execution, broker, watchdog, tests — all of it.
- **No toys.** If it doesn't handle real expiry calendars, real reconnects, real partial fills, it doesn't count.
- **Alpha is in the details.** -1R clamps, DTE-scaled sizing, per-index cooldowns, seqlock reads. Every detail is load-bearing.
- **Ship.** Opinions without running code are noise.

---

## What's next on LEOATIS

- Live paper-trading loop with auto-execute gated on SignalBook conviction
- Swift/iOS front-end for live portfolio + signal monitoring (build target iOS 26)
- Expanded shock corpus with continuous NER on live news firehose
- Hopsworks feature store sync for multi-horizon model retraining

---

<p align="center">
  <a href="https://github.com/LEOATIS/AYVID"><b>→ Explore AYVID</b></a>
</p>
