# AYVID — AI-Native Hedge Fund

**Status:** YC S26 applicant · AI-Native Hedge Funds RFS (Charlie Holtz) · on-time deadline **May 4, 2026 · 8:00 PM PT** · decisions by June 5 · batch runs July → September (San Francisco) · live IB-Paper runner since **2026-04-19** · two post-cost walk-forward backtests published (naive vs. shock-driven, 2018→2025) · Next.js 15 marketing site + operations console shipped ([full positioning doc](document/yc/COMPANY_POSITIONING.md))

AYVID is an AI-native hedge fund. Swarms of LLM agents read 10-Ks, earnings calls, SEC filings, and global news in real time, trace the causal consequences through a live Neo4j supply-chain graph, and route sized, risk-limited orders into Interactive Brokers — end-to-end, no humans in the loop on the trade path. The stack runs two tracks in parallel: **index F&O across 20 global markets** and **equities across 50+ dynamically-screened names**.

This is the fund Charlie Holtz described in the RFS. The repo maps 1:1 to his prose.

### Headline backtest

|                                              | Naive (trade everything every day) | **Shock-driven (AYVID's actual operating mode)** |
|----------------------------------------------|---------------------:|------------------------------:|
| Compounded return 2018→2025 (post-cost)      | **−99.98%**          | **+43.65%**                   |
| Trades                                       | 16,168               | 1,343                         |
| Avg cost drag                                | 7.40%                | 1.93%                         |
| % Sharpe > 1.0                               | 0.0%                 | 30.0%                         |
| Best individual outcome                      | −5.78% (90th pct)    | **+98.86% (COVID-19)**        |

Same engine, same data, same IBKR-Tiered cost model. The 144-percentage-point swing is the value of the graph layer — when the graph surfaces which tickers are causally linked to a detected shock, the orchestrator wins; when it trades everything blindly on technicals, it pays 7.4% annualized cost drag and loses everything. Full comparison: [`document/backtests/SHOCK_VS_NAIVE_COMPARISON.md`](document/backtests/SHOCK_VS_NAIVE_COMPARISON.md).

**Core thesis:** Supply-chain shocks propagate through hidden N-th order dependencies that the market systematically misprices in the first 30–300 seconds. GPT-4o extracts the event entities, Neo4j traces the causal paths, a 4-agent LangGraph debate adjudicates the direction, and the execution engine routes bracket orders before the consensus catches up. Every PnL number in this repo is **post-cost** (IBKR-Tiered commissions + symbol-aware slippage) and **walk-forward validated** — not cherry-picked.

> See [`document/yc/COMPANY_POSITIONING.md`](document/yc/COMPANY_POSITIONING.md) for the full YC S26 pitch — fund structure (3(c)(1) → RIA at $100M), regulatory path, demo video, and asks.

## Architecture

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                    AYVID System                          │
  ┌──────────────┐      │  ┌─────────────────────────────────────────────────────┐ │
  │   CLI + REPL │      │  │              Graph Intelligence (LangGraph)         │ │
  │  (Typer/Rich)│──────│──│  Strategist → Mapper → Auditor → Supervisor        │ │
  │  46 commands │      │  │  Strategy Orchestrator (Index F&O + Equity)         │ │
  └──────────────┘      │  │  Bayesian Multi-Analyst Debate                     │ │
                        │  └─────────────────────────────────────────────────────┘ │
  ┌──────────────┐      │  ┌───────────────────────┐ ┌──────────────────────────┐ │
  │  FastAPI     │      │  │  Data Primitives      │ │  Execution Engine        │ │
  │  Backend     │──────│──│  OpenBB / YFinance     │ │  Kelly Sizing            │ │
  │  REST + SSE  │      │  │  NER (GPT-4o)         │ │  Black-Scholes Pricing   │ │
  └──────────────┘      │  │  SEC Filing Analyzer  │ │  IB Bracket Orders       │ │
                        │  │  Market Scanner       │ │  C++ Kill Switch (10Hz)  │ │
  ┌──────────────┐      │  │  Universe Builder     │ │  Regime Classification   │ │
  │  Backtesting │      │  │  Reddit / StockTwits  │ │  Signal Book (cache)     │ │
  │  Engine      │──────│──│  Sentiment Pipeline   │ │  Options Pricing         │ │
  │  Two-Track   │      │  │  Tenacity Retries     │ │  Seqlock mmap IPC       │ │
  └──────────────┘      │  └───────────────────────┘ └──────────────────────────┘ │
                        │  ┌─────────────────────────────────────────────────────┐ │
                        │  │                    Storage                          │ │
                        │  │  Neo4j (graph)  │  Qdrant (vectors)                │ │
                        │  │  QuestDB (ticks)│  Hopsworks (features)            │ │
                        │  │  JSONL (local OHLCV for backtest)                  │ │
                        │  └─────────────────────────────────────────────────────┘ │
                        └──────────────────────────────────────────────────────────┘
```

### Three-Tier Execution Architecture

The system solves the core latency challenge of LLM agent debates during live market hours:

| Tier | Path | Latency | Description |
|------|------|---------|-------------|
| **Tier 1** | Pre-Compute | Offline | Background LLM pipeline pre-computes signals into a `SignalBook` every 15 minutes |
| **Tier 2** | Fast Path | <100ms | `fast_execute()` reads pre-computed signals — zero LLM calls at trade time |
| **Tier 3** | Shock Override | ~30s | Breaking news invalidates stale signals; news-aware fast pass + async LLM upgrade |

When a supply chain shock hits mid-cycle, the system immediately invalidates cached signals for affected tickers, blocks the fast path from trading on stale data, and fires an emergency re-compute with directional bias derived from the event itself (not lagging price action).

## Two-Track Trading

### Track 1 — Index F&O (Futures & Options)

Trades index derivatives across **20 global indices** with real-world contract specs:

| Region | Indices | Exchanges |
|--------|---------|-----------|
| US | S&P 500, NASDAQ 100, Dow Jones, Russell 2000 | CME |
| India | NIFTY 50, SENSEX | NSE, BSE |
| Europe | DAX, CAC 40, EURO STOXX 50, FTSE 100, SMI | EUREX, EURONEXT, ICE, SIX |
| Asia-Pacific | Nikkei 225, Hang Seng, KOSPI 200, ASX 200, TAIEX, STI | CME, HKEX, KRX, ASX, TAIFEX, SGX |
| Americas | Bovespa, S&P/TSX 60 | B3, TMX |
| China | CSI 300 | CFFEX |

**Instrument types**: `INDEX_FUTURE`, `INDEX_CALL`, `INDEX_PUT`, `INDEX_SPREAD` (debit spreads).

**Key features**:
- **Intraday-only by default** — all F&O trades open and close within the same trading session. BTST (Buy Today Sell Tomorrow) only permitted when conviction >= 0.80
- **Hard -1R stop** — every trade has a maximum loss of 1x initial risk. No exceptions. Losses are mathematically clamped at -1R regardless of gap moves
- **Dynamic conviction scoring** — multi-factor conviction (0.50–0.85 range) based on z-score depth, RSI depth, volume confirmation, and factor composite. Replaces flat conviction floors
- **Regime-aware directional filtering** — the system prevents bullish F&O entries in declining markets (`ret_5d < 0` and `z_score < -0.5`). Enables bear put spreads and protective puts in oversold regimes
- **Dynamic expected R** — expected reward-to-risk computed from realized volatility and Black-Scholes pricing, not hardcoded
- Real expiry calendars per exchange (weekly options on Thu/Fri, monthly futures on 3rd Fri/last Thu/etc.)
- 2% risk-per-trade lot sizing: `lots = (2% * available_capital) / (SL_distance * multiplier * usd_rate)`
- **DTE-scaled lot sizing** — position size scales down for shorter-dated options: `lots *= min(1.0, DTE / 5.0)`
- **DTE floor** — no trades with fewer than 2 days to expiry
- **Per-index cooldown** — after 2 consecutive losses on any index, that index is locked out for 3 trading days
- **50% F&O capital cap** — maximum 50% of portfolio can be at risk in F&O at any given time
- Available capital = cash minus capital already deployed (not total equity)
- Black-Scholes premium pricing for options and spreads
- Cost-of-carry futures pricing with DTE-based mark-to-market
- Trailing stop-to-cost (CTC) and partial profit-taking
- R-multiple tracking and distribution analysis

### Track 2 — Equity Investment

Long-term positions in 50+ global equities, dynamically screened:
- AI-native multi-factor ranker: momentum, value, quality, volume, volatility
- Deep value strategies (RSI < 30, z-score < -1.3)
- Supply chain shock-driven entries
- Stop-loss / take-profit with trailing stops
- Kelly Criterion (half-Kelly) position sizing

## Quickstart

### 1. Install

```bash
git clone <repo-url> && cd AYVID
python -m venv venv && source venv/bin/activate
make install
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

| Key | Service | Required? |
|-----|---------|-----------|
| `OPENAI_API_KEY` | GPT-4o (NER, agents, filing analysis) | Yes |
| `ANTHROPIC_API_KEY` | Claude (backup / debate) | Recommended |
| `NEO4J_URI` + `NEO4J_PASSWORD` | Supply chain graph | For graph features |
| `QDRANT_URL` | Semantic event search | For graph features |
| `OPENBB_FMP_API_KEY` | SEC filings, earnings, news | Recommended |
| `OPENBB_FRED_API_KEY` | Macro economic data | Optional |
| `IB_HOST` / `IB_PORT` | Interactive Brokers (paper or live) | For execution |
| `HOPSWORKS_API_KEY` | Feature store | Optional |

### 3. Run a backtest (fastest path to see results)

```bash
# Run with cached data
python -m cli.app data backtest --days 60

# Refresh all data + dynamically screen best equities first
python -m cli.app data backtest --days 60 --fresh

# Target a specific historical shock
python -m cli.app data backtest --scenario suez_canal_2021

# Custom date range
python -m cli.app data backtest --start-date 2022-02-20 --end-date 2022-04-30

# Or use make shortcuts
make backtest
make backtest-fresh
```

### 4. Run shock scenario backtests

```bash
# List all available shock scenarios
python -m cli.app data scenarios

# Run a specific scenario
python -m cli.app data backtest --scenario semiconductor_2022

# Generate a YC-ready performance report across all scenarios
python -m cli.app data report --scenarios all

# Compare AI-native vs baseline for alpha delta
python -m cli.app data report --scenarios all --compare-baseline
```

Available scenarios:

| Key | Event | Period |
|-----|-------|--------|
| `suez_canal_2021` | Suez Canal Blockage (Ever Given) | 2021-03-20 → 2021-04-15 |
| `semiconductor_2022` | Global Semiconductor Shortage | 2022-01-01 → 2022-06-30 |
| `russia_ukraine_2022` | Russia-Ukraine Invasion | 2022-02-20 → 2022-04-30 |
| `covid_crash_2020` | COVID-19 Market Crash | 2020-02-15 → 2020-04-30 |
| `us_china_tariff_2018` | US-China Trade War Escalation | 2018-06-01 → 2018-12-31 |

### 5. Initialize the supply chain graph

```bash
python -m cli.app graph init     # Create Neo4j schema + constraints
python -m cli.app graph seed     # Seed ~100 supply chain edges (12 sectors)
```

### 6. Analyze a shock

```bash
python -m cli.app graph analyze TSM    # Full LangGraph causal analysis
python -m cli.app graph scan TSM       # N-th order hidden victim scan
```

### 7. Run the full pipeline

```bash
python -m cli.app data pipeline   # News -> NER -> Graph -> Causal Analysis -> Signal Book
```

### 8. Start the API server

```bash
python -m cli.app system serve    # FastAPI on port 8000
# Or: make serve
```

### 9. Walk-forward validation (post-cost, 2018→2025)

Two complementary backtests are published, both post-cost on the same cached data:

```bash
# (a) Naive walk-forward — trade everything every day on technicals.
#     This is the "negative control": shows what the orchestrator does
#     without graph context. Compounded result: -99.98%. Expected.
python -m cli.app data walkforward --start 2018-01-01 --end 2025-12-31 \
    --window-months 9 --step-months 3 --equity 1000000 \
    --name walkforward_report_loose

# (b) Shock-driven walk-forward — AYVID's actual operating mode.
#     Iterates the named historical shock catalog (20 events), runs each
#     one with proper shock context so only causally-linked tickers
#     trade. Compounded result: +43.65%. Same engine, same data.
python -m cli.app data shock-walkforward --start 2018-01-01 --end 2025-12-31 \
    --equity 1000000 --name shock_driven_report

# Outputs (for both):
#   document/backtests/{name}.json
#   document/backtests/{name}.md
#
# Side-by-side analysis:
#   document/backtests/SHOCK_VS_NAIVE_COMPARISON.md
```

### 10. Live paper-trading (the YC artifact)

```bash
# IB paper account (requires IB Gateway on the configured port):
python -m cli.app trade paper-start --interval 60

# No IB credentials yet? Run the same loop in shadow mode against local OHLCV:
python -m cli.app trade paper-start --shadow --equity 1000000 --interval 60

# Daily snapshot of the equity curve:
python -m cli.app trade paper-status
python -m cli.app trade paper-render   # writes live/equity_curve.png (or .svg)
```

Artifacts land in `live/`:

```
live/equity_curve.jsonl   # one snapshot per cycle
live/trades.jsonl         # one row per fill (post-cost PnL)
live/positions.json       # current open positions
live/equity_curve.png     # rendered curve for the YC video
```

### 11. Live-shock demo (frontend, Next.js 15)

```bash
cd frontend
npm install
npm run dev   # → http://localhost:3000/live-shock
```

The `/live-shock` screen plays the full pipeline — shock detected → 3-hop graph propagation → signals generated → orders routed to IB Paper — in a 6-second loop. This is the 60-second YC demo video asset.

## Docker

```bash
# Bring up the entire stack: Neo4j + QuestDB + Qdrant + API + execution worker + frontend
docker compose up -d

# View the live-shock screen
open http://localhost:3000/live-shock

# View the API
open http://localhost:8000/docs

# Continuous ingestion pipeline (separate profile)
docker compose --profile pipeline up -d
```

Three production-ready images are built:

| Image | Dockerfile | Purpose |
|-------|------------|---------|
| `ayvid-api`        | `Dockerfile`             | FastAPI backend (uvicorn, 2 workers, non-root, healthchecked) |
| `ayvid-execution`  | `Dockerfile.execution`   | Live paper-trading runner (IB or shadow), writes equity curve to a shared volume |
| `ayvid-frontend`   | `frontend/Dockerfile`    | Next.js 15 standalone build (~150MB, non-root) |

All three use multi-stage builds, run as UID 1001, and ship Docker `HEALTHCHECK` directives so orchestrators (k8s, ECS, Fly, Railway) can route traffic correctly out of the box.

## Project Structure

```
AYVID/
├── shared/                   Config, types (50+ Pydantic models), exceptions, logger
│   ├── config.py               Pydantic Settings (all env vars incl AUTO_EXECUTE)
│   ├── types.py                OHLCVBar, TradeSignal, Position, SupplyEdge, ...
│   ├── logger.py               Rich-themed logging
│   └── sequence_validator.py   Pre-trade sync / sequence registry
│
├── data_primitives/          Market data extraction, transformation, loading
│   ├── extractors/
│   │   ├── openbb_client.py    OHLCV (with date-range), indices, FX, crypto, news, filings
│   │   │                       @retry with tenacity exponential backoff
│   │   ├── universe_builder.py Global universe builder (equities, ETFs, indices, FX)
│   │   ├── market_scanner.py   Z-score anomaly scanner across tickers
│   │   ├── ner_pipeline.py     GPT-4o named entity recognition for supply chain events
│   │   ├── filing_analyzer.py  SEC 10-K/10-Q analyzer (supply chain intelligence)
│   │   ├── news_scraper.py     Multi-source news aggregation
│   │   ├── finnhub_client.py   Alternative data (Finnhub)
│   │   ├── reddit_client.py    Reddit sentiment extraction
│   │   ├── stocktwits_client.py StockTwits sentiment
│   │   └── sec_edgar.py        SEC EDGAR filing fetcher
│   ├── transformers/
│   │   └── text_cleaner.py     Text normalization for NLP
│   ├── loaders/
│   │   ├── questdb_writer.py   QuestDB tick writer
│   │   └── hopsworks_sync.py   Hopsworks feature store sync (optional)
│   └── pipeline/
│       └── continuous_worker.py  Tiered ingestion cycle + shock interception
│                                 + auto_execute_from_book (gated by AUTO_EXECUTE)
│
├── graph_intelligence/       LangGraph causal reasoning pipeline
│   ├── agents/
│   │   ├── strategy_orchestrator.py  Two-track F&O + Equity decision engine
│   │   │                             Dynamic conviction, regime-aware filters,
│   │   │                             dynamic expected_r from realized vol
│   │   ├── strategist/        Macro regime + strategy selection
│   │   ├── mapper/            Supply chain path mapping
│   │   ├── analyst/           Multi-analyst swarm (streaming, early extraction)
│   │   ├── auditor/           Adversarial validation (kills bad trades)
│   │   └── supervisor/        Consensus + trade signal emission
│   ├── tools/
│   │   ├── neo4j_tools.py     Graph queries + @retry with tenacity
│   │   ├── qdrant_tools.py    Semantic event search
│   │   └── dynamic_universe.py  Dynamic instrument helpers
│   ├── pipeline.py            Full LangGraph DAG assembly + per-node latency
│   ├── debate.py              Bayesian multi-analyst debate (supermajority)
│   ├── compression/           Chronological compression + divergence decoding
│   └── graph_state/           CausalArbitrageState TypedDict
│
├── execution_engine/         Trading infrastructure
│   ├── math/
│   │   ├── kelly.py           Kelly Criterion (half-Kelly) position sizing
│   │   ├── options_pricing.py Black-Scholes, Greeks, spread pricing
│   │   ├── regime.py          Volatility + trend regime + fingerprinting
│   │   ├── signals.py         RSI, ATR, Hurst, z-score, trend strength
│   │   ├── risk_metrics.py    VaR, CVaR, drawdown, Sharpe
│   │   ├── institutional_strategies.py  Institutional strategy helpers
│   │   ├── multi_timeframe.py Multi-timeframe signal confluence
│   │   └── information_theory.py  Entropy-based signal quality
│   ├── broker/
│   │   ├── connection.py      IB async connection + full_recovery_sequence on reconnect
│   │   ├── orders.py          Bracket orders, idempotency gate, orphan cancellation
│   │   └── recovery.py        Full recovery: position reconciliation + bracket validation
│   ├── signal_book.py         Pre-computed signal cache (TTL, regime awareness,
│   │                          atomic persistence, override-pending, RLock)
│   ├── paper_runner.py        Live paper-trading runner (IB + shadow modes)
│   │                          emitting live/equity_curve.jsonl + trades.jsonl + positions.json
│   ├── kill_switch/
│   │   └── memory_map.py      mmap writer (dedicated daemon thread, no starvation)
│   └── watchdog/
│       └── mmap_bridge.py     C++ watchdog bridge (seqlock IPC, SIGKILL on breach)
│
├── backtesting/              Historical replay + walk-forward engine
│   ├── engine.py             Two-track backtest: Index F&O + Equity
│   │                         Intraday-only F&O (BTST gated by conviction >= 0.80),
│   │                         hard -1R stop on all trades, regime-aware directional filter,
│   │                         dynamic conviction & expected_r, per-index cooldowns,
│   │                         DTE floor + DTE-scaled lot sizing, 50% F&O capital cap,
│   │                         real expiry calendars, Black-Scholes pricing,
│   │                         R-multiple tracking, trailing stops, partial profit-taking
│   ├── cost_model.py         IBKR-Tiered commissions + symbol-aware bid/ask slippage
│   │                         (US options $0.65/ctr first 100, US futures ES/NQ ~$0.85/side,
│   │                         micros ~$0.25/side, equities ~$0.0035/share, $0.35 min cap 1%,
│   │                         non-US index F&O ~$1.20/ctr). Applied at every entry/exit.
│   ├── walkforward.py        Rolling-window aggregator:
│   │                         - run_walkforward(): naive out-of-sample sweep
│   │                         - run_shock_driven_walkforward(): 20-scenario catalog
│   │                         Both emit JSON + Markdown reports to document/backtests/.
│   └── report_generator.py   Institutional report generator (JSON + TXT)
│                             Executive summary, risk metrics (Sortino/Calmar/VaR),
│                             alpha decomposition, R-distribution, strategy attribution,
│                             per-index F&O summary, rich option chain trade logs
│
├── backend/                  FastAPI REST + SSE API
│   ├── core/
│   │   ├── main.py           App setup, CORS, lifespan (pipeline + kill-switch)
│   │   └── security.py       API key registry
│   ├── api/v1/endpoints/
│   │   ├── graph_routes.py   Graph stats, shock analysis, edge ingest
│   │   ├── trade_routes.py   Portfolio, signals, risk, execution, queued orders
│   │   └── live_routes.py    Live data surface for the ops console:
│   │                         /equity, /runner-status, /trades, /positions, /signals,
│   │                         /backtests, /backtests/{name}, /scenarios, /system-status
│   ├── api/sse/              and api/v1/sse/
│   │   └── streamer.py       Server-sent events for live updates
│   └── services/
│       └── orchestrator.py   Three-tier orchestration: signal book → fast path → LLM
│                             auto_execute_from_book(), fast_execute(), execute_signal()
│
├── cli/                      Typer CLI (46 commands across 4 groups + dashboard + REPL)
│   ├── app.py                Main app entry point — drops into REPL when no subcommand
│   ├── repl.py               Gemini-CLI-style persistent prompt with live status bar
│   ├── dashboard.py          Live Rich terminal dashboard
│   ├── commands/
│   │   ├── data.py           25 commands: backtest, walkforward, shock-walkforward,
│   │   │                     bulk-ingest, scenarios, report, universe, ...
│   │   ├── graph.py          6 commands: init, seed, stats, scan, analyze, centrality
│   │   ├── trade.py          9 commands: paper-start, paper-status, paper-render,
│   │   │                     portfolio, signals, execute, risk, sizing, queued
│   │   └── system.py         5 commands: status, config, serve, latency, killswitch
│   └── widgets/              Rich rendering: banner, portfolio, signals, supply chain
│
├── tests/                    pytest suite
│   ├── test_kelly.py         Kelly criterion edge cases
│   ├── test_signals.py       Signal math (RSI, ATR, Hurst)
│   ├── test_risk_metrics.py  VaR, Sharpe, drawdown
│   ├── test_regime.py        Regime classification
│   ├── test_order_routing.py Order routing logic
│   ├── test_ner_parsing.py   NER entity extraction
│   ├── test_types.py         Pydantic model validation
│   └── chaos/                Resilience / chaos engineering tests
│       ├── fault_injector.py Context managers: LLM, IB, OpenBB, Neo4j outage simulation
│       └── test_resilience.py 5 scenarios: LLM down, IB disconnect, OpenBB timeout,
│                              Neo4j down, total outage survival
│
├── data_store/               Local JSONL time-series storage
│   ├── indices/              Per-index OHLCV (GSPC.jsonl, NSEI.jsonl, ...)
│   ├── ohlcv/                Per-equity OHLCV (AAPL.jsonl, TSM.jsonl, ...)
│   └── intraday/             Intraday bars (created on demand)
│
├── live/                     Paper-trading runner artifacts (populated at runtime)
│   ├── equity_curve.jsonl    One snapshot per cycle (mode, equity, cash, PnL, ...)
│   ├── trades.jsonl          One row per executed (or shadow) fill, post-cost
│   ├── positions.json        Current open positions
│   ├── equity_curve.png/svg  Rendered curve for the YC video
│   └── run_meta.json         Run metadata (start time, mode, IB account, ...)
│
├── frontend/                 Next.js 15 app (marketing site + operations console)
│   ├── src/app/(marketing)/  Public marketing site
│   │   ├── page.tsx          Home (Hero, Marquee, Identity, Process, Comparison, ...)
│   │   ├── about/            Why AYVID exists, regulatory posture
│   │   ├── approach/         How we build (modular monolith, causal graph, kill switch)
│   │   ├── research/         Selected research notes
│   │   └── contact/          LP + YC-partner + console-access routing
│   ├── src/app/console/      Gated operations console
│   │   ├── page.tsx          Console home (equity curve, runner status, key stats)
│   │   ├── live-shock/       60-second YC demo: shock -> graph -> signals -> orders
│   │   ├── backtests/        Backtest index + per-report deep-dive (/backtests/{name})
│   │   ├── scenarios/        Named shock scenarios browser
│   │   ├── signals/          Live signal book (conviction, direction, R)
│   │   ├── portfolio/        Positions + PnL from /api/live
│   │   └── docs/             Inline rendering of architecture + positioning docs
│   └── src/components/       Hero, CapabilityMarquee, EquityChart, GraphPropagation,
│                             ShockEvent, SignalsList, TradesList, ConsoleGate, ...
│
├── document/
│   ├── yc/COMPANY_POSITIONING.md         Canonical YC S26 positioning document
│   ├── backtests/                        Published post-cost reports (YC artifacts)
│   │   ├── SHOCK_VS_NAIVE_COMPARISON.md  Headline naive-vs-shock methodology diff
│   │   ├── walkforward_report_loose.md   Naive walk-forward (negative control)
│   │   ├── shock_driven_report.md        Shock-driven walk-forward (operating mode)
│   │   └── shock_driven_strict.md        Sensitivity run (min-conviction=0.70)
│   └── AYVID_Codebase_Architecture_Analysis.md   Full subsystem architecture write-up
│
├── scripts/                  Demo and utility scripts (scripts/demo.py)
├── infra/                    Terraform stubs (cloudrun.tf, compute.tf, main.tf)
├── docker-compose.yml        Neo4j + QuestDB + Qdrant + AYVID API + execution + frontend
├── Dockerfile                FastAPI backend image (uvicorn, 2 workers, non-root, HC)
├── Dockerfile.execution      Paper-trading runner image (IB or shadow), shared volume
├── frontend/Dockerfile       Next.js 15 standalone build (~150 MB, non-root)
├── Makefile                  Shortcuts: make backtest, make serve, make test, make lint, ...
└── pyproject.toml            Python 3.11+, all dependencies (ayvid script entrypoint)
```

## CLI Reference

The CLI ships **46 commands** across four groups (`data` · `graph` · `trade` · `system`) plus two top-level conveniences (`dashboard`, `repl`). Invoke `ayvid` with no subcommand to drop into the **Gemini-CLI-style persistent REPL** (`cli/repl.py`).

### `ayvid data` — Market Data & Backtesting (25 commands)

```bash
# Backtesting + walk-forward (post-cost on IBKR-Tiered model)
data backtest [--days 60] [--equity 1000000] [--fresh]
              [--universe auto|us_mega|global|...]
              [--start-date 2022-02-20] [--end-date 2022-04-30]
              [--scenario suez_canal_2021]
data walkforward           --start 2018-01-01 --end 2025-12-31 \
                           --window-months 9 --step-months 3 \
                           --equity 1000000 --name walkforward_report_loose
data shock-walkforward     --start 2018-01-01 --end 2025-12-31 \
                           --equity 1000000 --name shock_driven_report
data backtest-fno [--days 60]        # Deprecated, use backtest

# Shock Scenarios & Reporting
data scenarios                       # List all named historical shock scenarios
data report [--scenarios all|suez_canal_2021,...]
            [--compare-baseline]     # YC-ready multi-scenario performance report (JSON + Markdown)

# Universe & Scanning
data universe                        # Build and display global investment universe
data refresh-universe                # Discover new instruments from live sources
data world-scan [TICKERS...] [--sigma 2.0] [--days 30]  # Z-score anomaly scanner

# Market Overview
data indices [--days 10]             # World index performance table
data fx                              # Major FX pair rates
data crypto                          # Top crypto asset prices
data commodities                     # Commodity futures prices
data earnings                        # Upcoming earnings (7-day window)
data sectors                         # US sector performance
data calendar                        # Macroeconomic event calendar (14 days)

# Data Ingestion
data ingest AAPL TSLA NVDA [--days 90]                    # Fetch OHLCV + news for tickers
data ingest-intraday AAPL [--interval 5m] [--days 5]      # Intraday bars
data bulk-ingest [--universe auto|global|...] [--days 180] [--workers 8]  # Parallel OHLCV backfill
data pipeline                        # Full cycle: news -> NER -> graph -> analysis

# Research
data prices AAPL [--days 30]         # OHLCV price table
data news AAPL [--limit 10]          # Company news
data filings AAPL                    # SEC filing analysis (GPT-4o)
data profile AAPL                    # Fundamental profile (filings + earnings + ratios)
data verify                          # Test all data source connections
```

### `ayvid graph` — Supply Chain Intelligence (6 commands)

```bash
graph init                           # Create Neo4j schema + constraints
graph seed                           # Seed ~100 supply chain edges (12 sectors)
graph stats                          # Node/edge counts
graph scan TSM [--depth 4] [--min-risk 0.01]   # Find N-th order hidden victims
graph analyze TSM                    # Full LangGraph causal analysis pipeline
graph centrality                     # PageRank + betweenness (top 15)
```

### `ayvid trade` — Portfolio & Execution (9 commands)

```bash
# Live paper-trading runner + equity-curve artifacts
trade paper-start [--interval 60] [--shadow] [--equity 1000000]
                                     # Writes live/equity_curve.jsonl, trades.jsonl, positions.json
trade paper-status                   # One-shot snapshot of the latest curve row
trade paper-render [--format png|svg]  # Render live/equity_curve.{png,svg} for the YC video

# Portfolio, signals, manual exec
trade portfolio                      # Current positions and P&L
trade signals                        # Recent trade signals
trade execute AAPL LONG [--confidence 0.7]  # Manual paper trade
trade risk                           # VaR, drawdown, risk snapshot
trade sizing AAPL [--confidence 0.7] [--equity 1000000]  # Kelly sizing
trade queued                         # Pre-market queued orders
```

### `ayvid system` — Infrastructure (5 commands)

```bash
system status                        # Health check (Python, LLM, Neo4j, Qdrant, IB)
system config                        # Current configuration (secrets redacted)
system serve [--host 0.0.0.0] [--port 8000]  # Start FastAPI server
system latency                       # Pipeline node latencies, fast-path speed, SignalBook stats
system killswitch                    # C++ watchdog status and mmap file
```

### Top-level conveniences

```bash
ayvid                                # Drops into the persistent REPL (Gemini-CLI style)
ayvid dashboard                      # Full-screen Rich terminal dashboard
```

## Backtest Output

The backtest generates two files:
- `backtest_report.txt` — Institutional-grade human-readable report
- `backtest_report.json` — Machine-readable full results with rich trade detail

The `report` command generates additional YC-ready files:
- `yc_performance_report.json` — Machine-readable multi-scenario report
- `yc_performance_report.md` — Human-readable Markdown with tables

### Report Sections

| Section | Description |
|---------|-------------|
| Executive Summary | Period, equity, return, Sharpe, drawdown, win rate, profit factor |
| Risk Metrics | Sortino, Calmar, max consecutive losses/wins, VaR (5% daily), best/worst day |
| Alpha Decomposition | Equity vs F&O PnL attribution, graph intelligence contribution % |
| R-Multiple Distribution | Mean, median, 25th/75th/90th percentiles, % positive R |
| Strategy Attribution | Per-strategy PnL, trade count, win rate |
| Per-Index F&O Summary | Per-index PnL, wins/losses, win rate across all global indices traded |
| Top F&O Trades | Rich option chain detail (see below) |
| Top Equity Trades | Entry/exit prices, shares, direction, PnL |

### F&O Trade Log Format

Each F&O trade shows full option chain context:

```
  ^NSEI      INDEX_PUT      LONG   [INTRADAY]
    Spot: 23,639.15 → 23,151.10 | Strike: 23,876 PE | IV: 15.7% | DTE: 7
    Premium: 362.29 → 726.36 | Lots: 17 | Expiry: 2026-03-19
    PnL: $+5,570 (+100.5%) | R=+1.67 | 1R=$3,326 | profit_target | conv=0.74

  ^GSPC      INDEX_PUT      LONG   [INTRADAY]
    Spot: 6,343.72 → 6,528.52 | Strike: 6,407 PE | IV: 15.0% | DTE: 4
    Premium: 82.71 → 6.12 | Lots: 1 | Expiry: 2026-04-03
    PnL: $-2,481 (-60.0%) | R=-1.00 | 1R=$2,481 | hard_1R_stop | conv=0.69
```

Each trade includes: underlying index, instrument type, direction, trade mode (INTRADAY/BTST), spot entry/exit, strike price, CE/PE, IV, DTE, entry/exit premium, lots, expiry date, PnL in USD and %, realized R-multiple, 1R risk in USD, exit reason, and conviction score.

### Equity Trade Log Format

```
  LRCX       LONG   | Entry: $199.93 → $263.66 | Shares: 52
    PnL: $+3,314 (+31.9%) | 11d | [deep_value_investment] RSI=0, z=-1.6
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ (strict type hints, async I/O, Pydantic) |
| LLM Orchestration | LangGraph, Claude 3.5 Sonnet, GPT-4o |
| Graph Database | Neo4j 5 (APOC + GDS plugins) |
| Vector Store | Qdrant (semantic event search) |
| Time-Series | QuestDB (tick data), JSONL (OHLCV backtest data) |
| Feature Store | Hopsworks (optional) |
| Market Data | OpenBB SDK (OHLCV, indices, FX, crypto, commodities, news, filings) |
| Broker | Interactive Brokers via `ib_async` |
| Options Pricing | Black-Scholes (in-house `execution_engine/math/options_pricing.py`) |
| Risk Management | Kelly Criterion, VaR/CVaR, C++ kill switch (mmap @ 10Hz, seqlock IPC) |
| Resilience | Tenacity retries (exponential backoff), chaos testing framework |
| Signal Cache | SignalBook (TTL, regime fingerprinting, atomic persistence, RLock) |
| CLI | Typer + Rich (40 commands, live dashboard) |
| API | FastAPI (REST + SSE) |
| Testing | pytest (Kelly, signals, risk, NER, types, chaos/resilience) |

## Testing

```bash
make install           # Install with dev dependencies
make test              # Run all tests
make lint              # Ruff linting
make format            # Ruff formatting

# Specific test suites
python -m pytest tests/test_kelly.py -v          # Kelly criterion
python -m pytest tests/chaos/ -v                 # Chaos / resilience tests
```

## Key Design Decisions

- **Modular monolith**: Single repo with strict dependency inversion (outer layers import inner, never reverse). No microservices.
- **Two-track architecture**: Index derivatives (F&O) and equity investments run as parallel strategies in one unified backtest, mirroring how institutional funds like Citadel and Jane Street operate.
- **Three-tier execution**: Pre-computed signal book (Tier 1, offline) → sub-100ms fast path (Tier 2, zero LLM) → async shock override (Tier 3, streaming LLM). Ensures trade execution doesn't wait on LLM inference.
- **News-aware shock interception**: When a supply chain shock hits mid-cycle, the system derives directional bias from the event metadata (NLP-driven, not price-driven), blocks stale signals, and writes defensive HOLDs until LLM confirmation.
- **Intraday-first F&O**: All index derivatives trades are intraday by default — opened and closed within the same trading session. BTST is only allowed when the system's multi-factor conviction score exceeds 0.80, reflecting very high confidence. This mirrors how institutional desks manage overnight gap risk.
- **Hard -1R stop**: No F&O trade is permitted to lose more than 1x its initial risk. The loss is mathematically clamped at -1R with a defensive cap after the simulation loop. This prevents catastrophic -80% to -90% single-trade drawdowns.
- **Dynamic conviction scoring**: Conviction is computed from four factors — z-score depth, RSI depth, volume confirmation, and factor composite — producing a dynamic 0.50–0.85 range instead of a flat floor. This prevents overtrading in weak-signal environments.
- **Regime-aware directional filter**: The system detects trailing bearish regimes (`ret_5d < 0` + `z_score < -0.5`) and blocks bullish F&O entries. In strong downtrends, it deploys bear put spreads and protective puts instead.
- **Over-trading prevention**: Per-index cooldown (3 days after 2 consecutive losses), DTE floor (no trades < 2 DTE), DTE-scaled lot sizing (smaller positions for short-dated options), and a 50% portfolio cap on total F&O capital at risk.
- **Real expiry calendars**: Every index has exchange-accurate expiry rules (weekly Thu/Fri for options, monthly 3rd Fri/last Thu for futures) rather than arbitrary hold periods.
- **2% risk-per-trade**: Institutional lot sizing derived from stop-loss distance and contract multipliers, using only available (undeployed) capital.
- **R-multiple framework**: Every trade is scored by reward-to-risk ratio. Trades below `min_r` are rejected before entry. R-distribution statistics (mean, median, percentiles) are tracked in every report.
- **Scenario-based backtesting**: Named historical shock scenarios (Suez, semiconductors, Russia-Ukraine, COVID, tariffs) with explicit date ranges and injected supply chain shock context in MarketContext for targeted alpha verification.
- **Tiered ingestion**: Supply-chain-critical tickers (Tier 1) ingested every cycle; broader universe (Tier 2) every 4th cycle.
- **Adversarial validation**: The Auditor agent stress-tests every Mapper proposal before execution. Trades can be killed, confidence-adjusted, or approved.
- **Half-Kelly**: All equity positions sized at 50% of full Kelly to protect against model overconfidence.
- **C++ kill switch**: Independent watchdog process reads mmap at 10Hz with seqlock (no torn reads) and can SIGKILL the Python process if drawdown exceeds threshold. mmap writer runs on a dedicated daemon thread immune to event loop starvation.
- **Broker resilience**: Idempotency gate prevents duplicate order submissions. Orphan parent orders are cancelled if child legs fail. Full recovery sequence (position reconciliation + bracket order validation) runs after every IB reconnection.
- **Tenacity retries**: Exponential backoff on all external data calls (`fetch_ohlcv`, `fetch_world_news`, `get_driver`, `verify_connection`) for transient network failure resilience.
- **Chaos engineering**: Fault injection framework simulates LLM outages, IB disconnects, OpenBB timeouts, and Neo4j failures to verify graceful degradation.
- **Auto-execute loop**: When `AUTO_EXECUTE=true`, the continuous worker automatically routes high-conviction signals from the SignalBook through Kelly sizing and the broker after each pre-compute cycle.
- **Paper trading by default**: `PAPER_TRADING=true` is the default; IB live trading requires explicit opt-in.

## License

Proprietary. All rights reserved.
