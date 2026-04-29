"""Central orchestrator — coordinates data flow between all modules.

This is the service layer between the API/CLI and the core domain logic.
Implements Strict Event Sourcing with Sequence Validation and wires in
the C++ kill switch mmap bridge for real-time risk monitoring.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from shared.config import settings
from shared.exceptions import GraphStaleError, SequenceValidationError
from shared.executors import (
    ingestion_plane,
)
from shared.logger import get_logger
from shared.sequence_validator import (
    SequenceMiddleware,
    enforce_pre_trade_sync,
    sequence_registry,
)

# One module-level middleware binding so every write goes through a
# single registry. This replaces the direct `sequence_registry.register_write`
# calls scattered across the ingestion path.
_seq_middleware = SequenceMiddleware(registry=sequence_registry)
from shared.types import (
    AlphaHorizon,
    AlphaScore,
    OHLCVBar,
    PortfolioState,
    Position,
    RiskSnapshot,
    SignalStatus,
    TradeAction,
    TradeSignal,
)

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import MarketContext

log = get_logger("orchestrator")

_portfolio = PortfolioState()
_signals: list[TradeSignal] = []
_price_cache: dict[str, list[OHLCVBar]] = {}
_risk_snapshot = RiskSnapshot()
_last_graph_sequence_id: int = 0
_mmap_bridge = None
_watchdog_proc = None
_fast_execute_latency_ms: float = 0.0


def get_portfolio_state() -> PortfolioState:
    return _portfolio


def get_recent_signals(limit: int = 50) -> list[TradeSignal]:
    return _signals[-limit:]


def get_risk_snapshot() -> RiskSnapshot:
    return _risk_snapshot


def update_graph_sequence(seq_id: int) -> None:
    """Update the last known graph sequence ID after a graph write."""
    global _last_graph_sequence_id
    _last_graph_sequence_id = max(_last_graph_sequence_id, seq_id)
    sequence_registry.register_write("neo4j", seq_id)


async def init_kill_switch() -> None:
    """Initialize the C++ kill switch mmap bridge and watchdog process."""
    global _mmap_bridge, _watchdog_proc
    try:
        from execution_engine.watchdog.mmap_bridge import RiskMmapBridge, start_watchdog

        _mmap_bridge = RiskMmapBridge()
        _mmap_bridge.update(
            drawdown_pct=0.0,
            var_95=0.0,
            var_99=0.0,
            portfolio_equity=_portfolio.total_equity,
            peak_equity=_portfolio.peak_equity,
            max_drawdown_pct=settings.MAX_DRAWDOWN_PCT,
        )
        _watchdog_proc = start_watchdog(_mmap_bridge)
        log.info("[bold green]C++ kill switch armed[/bold green]")
    except Exception as exc:
        log.warning(f"Kill switch init failed (non-fatal): {exc}")


def _update_kill_switch() -> None:
    """Push latest risk state to the mmap bridge for the C++ watchdog."""
    if _mmap_bridge is None:
        return
    try:
        _mmap_bridge.update(
            drawdown_pct=_portfolio.drawdown_pct,
            var_95=_risk_snapshot.value_at_risk_95,
            var_99=_risk_snapshot.value_at_risk_99,
            portfolio_equity=_portfolio.total_equity,
            peak_equity=_portfolio.peak_equity,
            max_drawdown_pct=settings.MAX_DRAWDOWN_PCT,
            max_var_pct=settings.MAX_VAR_PCT,
        )
    except Exception as exc:
        log.debug(f"Kill switch mmap update failed: {exc}")


async def kill_switch_writer_loop(interval: float = 0.1) -> None:
    """10Hz task that keeps the kill-switch mmap fresh for the C++ watchdog.

    The watchdog reads the seqlock-protected struct every 100ms. If the
    Python writer stops ticking, the struct goes stale and the watchdog
    escalates (which is desired: a frozen trading loop should trigger
    the kill path). This loop runs for the entire lifetime of the
    process and is cancelled on shutdown.
    """
    log.info(f"Kill-switch writer loop started ({1.0 / interval:.0f}Hz)")
    try:
        while True:
            _update_kill_switch()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Kill-switch writer loop cancelled")
        raise


async def stop_kill_switch() -> None:
    """Terminate the C++ watchdog and release the mmap file."""
    global _mmap_bridge, _watchdog_proc
    if _watchdog_proc is not None:
        try:
            from execution_engine.watchdog.mmap_bridge import stop_watchdog

            stop_watchdog(_watchdog_proc)
        except Exception as exc:
            log.warning(f"stop_watchdog failed: {exc}")
        _watchdog_proc = None
    if _mmap_bridge is not None:
        try:
            _mmap_bridge.close()
        except Exception as exc:
            log.warning(f"mmap bridge close failed: {exc}")
        _mmap_bridge = None


async def ingest_market_data(tickers: list[str]) -> dict[str, int]:
    """Run the full data ingestion pipeline for a watchlist.

    P1-E: every ticker gets its OWN sequence id, strictly monotonic
    across the batch, and the module-level `_last_graph_sequence_id`
    advances after each successful write. The prior implementation
    computed `seq_id = _last_graph_sequence_id + 1` once and reused
    it for every ticker — which silently collapsed to a single write
    in the sequence registry (`register_write` only advances on
    `sequence_id > prev`). That broke strict event sourcing: the
    pre-trade sync check couldn't tell a multi-ticker batch apart
    from a single write.
    """
    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from data_primitives.loaders.hopsworks_sync import sync_all_features, sync_features
    from data_primitives.loaders.questdb_writer import write_bars_questdb

    global _last_graph_sequence_id
    results: dict[str, int] = {}

    for ticker in tickers:
        try:
            bars = await fetch_ohlcv(ticker)
            _price_cache[ticker] = bars
            # Advance AFTER fetch so a failed fetch does not consume
            # a seq slot. The in-process counter is the source of
            # truth for manual-allocator fallback behaviour and the
            # graph-freshness gate.
            _last_graph_sequence_id += 1
            seq_id = _last_graph_sequence_id
            # Wrap every cross-plane write through the middleware so the
            # sequence registry is updated ONLY on successful commit.
            count = await _seq_middleware.wrap_questdb_write(
                write_bars_questdb(bars),
                seq_id,
            )
            await sync_features(ticker, bars)
            results[ticker] = count
            try:
                await ingestion_plane().put(
                    {
                        "ticker": ticker,
                        "seq_id": seq_id,
                        "bars": len(bars),
                    }
                )
            except Exception as qexc:  # pragma: no cover — defensive
                log.debug(f"ingestion_plane.put failed: {qexc}")
        except Exception as exc:
            log.error(f"Ingestion failed for {ticker}: {exc}")
            results[ticker] = 0

    if len(_price_cache) > 1:
        try:
            await sync_all_features(_price_cache)
            log.info(f"Synced {len(_price_cache)}-asset feature matrix to Hopsworks")
        except Exception as exc:
            log.debug(f"Batch feature sync failed: {exc}")

    return results


async def run_shock_analysis(shock_ticker: str) -> TradeSignal | None:
    """Execute the full causal arbitrage pipeline with NER, compression,
    multi-source sentiment, and LLM strategy orchestration.
    """
    from data_primitives.extractors.ner_pipeline import process_news_batch
    from data_primitives.extractors.news_scraper import scrape_news_for_tickers
    from graph_intelligence.pipeline import run_causal_analysis
    from graph_intelligence.tools.qdrant_tools import store_event

    news = await scrape_news_for_tickers([shock_ticker])

    extra_news = await _fetch_alternative_news(shock_ticker)
    news.extend(extra_news)
    news.sort(key=lambda n: n.timestamp)

    events = await process_news_batch(news)

    for event in events[:20]:
        if not event.sequence_id:
            continue
        try:
            await _seq_middleware.wrap_qdrant_write(
                store_event(event),
                event.sequence_id,
            )
        except Exception as exc:
            log.debug(f"Qdrant event store failed: {exc}")

    max_seq = max((e.sequence_id or 0 for e in events), default=0)
    sequence_registry.register_write("pipeline", max_seq)

    try:
        signal = await run_causal_analysis(
            shock_ticker=shock_ticker,
            timeline=events,
            sequence_id=max_seq,
        )
    except Exception as exc:
        log.error(f"Causal analysis failed (Neo4j/LLM): {exc}")
        signal = None

    if signal:
        signal = await _apply_strategy_orchestrator(signal, shock_ticker)
        _signals.append(signal)
        try:
            from backend.api.sse.streamer import publish_event

            await publish_event("trade_signal", signal.model_dump(mode="json"))
        except Exception as exc:
            log.warning(f"SSE publish failed: {exc}")

    return signal


async def _fetch_alternative_news(ticker: str) -> list:
    """Pull supplementary news from Reddit, StockTwits, and Finnhub."""
    from shared.types import NewsItem

    all_extra: list[NewsItem] = []

    try:
        from data_primitives.extractors.reddit_client import fetch_reddit_sentiment

        reddit_posts = await fetch_reddit_sentiment(ticker, limit=10)
        all_extra.extend(reddit_posts)
        if reddit_posts:
            log.info(f"Reddit: {len(reddit_posts)} posts for {ticker}")
    except Exception as exc:
        log.debug(f"Reddit fetch skipped: {exc}")

    try:
        from data_primitives.extractors.stocktwits_client import fetch_stocktwits_sentiment

        st_posts, st_agg = await fetch_stocktwits_sentiment(ticker, limit=15)
        all_extra.extend(st_posts)
        if st_agg.get("total", 0) > 0:
            log.info(
                f"StockTwits: {st_agg['total']} messages, "
                f"bull ratio={st_agg.get('bull_ratio', 0):.0%}"
            )
    except Exception as exc:
        log.debug(f"StockTwits fetch skipped: {exc}")

    try:
        from data_primitives.extractors.finnhub_client import fetch_finnhub_news

        fh_news = await fetch_finnhub_news(ticker, days_back=7)
        all_extra.extend(fh_news)
        if fh_news:
            log.info(f"Finnhub: {len(fh_news)} articles for {ticker}")
    except Exception as exc:
        log.debug(f"Finnhub fetch skipped: {exc}")

    # Fill in per-NewsItem sentiment for items that arrived unscored
    # (Reddit, Finnhub). StockTwits items already carry ±1 from the API.
    try:
        from data_primitives.transformers.sentiment import score_news_items

        await score_news_items(all_extra)
    except Exception as exc:
        log.debug(f"Per-item sentiment scoring skipped: {exc}")

    return all_extra


async def _build_market_context(
    ticker: str,
    shock_ticker: str | None = None,
    langgraph_final_state: dict | None = None,
) -> MarketContext | None:
    """Build a full MarketContext for the LLM/rule-based orchestrator.

    Phase-3 (Gap A1 fix): delegates to the shared `build_decision_context`
    so live trading receives the SAME enriched context as the backtest —
    including vrp, vol_compression, grind_up_flag, and (when LangGraph
    supplies it) forbidden_strategies, strategy_parameters, risk_multiplier,
    causal_conviction. Previously the live orchestrator saw an
    impoverished context and silently ignored LangGraph's regime gating.
    """
    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from execution_engine.math.decision_context import build_decision_context

    bars = _price_cache.get(ticker)
    if not bars or len(bars) < 30:
        bars = await fetch_ohlcv(ticker, days=90)
    if len(bars) < 30:
        return None

    shock_ctx: dict | None = None
    if shock_ticker and shock_ticker != ticker:
        shock_ctx = {
            "source": shock_ticker,
            "magnitude": 0.0,
            "shocked_tickers": [shock_ticker],
            "beneficiary_tickers": [],
            "victim_tickers": [ticker],  # downstream target is assumed victim until refined
        }

    final_state: dict | None = None
    if langgraph_final_state:
        final_state = dict(langgraph_final_state)

    return build_decision_context(
        ticker=ticker,
        bars=bars,
        asset_class="EQUITY",
        shock_context=shock_ctx,
        final_state=final_state,
    )


async def fast_execute(
    ticker: str,
    current_price: float,
    regime_fingerprint: str = "",
) -> TradeSignal | None:
    """Sub-second execution from the pre-computed signal book. Zero LLM calls.

    This is the Tier 2 fast path: lookup cached signal -> validate price zone
    -> risk check -> submit order. Target latency: <100ms.

    Shock interception: if the signal book has an override in-flight for this
    ticker (because a shock hit mid-cycle), lookup() returns None and we
    refuse to trade on stale data. The Tier 3 override will write a fresh
    signal once the emergency re-compute finishes.

    Returns None if no actionable signal exists in the book.
    """
    import time

    global _fast_execute_latency_ms

    t0 = time.perf_counter()

    from execution_engine.signal_book import get_signal_book

    book = get_signal_book()

    if book.is_override_pending(ticker):
        _fast_execute_latency_ms = (time.perf_counter() - t0) * 1000
        log.warning(
            f"Fast path BLOCKED: {ticker} has emergency override in-flight — "
            f"refusing to trade on stale pre-shock signal"
        )
        return None

    cached = book.lookup(ticker, regime_fingerprint)

    if cached is None:
        _fast_execute_latency_ms = (time.perf_counter() - t0) * 1000
        return None

    if cached.conviction < 0.6:
        _fast_execute_latency_ms = (time.perf_counter() - t0) * 1000
        log.debug(f"Fast path: {ticker} conviction {cached.conviction:.2f} too low")
        return None

    if not cached.price_in_entry_zone(current_price):
        _fast_execute_latency_ms = (time.perf_counter() - t0) * 1000
        log.debug(
            f"Fast path: {ticker} price ${current_price:.2f} outside entry zone "
            f"(computed at ${cached.entry_price_at_compute:.2f})"
        )
        return None

    action = TradeAction.LONG if cached.direction == "LONG" else TradeAction.SHORT
    # P0-A: Tier-3 shock signals MUST carry the flag forward so the
    # broker router (``_determine_order_type``) routes them as LIMIT
    # (cross the spread, news event needs a fill now) instead of
    # defaulting to TWAP/VWAP on the 3rd/4th order horizon.
    signal = TradeSignal(
        intent_id=f"fast_{ticker}_{int(time.time())}",
        execute=True,
        ticker=ticker,
        direction=action,
        alpha_horizon=AlphaHorizon("3rd_order"),
        alpha_score=AlphaScore(
            ticker=ticker,
            direction=action,
            factor_score=cached.conviction,
            bayesian_conviction=cached.conviction,
            unified_conviction=cached.conviction,
            expected_r=cached.expected_r,
            min_r=cached.min_r,
            rationale=f"signal_book({cached.strategy_name})",
        ),
        strategy_name=cached.strategy_name,
        tier3_shock=cached.tier3_shock,
        rationale=(
            f"[FAST PATH{' | TIER-3 SHOCK' if cached.tier3_shock else ''}] "
            f"{cached.strategy_name} | "
            f"{cached.instrument_type} {cached.direction} | "
            f"R={cached.expected_r:.1f} | {cached.rationale[:100]}"
        ),
    )

    _fast_execute_latency_ms = (time.perf_counter() - t0) * 1000
    log.info(
        f"Fast execute: {ticker} {cached.direction} via {cached.strategy_name} "
        f"in {_fast_execute_latency_ms:.1f}ms (signal book hit, "
        f"tier3_shock={cached.tier3_shock})"
    )

    _signals.append(signal)
    return signal


def get_fast_execute_latency_ms() -> float:
    return _fast_execute_latency_ms


async def auto_execute_from_book() -> list[TradeSignal]:
    """Scan the signal book and auto-execute all high-conviction signals.

    Called by the continuous worker after pre-computing signals. For each
    signal with conviction >= 0.6, runs fast_execute() then routes through
    Kelly sizing and the broker (paper or live).

    Gated by ``settings.AUTO_EXECUTE``. Returns the list of executed signals.
    """
    import time

    if not getattr(settings, "AUTO_EXECUTE", False):
        log.debug("Auto-execute disabled (AUTO_EXECUTE=False)")
        return []

    import numpy as np

    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from execution_engine.math.regime import compute_regime_fingerprint
    from execution_engine.signal_book import get_signal_book

    t0 = time.perf_counter()
    book = get_signal_book()
    stats = book.get_stats()
    executed: list[TradeSignal] = []

    all_tickers = list(stats.get("tickers", book._signals.keys()))

    for ticker in all_tickers:
        try:
            bars = _price_cache.get(ticker)
            if not bars or len(bars) < 30:
                bars = await fetch_ohlcv(ticker, days=30)
            if not bars:
                continue

            current_price = bars[-1].close
            closes = np.array([b.close for b in bars])
            highs = np.array([b.high for b in bars])
            lows = np.array([b.low for b in bars])
            volumes = np.array([b.volume for b in bars], dtype=float)

            regime_fp = ""
            if len(closes) >= 30:
                regime_fp = compute_regime_fingerprint(closes, highs, lows, volumes)

            signal = await fast_execute(ticker, current_price, regime_fp)
            if signal is None:
                continue

            result = await execute_signal(signal)
            if result.status == SignalStatus.FILLED:
                executed.append(result)
                log.info(
                    f"Auto-executed: {ticker} {signal.action.value} "
                    f"@ {signal.confidence:.0%} via fast path"
                )
        except Exception as exc:
            log.warning(f"Auto-execute failed for {ticker}: {exc}")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info(
        f"Auto-execute scan: {len(executed)}/{len(all_tickers)} signals executed "
        f"in {elapsed_ms:.0f}ms"
    )
    return executed


async def _apply_strategy_orchestrator(
    signal: TradeSignal,
    shock_ticker: str,
) -> TradeSignal:
    """Run the strategy orchestrator to enrich the signal.

    Three-tier latency architecture:
    1. Signal book lookup (0ms) — pre-computed decisions from background worker
    2. Rule-based orchestrator (0ms) — fast fallback, no LLM
    3. LLM orchestrator (async, optional) — only if LLM_LIVE_ORCHESTRATOR=True

    If a signal book hit is found, the LLM is never called.
    """
    try:
        from execution_engine.math.regime import compute_regime_fingerprint
        from execution_engine.signal_book import get_signal_book

        book = get_signal_book()
        bars = _price_cache.get(signal.ticker, [])

        if len(bars) >= 30:
            import numpy as np

            closes = np.array([b.close for b in bars])
            highs = np.array([b.high for b in bars])
            lows = np.array([b.low for b in bars])
            volumes = np.array([b.volume for b in bars], dtype=float)
            regime_fp = compute_regime_fingerprint(closes, highs, lows, volumes)
        else:
            regime_fp = ""

        cached = book.lookup(signal.ticker, regime_fp)
        if cached and cached.conviction >= 0.6:
            signal.rationale += (
                f" | SignalBook: {cached.instrument_type} {cached.direction} "
                f"via {cached.strategy_name} (pre-computed, 0ms)"
            )
            signal.alpha_score.unified_conviction = min(
                signal.alpha_score.unified_conviction,
                cached.conviction,
            )
            log.info(
                f"Signal book hit for {signal.ticker}: "
                f"{cached.instrument_type} {cached.direction} "
                f"@ {cached.conviction:.0%} (zero LLM latency)"
            )
            return signal
    except Exception as exc:
        log.debug(f"Signal book lookup skipped: {exc}")

    try:
        # Phase-3 (Gaps A1 + A2 fix): route the LangGraph final_state —
        # forbidden_strategies, supervisor strategy_parameters,
        # risk_multiplier, causal_intel — into the context builder so
        # the post-pipeline orchestrator sees the SAME regime-gating
        # the pipeline did. Previously these fields were silently
        # dropped and the orchestrator ran against an impoverished ctx.
        langgraph_state = signal.audit_trail if signal.audit_trail else None
        ctx = await _build_market_context(
            signal.ticker,
            shock_ticker,
            langgraph_final_state=langgraph_state,
        )
        if ctx is None:
            return signal

        from graph_intelligence.agents.strategy_orchestrator import orchestrate

        mode = "llm" if getattr(settings, "LLM_LIVE_ORCHESTRATOR", False) else "rule"
        intent = await orchestrate(ctx, mode=mode)
        log.info(
            f"Unified orchestrator ({mode}): "
            f"{intent.instrument_choice.value} {intent.direction.value} "
            f"{intent.ticker} | execute={intent.execute} "
            f"strategy={intent.strategy_name} "
            f"unified_conv={intent.alpha_score.unified_conviction:.2f}"
        )

        # Merge the fresh intent's decision onto the pipeline-emitted
        # signal. The pipeline-side intent carries the causal audit
        # trail (debate, analysts, divergence decoding); the fresh
        # orchestrate() call carries the strategy / sizing / routing
        # decision. We keep the pipeline's intent_id + audit_trail as
        # the authoritative identity and overlay the orchestrator's
        # choices on top.
        if intent.execute:
            signal.execute = True
            signal.direction = intent.direction
            signal.asset_class = intent.asset_class
            signal.instrument_choice = intent.instrument_choice
            signal.sizing_method = intent.sizing_method
            signal.max_risk_pct = intent.max_risk_pct
            signal.stop_loss_method = intent.stop_loss_method
            signal.take_profit_method = intent.take_profit_method
            signal.strike = intent.strike
            signal.expiry_days = intent.expiry_days
            signal.second_leg = intent.second_leg
            signal.futures_contract = intent.futures_contract
            signal.multiplier = intent.multiplier
            signal.exchange = intent.exchange
            signal.country = intent.country
            signal.strategy_name = intent.strategy_name
            signal.rationale += f" | Orchestrator: {intent.rationale}"
            # Take the min of pipeline conviction and scorer conviction —
            # both sides must believe for the trade to execute.
            signal.alpha_score.unified_conviction = min(
                signal.alpha_score.unified_conviction,
                intent.alpha_score.unified_conviction,
            )
            signal.alpha_score.factor_score = intent.alpha_score.factor_score
            signal.alpha_score.expected_r = intent.alpha_score.expected_r
            signal.alpha_score.factor_breakdown.update(intent.alpha_score.factor_breakdown)
        else:
            signal.execute = False
            signal.alpha_score.unified_conviction *= 0.5
            signal.rationale += f" | Orchestrator: SKIP — {intent.rationale}"

    except Exception as exc:
        log.debug(f"Strategy orchestrator skipped: {exc}")

    return signal


async def validate_graph_freshness(signal_sequence_id: int) -> bool:
    """Enforce Strict Event Sourcing: verify the Neo4j graph is not stale
    relative to the signal's data. If the graph hasn't been updated since
    the signal was generated, the topology may be phantom.
    """
    if signal_sequence_id == 0:
        return True

    staleness_gap = signal_sequence_id - _last_graph_sequence_id
    if staleness_gap > 100:
        raise GraphStaleError(
            expected_seq=signal_sequence_id,
            actual_seq=_last_graph_sequence_id,
        )

    try:
        from graph_intelligence.tools.neo4j_tools import _session_kwargs, get_driver

        driver = await get_driver()
        async with driver.session(**_session_kwargs()) as session:
            result = await session.run(
                "MATCH (c:Company) RETURN max(c.last_updated) AS ts LIMIT 1"
            )
            record = await result.single()
            if record and record["ts"]:
                return True
    except Exception as exc:
        log.warning(f"Graph freshness check failed: {exc}")

    if staleness_gap > 50:
        raise GraphStaleError(
            expected_seq=signal_sequence_id,
            actual_seq=_last_graph_sequence_id,
        )
    return True


async def execute_signal(
    signal: TradeSignal,
    sequence_id: int = 0,
) -> TradeSignal:
    """Execute a trade signal through the paper trading engine.

    Enforces both graph freshness AND cross-database sequence validation.
    """
    from data_primitives.loaders.hopsworks_sync import get_cached_features
    from execution_engine.broker.orders import submit_signal
    from execution_engine.math.kelly import optimal_allocation_for_signal

    try:
        await validate_graph_freshness(sequence_id)
        await enforce_pre_trade_sync(sequence_id)
    except (GraphStaleError, SequenceValidationError) as exc:
        log.error(f"Trade aborted — {exc}")
        signal.status = SignalStatus.REJECTED
        return signal

    features = get_cached_features(signal.ticker)

    try:
        from data_primitives.extractors.openbb_client import fetch_ohlcv

        bars = await fetch_ohlcv(signal.ticker, days=90)
        _price_cache[signal.ticker] = bars
        price = bars[-1].close if bars else 100.0

        if not features:
            from data_primitives.loaders.hopsworks_sync import sync_features

            features = await sync_features(signal.ticker, bars)
    except Exception as exc:
        log.warning(f"Price/feature fetch failed for {signal.ticker}, using default: {exc}")
        price = 100.0

    vol = features.get("rolling_volatility_30d", 0.2) if features else 0.2
    expected_ret = features.get("expected_return_annualized", 0.05) if features else 0.05

    frac, shares = optimal_allocation_for_signal(
        portfolio_equity=_portfolio.total_equity,
        win_probability=signal.confidence,
        expected_return=expected_ret,
        volatility=vol,
        current_price=price,
        # Batch-1/D: size scales linearly with the supervisor-approved
        # conviction so a 0.62 signal gets materially less size than
        # a 0.95 signal even when both clear the supervisor floor.
        conviction=float(signal.confidence),
        asset_class=signal.asset_class.value,
    )

    if shares == 0:
        signal.status = SignalStatus.CANCELLED
        log.info(f"Signal cancelled — Kelly allocation too small for {signal.ticker}")
        return signal

    # Portfolio-level risk check before routing to broker
    from execution_engine.math.portfolio_risk import get_portfolio_risk_manager

    risk_mgr = get_portfolio_risk_manager()
    risk_mgr.update_portfolio_state(
        positions=_portfolio.positions,
        total_equity=_portfolio.total_equity,
        peak_equity=_portfolio.peak_equity,
        daily_pnl_pct=_portfolio.daily_pnl / max(_portfolio.total_equity, 1) * 100,
    )
    risk_check = risk_mgr.check_risk(
        ticker=signal.ticker,
        asset_class=signal.asset_class.value,
        proposed_notional=shares * price,
    )
    if not risk_check.allowed:
        signal.status = SignalStatus.CANCELLED
        log.info(f"[RISK-GATE] {signal.ticker} blocked: {risk_check.reason}")
        return signal
    if risk_check.size_reduction < 1.0:
        shares = max(1, int(shares * risk_check.size_reduction))
        log.info(f"[RISK-GATE] {signal.ticker} size reduced: {risk_check.reason}")

    result = await submit_signal(signal, _portfolio.total_equity, price, shares)

    if result.status == SignalStatus.FILLED:
        position = Position(
            ticker=signal.ticker,
            side=signal.action,
            quantity=shares,
            avg_entry_price=price,
            current_price=price,
            kelly_allocation=frac,
        )
        _portfolio.positions.append(position)
        _portfolio.cash -= shares * price
        _update_portfolio_equity()

        try:
            from backend.api.sse.streamer import publish_event

            await publish_event("position_update", position.model_dump(mode="json"))
        except Exception as exc:
            log.debug(f"SSE position publish failed: {exc}")

    return result


async def execute_signal_batch(signals: list[TradeSignal]) -> list[TradeSignal]:
    """Execute multiple trade signals with portfolio-level multivariate Kelly sizing.

    When multiple signals fire in the same cycle, uses the covariance-aware
    Kelly to optimally allocate across all positions simultaneously.
    """
    import numpy as np

    from data_primitives.loaders.hopsworks_sync import get_cached_features
    from execution_engine.broker.orders import submit_signal
    from execution_engine.math.kelly import compute_position_size, multivariate_kelly

    if not signals:
        return []

    if len(signals) == 1:
        return [await execute_signal(signals[0])]

    n = len(signals)
    expected_returns = np.zeros(n)
    volatilities = np.zeros(n)
    prices: list[float] = []

    for i, signal in enumerate(signals):
        features = get_cached_features(signal.ticker)
        try:
            from data_primitives.extractors.openbb_client import fetch_ohlcv

            bars = await fetch_ohlcv(signal.ticker, days=90)
            _price_cache[signal.ticker] = bars
            price = bars[-1].close if bars else 100.0
        except Exception as exc:
            log.warning(f"Batch price fetch failed for {signal.ticker}: {exc}")
            price = 100.0

        vol = features.get("rolling_volatility_30d", 0.2) if features else 0.2
        ret = features.get("expected_return_annualized", 0.05) if features else 0.05
        expected_returns[i] = ret
        volatilities[i] = vol
        prices.append(price)

    cov_matrix = np.diag(volatilities**2)
    allocations = multivariate_kelly(expected_returns, cov_matrix)

    results: list[TradeSignal] = []
    for i, signal in enumerate(signals):
        frac = float(allocations[i])
        shares = compute_position_size(_portfolio.total_equity, frac, prices[i])

        if shares == 0:
            signal.status = SignalStatus.CANCELLED
            log.info(f"Signal cancelled — multivariate Kelly too small for {signal.ticker}")
            results.append(signal)
            continue

        result = await submit_signal(signal, _portfolio.total_equity, prices[i], shares)

        if result.status == SignalStatus.FILLED:
            position = Position(
                ticker=signal.ticker,
                side=signal.action,
                quantity=shares,
                avg_entry_price=prices[i],
                current_price=prices[i],
                kelly_allocation=frac,
            )
            _portfolio.positions.append(position)
            _portfolio.cash -= shares * prices[i]
            _update_portfolio_equity()

        results.append(result)

    log.info(f"Batch executed {len(results)} signals via multivariate Kelly")
    return results


async def execute_manual_signal(
    ticker: str,
    action: str,
    confidence: float = 0.7,
    alpha_horizon: str = "3rd_order",
) -> dict[str, Any]:
    """Create and execute a manual trade signal.

    The allocated ``sequence_id`` is the max of all currently-registered
    data-source sequences. This keeps the manual intent synchronised
    with the autopilot registry so ``enforce_pre_trade_sync`` raises
    when any source is materially behind, instead of silently passing
    through with seq=0 (the P0-B defect).
    """
    direction = TradeAction(action)
    signal = TradeSignal(
        intent_id=f"manual_{uuid.uuid4().hex[:8]}",
        execute=True,
        ticker=ticker,
        direction=direction,
        alpha_horizon=AlphaHorizon(alpha_horizon),
        alpha_score=AlphaScore(
            ticker=ticker,
            direction=direction,
            factor_score=confidence,
            bayesian_conviction=confidence,
            unified_conviction=confidence,
            expected_r=2.0,
            min_r=1.5,
            rationale="manual operator intent",
        ),
        rationale="Manual signal",
    )
    _signals.append(signal)
    seq_id = _allocate_manual_sequence_id()
    result = await execute_signal(signal, sequence_id=seq_id)
    return result.model_dump(mode="json")


def _allocate_manual_sequence_id() -> int:
    """Return a sequence_id that makes the manual signal subject to the
    same freshness gate as autopilot signals.

    We take the max of all registered source sequences. That way:
      * On fresh startup (all sources at 0), we return 0 — the legacy
        bypass in ``validate_graph_freshness`` still applies, so cold
        starts without any ingestion don't deadlock the operator.
      * Once any ingestion has happened, we return the max seq. If any
        source is >10 behind that max, ``enforce_pre_trade_sync`` will
        raise ``SequenceValidationError`` and the trade is REJECTED.
      * If the graph (``_last_graph_sequence_id``) is materially behind
        the max, ``validate_graph_freshness`` surfaces ``GraphStaleError``.

    Callers should pass the returned value into ``execute_signal`` as
    the ``sequence_id`` argument.
    """
    seqs = sequence_registry.get_all()
    if not seqs:
        return 0
    return max(seqs.values())


async def update_portfolio_prices() -> None:
    """Refresh current prices for all positions."""
    from data_primitives.extractors.openbb_client import fetch_current_price

    for pos in _portfolio.positions:
        try:
            pos.current_price = await fetch_current_price(pos.ticker)
            if pos.side == TradeAction.LONG:
                pos.unrealized_pnl = (pos.current_price - pos.avg_entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.avg_entry_price - pos.current_price) * pos.quantity
        except Exception as exc:
            log.warning(f"Price update failed for {pos.ticker}: {exc}")

    _update_portfolio_equity()


def _update_portfolio_equity() -> None:
    """Recalculate total equity, drawdown, and push to C++ kill switch."""
    position_value = sum(p.quantity * p.current_price for p in _portfolio.positions)
    _portfolio.total_equity = _portfolio.cash + position_value
    _portfolio.total_pnl = sum(p.unrealized_pnl + p.realized_pnl for p in _portfolio.positions)

    from execution_engine.math.risk_metrics import update_drawdown

    update_drawdown(_portfolio)
    _update_kill_switch()


async def refresh_risk() -> RiskSnapshot:
    """Recompute the risk snapshot and push to the C++ watchdog."""
    global _risk_snapshot
    from execution_engine.math.risk_metrics import build_risk_snapshot

    _risk_snapshot = build_risk_snapshot(_portfolio, _price_cache)
    _update_kill_switch()
    return _risk_snapshot
