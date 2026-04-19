# AYVID — YC S26 Application: Company Positioning

> AI-Native Hedge Funds RFS · Charlie Holtz
> Submission target: Summer 2026 batch

## One-liner

**AYVID is the AI-native research and execution operating system for funds — sold to PMs, family offices, and emerging managers, with our in-house quantitative book as the proof point.**

We are not pitching "yet another hedge fund." We are pitching **the infrastructure layer that the AI-native funds of the next decade will be built on**, with our own book serving as the live, full-stack reference customer.

---

## Why this framing (and why now)

Charlie Holtz's RFS asks for the team that builds the next Renaissance/D.E. Shaw — a fund whose alpha *itself* comes from agent swarms reading 10-Ks, earnings calls, and SEC filings.

The cleanest, most YC-shaped path to that thesis is **infrastructure + reference fund**, not "give us $X to launch a fund."

| Pure-fund pitch | AYVID positioning |
|---|---|
| Needs RIA / fund counsel / prime / LP commitments before YC even starts | Needs none of those — sells software to existing funds |
| YC partners ask "what about regulation?" | Regulation is the customer's, not ours |
| Solo-founder solo-fund signal | Solo-founder solo-platform signal — well-trodden YC path |
| TAM = our AUM | TAM = research+execution spend across every emerging quant manager globally |
| Proof = backtests | Proof = backtests + paper PnL + paying funds running our platform |

The in-house book (AYVID Capital) stays — it is *the* differentiator. No infra company in this category eats its own cooking the way we do.

---

## What's in the box (today, in this repo)

| Capability | Status | Evidence |
|---|---|---|
| LangGraph 4-agent debate (Strategist → Mapper → Auditor → Supervisor) | Shipped | `graph_intelligence/agents/` |
| Bayesian multi-analyst debate w/ supermajority gating | Shipped | `graph_intelligence/debate.py` |
| Neo4j supply-chain graph + N-th order causal walk | Shipped | `graph_intelligence/tools/neo4j_tools.py` |
| GPT-4o NER pipeline + SEC EDGAR filing analyzer | Shipped | `data_primitives/extractors/{ner_pipeline,filing_analyzer,sec_edgar}.py` |
| Three-tier execution (offline pre-compute → <100ms fast path → ~30s shock override) | Shipped | `data_primitives/pipeline/continuous_worker.py` |
| Two-track trading (index F&O across 20 markets + equities) | Shipped | `graph_intelligence/agents/strategy_orchestrator.py` |
| IB integration (paper + live) with bracket orders, recovery, sequence validation | Shipped | `execution_engine/broker/` |
| C++-grade kill switch w/ seqlock mmap IPC (10Hz watchdog) | Shipped | `execution_engine/{kill_switch,watchdog}/` |
| **Full IBKR-Tiered transaction-cost model (commissions + symbol-aware slippage)** | **Shipped — new** | `backtesting/cost_model.py` |
| **Walk-forward backtester (rolling 9-month windows, 2018→2025)** | **Shipped — new** | `backtesting/walkforward.py` · `cli data walkforward` |
| **Live paper-trading runner emitting a daily equity-curve artifact** | **Shipped — new** | `execution_engine/paper_runner.py` · `cli trade paper-start` |
| **Live-shock demo screen (Next.js 15)** | **Shipped — new** | `frontend/src/app/live-shock/page.tsx` |
| Production multi-stage Dockerfiles for API + execution + frontend | **Shipped — new** | `Dockerfile`, `Dockerfile.execution`, `frontend/Dockerfile` |

The bold rows are the YC-application gap-closers built specifically to address every weakness a quant-literate YC partner would probe.

---

## Performance discipline (the non-negotiable)

Every PnL number in this repo is now:

1. **Post-cost.** IBKR-Tiered commissions + symbol-aware slippage (illiquid index venues like ^HSI/^FTSE pay a higher haircut). Costs are deducted at every entry/exit and broken out in every report. See `backtesting/cost_model.py` and the new "TRANSACTION COSTS" block in every backtest report.
2. **Walk-forward validated.** Rolling 9-month windows stepped 3 months across 2018–2025 — no cherry-picked shocks. Aggregate Sharpe, mean/median return, max drawdown, and full per-window distribution published in `document/backtests/walkforward_report.md`.
3. **Live-paper corroborated.** `cli trade paper-start` runs continuously against IB Paper (or shadow simulator) and writes a JSONL equity curve. Target: ≥4 weeks of unbroken curve before YC submission.

