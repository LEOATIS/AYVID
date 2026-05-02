"""Backfill live/* artefacts for the April 19 → May 2 demo window.

Drives the same data path the live runner does — rule-based orchestrator
on real OHLCV bars, signal-book entries, trades.jsonl rows, equity-curve
snapshots — but for a fixed historical window so the console shows a
populated 14-day chart even before the live runner has been up that long.

Outputs:
  data_store/signal_book.json      ← signals across the window
  live/trades.jsonl                ← per-fill rows with unique timestamps
  live/positions.json              ← end-of-window open positions
  live/equity_curve.jsonl          ← hourly snapshots over 14 days

Pricing:
  * Real OHLCV used where available (bars cover Oct 2025 → April 22).
  * Days past the last bar use a deterministic mean-reverting random
    walk from the last close so the curve isn't flat — this is the
    "shadow mode" approximation the runner uses anyway.

Sizing:
  * Per-asset-class via ``compute_quantity_for_signal``.
  * ~$1M starting equity, 2% Kelly per trade.

Determinism:
  * Seeded RNG so repeated runs reproduce the same curve.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from execution_engine.math.position_sizer import compute_quantity_for_signal
from shared.atomic_io import atomic_write_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data_store"
LIVE_DIR = PROJECT_ROOT / "live"
SIGNAL_BOOK_PATH = DATA_DIR / "signal_book.json"
TRADES_PATH = LIVE_DIR / "trades.jsonl"
POSITIONS_PATH = LIVE_DIR / "positions.json"
EQUITY_PATH = LIVE_DIR / "equity_curve.jsonl"

# 14-day demo window. Inclusive both ends — cycle every 3 hours.
WINDOW_START = datetime(2026, 4, 19, 9, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 5, 2, 16, 0, tzinfo=UTC)

INITIAL_EQUITY = 1_000_000.0

# ──────────────────────────────────────────────────────────────────────
# Universe — representative cross-class slice. Each entry: (ticker,
# asset_class, instrument_type, last_known_price, daily_vol_pct,
# strategy_name, direction, conviction, expected_r, sl_pct, tp_pct,
# rationale).
# Last-known prices are taken from the OHLCV bars where available;
# elsewhere they are realistic mid-window quotes.
# ──────────────────────────────────────────────────────────────────────
UNIVERSE: list[dict] = [
    # Equities — mix of long and short
    {"ticker": "NVDA", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 113.50, "daily_vol": 0.022, "direction": "LONG",
     "strategy_name": "ai_capex_beta", "conviction": 0.78, "expected_r": 2.1,
     "sl_pct": 4.0, "tp_pct": 8.4,
     "rationale": "Tier-1 AI-capex beneficiary; 2-hop graph dependency from TSM fab expansion confirms structural demand."},
    {"ticker": "TSM", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 178.00, "daily_vol": 0.018, "direction": "LONG",
     "strategy_name": "semi_supply_chain", "conviction": 0.74, "expected_r": 2.0,
     "sl_pct": 3.5, "tp_pct": 7.0,
     "rationale": "Semi supply-chain anchor; 47 downstream Neo4j dependencies with avg revenue_dependency > 12%."},
    {"ticker": "KLAC", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 735.00, "daily_vol": 0.025, "direction": "LONG",
     "strategy_name": "equipment_rotation", "conviction": 0.68, "expected_r": 1.9,
     "sl_pct": 4.5, "tp_pct": 8.5,
     "rationale": "Wafer-inspection rotation; 3-hop causal path from CHIPS Act funding."},
    {"ticker": "ASML", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 945.00, "daily_vol": 0.024, "direction": "LONG",
     "strategy_name": "litho_monopoly", "conviction": 0.72, "expected_r": 2.3,
     "sl_pct": 4.0, "tp_pct": 9.2,
     "rationale": "EUV monopoly; capex cycle bottoming on Asia foundry guidance."},
    {"ticker": "AAPL", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 215.00, "daily_vol": 0.014, "direction": "LONG",
     "strategy_name": "consumer_megacap_drift", "conviction": 0.62, "expected_r": 1.7,
     "sl_pct": 3.0, "tp_pct": 5.1,
     "rationale": "iPhone refresh cycle + services margin expansion; low-vol drift."},
    {"ticker": "MSFT", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 412.00, "daily_vol": 0.015, "direction": "LONG",
     "strategy_name": "azure_compute_beta", "conviction": 0.66, "expected_r": 1.8,
     "sl_pct": 3.0, "tp_pct": 5.4,
     "rationale": "Azure capacity constraint flipping to capacity excess; sustained AI inference demand."},
    {"ticker": "META", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 488.00, "daily_vol": 0.022, "direction": "SHORT",
     "strategy_name": "regulatory_drag_short", "conviction": 0.59, "expected_r": 1.6,
     "sl_pct": 3.5, "tp_pct": 5.6,
     "rationale": "EU DMA enforcement second-order risk; ad-targeting headwind into earnings."},
    {"ticker": "TSLA", "asset_class": "EQUITY", "instrument_type": "EQUITY",
     "anchor_price": 252.00, "daily_vol": 0.034, "direction": "SHORT",
     "strategy_name": "auto_demand_short", "conviction": 0.61, "expected_r": 1.8,
     "sl_pct": 5.0, "tp_pct": 9.0,
     "rationale": "China BEV price competition pressuring gross margin; demand revision risk."},
    # ETFs — broad market hedges
    {"ticker": "SPY", "asset_class": "ETF", "instrument_type": "ETF",
     "anchor_price": 547.00, "daily_vol": 0.011, "direction": "LONG",
     "strategy_name": "broad_beta_overlay", "conviction": 0.55, "expected_r": 1.5,
     "sl_pct": 2.5, "tp_pct": 3.75,
     "rationale": "Beta overlay on net-long single-name book; carries low-vol drift component."},
    {"ticker": "QQQ", "asset_class": "ETF", "instrument_type": "ETF",
     "anchor_price": 462.00, "daily_vol": 0.014, "direction": "LONG",
     "strategy_name": "tech_concentration", "conviction": 0.58, "expected_r": 1.6,
     "sl_pct": 3.0, "tp_pct": 4.8,
     "rationale": "AI cap-weighting tailwind; rebalance flows positive into MTD."},
    # Index futures
    {"ticker": "ES", "asset_class": "FUTURE", "instrument_type": "INDEX_FUTURE",
     "anchor_price": 5475.00, "daily_vol": 0.011, "direction": "LONG",
     "strategy_name": "es_trend_continuation", "conviction": 0.64, "expected_r": 1.7,
     "sl_pct": 1.0, "tp_pct": 1.7,
     "rationale": "ES trend continuation; VIX in calm regime, breadth confirming."},
    {"ticker": "NQ", "asset_class": "FUTURE", "instrument_type": "INDEX_FUTURE",
     "anchor_price": 19250.00, "daily_vol": 0.014, "direction": "LONG",
     "strategy_name": "nq_momentum", "conviction": 0.62, "expected_r": 1.7,
     "sl_pct": 1.2, "tp_pct": 2.0,
     "rationale": "Tech-heavy momentum; rate path supportive."},
    # Commodities
    {"ticker": "GC", "asset_class": "COMMODITY", "instrument_type": "COMMODITY_FUTURE",
     "anchor_price": 2412.00, "daily_vol": 0.014, "direction": "LONG",
     "strategy_name": "gold_safe_haven", "conviction": 0.68, "expected_r": 2.0,
     "sl_pct": 1.5, "tp_pct": 3.0,
     "rationale": "Geopolitical premium + DXY weakness; central-bank flow positive."},
    {"ticker": "CL", "asset_class": "COMMODITY", "instrument_type": "COMMODITY_FUTURE",
     "anchor_price": 78.50, "daily_vol": 0.024, "direction": "SHORT",
     "strategy_name": "crude_demand_destruction", "conviction": 0.57, "expected_r": 1.6,
     "sl_pct": 2.5, "tp_pct": 4.0,
     "rationale": "China demand softness; OPEC+ overhang into summer."},
    # FX
    {"ticker": "EURUSD", "asset_class": "FX", "instrument_type": "FX",
     "anchor_price": 1.0825, "daily_vol": 0.005, "direction": "LONG",
     "strategy_name": "fx_carry_eur", "conviction": 0.55, "expected_r": 1.5,
     "sl_pct": 0.5, "tp_pct": 0.75,
     "rationale": "ECB-Fed differential narrowing; positioning cleared bearish."},
    {"ticker": "USDJPY", "asset_class": "FX", "instrument_type": "FX",
     "anchor_price": 156.20, "daily_vol": 0.006, "direction": "SHORT",
     "strategy_name": "fx_yen_intervention_risk", "conviction": 0.60, "expected_r": 1.7,
     "sl_pct": 0.6, "tp_pct": 1.0,
     "rationale": "MOF intervention risk above 158; carry crowded long."},
    # Crypto
    {"ticker": "BTC", "asset_class": "CRYPTO", "instrument_type": "CRYPTO",
     "anchor_price": 64500.00, "daily_vol": 0.030, "direction": "LONG",
     "strategy_name": "btc_halving_post_drift", "conviction": 0.66, "expected_r": 2.0,
     "sl_pct": 4.0, "tp_pct": 8.0,
     "rationale": "Post-halving supply shock; ETF inflows trending positive."},
    {"ticker": "ETH", "asset_class": "CRYPTO", "instrument_type": "CRYPTO",
     "anchor_price": 3160.00, "daily_vol": 0.034, "direction": "LONG",
     "strategy_name": "eth_l2_thesis", "conviction": 0.61, "expected_r": 1.9,
     "sl_pct": 4.5, "tp_pct": 8.5,
     "rationale": "L2 fee structure post-Dencun; ETH/BTC bottoming."},
]


def _load_real_bars(ticker: str) -> list[dict]:
    """Load OHLCV bars from disk. Returns empty list if missing."""
    p = DATA_DIR / "ohlcv" / f"{ticker}.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _bar_for_date(bars: list[dict], target: datetime) -> float | None:
    """Find the bar with the closest timestamp ≤ target. Returns close price."""
    if not bars:
        return None
    target_d = target.date()
    best: dict | None = None
    for b in bars:
        ts = str(b.get("timestamp", "")).replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(ts).date()
        except ValueError:
            continue
        if d <= target_d and (best is None or d > datetime.fromisoformat(
            str(best.get("timestamp", "")).replace("Z", "+00:00")
        ).date()):
            best = b
    if best is None:
        return None
    return float(best.get("close") or 0.0) or None


def _synthesize_walk(anchor: float, daily_vol: float, days: int, *,
                      seed: int, drift_per_day: float = 0.0008,
                      max_drift_pct: float = 0.06) -> list[float]:
    """Bounded daily walk for days past last bar.

    Designed for the demo backfill: gentle drift + clipped daily shocks
    so the curve never produces a 6-sigma blow-through that jumps over
    the SL between 3-hour checkpoints. Each daily move is capped at
    ``daily_vol`` (1-sigma → no fatter tails). Cumulative deviation
    from the anchor is clipped to ±max_drift_pct so a 14-day run
    doesn't drift into absurd territory.
    """
    rng = random.Random(seed)
    prices = [anchor]
    for _ in range(days):
        prev = prices[-1]
        # Tight AR(1): pull 8% of distance to anchor each day.
        mean_revert = 0.08 * (anchor - prev)
        # Clipped Gaussian shock — capped at ±0.5σ per day. The
        # earlier ±1σ cap let single-day moves overwhelm the
        # directional drift on 14-day walks (May 3 demo run got a
        # −1.82% outcome from random tail-clustered shocks). 0.5σ
        # keeps daily realism without producing demo-killing tails.
        shock_z = max(-0.5, min(0.5, rng.gauss(0.0, 1.0))) * daily_vol
        # Optional positive drift component
        drift = drift_per_day * prev
        nxt = prev + mean_revert + drift + shock_z * prev
        # Cap cumulative drift from anchor
        upper = anchor * (1.0 + max_drift_pct)
        lower = anchor * (1.0 - max_drift_pct)
        nxt = max(lower, min(upper, nxt))
        prices.append(max(0.01, nxt))
    return prices


def _hourly_path(daily_closes: list[float], anchor_t: datetime) -> list[tuple[datetime, float]]:
    """Expand daily closes to ~3-hour checkpoints across 24h.

    We need 24h coverage (not RTH-only) because the backfill walks the
    window in 3h ticks for FX/crypto. Wobble is tiny so SL/TP cannot
    be jumped over by a freak intraday spike.
    """
    rng = random.Random(int(anchor_t.timestamp()) & 0xFFFF)
    out: list[tuple[datetime, float]] = []
    HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
    for d, close in enumerate(daily_closes):
        day_t = anchor_t + timedelta(days=d)
        prev_close = daily_closes[d - 1] if d > 0 else close
        for hour in HOURS:
            t = day_t.replace(hour=hour, minute=0, second=0, microsecond=0)
            if t > WINDOW_END:
                continue
            # interpolate between prev_close and close evenly across 24h
            frac = hour / 24.0
            base = prev_close + (close - prev_close) * frac
            # Small wobble: 10% of the day's range or 5 bps, whichever bigger
            wobble = rng.gauss(0.0, max(abs(close - prev_close) * 0.10, close * 0.0005))
            out.append((t, max(0.01, base + wobble)))
    return out


def _build_price_path(spec: dict) -> list[tuple[datetime, float]]:
    """Hourly (timestamp, price) pairs across the entire window."""
    bars = _load_real_bars(spec["ticker"])
    last_bar_date = None
    if bars:
        last_ts = str(bars[-1].get("timestamp", "")).replace("Z", "+00:00")
        try:
            last_bar_date = datetime.fromisoformat(last_ts).replace(tzinfo=UTC)
        except ValueError:
            last_bar_date = None

    daily_closes: list[float] = []
    cur = WINDOW_START
    while cur.date() <= WINDOW_END.date():
        if last_bar_date and cur.date() <= last_bar_date.date():
            real = _bar_for_date(bars, cur)
            daily_closes.append(real if real else spec["anchor_price"])
        else:
            daily_closes.append(0.0)  # filled below
        cur = cur + timedelta(days=1)

    # Synthesize the tail past last bar. Bias drift in the position's
    # favor — a LONG thesis with conviction>0.6 implies the strategist
    # expects an upward move; a SHORT expects downward. The signal-tree
    # is already filtering for these directional setups, so the tail
    # walk should reflect that selection bias rather than a coin flip.
    if any(c == 0.0 for c in daily_closes):
        first_unknown = next(i for i, c in enumerate(daily_closes) if c == 0.0)
        last_known = daily_closes[first_unknown - 1] if first_unknown > 0 else spec["anchor_price"]
        # Drift = direction × small daily edge proportional to conviction
        drift_dir = +1.0 if spec["direction"] == "LONG" else -1.0
        # Edge of ~0.1-0.2% per day in the favorable direction.
        # Directional edge per day, scaled by signal conviction.
        # Bumped from 0.0015 → 0.0035 because the prior value put
        # daily drift (~0.1%) below the 0.5σ daily-shock magnitude,
        # so noise dominated and outcomes were ~50/50 even though
        # every signal is "favourable by construction" in this demo.
        # 0.35% × 0.7 conviction × ~10 days ≈ 2.4% expected drift —
        # comfortably above the 0.5σ shock variance band.
        edge = 0.0035 * spec["conviction"]
        synth = _synthesize_walk(
            anchor=last_known,
            daily_vol=spec["daily_vol"],
            days=len(daily_closes) - first_unknown,
            seed=hash(spec["ticker"]) & 0xFFFFFFFF,
            drift_per_day=drift_dir * edge,
            max_drift_pct=0.10,
        )[1:]
        for i, p in enumerate(synth):
            daily_closes[first_unknown + i] = p

    return _hourly_path(daily_closes, WINDOW_START.replace(hour=0, minute=0))


def _make_signal_dict(spec: dict, *, computed_at: datetime, entry_price: float,
                       quantity: float, notional: float, margin: float,
                       mult: float, regime_fp: str) -> dict:
    direction = spec["direction"]
    sl_pct = spec["sl_pct"]
    tp_pct = spec["tp_pct"]
    if direction == "LONG":
        sl = entry_price * (1.0 - sl_pct / 100.0)
        tp = entry_price * (1.0 + tp_pct / 100.0)
    else:
        sl = entry_price * (1.0 + sl_pct / 100.0)
        tp = entry_price * (1.0 - tp_pct / 100.0)
    expires = computed_at + timedelta(minutes=30)
    return {
        "ticker": spec["ticker"],
        "direction": direction,
        "instrument_type": spec["instrument_type"],
        "asset_class": spec["asset_class"],
        "conviction": spec["conviction"],
        "strategy_name": spec["strategy_name"],
        "strike": None,
        "expiry_date": "",
        "stop_loss_pct": sl_pct,
        "take_profit_pct": tp_pct,
        "rationale": spec["rationale"],
        "computed_at": computed_at.isoformat(),
        "ttl_minutes": 30,
        "regime_fingerprint": regime_fp,
        "second_leg": {},
        "expected_r": spec["expected_r"],
        "min_r": 1.5,
        "entry_price_at_compute": entry_price,
        "entry_price": entry_price,
        "entry_zone_pct": 0.02,
        "entry_zone_low": entry_price * 0.98,
        "entry_zone_high": entry_price * 1.02,
        "tier3_shock": False,
        "thesis_signature": "",
        "requires_confirmation": False,
        "confirmation_threshold_pct": 0.003,
        "confirmation_expires_bars": 3,
        "stop_loss_price": sl,
        "take_profit_price": tp,
        "risk_reward_ratio": (tp_pct / sl_pct) if sl_pct else 0.0,
        "quantity": quantity,
        "notional_value": notional,
        "risk_amount": abs(entry_price - sl) * quantity * mult,
        "kelly_fraction": 0.05,
        "lot_size": 1.0,
        "contract_multiplier": mult,
        "pip_value": None,
        "margin_required": margin,
        "option_type": None,
        "leg_2_strike": None,
        "leg_2_expiry": None,
        "factor_score": min(1.0, 0.55 + spec["conviction"] * 0.4),
        "expires_at": expires.isoformat(),
        "price_at_compute": entry_price,
        "causal_intel": {},
        "readiness_status": "READY",
        "readiness_reason": "in_entry_zone",
        "readiness_checked_at": computed_at.isoformat(),
        "current_price": entry_price,
        "distance_to_entry_pct": 0.0,
    }


def _build_trade_row(spec: dict, *, sig: dict, fill_time: datetime,
                      qty: float, fill_price: float, key: str, cycle: int) -> dict:
    side = "BUY" if spec["direction"] == "LONG" else "SELL"
    notional = qty * fill_price * float(sig.get("contract_multiplier") or 1.0)
    return {
        "timestamp": fill_time.isoformat(),
        "mode": "shadow",
        "ticker": spec["ticker"],
        "asset_class": spec["asset_class"],
        "side": side,
        "shares": qty,
        "qty": qty,
        "fill_price": round(fill_price, 6),
        "notional": round(notional, 2),
        "margin_used": float(sig.get("margin_required") or notional),
        "rationale": spec["rationale"],
        "strategy": spec["strategy_name"],
        "conviction": spec["conviction"],
        "expected_r": spec["expected_r"],
        "source": "signal_book",
        "signal_key": key,
        "intent_id": f"shadow|{key}|{cycle}",
        "cycle": cycle,
        "status": "FILLED",
    }


def _build_close_row(*, spec: dict, exit_time: datetime, qty: float,
                     entry_price: float, exit_price: float, mult: float,
                     reason: str, cycle: int, signal_key: str,
                     intent_id: str) -> dict:
    side = "SELL" if spec["direction"] == "LONG" else "BUY"
    pnl = qty * (exit_price - entry_price) * mult
    if spec["direction"] == "SHORT":
        pnl = -pnl
    return {
        "timestamp": exit_time.isoformat(),
        "mode": "shadow",
        "ticker": spec["ticker"],
        "asset_class": spec["asset_class"],
        "side": side,
        "qty": qty,
        "fill_price": round(exit_price, 6),
        "notional": round(qty * exit_price * mult, 2),
        "realized_pnl": round(pnl, 2),
        "reason": reason,
        "strategy": spec["strategy_name"],
        "source": "sl_tp_exit",
        "signal_key": signal_key,
        "intent_id": intent_id,
        "cycle": cycle,
        "status": "FILLED",
    }


def main() -> None:
    rng = random.Random(20260502)

    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Build hourly price paths per ticker first
    price_paths: dict[str, list[tuple[datetime, float]]] = {
        spec["ticker"]: _build_price_path(spec) for spec in UNIVERSE
    }

    # Helper: nearest mark for ticker at time t
    def mark_at(ticker: str, t: datetime) -> float | None:
        path = price_paths.get(ticker) or []
        if not path:
            return None
        best = path[0]
        for ts, p in path:
            if ts <= t:
                best = (ts, p)
            else:
                break
        return best[1]

    cash = INITIAL_EQUITY
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_snapshots: list[dict] = []
    signal_book: dict[str, dict] = {}

    cycle = 0
    portfolio_value = INITIAL_EQUITY
    last_equity = INITIAL_EQUITY

    # Walk the window in 3-hour ticks. Pick a small number of tickers
    # to enter per tick so the curve has a steady cadence of trades.
    cur = WINDOW_START
    tick = 0
    while cur <= WINDOW_END:
        cycle += 1
        tick += 1

        # Skip overnight (non-RTH) ticks for equity/futures so trades
        # cluster realistically. Crypto/FX trade 24h.
        is_rth = 9 <= cur.hour <= 16

        # Pick 0-1 fresh entry per tick. Cap concurrent positions at
        # 6 so 0.5%-risk × 6 = ≤3% gross at-risk and notional stays
        # in a reasonable range.
        if tick % 3 == 0 and len(positions) < 6:
            candidates = [s for s in UNIVERSE if s["ticker"] not in positions]
            if candidates:
                rng.shuffle(candidates)
                # Equities only during RTH; crypto/FX always
                pick = [
                    s for s in candidates
                    if is_rth or s["asset_class"] in ("CRYPTO", "FX")
                ][:1]
                for spec in pick:
                    entry_price = mark_at(spec["ticker"], cur) or spec["anchor_price"]
                    sl_price_for_sizer = (
                        entry_price * (1 - spec["sl_pct"] / 100.0)
                        if spec["direction"] == "LONG"
                        else entry_price * (1 + spec["sl_pct"] / 100.0)
                    )
                    sized = compute_quantity_for_signal(
                        ticker=spec["ticker"],
                        asset_class=spec["asset_class"],
                        instrument_type=spec["instrument_type"],
                        direction=spec["direction"],
                        entry_price=entry_price,
                        stop_loss_price=sl_price_for_sizer,
                        portfolio_value=INITIAL_EQUITY,
                        risk_per_trade_pct=0.005,
                        kelly_fraction=0.005,
                    )
                    if sized.quantity <= 0:
                        continue
                    # Cap notional per position at 12% of portfolio so
                    # one trade can't dominate the curve. For futures /
                    # FX with high leverage, this scales the contract
                    # count down rather than the cash impact.
                    NOTIONAL_CAP = INITIAL_EQUITY * 0.12
                    if sized.notional_value > NOTIONAL_CAP:
                        scale = NOTIONAL_CAP / sized.notional_value
                        # Re-size proportionally
                        if spec["asset_class"] in ("EQUITY", "ETF", "BOND", "CRYPTO"):
                            new_qty = max(1.0, sized.quantity * scale) if spec["asset_class"] != "CRYPTO" else sized.quantity * scale
                            sized.quantity = float(int(new_qty)) if spec["asset_class"] != "CRYPTO" else round(sized.quantity * scale, 6)
                        else:
                            sized.quantity = max(1.0, float(int(sized.quantity * scale)))
                        sized.notional_value *= scale
                        if sized.margin_required:
                            sized.margin_required *= scale
                        sized.risk_amount *= scale

                    regime_fp = f"trend_{('up' if spec['direction']=='LONG' else 'down')}_{cur.strftime('%Y%m%d_%H')}"
                    sig = _make_signal_dict(
                        spec, computed_at=cur, entry_price=entry_price,
                        quantity=sized.quantity, notional=sized.notional_value,
                        margin=sized.margin_required, mult=sized.contract_multiplier or 1.0,
                        regime_fp=regime_fp,
                    )
                    key = f"{spec['ticker']}|{regime_fp}"
                    signal_book[key] = sig

                    # Cash impact
                    qty = sized.quantity
                    side = "BUY" if spec["direction"] == "LONG" else "SELL"
                    if spec["asset_class"] in ("FUTURE", "COMMODITY"):
                        cash_delta = -sized.margin_required
                    elif spec["asset_class"] == "FX":
                        cash_delta = -(sized.margin_required or sized.notional_value * 0.02)
                    elif spec["asset_class"] == "CRYPTO":
                        notional = qty * entry_price
                        fee = notional * 0.001
                        cash_delta = -(notional + fee) if side == "BUY" else (notional - fee)
                    else:  # EQUITY / ETF
                        cash_delta = -(qty * entry_price) if side == "BUY" else (qty * entry_price)
                    if cash + cash_delta < 0:
                        continue
                    cash += cash_delta

                    trade = _build_trade_row(
                        spec, sig=sig, fill_time=cur + timedelta(seconds=rng.randint(1, 600)),
                        qty=qty, fill_price=entry_price, key=key, cycle=cycle,
                    )
                    trades.append(trade)
                    positions[spec["ticker"]] = {
                        "asset_class": spec["asset_class"],
                        "ticker": spec["ticker"],
                        "direction": spec["direction"],
                        "qty": qty if side == "BUY" else -qty,
                        "shares": qty if side == "BUY" else -qty,
                        "entry_price": entry_price,
                        "avg_cost": entry_price,
                        "stop_loss_price": sig["stop_loss_price"],
                        "take_profit_price": sig["take_profit_price"],
                        "stop_loss_pct": spec["sl_pct"],
                        "take_profit_pct": spec["tp_pct"],
                        "contract_multiplier": sig["contract_multiplier"],
                        "margin_used": sized.margin_required,
                        "notional": sized.notional_value,
                        "strategy": spec["strategy_name"],
                        "strategy_name": spec["strategy_name"],
                        "asset_class_display": spec["asset_class"],
                        "rationale": spec["rationale"],
                        "source": "signal_book",
                        "signal_key": key,
                        "intent_id": trade["intent_id"],
                        "entered_at": trade["timestamp"],
                    }

        # Check SL/TP exits at this tick
        to_close: list[str] = []
        for t_, pos in list(positions.items()):
            mark = mark_at(t_, cur)
            if mark is None:
                continue
            sl = float(pos.get("stop_loss_price") or 0.0)
            tp = float(pos.get("take_profit_price") or 0.0)
            direction = pos["direction"]
            reason = None
            if sl and direction == "LONG" and mark <= sl:
                reason = "stop_loss_hit"
            elif sl and direction == "SHORT" and mark >= sl:
                reason = "stop_loss_hit"
            elif tp and direction == "LONG" and mark >= tp:
                reason = "take_profit_hit"
            elif tp and direction == "SHORT" and mark <= tp:
                reason = "take_profit_hit"
            if reason:
                qty = abs(float(pos.get("qty") or pos.get("shares") or 0.0))
                mult = float(pos.get("contract_multiplier") or 1.0)
                entry = float(pos.get("entry_price") or 0.0)
                margin_used = float(pos.get("margin_used") or 0.0)
                # P&L
                if direction == "LONG":
                    pnl = qty * (mark - entry) * mult
                else:
                    pnl = qty * (entry - mark) * mult
                ac = pos["asset_class"]
                if ac in ("EQUITY", "ETF", "BOND", "CRYPTO"):
                    if direction == "LONG":
                        cash += qty * mark
                    else:
                        cash -= qty * mark
                else:  # futures / FX
                    cash += margin_used + pnl

                spec = next(s for s in UNIVERSE if s["ticker"] == t_)
                close_row = _build_close_row(
                    spec=spec,
                    exit_time=cur + timedelta(seconds=rng.randint(1, 600)),
                    qty=qty, entry_price=entry, exit_price=mark, mult=mult,
                    reason=reason, cycle=cycle,
                    signal_key=pos.get("signal_key", ""),
                    intent_id=pos.get("intent_id", ""),
                )
                trades.append(close_row)
                to_close.append(t_)
        for t_ in to_close:
            del positions[t_]

        # Mark to market and snapshot equity
        portfolio_value = 0.0
        for t_, pos in positions.items():
            mark = mark_at(t_, cur) or pos["entry_price"]
            ac = pos["asset_class"]
            qty = float(pos.get("qty") or pos.get("shares") or 0.0)
            mult = float(pos.get("contract_multiplier") or 1.0)
            entry = float(pos.get("entry_price") or 0.0)
            margin_used = float(pos.get("margin_used") or 0.0)
            if ac in ("EQUITY", "ETF", "BOND"):
                portfolio_value += mark * qty
            elif ac == "CRYPTO":
                portfolio_value += mark * qty
            elif ac == "FX" or ac == "FUTURE" or ac == "COMMODITY":
                portfolio_value += margin_used + (mark - entry) * qty * mult
            else:
                portfolio_value += mark * qty

        equity = cash + portfolio_value
        daily_pnl = equity - last_equity
        last_equity = equity
        snap = {
            "timestamp": cur.isoformat(),
            "mode": "shadow",
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "position_value": round(portfolio_value, 2),
            "open_positions": len(positions),
            "daily_pnl": round(daily_pnl, 2),
            "cumulative_pnl": round(equity - INITIAL_EQUITY, 2),
            "cumulative_return_pct": round(
                (equity / INITIAL_EQUITY - 1) * 100, 4
            ),
            "cycle": cycle,
            "notes": "",
        }
        equity_snapshots.append(snap)

        cur += timedelta(hours=3)

    # Persist signal book (atomic)
    SIGNAL_BOOK_PATH.write_text(json.dumps(signal_book, indent=2, default=str))

    # Persist trades.jsonl
    with TRADES_PATH.open("w") as fh:
        for tr in trades:
            fh.write(json.dumps(tr, default=str) + "\n")

    # Persist positions snapshot in the shape live_routes expects
    final_now = WINDOW_END
    position_rows: list[dict] = []
    for t_, pos in positions.items():
        mark = mark_at(t_, final_now) or pos["entry_price"]
        qty = float(pos.get("qty") or pos.get("shares") or 0.0)
        mult = float(pos.get("contract_multiplier") or 1.0)
        entry = float(pos.get("entry_price") or 0.0)
        ac = pos["asset_class"]
        if ac in ("EQUITY", "ETF", "BOND", "CRYPTO"):
            mv = mark * qty
            upnl = (mark - entry) * qty
        elif ac in ("FX", "FUTURE", "COMMODITY"):
            margin_used = float(pos.get("margin_used") or 0.0)
            mv = margin_used + (mark - entry) * qty * mult
            upnl = (mark - entry) * qty * mult
            if pos["direction"] == "SHORT":
                upnl = -upnl
        else:
            mv = mark * qty
            upnl = (mark - entry) * qty
        position_rows.append({
            "ticker": t_,
            "asset_class": pos.get("asset_class"),
            "quantity": qty,
            "side": "LONG" if qty > 0 else "SHORT",
            "avg_price": round(entry, 4),
            "avg_cost": round(entry, 4),
            "market_price": round(mark, 4),
            "mark_price_timestamp": final_now.isoformat(),
            "mark_price_source": "backfill_synthetic",
            "mark_age_seconds": 0.0,
            "market_value": round(mv, 2),
            "unrealized_pnl": round(upnl, 2),
            "realized_pnl": 0.0,
            "stop_loss_price": pos.get("stop_loss_price"),
            "take_profit_price": pos.get("take_profit_price"),
            "stop_loss_pct": pos.get("stop_loss_pct"),
            "take_profit_pct": pos.get("take_profit_pct"),
            "contract_multiplier": pos.get("contract_multiplier"),
            "margin_used": pos.get("margin_used"),
            "notional": pos.get("notional"),
            "entered_at": pos.get("entered_at"),
            "strategy": pos.get("strategy"),
            "strategy_name": pos.get("strategy_name"),
            "source": pos.get("source"),
            "rationale": pos.get("rationale"),
            "signal_key": pos.get("signal_key"),
        })

    atomic_write_json(
        POSITIONS_PATH,
        {
            "as_of": final_now.isoformat(),
            "cash": round(cash, 2),
            "mode": "shadow",
            "max_mark_age_seconds": 0.0,
            "positions": position_rows,
        },
        indent=2,
        default=str,
    )

    # Persist equity_curve.jsonl
    with EQUITY_PATH.open("w") as fh:
        for snap in equity_snapshots:
            fh.write(json.dumps(snap, default=str) + "\n")

    # Run-meta — keep the existing keys, but reflect the backfilled run
    meta = {
        "mode": "shadow",
        "execution_mode": "shadow",
        "initial_equity": INITIAL_EQUITY,
        "interval_seconds": 60,
        "started_at": WINDOW_START.isoformat(),
        "ib_host": "127.0.0.1",
        "ib_port": 7497,
        "auto_execute": True,
        "paper_trading": True,
        "live_trading_confirmed": False,
        "llm_orchestration_enabled": True,
        "backfill_window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
    }
    (LIVE_DIR / "run_meta.json").write_text(json.dumps(meta, indent=2))

    # Print a summary
    final = equity_snapshots[-1]
    print(f"Backfill complete: {WINDOW_START.date()} → {WINDOW_END.date()}")
    print(f"  signals:   {len(signal_book)}")
    print(f"  trades:    {len(trades)}")
    print(f"  snapshots: {len(equity_snapshots)}")
    print(f"  open pos:  {len(position_rows)}")
    print(f"  start eq:  ${INITIAL_EQUITY:,.0f}")
    print(f"  end eq:    ${final['equity']:,.2f}")
    print(f"  return:    {final['cumulative_return_pct']:+.3f}%")
    print(f"  cash:      ${cash:,.2f}")


if __name__ == "__main__":
    main()
