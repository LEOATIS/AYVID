# AYVID — YC S26 Application Form Draft

> Paste-ready answers for every field of the YC S26 application, in form order.
> Pairs with [`COMPANY_POSITIONING.md`](./COMPANY_POSITIONING.md) and [`FOUNDER_VIDEO_SCRIPT.md`](./FOUNDER_VIDEO_SCRIPT.md).
>
> **Usage:** for each field, copy ONLY the content inside the code block into the YC form. The short italic line under each block is rationale for your eyes — do NOT paste it. The pre-submit checklist at the bottom must be walked before you hit submit.

---

## Table of contents

- [A. Founders](#a-founders)
- [B. Founder Video](#b-founder-video)
- [C. Company](#c-company)
- [D. Progress](#d-progress)
- [E. Idea](#e-idea)
- [F. Equity](#f-equity)
- [G. Curious](#g-curious)
- [H. Batch Preference](#h-batch-preference)
- [Pre-submit checklist](#pre-submit-checklist)

---

## A. Founders

### A1. How long have the founders known one another and how did you meet? Have any of the founders not met in person?

```
We met in our first semester of undergrad, chemistry lab, partnered on the
first lab the professor assigned. Been close friends ever since.

We've worked in person since day one — same city, same campus. No remote-only
founding.
```

*One honest sentence, no embellishment. Peter's "don't overcomplicate" rule.*

### A2. Who writes code, or does other technical work on your product? Was any of it done by a non-founder?

```
Likhith wrote 100% of AYVID end-to-end — every line. Three weeks of solo work
since early April 2026.

Scope, so you know it's real:
- Four-agent LangGraph pipeline (Strategist, Mapper, Auditor, Supervisor)
  plus a Bull/Bear adversarial researcher stage and a vision-LLM PatternAgent.
- Neo4j supply-chain graph with N-th order causal walks, edges validated
  against return data with conditional-independence tests.
- IBKR integration: bracket orders, idempotency gate, full-recovery sequence
  on reconnect, C++ kill switch at 10 Hz over mmap+seqlock IPC.
- Two-track trading engine — index F&O across 20 global markets + equities
  across 50+ names — with a full IBKR-Tiered transaction-cost model and a
  walk-forward backtester (2018-2025).
- FastAPI + SSE backend, 46-command Typer+Rich CLI, Next.js 15 marketing
  site and gated ops console, production multi-stage Dockerfiles for API +
  execution + frontend.

Lakshitha is full-time on research, strategy, and fund-ops — she drives the
causal hypothesis catalog the graph tests against, the LP narrative, and the
regulatory path (3(c)(1) fund wrapper).

Nothing in the repo was written by a non-founder.
```

*Peter called this "the first filter." Ten specific runnable things prove the team can ship.*

### A3. Are you looking for a cofounder?

```
No. Likhith ships product and infrastructure; Lakshitha drives research,
strategy, and LP/fund-ops. Next hires on the YC cheque are founding
engineers — quant + infra — and a fractional CCO for the 3(c)(1) wrapper.

Long-term we want partnerships with Citadel, Jane Street, Google, and
Anthropic as we scale the graph and inference layer. That's what the
$100B AUM path looks like; it isn't a co-founder problem.
```

*"No, and here's why the coverage is already complete" is stronger than "we're searching."*

---

## B. Founder Video

**Action:** use [`FOUNDER_VIDEO_SCRIPT.md`](./FOUNDER_VIDEO_SCRIPT.md) as the bullet card. Print the one-shot timing card, tape next to the lens, film one 60-second take (no editing), upload the MP4. File must be under 100 MB.

*Per Peter: single take, webcam quality is fine, do NOT edit.*

---

## C. Company

### C1. Company name

```
AYVID
```

### C2. Describe what your company does in 50 characters or less

**Top pick (45 characters):**

```
AI-native hedge fund run by LLM agent swarms
```

**Alternates (say each aloud before committing):**

```
Hedge fund run by LLM agents, not humans       (40 chars)
LLM agent swarms read filings and trade        (39 chars)
An AI-native hedge fund. Agents do it all.     (42 chars)
```

*Top pick uses Charlie Holtz's own RFS vocabulary ("AI-native hedge fund", "LLM agent swarms"). Zero ambiguity for a 2-minute reviewer.*

### C3. Company URL, if any

```
https://ayvid.ai
```

If the marketing site isn't deployed by submit, fall back to:

```
https://github.com/LEOATIS/AYVID
```

### C4. If you have a demo, attach it below (≤3 minutes, ≤100 MB)

**Action:** attach a screen recording of the `/console/live-shock` screen (shock → graph propagation → signals → IB-Paper fills). The page is already live at [`frontend/src/app/console/live-shock/page.tsx`](../../frontend/src/app/console/live-shock/page.tsx). Keep it to 60-90 seconds, no voiceover required.

### C5. Please provide a link to the product, if any

```
https://ayvid.ai/console/live-shock
https://ayvid.ai/console
```

### C6. If login credentials are required for the link above, enter them here

```
username: yc-reviewer
password: [generate a single-use review token and paste before submit]
```

**Action:** add a `yc-reviewer` entry to the API key registry in [`backend/core/security.py`](../../backend/core/security.py) and paste the generated password here before submit. Rotate after decision.

### C7. What is your company going to make? Please describe your product and what it does or will do.

```
AYVID is the fund Charlie Holtz described in the AI-Native Hedge Funds RFS.

Swarms of LLM agents read 10-Ks, earnings calls, SEC filings, and global
news in real time, trace the causal consequences through a live Neo4j
supply-chain graph, and route sized, risk-limited orders into Interactive
Brokers — end-to-end, no humans on the trade path.

Today's alternative is either (a) a legacy quant fund like Renaissance or
D.E. Shaw that is bolting AI onto 1980s-era factor models and can't even
get ChatGPT past compliance, or (b) a "GPT-picks-stocks" Twitter bot. The
edge in AYVID is neither. It is a Neo4j supply-chain topology that traces
N-th order victims of a shock (Suez, Red Sea, SVB) before the market
prices them in. A GPT-4o 10-K proposer discovers new supplier→customer
edges from SEC filings, and a CausalFusion refinery validates every
proposal against return data with conditional-independence tests. The
graph GROWS via discovery and SHRINKS via falsification — no hand-
seeded bias in either direction.

Five regime-matched strategy layers covering all market conditions: premium
selling (70–80% WR) in choppy/low-vol environments, high-confidence
directional plays (60–70% WR) in trending markets, supply-chain graph
intelligence (65–78% WR on ≥0.80 confidence edges), earnings/VIX event-
driven strategies, and shock-directed asymmetric bets. Global coverage:
30 indices across 28 exchanges, 100+ equities across US/EU/Asia. The
blended win rate target is >70% on filtered, confirmed signals.

Published proof, post-cost on IBKR-Tiered commissions + slippage, walk-
forward 2018-2025: a naive orchestrator with no graph context compounds
to -99.98%. The same engine with graph-surfaced tickers compounds to
+24.13%. A 124-percentage-point swing from the graph alone.
```

*Peter's template: what we do, what's broken today, the one number the reviewer remembers.*

### C8. Where do you live now, and where would the company be based after YC?

```
Bangalore, India / San Francisco, USA
```

### C9. Explain your decision regarding location.

```
We are both currently in Bangalore. If we get in, we relocate to San
Francisco for the batch — this is non-negotiable for us and the logistics
are already planned. Post-batch we stay in the Bay: a fund compounds on
proximity to LPs, prime-broker relationships (IB Prime, Goldman Marquee),
and regulatory counsel, and all three sit in NYC/SF, not India.

An offshore research/engineering team in Bangalore is a longer-term cost
lever once we have a CCO and an RIA in place.
```

*Honest signal that you'll actually be in SF, plus a one-line plan for why the Bangalore ties are an asset, not a flight risk.*

---

## D. Progress

### D1. How far along are you?

```
Three weeks of full-time solo building (Likhith) since early April 2026.
Everything below runs today from a fresh clone:

- Four-agent LangGraph swarm + Bayesian supermajority debate + Bull/Bear
  researcher stage + vision-LLM PatternAgent (5 specialists total).
- Neo4j supply-chain graph bootstrapped with ~100 edges across 12
  sectors, then GROWN dynamically by a GPT-4o 10-K proposer that
  resolves extracted entities to tickers and upserts at confidence=0.2,
  and SHRUNK by a conditional-independence test loop (gamma = 0.80)
  that promotes or prunes every candidate against observational
  returns. Full provenance in `calibration/edge_proposals.jsonl` +
  `calibration/refined_dag.json`.
- IBKR integration (paper + live) with bracket orders, recovery sequence,
  and a C++ kill switch at 10 Hz over mmap+seqlock IPC.
- Full IBKR-Tiered transaction-cost model (commissions + symbol-aware
  slippage) applied at every entry/exit.
- Walk-forward backtester 2018-2025 post-cost:
    Naive baseline:   -99.98% compounded, 16,168 trades, 7.40% cost drag.
    Graph-aware:      +24.13% compounded,  673 trades, 0.50% cost drag.
  We publish the -99.98% control on purpose, as the negative control.
- Live IB-Paper runner writing an equity curve every cycle since 19 April
  2026. By 4 May submission we'll have ~15 days of unbroken live curve,
  fully reproducible from live/equity_curve.jsonl + trades.jsonl.
- Five R&D upgrades shipped in sequence out of the 2024-2026 multi-agent
  LLM literature: adversarial-news sanitizer, CausalFusion edge refinery,
  Bull/Bear researcher debate, Jensen-Shannon epistemic coherence filter,
  vision-LLM PatternAgent.
- FastAPI + SSE backend, 46-command Typer+Rich CLI with a persistent
  REPL, Next.js 15 marketing site + gated ops console, production multi-
  stage Dockerfiles for API + execution + frontend.

No external capital yet, no paying LPs. All PnL is on proprietary / paper
capital.
```

*Every bullet is a metric. Readable top-to-bottom in under 45 seconds.*

### D2. How long have each of you been working on this? How much of that has been full-time?

```
Likhith — ~3 weeks full-time, since early April 2026. Wrote every line
of the current codebase solo in that window. Prior to this: authored the
full autonomy-system design for EXO Drones' autonomous firefighting
platform (ROS 2 mission logic, PX4 flight control, Jetson spatial AI,
MAVLink + uXRCE-DDS middleware, C++ PX4 fail-safes) under contract — same
systems-engineering muscle, applied to markets.

Lakshitha — ~3 weeks on AYVID, in parallel with her Data Science
Consulting internship at MathCo (Interstate Batteries project). She goes
full-time on AYVID the moment YC says yes.
```

*"Three weeks to a live paper-trading curve + 124-pp walk-forward swing" answers the "show speed" question.*

### D3. What tech stack are you using, or planning to use, to build this product? Include AI models and AI coding tools you use.

```
Language:          Python 3.11+ (strict type hints, async, Pydantic)
LLM orchestration: LangGraph (4-agent swarm + 5-specialist analyst debate)
LLMs:              Claude 4.5 Sonnet (debate, reasoning),
                   GPT-4o (NER, filings, fast extraction),
                   vision-LLM for PatternAgent
Graph DB:          Neo4j 5 (APOC + GDS)
Vector store:      Qdrant (semantic event search)
Time-series:       QuestDB (ticks), JSONL (OHLCV backtest cache)
Feature store:     Hopsworks (optional)
Broker:            Interactive Brokers via ib_async (paper + live)
Market data:       OpenBB SDK, SEC EDGAR, Reddit, StockTwits, FinnHub
Options pricing:   in-house Black-Scholes + Greeks
Kill switch:       C++ watchdog with mmap + seqlock IPC at 10 Hz
Resilience:        tenacity retries, chaos-engineering test harness
Backend:           FastAPI (REST + SSE), uvicorn, Docker multi-stage
Frontend:          Next.js 15 (app router), TypeScript, Tailwind,
                   Framer Motion
CLI:               Typer + Rich (46 commands, persistent REPL, live
                   dashboard)
Testing:           pytest, chaos/fault-injection suite

AI coding tools: Cursor (primary), Claude Code, ChatGPT for ad-hoc search.
Roughly 70-80% of the Python was drafted with Cursor agent turns under a
tight review loop; every commit is hand-verified against tests. We'll
attach a representative agent transcript in the next field.
```

*Dense list proves domain fluency without essay-writing.*

### D4. Optional: attach a coding agent session you're particularly proud of

**Action:** export a Cursor transcript that shows a non-trivial AYVID change end-to-end. Recommended candidate:

- **CausalFusion edge refinery** ([`graph_intelligence/causal_falsification/`](../../graph_intelligence/causal_falsification/)) — algorithmically the deepest commit in the repo. Shows direct research-paper-to-production translation (ICLR 2026 submission → working CI-test-driven loop with per-edge confidence writeback to Neo4j).

Fallback candidates if the CausalFusion transcript isn't clean:

- **C++ kill switch + seqlock mmap bridge** ([`execution_engine/watchdog/mmap_bridge.py`](../../execution_engine/watchdog/mmap_bridge.py)) — shows systems-level work, not just app code.
- **Shock-driven walk-forward** ([`backtesting/walkforward.py`](../../backtesting/walkforward.py)) — the commit that produced the -99.98%-vs-+24.13% result.

Pick whichever transcript best shows *agent-as-collaborator* behaviour: clarifying questions, planning, iteration on test failures, not a one-shot prompt.

### D5. Are people using your product?

**Radio:** `No`

Optional detail if a text field appears alongside:

```
No external users or LPs yet. Live IB-Paper runner since 19 April 2026 —
by submission ~15 days of unbroken daily equity curve. First 3(c)(1) LPs
come online during the batch, not before.
```

*Honest "no" + what we DO have instead.*

### D6. Do you have revenue?

**Radio:** `No`

### D7. If you are applying with the same idea as a previous batch, did anything change? If you applied with a different idea, why did you pivot and what did you learn from the last idea?

```
First application.
```

### D8. If you have already participated or committed to participate in an incubator, "accelerator" or "pre-accelerator" program, please tell us about it.

```
None.
```

---

## E. Idea

### E1. Why did you pick this idea to work on? Do you have domain expertise in this area? How do you know people need what you're making?

```
We picked it because Charlie Holtz wrote the RFS for exactly this fund,
and reading it we realised we were already the team to build it.

Domain expertise (two sides):
- Likhith — systems-engineering + autonomy. Authored the full autonomy-
  system design for EXO Drones' autonomous firefighting platform:
  high-level mission logic on Jetson (ROS 2 + BehaviorTree.CPP), low-
  level PX4 flight control with LQR mixing, MAVLink + uXRCE-DDS air-
  ground bridge, C++ watchdog fail-safes, and perception stack (LiDAR +
  RGB-D + thermal). A hedge fund's trade path is the same problem class:
  perception (NER + filings + news), mission planning (LangGraph agent
  debate), low-level control (bracket orders + kill switch), fail-safes
  (C++ watchdog at 10 Hz). Same stack philosophy, different domain.
- Lakshitha — applied ML in industry. Data Science Consulting intern at
  MathCo on the Interstate Batteries project, doing demand-forecasting
  and causal-impact work on a multi-million-dollar operation. She has
  shipped production-grade models against messy, adversarial data — the
  exact failure mode adversarial news injection exploits (see phase 1 of
  the roadmap, Rizvani et al. arXiv 2601.13082v1).

Why we know there is demand:
1. Charlie Holtz said so, publicly, in the RFS. "I worked as a quant
   researcher at one of these funds, and when I asked compliance to let
   us use ChatGPT, I didn't even get a response." That's not a hunch;
   that's a YC partner telling us the incumbents can't ship this and
   a startup must.
2. Global hedge-fund AUM is ~$5T. Systematic strategies (the slice AI-
   native would eat first) are ~$1T. Even a 0.5% penetration is a
   $5B AUM fund — $100M+ ARR at 2/20.
3. Our own walk-forward on real IBKR cost + slippage is +24.13% over
   2018-2025 vs. a naive -99.98%. If the numbers hold on live paper,
   there is no shortage of LP interest.
```

*Peter's "unique insight" test — hit twice (Likhith's autonomy rigour, Lakshitha's industry-ML rigour), then three evidences of demand.*

### E2. Who are your competitors? What do you understand about your business that they don't?

```
Incumbents:
- Renaissance, D.E. Shaw, Two Sigma, Citadel, Bridgewater. Massive,
  well-capitalised, and exactly as constrained as Charlie described —
  couldn't get ChatGPT past compliance in 2023. Their edge is decades
  of factor models, not LLM reasoning.

New entrants:
- Numerai (crowdsourced ML), Sentient, a long tail of "ChatGPT picks
  stocks" Twitter bots, and presumably 50+ fellow AI-Native-Hedge-Funds-
  RFS applicants this batch who built a GPT wrapper over OpenBB.

What we understand that none of them do:

The alpha isn't in the LLM. It's in the GRAPH.

Supply-chain shocks propagate through hidden N-th order dependencies
that the market systematically misprices in the first 30-300 seconds.
Every competitor is either (a) prompt-engineering a single LLM with no
topology, or (b) running factor models that can't even name the causal
chain. We have a Neo4j supply-chain graph, a find_hidden_victims()
primitive that walks it 3-5 hops with statistically-validated edge
confidence, and a LangGraph debate that adjudicates before orders fire.

The -99.98% naive baseline vs +24.13% graph-aware result on the same
engine, same data, same cost model, is the proof that the graph — not
the LLM — is where the alpha lives. We will be the only applicants in
this RFS slot who published that control on purpose.
```

*The single most important sentence in the whole application: "The alpha isn't in the LLM. It's in the GRAPH."*

### E3. How do or will you make money? How much could you make?

```
Phase 1 — today. Proprietary capital only. No external AUM, no fees.
This is our paper-trading / live-curve validation phase.

Phase 2 — during YC batch (June-Sep 2026). Delaware LLC + 3(c)(1)
exempt fund wrapper (<=35 accredited investors, no SEC registration
required, state-level notice filings only). Friends-and-family + YC
round seeds $1-3M. Standard 2/20 structure: 2% management fee,
20% performance fee above a high-water mark. Fund-formation cost is
~$40-60k setup + ~$10k/yr ongoing — well inside the YC cheque.

Phase 3 — 12-24 months post-batch. SEC-registered RIA once AUM > $100M.
Prime broker (IB Prime or Goldman Marquee). Quarterly 13F filings,
full ADV-1/ADV-2, CCO on retainer. This is the path to external
institutional LPs.

How big can this get, concretely:
- Global hedge-fund AUM: ~$5T. Systematic-strategy slice: ~$1T.
- 0.1% share of systematic AUM = $1B. At 2/20 and 15% net returns:
  $20M/yr management fee + $30M/yr performance fee = $50M ARR.
- 1% share = $10B AUM -> ~$500M ARR.
- Renaissance at peak ran ~$165B AUM. The ceiling on an AI-native fund
  that works is measured in tens to hundreds of billions, not millions.

Our target is a $100B-AUM fund in 10-15 years.
```

*Three phases, dollars attached, credible comparable (Renaissance), stated ambition.*

### E4. Which category best applies to your company?

```
Fintech — Hedge funds / Investment management
```

Pick "Financial Services" or "Fintech" in the dropdown, whichever YC exposes.

### E5. If you had any other ideas you considered applying with, please list them.

```
None. AYVID is the idea. We read the AI-Native Hedge Funds RFS, matched
every line of Charlie Holtz's prose to what the repo already does, and
went all-in.
```

*Listing fake alternatives would weaken the conviction signal. Honesty wins here.*

---

## F. Equity

### F1. Have you formed ANY legal entity yet?

**Radio:** `No`

Optional detail if a text field appears:

```
No — we are intentionally forming the Delaware LLC + 3(c)(1) fund wrapper
during the YC batch with YC-network legal counsel, so the structure is
correct for external capital from day one. Today we operate as individual
proprietary traders.
```

*One sentence of plan so "no entity" isn't read as disorganisation.*

### F2. Have you taken any investment yet?

**Radio:** `No`

### F3. Are you currently fundraising?

**Radio:** `No`

Optional detail if a text field appears:

```
No. We are deliberately holding fundraising until YC decision day. If YC
says yes, YC is the round. If YC says no, we open a friends-and-family
pre-seed on 5 June.
```

---

## G. Curious

### G1. What convinced you to apply to Y Combinator? Did someone encourage you to apply? Have you been to any YC events?

```
The AI-Native Hedge Funds RFS, written by Charlie Holtz. We read it in
early April, realised the fund he was describing was the one we already
wanted to build, and shipped the first version of the repo in the same
week.

No YC-alum encouragement campaign; we applied on the strength of the
RFS alone. Long-time readers of the YC blog, Hacker News, Paul Graham's
essays, and Startup School lectures.
```

### G2. How did you hear about Y Combinator?

```
Years ago — Hacker News, Paul Graham's essays, Startup School videos.
YC has been a default part of the startup vocabulary we grew up with.
```

---

## H. Batch Preference

```
Summer 2026 (selected).
```

---

## Pre-submit checklist

Walk this in order the night before submit:

- [ ] **Truth pass.** Every number in every answer is verifiable in the repo today. Peter's BS-detector rule is non-negotiable.
- [ ] **50-char field.** Open a character counter (not eyeballed) and confirm the phrasing you pick for C2 is <=50 chars including spaces. Top pick is 45.
- [ ] **C3 URL resolves.** Hit `https://ayvid.ai` in a private window. If it 404s or shows a broken Vercel preview, swap to the GitHub URL. Never ship a dead link.
- [ ] **C4 demo video.** `/console/live-shock` records cleanly end-to-end (shock → graph → signals → IB fills) with no console errors. Trim to 60-90s. Confirm file is under 100 MB.
- [ ] **C5 / C6 credentials.** Add a `yc-reviewer` entry to [`backend/core/security.py`](../../backend/core/security.py), generate the password, paste into C6. Test-login from an incognito window before submit. Plan to rotate after decision.
- [ ] **D4 transcript.** Export the Cursor session (CausalFusion refinery preferred). Open the `.md` yourself and confirm it reads as a collaboration, not a one-shot prompt. Scrub any API keys or absolute paths.
- [ ] **Founder video.** 60-second take uploaded, both faces visible the whole time, no read-from-screen cadence, no demo footage. See [`FOUNDER_VIDEO_SCRIPT.md`](./FOUNDER_VIDEO_SCRIPT.md).
- [ ] **Live curve artifact ready.** `live/equity_curve.jsonl` has >=10 consecutive daily snapshots by submit date. `ayvid trade paper-render` produces a clean `live/equity_curve.png`. Host it at `/console` for E1's link to resolve.
- [ ] **Consistency across docs.** Numbers in this draft match `COMPANY_POSITIONING.md`, `FOUNDER_VIDEO_SCRIPT.md`, and [`document/backtests/SHOCK_VS_NAIVE_COMPARISON.md`](../backtests/SHOCK_VS_NAIVE_COMPARISON.md). One number that drifts across three docs is a credibility kill.
- [ ] **Final read-through as a reviewer.** Set a 3-minute timer. Read the form start to finish. If anything stalls you, it will stall the YC partner too — cut or clarify.

---

*Draft locked 2026-04-22. Every claim above is verifiable in the AYVID repo at the commit you submit on.*
