"""Continuous ingestion pipeline — background worker that runs the full
data->NER->graph->analysis cycle on a configurable schedule.

This is the heartbeat of AYVID: continuously scanning for new shocks,
ingesting market data, updating the Neo4j graph, and generating signals.

Implements tiered ingestion:
  Tier 1 (supply-chain-critical): Full-frequency ingestion every cycle
  Tier 2 (broader universe): Reduced-frequency ingestion (every N cycles)
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import TYPE_CHECKING, Any

from shared.config import settings
from shared.logger import get_logger
from shared.types import CausalEvent, TradeSignal

if TYPE_CHECKING:
    from execution_engine.signal_book import PrecomputedSignal

log = get_logger("pipeline.worker")

_running = False
_last_cycle: dt.datetime | None = None
_cycle_count = 0
_cycle_results: list[dict[str, Any]] = []

_NER_BATCH_SIZE: int = settings.NER_BATCH_SIZE
_NER_CONCURRENCY: int = settings.NER_CONCURRENCY
_TIER2_CYCLE_INTERVAL: int = settings.TIER2_CYCLE_INTERVAL


async def _build_tiered_watchlists() -> tuple[list[str], list[str]]:
    """Build Tier 1 (supply-chain critical) and Tier 2 (broader universe) watchlists
    from the universe builder instead of using a hardcoded list.
    """
    try:
        from data_primitives.extractors.universe_builder import (
            GLOBAL_EQUITY_SCREENS,
            build_equity_universe,
        )

        await build_equity_universe()

        tier1: list[str] = list(GLOBAL_EQUITY_SCREENS.get("SUPPLY_CHAIN_CRITICAL", []))
        tier1_set = set(tier1)

        tier2: list[str] = []
        for region, tickers in GLOBAL_EQUITY_SCREENS.items():
            if region == "SUPPLY_CHAIN_CRITICAL":
                continue
            for t in tickers:
                if t not in tier1_set:
                    tier2.append(t)
                    tier1_set.add(t)

        log.info(f"Universe loaded: Tier1={len(tier1)} tickers, Tier2={len(tier2)} tickers")
        return tier1, tier2
    except Exception as exc:
        log.warning(f"Universe build failed, using fallback watchlist: {exc}")
        return _get_fallback_watchlist(), []


def _get_fallback_watchlist() -> list[str]:
    """Fallback supply-chain-critical watchlist when universe builder fails."""
    return [
        "TSM",
        "NVDA",
        "AAPL",
        "ASML",
        "AMZN",
        "MSFT",
        "GOOG",
        "AMD",
        "INTC",
        "QCOM",
        "TSLA",
        "BA",
        "SHEL",
        "BHP",
        "005930.KS",
        "2317.TW",
        "AMKBY",
        "META",
        "AVGO",
    ]


async def run_ingestion_cycle(
    watchlist: list[str] | None = None,
    run_analysis: bool = True,
) -> dict[str, Any]:
    """Execute one full ingestion cycle:

    1. Build tiered watchlists from the universe builder
    2. Fetch global news + company news for active tickers
    3. Run NER pipeline -> extract entities -> upsert to Neo4j
    4. Store events in Qdrant for semantic search
    5. Ingest OHLCV data -> write to QuestDB/local
    6. Detect shocks & run causal analysis
    7. Publish results via SSE
    """
    global _last_cycle, _cycle_count

    cycle_start = dt.datetime.now(dt.UTC)
    _cycle_count += 1
    cycle_id = _cycle_count
    log.info(f"[Cycle {cycle_id}] Starting ingestion cycle")

    result: dict[str, Any] = {
        "cycle_id": cycle_id,
        "started_at": cycle_start.isoformat(),
        "watchlist_size": 0,
        "tier1_size": 0,
        "tier2_size": 0,
        "news_fetched": 0,
        "entities_extracted": 0,
        "events_created": 0,
        "bars_ingested": 0,
        "signals_generated": 0,
        "errors": [],
    }

    if watchlist is not None:
        tier1 = watchlist
        tier2: list[str] = []
    else:
        tier1, tier2 = await _build_tiered_watchlists()

    is_tier2_cycle = (cycle_id % _TIER2_CYCLE_INTERVAL == 0) and len(tier2) > 0
    active_watchlist = tier1 + (tier2 if is_tier2_cycle else [])
    result["watchlist_size"] = len(active_watchlist)
    result["tier1_size"] = len(tier1)
    result["tier2_size"] = len(tier2) if is_tier2_cycle else 0

    if is_tier2_cycle:
        log.info(
            f"[Cycle {cycle_id}] Tier 2 cycle — ingesting {len(active_watchlist)} total tickers"
        )

    # -- Step 1: Fetch News (OpenBB + Reddit + StockTwits + Finnhub) ----
    all_news = []
    try:
        from data_primitives.extractors.news_scraper import scrape_news_for_tickers
        from data_primitives.extractors.openbb_client import fetch_world_news

        fetch_tasks: list = [
            fetch_world_news(limit=50),
            scrape_news_for_tickers(active_watchlist, limit_per_ticker=10),
        ]

        try:
            from data_primitives.extractors.reddit_client import fetch_reddit_batch

            fetch_tasks.append(fetch_reddit_batch(active_watchlist[:20], limit_per=5))
        except ImportError:
            pass

        try:
            from data_primitives.extractors.finnhub_client import fetch_finnhub_news

            for t in active_watchlist[:10]:
                fetch_tasks.append(fetch_finnhub_news(t, days_back=3))
        except ImportError:
            pass

        results_raw = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for r in results_raw:
            if isinstance(r, Exception):
                log.debug(f"News source failed: {r}")
                continue
            if isinstance(r, list):
                all_news.extend(r)

        all_news.sort(key=lambda n: n.timestamp)

        result["news_fetched"] = len(all_news)
        log.info(f"[Cycle {cycle_id}] Fetched {len(all_news)} news items (multi-source)")
    except Exception as exc:
        result["errors"].append(f"News fetch: {exc}")
        log.error(f"[Cycle {cycle_id}] News fetch failed: {exc}")

    # -- Step 2: NER Pipeline -> Neo4j (batched with configurable concurrency) ----
    events: list[CausalEvent] = []
    try:
        from data_primitives.extractors.ner_pipeline import process_news_batch

        total_batches = (len(all_news) + _NER_BATCH_SIZE - 1) // _NER_BATCH_SIZE if all_news else 0
        for batch_idx, batch_start in enumerate(range(0, len(all_news), _NER_BATCH_SIZE)):
            batch = all_news[batch_start : batch_start + _NER_BATCH_SIZE]
            log.info(
                f"[Cycle {cycle_id}] NER batch {batch_idx + 1}/{total_batches} "
                f"({len(batch)} items, concurrency={_NER_CONCURRENCY})"
            )
            batch_events = await process_news_batch(batch, concurrency=_NER_CONCURRENCY)
            events.extend(batch_events)

        result["events_created"] = len(events)
        log.info(
            f"[Cycle {cycle_id}] NER produced {len(events)} events from {len(all_news)} news items"
        )
    except Exception as exc:
        result["errors"].append(f"NER pipeline: {exc}")
        log.error(f"[Cycle {cycle_id}] NER pipeline failed: {exc}")

    # -- Step 3: Store Events in Qdrant ----
    try:
        from graph_intelligence.tools.qdrant_tools import store_event

        stored = 0
        for event in events:
            try:
                await store_event(event)
                stored += 1
            except Exception as exc:
                log.debug(f"Qdrant event store failed: {exc}")
        log.info(f"[Cycle {cycle_id}] Stored {stored} events in Qdrant")
    except Exception as exc:
        result["errors"].append(f"Qdrant storage: {exc}")

    # -- Step 4: Ingest Market Data ----
    try:
        from backend.services.orchestrator import ingest_market_data

        bar_counts = await ingest_market_data(active_watchlist)
        result["bars_ingested"] = sum(bar_counts.values())
        log.info(
            f"[Cycle {cycle_id}] Ingested {result['bars_ingested']} bars for {len(active_watchlist)} tickers"
        )
    except Exception as exc:
        result["errors"].append(f"Market data: {exc}")
        log.error(f"[Cycle {cycle_id}] Market data ingestion failed: {exc}")

    # -- Step 5: Detect Shocks & Run Analysis ----
    # When shocks are detected, immediately invalidate stale cached signals
    # and fire emergency re-compute (Tier 3 override) so the fast path
    # doesn't trade on pre-shock data.
    signals: list[TradeSignal] = []
    if run_analysis and events:
        try:
            shock_tickers = _detect_shock_tickers(events)
            if shock_tickers:
                log.info(f"[Cycle {cycle_id}] Detected shocks: {shock_tickers}")

                override_results = await emergency_recompute(
                    shocked_tickers=shock_tickers,
                    events=events,
                    trigger=f"cycle_{cycle_id}_shock",
                )
                result["shock_overrides"] = override_results

                from graph_intelligence.pipeline import run_causal_analysis

                for ticker in shock_tickers:
                    ticker_events = [e for e in events if e.source_node == ticker]
                    signal = await run_causal_analysis(
                        shock_ticker=ticker,
                        timeline=ticker_events,
                        sequence_id=max(e.sequence_id or 0 for e in ticker_events),
                    )
                    if signal:
                        signals.append(signal)

                result["signals_generated"] = len(signals)
        except Exception as exc:
            result["errors"].append(f"Analysis: {exc}")
            log.error(f"[Cycle {cycle_id}] Causal analysis failed: {exc}")

    # -- Step 6: Publish SSE Update ----
    try:
        from backend.api.sse.streamer import publish_event

        await publish_event(
            "cycle_complete",
            {
                "cycle_id": cycle_id,
                "news": result["news_fetched"],
                "events": result["events_created"],
                "signals": result["signals_generated"],
                "tier2_active": is_tier2_cycle,
            },
        )
        for signal in signals:
            await publish_event("trade_signal", signal.model_dump(mode="json"))
    except Exception as exc:
        log.warning(f"SSE event publish failed: {exc}")

    # -- Step 7: Pre-Compute Signal Book (fast execution cache) ----
    precompute_count = 0
    try:
        precompute_count = await pre_compute_signals(active_watchlist)
        result["precomputed_signals"] = precompute_count
    except Exception as exc:
        result["errors"].append(f"Signal pre-compute: {exc}")
        log.warning(f"[Cycle {cycle_id}] Signal pre-compute failed: {exc}")

    # -- Step 8: Auto-Execute from Signal Book (gated by AUTO_EXECUTE) --
    auto_executed = 0
    if getattr(settings, "AUTO_EXECUTE", False):
        try:
            from backend.services.orchestrator import auto_execute_from_book

            executed_signals = await auto_execute_from_book()
            auto_executed = len(executed_signals)
            result["auto_executed"] = auto_executed
            if auto_executed:
                log.info(
                    f"[Cycle {cycle_id}] Auto-executed {auto_executed} trades from signal book"
                )
        except Exception as exc:
            result["errors"].append(f"Auto-execute: {exc}")
            log.warning(f"[Cycle {cycle_id}] Auto-execute failed: {exc}")

    _last_cycle = dt.datetime.now(dt.UTC)
    elapsed = (_last_cycle - cycle_start).total_seconds()
    result["elapsed_seconds"] = elapsed
    _cycle_results.append(result)
    if len(_cycle_results) > 100:
        _cycle_results.pop(0)

    log.info(
        f"[Cycle {cycle_id}] Complete in {elapsed:.1f}s — "
        f"news={result['news_fetched']} events={result['events_created']} "
        f"signals={result['signals_generated']} precomputed={precompute_count} "
        f"auto_executed={auto_executed}"
    )
    return result


async def pre_compute_signals(equity_tickers: list[str] | None = None) -> int:
    """Pre-compute trading signals for all tradeable indices and top equities.

    Populates the global SignalBook so the fast execution path can trade
    with zero LLM latency. This is the "morning meeting" — heavy analysis
    runs here, live trading just reads the book.

    Returns the number of signals written to the book.
    """
    import time

    import numpy as np

    from execution_engine.math.regime import (
        classify_trend,
        classify_volatility,
        compute_regime_fingerprint,
    )
    from execution_engine.math.signals import (
        compute_atr,
        compute_hurst_exponent,
        compute_rsi,
        compute_trend_strength,
        compute_volume_profile,
    )
    from execution_engine.signal_book import PrecomputedSignal, get_signal_book
    from graph_intelligence.agents.strategy_orchestrator import (
        TRADEABLE_INDEX_SYMBOLS,
        MarketContext,
        rule_based_orchestrate,
    )

    book = get_signal_book()
    t0 = time.perf_counter()
    computed = 0

    all_tickers: list[tuple[str, str]] = [(sym, "INDEX") for sym in TRADEABLE_INDEX_SYMBOLS]
    if equity_tickers:
        all_tickers.extend((t, "EQUITY") for t in equity_tickers[:50])

    # Multi-asset universe: FX, commodity, crypto, ETFs
    from data_primitives.extractors.universe_builder import (
        CRYPTO_ASSETS,
        GLOBAL_EQUITY_SCREENS,
        MAJOR_FX_PAIRS,
    )
    from shared.contract_specs import COMMODITY_CONTRACT_SPECS

    all_tickers.extend((pair[0], "FX") for pair in MAJOR_FX_PAIRS[:20])
    all_tickers.extend((sym, "COMMODITY") for sym in list(COMMODITY_CONTRACT_SPECS.keys())[:12])
    all_tickers.extend((sym, "CRYPTO") for sym in CRYPTO_ASSETS[:15])
    all_tickers.extend((sym, "ETF") for sym in GLOBAL_EQUITY_SCREENS.get("MAJOR_ETFS", [])[:20])

    for ticker, asset_class in all_tickers:
        try:
            from data_primitives.extractors.openbb_client import fetch_ohlcv

            bars = await fetch_ohlcv(ticker, days=120)
            if len(bars) < 30:
                continue

            closes = np.array([b.close for b in bars])
            highs = np.array([b.high for b in bars])
            lows = np.array([b.low for b in bars])
            volumes = np.array([b.volume for b in bars], dtype=float)

            log_returns = (
                np.diff(np.log(closes[-31:])) if len(closes) > 31 else np.diff(np.log(closes))
            )
            hurst = compute_hurst_exponent(log_returns)
            rsi = compute_rsi(closes, 14)
            atr_val = compute_atr(highs, lows, closes, 14)
            trend = compute_trend_strength(closes, short=10, long=30)
            vol_ratio = compute_volume_profile(volumes, 20)

            vol_regime, rv, _ = classify_volatility(log_returns)
            trend_regime, _ = classify_trend(highs, lows, closes, hurst)

            price = float(closes[-1])
            z_score = 0.0
            if len(closes) >= 20:
                mu = float(np.mean(closes[-20:]))
                sd = float(np.std(closes[-20:], ddof=1))
                if sd > 1e-10:
                    z_score = (price - mu) / sd

            ret_5d = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
            ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

            regime_fp = compute_regime_fingerprint(closes, highs, lows, volumes, hurst)

            ctx = MarketContext(
                ticker=ticker,
                current_price=price,
                volatility_regime=vol_regime.value,
                trend_regime=trend_regime.value,
                hurst=hurst,
                rsi=rsi,
                atr=atr_val,
                realized_vol=rv,
                asset_class=asset_class,
                z_score=z_score,
                trend_strength=trend,
                volume_ratio=vol_ratio,
                ret_5d=ret_5d,
                ret_20d=ret_20d,
            )

            decision = rule_based_orchestrate(ctx)

            if decision.action == "SKIP":
                continue

            sig = PrecomputedSignal(
                ticker=ticker,
                direction=decision.direction,
                instrument_type=decision.instrument_type,
                conviction=decision.conviction,
                strategy_name=decision.strategy_name,
                strike=decision.strike,
                expiry_date="",
                stop_loss_pct=decision.stop_loss_pct,
                take_profit_pct=decision.take_profit_pct,
                rationale=decision.rationale,
                computed_at=dt.datetime.now(dt.UTC).isoformat(),
                regime_fingerprint=regime_fp,
                expected_r=decision.expected_r,
                min_r=decision.min_r,
                entry_price_at_compute=price,
                second_leg=decision.second_leg or {},
            )
            book.upsert(sig)
            computed += 1

        except Exception as exc:
            log.debug(f"Pre-compute failed for {ticker}: {exc}")
            continue

    elapsed_ms = (time.perf_counter() - t0) * 1000
    book.record_precompute(elapsed_ms)
    book.save_to_disk()

    log.info(
        f"Signal book pre-computed: {computed} signals in {elapsed_ms:.0f}ms "
        f"(book size={book.size()})"
    )
    return computed


def _detect_shock_tickers(events: list[CausalEvent]) -> list[str]:
    """Identify tickers experiencing high-confidence shock events."""
    from shared.types import EventType

    shock_types = {
        EventType.SHOCK,
        EventType.GEOPOLITICAL,
        EventType.REGULATORY,
        EventType.COMMODITY_SHOCK,
        EventType.FX_SHOCK,
    }

    ticker_scores: dict[str, float] = {}
    for event in events:
        if event.event_type in shock_types and event.confidence > 0.35:
            ticker_scores[event.source_node] = (
                ticker_scores.get(event.source_node, 0.0) + event.confidence
            )

    sorted_tickers = sorted(ticker_scores, key=ticker_scores.get, reverse=True)
    return sorted_tickers[:10]


_emergency_tasks: dict[str, asyncio.Task] = {}


async def emergency_recompute(
    shocked_tickers: list[str],
    events: list[CausalEvent] | None = None,
    trigger: str = "shock_detected",
) -> dict[str, str]:
    """Tier 3 shock interception — invalidate stale signals and re-compute
    immediately for affected tickers without waiting for the next 15-min cycle.

    Flow:
    1. Immediately invalidate all cached signals for shocked tickers
    2. Mark each ticker as override-pending (fast path will refuse to trade)
    3. Fire async re-compute for each ticker:
       a. Fetch fresh OHLCV
       b. Re-run rule_based_orchestrate with current data
       c. Optionally run full LangGraph causal analysis if events are available
       d. Upsert new signal to book
    4. Clear override-pending flag (fast path unblocked with fresh signal)

    This is the critical path that prevents the fast path from blindly
    executing on a stale, pre-shock cache.
    """
    import time

    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    results: dict[str, str] = {}

    for ticker in shocked_tickers:
        evicted = book.invalidate(ticker, reason=trigger)
        book.mark_override_pending(ticker)
        log.warning(
            f"SHOCK INTERCEPT: {ticker} — evicted {evicted} stale signals, "
            f"override pending, fast path blocked"
        )

    async def _recompute_one(ticker: str) -> None:
        t0 = time.perf_counter()
        try:
            await _emergency_recompute_single(ticker, events, trigger)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            results[ticker] = f"upgraded in {elapsed_ms:.0f}ms"
            log.info(
                f"SHOCK OVERRIDE COMPLETE: {ticker} — fresh signal in book ({elapsed_ms:.0f}ms)"
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            results[ticker] = f"failed: {exc}"
            log.error(f"SHOCK OVERRIDE FAILED: {ticker} — {exc} ({elapsed_ms:.0f}ms)")
        finally:
            book.clear_override_pending(ticker)

    tasks = [_recompute_one(ticker) for ticker in shocked_tickers]
    await asyncio.gather(*tasks, return_exceptions=True)

    return results


async def fire_emergency_recompute_background(
    shocked_tickers: list[str],
    events: list[CausalEvent] | None = None,
    trigger: str = "shock_detected",
) -> None:
    """Non-blocking version: fire emergency re-compute as a background task.

    Used by the SSE event handler or news listener to kick off Tier 3
    overrides without blocking the calling coroutine.
    """
    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()
    for ticker in shocked_tickers:
        book.invalidate(ticker, reason=trigger)
        book.mark_override_pending(ticker)

    task = asyncio.create_task(emergency_recompute(shocked_tickers, events, trigger))
    for ticker in shocked_tickers:
        _emergency_tasks[ticker] = task
    log.warning(f"Background emergency recompute fired for {shocked_tickers}")


def _analyze_shock_events(
    ticker: str,
    events: list[CausalEvent],
) -> tuple[bool, str, float, str]:
    """Extract directional shock signal from raw CausalEvent metadata.

    This is the critical function that makes the fast-pass "news-aware."
    Instead of relying on trailing price indicators that haven't priced in
    the shock yet, we derive direction and magnitude from the *event itself*.

    Returns (is_shock, source_summary, magnitude, implied_direction).
    - magnitude: positive = bullish shock, negative = bearish shock
    - implied_direction: "LONG" | "SHORT" | "HOLD"
    """
    from shared.types import EventType

    if not events:
        return False, "", 0.0, "HOLD"

    ticker_events = [e for e in events if e.source_node == ticker]
    if not ticker_events:
        ticker_events = events

    bearish_types = {
        EventType.SHOCK,
        EventType.GEOPOLITICAL,
        EventType.REGULATORY,
        EventType.FX_SHOCK,
    }
    bullish_types = {EventType.RECOVERY}
    ambiguous_types = {EventType.COMMODITY_SHOCK, EventType.MACRO, EventType.EARNINGS}

    bearish_weight = 0.0
    bullish_weight = 0.0
    sources: list[str] = []
    max_confidence = 0.0

    for ev in ticker_events:
        conf = ev.confidence
        max_confidence = max(max_confidence, conf)
        sources.append(ev.compressed_summary[:60])

        if ev.event_type in bearish_types:
            bearish_weight += conf
        elif ev.event_type in bullish_types:
            bullish_weight += conf
        elif ev.event_type in ambiguous_types:
            summary_lower = ev.compressed_summary.lower()
            negative_keywords = {
                "crash",
                "plunge",
                "decline",
                "tariff",
                "sanction",
                "war",
                "default",
                "bankrupt",
                "layoff",
                "recall",
                "shortage",
                "downgrade",
                "sell",
                "dump",
                "collapse",
                "fear",
                "panic",
                "loss",
                "miss",
                "miss",
                "cut",
                "suspend",
                "halt",
            }
            positive_keywords = {
                "surge",
                "rally",
                "beat",
                "upgrade",
                "grow",
                "expand",
                "deal",
                "approve",
                "launch",
                "break",
                "record",
                "gain",
                "profit",
                "dividend",
                "buyback",
                "recovery",
            }
            neg_hits = sum(1 for kw in negative_keywords if kw in summary_lower)
            pos_hits = sum(1 for kw in positive_keywords if kw in summary_lower)
            if neg_hits > pos_hits:
                bearish_weight += conf * 0.7
            elif pos_hits > neg_hits:
                bullish_weight += conf * 0.7
            else:
                bearish_weight += conf * 0.3

    net = bullish_weight - bearish_weight
    total_weight = bullish_weight + bearish_weight

    if total_weight < 0.3:
        return False, "", 0.0, "HOLD"

    magnitude = net / max(total_weight, 1.0) * max_confidence * 0.10

    if net < -0.3:
        direction = "SHORT"
    elif net > 0.3:
        direction = "LONG"
    else:
        direction = "HOLD"

    source_summary = " | ".join(sources[:3])

    log.info(
        f"Shock analysis for {ticker}: direction={direction}, "
        f"magnitude={magnitude:+.3f}, "
        f"bearish={bearish_weight:.2f}, bullish={bullish_weight:.2f}, "
        f"events={len(ticker_events)}"
    )

    return True, source_summary, magnitude, direction


async def _emergency_recompute_single(
    ticker: str,
    events: list[CausalEvent] | None,
    trigger: str,
) -> None:
    """Re-compute a single ticker's signal after a shock.

    The fast pass is NEWS-AWARE: it injects shock event metadata (direction,
    magnitude, source) into the MarketContext so the rule-based orchestrator
    fires its shock-specific code paths instead of looking at trailing
    indicators that haven't priced in the event yet.

    If no shock events are provided or the shock analysis is ambiguous,
    the fast pass writes a defensive HOLD signal — refusing to let the
    fast path trade on stale pre-shock technicals.

    The full LLM pass runs second and can upgrade the signal once the
    complete causal analysis is available.
    """
    import numpy as np

    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from execution_engine.math.regime import (
        classify_trend,
        classify_volatility,
        compute_regime_fingerprint,
    )
    from execution_engine.math.signals import (
        compute_atr,
        compute_hurst_exponent,
        compute_rsi,
        compute_trend_strength,
        compute_volume_profile,
    )
    from execution_engine.signal_book import PrecomputedSignal, get_signal_book
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        rule_based_orchestrate,
    )

    book = get_signal_book()

    # --- Shock event analysis (news-aware, not price-aware) ---
    is_shock, shock_source, shock_magnitude, shock_direction = _analyze_shock_events(
        ticker, events or []
    )

    # --- Fetch OHLCV for technical context ---
    bars = await fetch_ohlcv(ticker, days=120)
    if len(bars) < 30:
        log.warning(f"Emergency recompute: {ticker} has only {len(bars)} bars")
        return

    closes = np.array([b.close for b in bars])
    highs = np.array([b.high for b in bars])
    lows = np.array([b.low for b in bars])
    volumes = np.array([b.volume for b in bars], dtype=float)

    log_returns = np.diff(np.log(closes[-31:])) if len(closes) > 31 else np.diff(np.log(closes))
    hurst = compute_hurst_exponent(log_returns)
    rsi = compute_rsi(closes, 14)
    atr_val = compute_atr(highs, lows, closes, 14)
    trend = compute_trend_strength(closes, short=10, long=30)
    vol_ratio = compute_volume_profile(volumes, 20)

    vol_regime, rv, _ = classify_volatility(log_returns)
    trend_regime, _ = classify_trend(highs, lows, closes, hurst)

    price = float(closes[-1])
    z_score = 0.0
    if len(closes) >= 20:
        mu = float(np.mean(closes[-20:]))
        sd = float(np.std(closes[-20:], ddof=1))
        if sd > 1e-10:
            z_score = (price - mu) / sd

    ret_5d = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
    ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

    regime_fp = compute_regime_fingerprint(closes, highs, lows, volumes, hurst)

    # --- Fast pass: NEWS-AWARE orchestrator call ---
    # Wire the shock event metadata into MarketContext so the orchestrator's
    # shock-specific paths fire correctly, even when trailing price indicators
    # still show pre-shock equilibrium.
    # Detect asset class from ticker format
    _fx_pairs = {"EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "USD", "NOK", "SEK", "SGD", "HKD", "MXN", "ZAR", "TRY", "CNH", "INR", "BRL", "KRW", "PLN"}
    _crypto = {"BTC", "ETH", "SOL", "BNB", "ADA", "XRP", "DOGE", "DOT", "AVAX", "MATIC", "LINK", "UNI", "AAVE", "LTC", "ATOM", "NEAR", "ARB", "OP", "APT", "SUI", "FIL", "INJ"}
    if ticker.startswith("^"):
        asset_class = "INDEX"
    elif len(ticker) == 6 and ticker[:3] in _fx_pairs and ticker[3:] in _fx_pairs:
        asset_class = "FX"
    elif ticker in _crypto or ticker.endswith("USD") and ticker[:-3] in _crypto:
        asset_class = "CRYPTO"
    else:
        asset_class = "EQUITY"
    ctx = MarketContext(
        ticker=ticker,
        current_price=price,
        volatility_regime=vol_regime.value,
        trend_regime=trend_regime.value,
        hurst=hurst,
        rsi=rsi,
        atr=atr_val,
        realized_vol=rv,
        asset_class=asset_class,
        z_score=z_score,
        trend_strength=trend,
        volume_ratio=vol_ratio,
        ret_5d=ret_5d,
        ret_20d=ret_20d,
        supply_chain_shock=is_shock,
        shock_source=shock_source,
        shock_magnitude=shock_magnitude,
    )

    decision = rule_based_orchestrate(ctx)

    # --- Safety net: if the orchestrator returned EXECUTE based on benign
    # technicals despite an active shock, check whether the decision
    # direction conflicts with the shock's implied direction.
    # If a bearish shock hit the wire and the orchestrator says LONG
    # (because RSI/EMA still look calm), override to HOLD.
    if is_shock and decision.action == "EXECUTE" and shock_direction != "HOLD":
        technicals_agree = decision.direction == shock_direction
        shock_is_strong = abs(shock_magnitude) >= 0.03

        if not technicals_agree and shock_is_strong:
            log.warning(
                f"SHOCK SAFETY NET: {ticker} — orchestrator says "
                f"{decision.direction} but shock implies {shock_direction} "
                f"(magnitude={shock_magnitude:+.3f}). Overriding to "
                f"defensive HOLD until LLM pass confirms."
            )
            sig = PrecomputedSignal(
                ticker=ticker,
                direction="HOLD",
                instrument_type=decision.instrument_type,
                conviction=0.0,
                strategy_name="shock_safety_hold",
                strike=None,
                expiry_date="",
                stop_loss_pct=decision.stop_loss_pct,
                take_profit_pct=decision.take_profit_pct,
                rationale=(
                    f"[SHOCK SAFETY HOLD: {trigger}] Technicals say "
                    f"{decision.direction} but news implies {shock_direction} "
                    f"(magnitude={shock_magnitude:+.3f}, source={shock_source[:80]}). "
                    f"Waiting for LLM confirmation."
                ),
                computed_at=dt.datetime.now(dt.UTC).isoformat(),
                ttl_minutes=5,
                regime_fingerprint=regime_fp,
                expected_r=0.0,
                min_r=0.0,
                entry_price_at_compute=price,
                tier3_shock=True,
            )
            book.upsert(sig)
            log.info(
                f"Emergency fast pass: {ticker} -> HOLD (shock safety net, awaiting LLM pass)"
            )

            await _run_full_llm_pass(
                ticker,
                events,
                trigger,
                sig,
                regime_fp,
                price,
            )
            return

    # --- Standard fast pass: shock-aware orchestrator agreed with events ---
    sig = PrecomputedSignal(
        ticker=ticker,
        direction=decision.direction if decision.action == "EXECUTE" else "HOLD",
        instrument_type=decision.instrument_type,
        conviction=decision.conviction if decision.action == "EXECUTE" else 0.0,
        strategy_name=decision.strategy_name,
        strike=decision.strike,
        expiry_date="",
        stop_loss_pct=decision.stop_loss_pct,
        take_profit_pct=decision.take_profit_pct,
        rationale=(
            f"[SHOCK OVERRIDE: {trigger}] "
            f"{'News-aware: ' + shock_source[:60] + ' | ' if is_shock else ''}"
            f"{decision.rationale}"
        ),
        computed_at=dt.datetime.now(dt.UTC).isoformat(),
        ttl_minutes=10 if is_shock else 15,
        regime_fingerprint=regime_fp,
        expected_r=decision.expected_r,
        min_r=decision.min_r,
        entry_price_at_compute=price,
        second_leg=decision.second_leg or {},
        tier3_shock=True,
    )
    book.upsert(sig)
    log.info(
        f"Emergency fast pass: {ticker} -> {sig.direction} "
        f"@ {sig.conviction:.2f} "
        f"({'news-aware' if is_shock else 'rule-based'})"
    )

    # --- Full LLM pass (optional) ---
    await _run_full_llm_pass(ticker, events, trigger, sig, regime_fp, price)


async def _run_full_llm_pass(
    ticker: str,
    events: list[CausalEvent] | None,
    trigger: str,
    fast_pass_sig: PrecomputedSignal,
    regime_fp: str,
    price: float,
) -> None:
    """Run the full LangGraph causal analysis and upgrade the fast-pass
    signal if the LLM produces a higher-conviction result."""
    if not events:
        return

    from execution_engine.signal_book import PrecomputedSignal, get_signal_book

    book = get_signal_book()
    ticker_events = [e for e in events if e.source_node == ticker]
    if not ticker_events:
        return

    try:
        from graph_intelligence.pipeline import run_causal_analysis

        full_signal = await run_causal_analysis(
            shock_ticker=ticker,
            timeline=ticker_events,
            sequence_id=max(e.sequence_id or 0 for e in ticker_events),
        )

        if full_signal and full_signal.confidence > fast_pass_sig.conviction:
            direction = full_signal.action.value
            upgraded = PrecomputedSignal(
                ticker=ticker,
                direction=direction,
                instrument_type=fast_pass_sig.instrument_type,
                conviction=full_signal.confidence,
                strategy_name=f"causal_{fast_pass_sig.strategy_name}",
                strike=fast_pass_sig.strike,
                expiry_date="",
                stop_loss_pct=fast_pass_sig.stop_loss_pct,
                take_profit_pct=fast_pass_sig.take_profit_pct,
                rationale=(f"[SHOCK LLM UPGRADE: {trigger}] {full_signal.rationale[:200]}"),
                computed_at=dt.datetime.now(dt.UTC).isoformat(),
                ttl_minutes=15,
                regime_fingerprint=regime_fp,
                expected_r=fast_pass_sig.expected_r,
                min_r=fast_pass_sig.min_r,
                entry_price_at_compute=price,
                second_leg=fast_pass_sig.second_leg,
                tier3_shock=True,
            )
            book.upsert(upgraded)
            log.info(
                f"Emergency LLM upgrade: {ticker} -> {direction} "
                f"@ {full_signal.confidence:.2f} (causal analysis)"
            )
    except Exception as exc:
        log.debug(f"Emergency LLM analysis failed for {ticker}: {exc}")


async def start_continuous_pipeline(
    interval_minutes: int = 15,
    watchlist: list[str] | None = None,
) -> None:
    """Run the ingestion pipeline continuously on a schedule."""
    global _running
    _running = True
    log.info(f"Starting continuous pipeline (interval={interval_minutes}min)")

    while _running:
        try:
            await run_ingestion_cycle(watchlist=watchlist)
        except Exception as exc:
            log.error(f"Pipeline cycle failed: {exc}")

        # Weekly critical-node discovery refresh
        try:
            await _maybe_refresh_critical_nodes(_cycle_count)
        except Exception as exc:
            log.warning(f"Critical-node refresh failed: {exc}")

        await asyncio.sleep(interval_minutes * 60)


_WEEKLY_DISCOVERY_INTERVAL = 7 * 24  # cycles between critical node refresh
_last_discovery_cycle = 0


async def _maybe_refresh_critical_nodes(cycle_id: int) -> None:
    """Run weekly critical-node discovery via graph centrality + SEC EDGAR."""
    global _last_discovery_cycle
    if cycle_id - _last_discovery_cycle < _WEEKLY_DISCOVERY_INTERVAL:
        return
    _last_discovery_cycle = cycle_id
    log.info(f"[Cycle {cycle_id}] Weekly critical-node discovery starting")
    try:
        from graph_intelligence.tools.find_critical_nodes import get_finder

        finder = get_finder()
        nodes = await finder.discover_critical_nodes(max_nodes=60)
        log.info(f"[Cycle {cycle_id}] Discovered {len(nodes)} critical nodes")

        # Update the universe builder's supply-chain-critical list
        from data_primitives.extractors.universe_builder import GLOBAL_EQUITY_SCREENS

        GLOBAL_EQUITY_SCREENS["SUPPLY_CHAIN_CRITICAL"] = [n.ticker for n in nodes]
        log.info(f"[Cycle {cycle_id}] Updated SUPPLY_CHAIN_CRITICAL with {len(nodes)} tickers")
    except Exception as exc:
        log.warning(f"[Cycle {cycle_id}] Critical-node discovery failed: {exc}")


def stop_continuous_pipeline() -> None:
    """Signal the pipeline to stop after the current cycle."""
    global _running
    _running = False
    log.info("Continuous pipeline stop requested")


def get_pipeline_status() -> dict[str, Any]:
    """Return current pipeline status."""
    return {
        "running": _running,
        "cycle_count": _cycle_count,
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "recent_results": _cycle_results[-5:],
    }
