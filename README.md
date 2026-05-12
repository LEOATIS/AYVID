# AYVID

**AI-native, institutional-grade, shock-driven quantitative hedge fund.**

Swarms of LLM agents read SEC filings, earnings calls, and global news in
real time; a Neo4j supply-chain graph traces the N-th-order causal
consequences of each detected shock; an execution engine sizes orders
with Half-Kelly and routes bracketed fills into Interactive Brokers —
end-to-end, no humans in the loop on the trade path. Six asset classes
run in parallel: **equities** (~50+ dynamically-screened names), **index
F&O** (20 global venues), **FX** (34 pairs), **commodities** (17 futures),
**crypto** (22 assets), and **ETFs & bonds** (37 ETFs + 4 rate futures).

The fund's edge is not an LLM model choice or a clever prompt. It is the
architectural property that **every trade the system takes has (a) passed
through the orchestrator's 4× EV hurdle, (b) had its causal thesis
validated against an empirically-calibrated priors file, and (c) been
sized by a volatility-regulated Kelly criterion that scales position with
conviction**. Everything else — the agents, the graph, the debate
protocol — is machinery in service of that contract.

---

## Headline

Post-cost walk-forward, 2018 → 2025, 20 named historical shocks,
$1M initial equity per scenario.

| Mode | Compounded return | Pooled WR | Avg Sharpe | Worst DD | Cost drag |
|---|---:|---:|---:|---:|---:|
| Naive (trade every ticker daily) | −99.98% | 34.2% | −2.15 | 98.1% | 7.40% |
| **Shock-driven (AYVID's mode)** | **+33.43%** | **63.7%** | **0.08** | **7.14%** | **0.55%** |

Same engine. Same data. Same IBKR-Tiered cost model. Full numbers live
in [`document/backtests/shock_driven_option_c_full.json`](document/backtests/shock_driven_option_c_full.json);
methodology and naive-vs-shock comparison in
[`document/backtests/SHOCK_VS_NAIVE_COMPARISON.md`](document/backtests/SHOCK_VS_NAIVE_COMPARISON.md);
YC S26 pitch in [`document/yc/COMPANY_POSITIONING.md`](document/yc/COMPANY_POSITIONING.md).

---

## Quickstart

You need Python ≥ 3.12 and (optionally, for the full stack) Docker.

```bash
# 1. Install.
make install                                     # editable install of all deps

# 2. Configure. Copy the template and fill in the secrets.
cp .env.example .env                             # OPENAI_API_KEY + NEO4J + IBKR…

# 3. Run a backtest.
ayvid data shock-walkforward --start 2018-01-01 --end 2025-12-31

# 4. (optional) Full pipeline: fresh OHLCV + calibrate + walkforward +
#    auto-rebuild priors from outcomes. Single command.
ayvid data shock-walkforward --ingest --precalibrate --recalibrate \
                             --reset-outcomes

# 5. Start the API + console.
docker compose up -d                             # Neo4j, QuestDB, Qdrant, API, frontend

# 6. Live paper trading (shadow mode, no IB required).
ayvid trade paper-start --shadow --equity 1_000_000
```

Every CLI command has `--help`. Deep operator workflows live in
`calibration/README.md` (priors feedback loop) and
[`document/AYVID_Codebase_Architecture_Analysis.md`](document/AYVID_Codebase_Architecture_Analysis.md).

---

## Architecture

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                              AYVID                                  │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐   │
 │  │  Graph Intelligence (LangGraph DAG)                         │   │
 │  │    Strategist → Mapper → Researchers → Analyst swarm →      │   │
 │  │    Auditor → Supervisor → Signal                            │   │
 │  │    Bull/Bear debate · JSD epistemic filter · PatternAgent   │   │
 │  └─────────────────────────────────────────────────────────────┘   │
 │                                                                     │
 │  ┌───────────────────────┐     ┌────────────────────────────────┐   │
 │  │ Data Primitives       │     │ Execution Engine               │   │
 │  │   OpenBB / Finnhub    │     │   Kelly + conviction scaling   │   │
 │  │   NER (GPT-4o)        │     │   Black-Scholes, Greeks, EV    │   │
 │  │   Adversarial sanit.  │     │   IB bracket orders + recovery │   │
 │  │   SEC filing analyzer │     │   Pyramid trailing stops       │   │
 │  │   Reddit / StockTwits │     │   C++ kill switch (10 Hz seqlock mmap) │
 │  └───────────────────────┘     └────────────────────────────────┘   │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐   │
 │  │  Storage                                                    │   │
 │  │    Neo4j (supply graph) · Qdrant (vectors) ·                │   │
 │  │    QuestDB (ticks)      · Hopsworks (features)              │   │
 │  │    JSONL (local OHLCV, outcomes log)                        │   │
 │  └─────────────────────────────────────────────────────────────┘   │
 │                                                                     │
 │  ┌────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────┐  │
 │  │ CLI (Typer)│ │  FastAPI API │ │ Backtesting │ │ Next.js 15    │  │
 │  │ + REPL     │ │  REST + SSE  │ │ engine      │ │ console + site│  │
 │  └────────────┘ └──────────────┘ └─────────────┘ └──────────────┘  │
 └─────────────────────────────────────────────────────────────────────┘
```

### Three tiers of latency

| Tier | Path | Latency | What runs |
|---|---|---|---|
| 1 | Offline pre-compute | 15 min cadence | LangGraph DAG writes `SignalBook` cache |
| 2 | Fast path | < 100 ms | `fast_execute()` reads cache, routes to IB |
| 3 | Shock override | ~30 s | News invalidates cache, fresh news-aware signal is emitted |

The fast path is pure cache lookup + routing — no LLM calls at trade
time. The graph runs in the background; shocks are detected by the
continuous ingestion worker and trigger thesis-invalidation closes on
any open position whose pinned causal signature no longer holds. See
[`execution_engine/signal_book.py`](execution_engine/signal_book.py)
and
[`data_primitives/pipeline/continuous_worker.py`](data_primitives/pipeline/continuous_worker.py).

### Gate topology (post-Option-C refactor)

Every gate lives in exactly one place:

```
[Orchestrator]  ──  EV gate (4× cost) · priors blocklist · grind-up gate
       │
       ▼  (EXECUTE | SKIP)
[Backtest Engine — dumb simulator]
   capital caps · physics (lots, premium, DTE) · thesis-invalidation ·
   pyramid trailing · hold-time gating
       │
       ▼  (closed trade)
[calibration/trade_outcomes.jsonl]  append-only, backtest + live
       │
       ▼
[rebuild_priors_from_outcomes]  auto-block, auto-unblock, preserve Batch-*
       │
       ▼
[calibration/priors.json]  ◄── supervisor reads on next decision
```

The engine is NOT a second line of defence against bad signals. That is
the orchestrator's job. The engine enforces capital, physics, and
lifecycle only.

---

## Repository layout

```
AYVID/
├── shared/                     Config, types, exceptions, sequence validator
├── data_primitives/            Ingestion, NER, sanitisation, sentiment
├── graph_intelligence/         LangGraph agents + supply-chain tooling
│   ├── agents/                  strategist · mapper · researchers ·
│   │                            analyst · pattern · auditor · supervisor
│   ├── causal_falsification/    Off-hot-path DAG refinery
│   ├── compression/             Chronological + divergence decoding
│   └── tools/                   neo4j, qdrant, dynamic universe
├── execution_engine/           Trading infrastructure
│   ├── math/                    Kelly, BSM, regime, risk, costs
│   ├── broker/                  IB connection, orders, recovery
│   ├── signal_book.py           Pre-computed signal cache + thesis pins
│   └── watchdog/                C++ kill switch (seqlock mmap, 10 Hz)
├── backtesting/                Historical replay + feedback loop
│   ├── engine.py                Multi-asset simulator (6 asset classes)
│   ├── walkforward.py           Rolling-window aggregator
│   ├── calibrate.py             Offline grid-sweep calibrator
│   └── trade_outcomes.py        Outcomes logger + priors rebuilder
├── backend/                    FastAPI REST + SSE
│   └── core/observability.py    Request IDs, JSON logs, /ready probe
├── cli/                        Typer CLI (46 commands) + REPL + dashboard
│   └── exit_codes.py            Canonical exit codes (CI-parseable)
├── frontend/                   Next.js 15 — marketing site + operations console
├── calibration/                priors.json + trade_outcomes.jsonl (runbook in README)
├── data_store/                 Local OHLCV / features (JSONL)
├── live/                       Paper-trading runtime artifacts
├── tests/                      pytest — 352 tests, chaos suite, backtest validators
├── infra/                      Terraform (Cloud Run, Compute Engine, Secret Manager)
└── document/
    ├── yc/                      YC S26 positioning doc
    ├── backtests/               Published post-cost reports
    └── AYVID_Codebase_Architecture_Analysis.md
```

One file, one responsibility. The engine does not validate signals; the
orchestrator does not execute orders; the backend does not mutate
priors. These lines are test-guarded.

---

## Six asset classes

| Asset class | Instruments | Sizing | Hold window |
|---|---|---|---|
| **Equities** | Stocks across a dynamically-screened universe | Kelly-equity (notional basis) | 1–42 days |
| **Index F&O** | Futures, options, spreads on 20 global indices (ES, NQ, NIFTY, FDAX, FTSE, N225, HSI …) | Kelly-futures + options (risk-capital basis) | Intraday / BTST |
| **FX** | Major & minor currency pairs (EUR/USD, GBP/JPY, AUD/NZD …) | Kelly-FX (pip-risk basis) | 1–5 days |
| **Commodities** | Crude oil, gold, natural gas, copper, wheat … | Kelly-futures (contract multiplier basis) | 1–10 days |
| **Crypto** | BTC, ETH, SOL and majors via regulated venues | Kelly-crypto (volatility-adjusted) | 1–7 days |
| **ETFs & Bonds** | Sector ETFs, rate-futures (ZB, ZN, ZF), credit ETFs | Kelly-fixed-income | 1–21 days |

Every strategy name is a canonical key in
[`graph_intelligence/agents/strategist/schema.py::CANONICAL_STRATEGIES`](graph_intelligence/agents/strategist/schema.py).

### Strategy Win Rates (by type)

| Strategy Type | Example Strategies | Target Win Rate | Regime |
|---|---|---|---|
| Premium Selling | iron_condor, credit_spread, cash_secured_put | 70–80% | CHOPPY, MEAN_REVERTING |
| High-Confidence Directional | earnings_drift, momentum_equity | 60–70% | TRENDING |
| Supply Chain High-Confidence | supply_chain_hc (≥0.80 confidence edges) | 65–78% | ALL non-CRISIS |
| VIX Spike Recovery | vix_mean_reversion | ~75% | CRISIS→TRENDING |
| Shock/Event-Driven | shock_directed_* | 35–50% | CRISIS (high R-multiple) |

**Blended target: >70% on confirmed signals. Shock-driven layer intentionally carries lower win rate but compensates with 2–4R mean returns when right.**

---

## Operating the system

### CLI entry points (all have `--help`)

```bash
ayvid data   backtest | walkforward | shock-walkforward | calibrate | bulk-ingest
ayvid graph  init | seed | analyze | propose | refine
ayvid trade  paper-start | execute | portfolio | signals | queued | risk
ayvid system status | serve | killswitch | latency
ayvid dashboard         # full-screen Rich terminal monitor
ayvid                   # drops into persistent REPL
```

### Backend API

```bash
ayvid system serve                                # starts FastAPI on :8001
curl http://localhost:8001/health                 # liveness
curl http://localhost:8001/ready                  # readiness (per-component)
curl -H 'X-API-Key: ...' http://localhost:8001/api/v1/trade/portfolio
```

Request IDs are stamped on every response (`X-Request-ID`) and threaded
through logs via a `ContextVar`. Set `AYVID_LOG_FORMAT=json` for
structured JSON logs (Cloud Logging / Datadog / Loki friendly). See
[`backend/core/observability.py`](backend/core/observability.py).

### Feedback loop (priors auto-update from outcomes)

```bash
# Run, log outcomes, rebuild priors from outcomes, restart clean:
ayvid data shock-walkforward --recalibrate --reset-outcomes
```

The rebuilder is idempotent, preserves operator-authored `Batch-*`
policy blocks, and auto-blocks any cell with `n ≥ 5` and `sharpe ≤
−0.5`. Operator runbook in [`calibration/README.md`](calibration/README.md).

---

## Development

### Running the test suite

```bash
make test                                         # all 352 tests, ~15 s
pytest tests/chaos                                # chaos / resilience
pytest tests/test_batch2_lifecycle.py -v          # specific module
```

The chaos suite simulates LLM outages, IB disconnects, OpenBB
timeouts, Neo4j downtime, and total outages. Tests MUST pass before
any change to the decision layer is merged.

### Local environment

```bash
# Bring up the supporting services only (Neo4j / QuestDB / Qdrant).
docker compose up -d neo4j questdb qdrant

# Run the backend locally against those services.
AYVID_BACKEND_URL=http://localhost:8001 ayvid system serve
```

### Debugging a backtest scenario

```bash
ayvid data shock-walkforward --only covid_crash_2020 --name debug_covid
python -c "import asyncio; \
  from backtesting.engine import run_scenario_backtest; \
  r = asyncio.run(run_scenario_backtest('covid_crash_2020', 1e6, min_conviction=0.55)); \
  print(r.total_return_pct)"
```

---

## Deployment

### Docker (local, single-host)

```bash
docker compose up -d              # full stack: services + API + execution + frontend
```

API image runs as non-root (`USER ayvid`), has a healthcheck on
`/health`, and ships tini as the init. The execution image has its
own healthcheck (stale-equity-curve detection) + cpus/mem limits. See
[`Dockerfile`](Dockerfile), [`Dockerfile.execution`](Dockerfile.execution),
[`docker-compose.yml`](docker-compose.yml).

### GCP (production)

Terraform modules under [`infra/`](infra) provision:

- **Cloud Run** for the FastAPI backend. `AYVID_REQUIRE_AUTH=true` is
  injected as env so `verify_api_key` never degrades to dev-mode.
  IAM posture is driven by `backend_public_invoker` (default false)
  and `backend_invoker_members`.
- **Compute Engine VM** for the IBKR Gateway + paper runner + C++
  watchdog. SSH ingress is empty by default
  (`ssh_source_ranges = []`) — operators tunnel via IAP.
- **Artifact Registry** for container images.
- **Secret Manager** for every credential.

```bash
terraform -chdir=infra init -backend-config="bucket=$AYVID_TFSTATE_BUCKET"
terraform -chdir=infra plan  -var "project_id=$GCP_PROJECT"
terraform -chdir=infra apply -var "project_id=$GCP_PROJECT"
```

---

## Design principles

These are not aspirations. They are code-enforced invariants.

1. **One gate per concern.** Signal-quality gating is the orchestrator's
   job. The engine is a simulator. The priors file is the evidence
   ledger. See
   [`tests/test_option_c_pipeline.py::TestOptionCEngineTrustsOrchestrator`](tests/test_option_c_pipeline.py).

2. **Evidence is reversible; policy is not.** Auto-blocks are evidence-
   driven and get auto-unblocked when stats recover. Operator-authored
   `Batch-*` blocks are immortal. See
   [`calibration/README.md`](calibration/README.md).

3. **The watchdog is an independent process.** The Python runner writes
   an 88-byte seqlock frame at 10 Hz; the C++ watchdog reads it with
   acquire-ordered atomic loads, and on breach it sends SIGKILL to
   Python AND flattens the IB book via the Client Portal REST API in
   parallel. Compile-time `static_assert`s enforce frame layout.
   See [`execution_engine/watchdog/kill_switch.cpp`](execution_engine/watchdog/kill_switch.cpp).

4. **Every trade is post-cost.** The backtest engine and the live
   runner use the identical IBKR-Tiered cost model
   ([`shared/cost_model.py`](shared/cost_model.py)) at every
   entry and exit. No cost-free edge ever makes it into a report.

5. **Production surfaces are instrumented.** Every HTTP response
   carries a request-id. Every CLI exit has a canonical code in
   [`cli/exit_codes.py`](cli/exit_codes.py). Every closed trade
   lands in `calibration/trade_outcomes.jsonl`. There is nowhere
   the system "silently succeeds" — if something doesn't show up in
   the logs / the outcomes file / the priors diff, it didn't happen.

6. **The causal graph is empirically falsified.** Supply-chain edges
   proposed by the LLM edge-proposer enter Neo4j at confidence 0.20;
   the weekly refinery runs conditional-independence tests on
   observational returns and updates `r.confidence` only on cells
   that survive. Live traversal filters at `min_edge_confidence=0.6`
   — the LLM cannot smuggle a bad edge into the hot path.

---

## Anthropic Financial Services integration

AYVID is the buy-side production of Anthropic's
[Claude for Financial Services](https://github.com/anthropics/financial-services)
stack. The integration brings three things into the repo:

1. **A Claude Skills layer** at `.claude/skills/`. 15 skill bundles
   (8 equity-research adapted from upstream, 2 financial-analysis
   adapted from upstream, 5 AYVID-native — including the
   bull/bear/facilitator debate, shock-thesis, causal validation,
   Kelly sizing, and post-mortem). Each skill is a `SKILL.md`
   manifest + sibling `.md` prompt files — Anthropic's file-based
   convention, no build step.

2. **An MCP connector registry** at `.mcp.json` (root) plus runtime
   wrappers at `data_primitives/mcp/`. Five buy-side-relevant MCP
   servers: MT Newswires (real-time news), Aiera (earnings calls),
   FactSet (fundamentals), Moody's (credit), S&P Global (sector tear
   sheets). Each is opt-in via `MCP_*_URL` env vars; when unset, AYVID
   falls back to its existing extractor paths.

3. **A skills loader bridge** at [`shared/skills_loader.py`](shared/skills_loader.py).
   The researcher agent's prompts (`graph_intelligence/agents/researchers/prompts.py`)
   now load their bull/bear/facilitator system + user templates from
   the markdown skills, so the skill files are the single source of
   truth and the Python module is a shim.

See [`document/yc/anthropic_integration.md`](document/yc/anthropic_integration.md)
for the full integration map (what's adapted, what's AYVID-native, the
opt-in matrix, the runtime topology, and the migration recipe for
moving the remaining agents through the loader).

---

## Further reading

Order of usefulness for a new contributor:

1. [`document/AYVID_Codebase_Architecture_Analysis.md`](document/AYVID_Codebase_Architecture_Analysis.md)
   — canonical walk-through of every subsystem with inline design
   rationale.
2. [`calibration/README.md`](calibration/README.md) — the feedback-loop
   operator runbook (priors, outcomes, auto-block semantics).
3. [`document/yc/COMPANY_POSITIONING.md`](document/yc/COMPANY_POSITIONING.md)
   — YC S26 positioning doc (fund structure, regulatory path, demo).
4. [`document/backtests/SHOCK_VS_NAIVE_COMPARISON.md`](document/backtests/SHOCK_VS_NAIVE_COMPARISON.md)
   — headline naive-vs-shock comparison methodology.
5. [`document/backtests/shock_driven_option_c_full.md`](document/backtests/shock_driven_option_c_full.md)
   — the full 20-scenario walkforward the numbers at the top of this
   README come from.

---

## License

See [`LICENSE`](LICENSE).
