"""Live daily prices for the research workspace.

The repo's ``data_store/ohlcv/*.jsonl`` files are a frozen snapshot (they also
feed the backtests, so we never rewrite them). This module tops each listing up
with recent daily bars from Yahoo Finance (unadjusted close — the same basis as
the local files), stores them in ``price_bars``, and recomputes the market
metrics (close, 90-day return, volatility, drawdown, Hurst) so every price the
UI shows is current. Failures never raise: the listing keeps its last good bars
and is labelled stale by the normal freshness rule.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Any

from backend.services.research_seed import OHLCV_DIR, _market_rows, read_bars
from backend.services.research_store import MARKET_PROVIDERS, ResearchStore, utcnow
from shared.logger import get_logger

log = get_logger("research.prices")

PROVIDER = MARKET_PROVIDERS[1]
REFRESH_TTL_SEC = 15 * 60  # one Yahoo hit per listing per 15 min, tops
BACKFILL_DAYS = 10  # overlap so revised recent closes replace stale ones
FIRST_FETCH_DAYS = 400  # no local file yet: enough history for 90-day stats
BATCH = 40  # symbols per yf.download call

# symbol → [(day, close, volume)]; injectable so tests never touch the network
Bars = list[tuple[str, float, float | None]]
Fetch = Callable[[list[str], date], dict[str, Bars]]

_lock = threading.Lock()


def _frame_rows(frame: Any) -> Bars:
    rows: Bars = []
    for ts, row in frame.iterrows():
        close = float(row["Close"])
        if math.isfinite(close) and close > 0:
            vol = float(row["Volume"]) if "Volume" in row else math.nan
            rows.append((ts.date().isoformat(), close, vol if math.isfinite(vol) else None))
    return rows


def yahoo_fetch(symbols: list[str], start: date) -> dict[str, Bars]:
    import yfinance as yf

    out: dict[str, Bars] = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i : i + BATCH]
        try:
            df = yf.download(chunk, start=start.isoformat(), auto_adjust=False, progress=False,
                             group_by="ticker", threads=True)
        except Exception as exc:  # yfinance raises many types; one bad batch must not stop the rest
            log.warning("yahoo batch failed (%d symbols): %s", len(chunk), exc)
            continue
        for sym in chunk:
            try:
                frame = df[sym]
                rows = _frame_rows(frame)
                # Some venues (HK, KRX) publish today's row with no close until settlement:
                # fill it from the live last trade so the price is current, not yesterday's.
                last_day = frame.index[-1].date().isoformat() if len(frame) else None
                if last_day and (not rows or rows[-1][0] != last_day) and math.isfinite(float(frame["Open"].iloc[-1])):
                    live = float(yf.Ticker(sym).fast_info.last_price)
                    if math.isfinite(live) and live > 0:
                        rows.append((last_day, live, None))
                out[sym] = rows
            except Exception:  # yfinance raises many types per symbol; skip that symbol only
                continue
    return out


def merged_bars(store: ResearchStore, inst: dict[str, Any]) -> list[dict[str, Any]]:
    """Local snapshot + live bars, one per day, live wins on overlap."""
    by_day: dict[str, dict[str, Any]] = {}
    path = OHLCV_DIR / f"{inst['local_symbol']}.jsonl"
    if path.exists():
        for b in read_bars(path)[0]:
            day = b["timestamp"][:10]
            by_day[day] = {"timestamp": day, "close": b["close"], "volume": b.get("volume")}
    for r in store.query("SELECT day, close, volume FROM price_bars WHERE instrument_id = ?",
                         (inst["instrument_id"],)):
        by_day[r["day"]] = {"timestamp": r["day"], "close": r["close"], "volume": r["volume"]}
    return [by_day[d] for d in sorted(by_day)]


def _due(store: ResearchStore, insts: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    checked = {r["instrument_id"]: r["checked_at"] for r in store.query("SELECT * FROM price_refresh")}
    return [i for i in insts if now - checked.get(i["instrument_id"], 0) >= REFRESH_TTL_SEC]


def _apply(store: ResearchStore, inst: dict[str, Any], fetched: Bars) -> None:
    iid, local = inst["instrument_id"], inst["local_symbol"]
    store.insert_many("price_bars", [{"instrument_id": iid, "day": d, "close": c, "volume": v} for d, c, v in fetched])
    bars = merged_bars(store, inst)
    source_id = f"SRC-OHLCV-{local}"
    rows = [{**r, "provider": PROVIDER} for r in _market_rows(iid, local, bars, inst["currency"], source_id)]
    first, last = bars[0]["timestamp"], bars[-1]["timestamp"]
    with store.connect() as conn:
        conn.execute("UPDATE instruments SET last_bar = ?, first_bar = ? WHERE instrument_id = ?", (last, first, iid))
        conn.execute(
            "UPDATE source_documents SET provider = ?, title = ?, published_at = ?, retrieved_at = ?, "
            "locator = ?, excerpt = ? WHERE source_id = ?",
            (PROVIDER, f"{local} daily close ({first} → {last})", last, utcnow(), f"{len(bars)} daily bars",
             f"{len(bars)} daily bars from {first} to {last}. Last close {bars[-1]['close']:.2f} "
             f"{inst['currency']} on {last} (Yahoo Finance, unadjusted; the latest session may still be trading).",
             source_id),
        )
    store.insert_many("financial_observations", rows)


def refresh_prices(store: ResearchStore, insts: Iterable[dict[str, Any]], *, fetch: Fetch = yahoo_fetch,
                   force: bool = False) -> dict[str, int]:
    """Top up due listings. Returns counts; never raises."""
    listed = [i for i in insts if i.get("local_symbol")]
    with _lock:  # one refresher at a time; a concurrent page load waits, then sees fresh rows
        now = time.time()
        due = listed if force else _due(store, listed, now)
        if not due:
            return {"due": 0, "updated": 0, "failed": 0}
        last_bars = [date.fromisoformat(i["last_bar"]) for i in due if i.get("last_bar")]
        start = (min(last_bars) - timedelta(days=BACKFILL_DAYS) if last_bars
                 else date.today() - timedelta(days=FIRST_FETCH_DAYS))
        try:
            data = fetch([i["local_symbol"] for i in due], start)
        except Exception as exc:
            log.warning("price refresh failed: %s", exc)
            data = {}
        updated = 0
        for inst in due:
            rows = data.get(inst["local_symbol"]) or []
            if not rows:
                continue
            try:
                _apply(store, inst, rows)
                updated += 1
            except Exception as exc:  # a bad listing must not block the others
                log.warning("price apply failed for %s: %s", inst["instrument_id"], exc)
        store.insert_many("price_refresh", [{"instrument_id": i["instrument_id"], "checked_at": now} for i in due])
    log.info("price refresh: %d due, %d updated", len(due), updated)
    return {"due": len(due), "updated": updated, "failed": len(due) - updated}


def start_background_refresh(get_store: Callable[[], ResearchStore],
                             interval_sec: float = REFRESH_TTL_SEC) -> threading.Thread:
    """Daemon thread: refresh every listing now, then every ``interval_sec``."""

    def loop() -> None:
        while True:
            try:
                store = get_store()
                refresh_prices(store, store.list_instruments())
            except Exception as exc:
                log.warning("background price refresh error: %s", exc)
            time.sleep(interval_sec)

    thread = threading.Thread(target=loop, name="research-price-refresh", daemon=True)
    thread.start()
    return thread