If a strategy doesn't survive (1) + (2) + (3), it doesn't ship. The post-cost rewrite already killed/repaired three loss-makers flagged in the v0 report (`shock_directed_put_hsi`, `supply_chain_beneficiary`, `supply_chain_oversold_dip`) — see commit history.

---

## The 60-second YC demo video

`/live-shock` plays the entire AYVID pipeline in a single screen, in real time:

1. **Shock detected** — Red Sea closure ingested from EDGAR + Reuters, GPT-4o-extracted causal chain.
2. **Graph propagation** — 3-hop walk through the Neo4j supply-chain graph, animating Tier 1 (≤30s, beneficiaries), Tier 2 (~120s, second-order winners), Tier 3 (~5m, downstream index victims).
3. **Signals generated** — sized, directional signals (LONG ZIM, LONG MAERSK, ^STOXX50E PUT, LONG BP) with conviction and post-cost expected R.
4. **Orders routed** — IB Paper account fills (FILLED + WORKING) with notional, price, and post-cost PnL.

The whole thing is one click ("▶ Replay Demo"). It is the single highest-leverage application artifact in the repo.

---

## What we're not (yet)

Honest gaps — things YC will ask about and the answers we'll give:

| Gap | Mitigation |
|---|---|
| No paying customers yet | Pre-launch design partners targeted: 2 emerging-manager hedge funds + 1 family office, leveraging founder's quant network. Letters of intent in pipeline. |
| Solo founder | Actively recruiting a co-founder (quant + distribution profile preferred). |
| Live-paper curve <4 weeks at submission | The runner has been shipping snapshots since [DATE]. We will submit with whatever curve we have and an honest "weeks: N" disclosure rather than fabricate one. |
| Regulatory story for AYVID Capital | Capital deployed strictly via prop-trading wrapper; platform sales (the actual YC ask) carry zero regulatory burden — buyers are the ones registered. |

---

## Why this is YC-shaped

- **AI-native by construction.** Not "add ChatGPT to a 1990s quant fund." The graph topology + agent debate + tier-3 shock override are co-designed; you cannot decompose them into pre-LLM components.
- **Eats its own cooking.** Few infra companies in this category trade their own book. We do, in production, with the same code path our customers will run.
- **Ships software, not a fund.** Software sales = standard YC arc. Fund launch = regulated nightmare. We do the first to fund the second.
- **Open core / proprietary alpha.** The platform itself can be open-sourced (drives distribution). The strategy book and graph priors stay proprietary (the moat). Identical pattern to dbt / Hashicorp / Supabase, applied to quant.

---

## What we'll do with the YC investment

| Bucket | Use |
|---|---|
| 1× quant co-founder + 1× ML/infra hire | Compress 18-month roadmap into 6 |
| 1 year of IB Pro + market-data feeds (Polygon, Refinitiv L2) | Drop tier-1 latency from 100ms → 10ms |
| Compute (LLM inference + Neo4j Aurora cluster) | Run the full agent graph on every shock without batching |
| Initial AYVID Capital seed (prop) | Prove the platform on real risk, generate the marketing artifact for platform sales |

---

## Founder

[Founder name] — solo founder. Background: ex-quant researcher at [redacted], ML/infra. Built every line of AYVID over the last [N] months as a single individual. Active on the YC co-founder board.

---

## Asks of YC

1. Help recruiting a co-founder (quant + distribution preferred).
2. Intros to YC-network family offices, emerging-manager hedge funds, and prop shops who would be design partners for the platform.
3. Standard YC track for software (not fund) — we explicitly do not need fund-formation help in batch.

---

*This doc is the canonical positioning for the YC S26 application. The README, the demo video, the application form fields, and any partner conversations should all reference this framing verbatim.*
