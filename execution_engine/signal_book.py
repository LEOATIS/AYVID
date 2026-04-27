"""Pre-computed signal book — the bridge between heavy LLM analysis and
sub-second live execution.

The signal book stores trading decisions keyed by (ticker, regime_fingerprint).
The background pre-compute worker writes to it every cycle; the fast execution
path reads from it at trade time with zero LLM calls.

This is how institutional funds operate: heavy research offline, fast
execution live. The signal book is the "morning meeting" distilled into
a machine-readable cache.

Concurrency model: uses threading.RLock for synchronous callers (CLI, tests)
and cross-thread access from asyncio.to_thread workers. All critical sections
are short (dict lookups, no I/O) so lock contention is sub-microsecond.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.logger import get_logger

log = get_logger("signal_book")

_DEFAULT_TTL_MINUTES = 30
_BOOK_PATH = Path("data_store/signal_book.json")


def compose_thesis_signature(causal_intel: dict | None) -> str:
    """Batch-2/C: canonical, short, deterministic thesis signature.

    Given a ``causal_intel`` dict (the payload the graph layer hands
    out — shock_type, expected_direction, shock_bias, plus
    beneficiary / victim sets), produce a stable string key that
    changes iff the *meaningful* causal thesis has changed.

    We intentionally keep this narrow:
      * ``shock_bias``            — BULLISH / BEARISH / NEUTRAL
      * ``expected_direction``    — explicit side
      * ``shock_type``            — e.g. supply_chain, geopolitics
      * ``shock_resolved``        — True when the news says it's over

    Noise fields (timestamps, magnitudes, source strings, full
    beneficiary lists) are NOT part of the signature — we do not want
    the monitor closing a position merely because the intel text was
    rewritten. Real thesis flips (BEARISH → BULLISH, shock_resolved
    flipped to True) are the only events we trade on.
    """
    if not isinstance(causal_intel, dict) or not causal_intel:
        return ""
    # Only emit the resolved/active token when we have ANY other
    # meaningful field. A literally-empty dict is "no thesis", not
    # "active thesis" — otherwise we'd generate spurious signatures
    # on cold-start cycles with no intel yet.
    bias = str(causal_intel.get("shock_bias", "")).upper()
    direction = str(causal_intel.get("expected_direction", "")).upper()
    shock_type = str(causal_intel.get("shock_type", "")).lower()
    if not (bias or direction or shock_type):
        return ""
    resolved_tag = "resolved" if causal_intel.get("shock_resolved") else "active"
    parts = [bias, direction, shock_type, resolved_tag]
    return "|".join(p for p in parts if p)


@dataclass
class PrecomputedSignal:
    """A single pre-computed trading decision ready for instant execution.

    ``tier3_shock`` is the propagation channel for P0-A: signals produced
    by the Tier-3 emergency re-compute path MUST set this to True so the
    fast-execute → broker router knows to cross the spread with a LIMIT
    instead of TWAP/VWAP'ing the entry. Normal periodic pre-computes set
    False (passive routing on horizon).
    """

    ticker: str
    direction: str
    instrument_type: str
    conviction: float
    strategy_name: str
    strike: float | None
    expiry_date: str
    stop_loss_pct: float
    take_profit_pct: float
    rationale: str
    computed_at: str
    ttl_minutes: int = _DEFAULT_TTL_MINUTES
    regime_fingerprint: str = ""
    second_leg: dict[str, Any] = field(default_factory=dict)
    expected_r: float = 2.0
    min_r: float = 1.5
    entry_price_at_compute: float = 0.0
    entry_zone_pct: float = 0.02
    tier3_shock: bool = False

    # Batch-2/C thesis-invalidation channel. When this signal was
    # generated under a specific causal thesis (e.g. "TSMC fab fire
    # → short KLAC"), we pin the thesis signature here so the live
    # monitor can compare against the next cycle's causal intel
    # and close any open position if the thesis is contradicted
    # (e.g. "TSMC resumes shipments"). The signature is a short
    # deterministic string — not a full JSON dump — so string
    # equality is the invalidation check.
    thesis_signature: str = ""

    @property
    def is_stale(self) -> bool:
        try:
            computed = dt.datetime.fromisoformat(self.computed_at)
            age = dt.datetime.now(dt.UTC) - computed.replace(tzinfo=dt.UTC)
            return age.total_seconds() > self.ttl_minutes * 60
        except (ValueError, TypeError):
            return True

    def price_in_entry_zone(self, current_price: float) -> bool:
        """Check if current price is still within acceptable entry range."""
        if self.entry_price_at_compute <= 0:
            return True
        pct_move = abs(current_price - self.entry_price_at_compute) / self.entry_price_at_compute
        return pct_move <= self.entry_zone_pct


class SignalBook:
    """Thread-safe in-memory signal cache with TTL expiry and regime awareness.

    Keys are (ticker, regime_fingerprint) tuples. A signal is only valid
    when the current regime matches the regime at compute time.

    Shock interception: when a major event hits mid-cycle, the book supports
    immediate invalidation of affected tickers and tracks which tickers have
    an emergency re-compute in flight. The fast path refuses to trade any
    ticker with a pending override, preventing stale pre-shock signals from
    executing while the Tier 3 LLM override is still running.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._signals: dict[str, PrecomputedSignal] = {}
        self._overrides_pending: dict[str, dt.datetime] = {}
        # Batch-2/C open-position thesis map:
        #   intent_id → (ticker, thesis_signature)
        # Populated by `register_open_thesis` when an order fires and
        # cleared by `clear_open_thesis` when the position closes. The
        # live monitor iterates this on each cycle and flags any
        # intent_id whose ticker's next causal intel contradicts the
        # pinned signature — forcing a manual close.
        self._open_theses: dict[str, tuple[str, str]] = {}
        self._stats = {
            "lookups": 0,
            "hits": 0,
            "misses": 0,
            "blocked_by_override": 0,
            "stale_evictions": 0,
            "shock_invalidations": 0,
            "upserts": 0,
            "thesis_invalidations": 0,
            "last_precompute_at": None,
            "last_precompute_duration_ms": 0,
        }

    def _key(self, ticker: str, regime: str) -> str:
        return f"{ticker}|{regime}"

    def invalidate(self, ticker: str, reason: str = "") -> int:
        """Immediately remove all cached signals for a ticker.

        Called when a shock event makes existing signals unreliable.
        Returns the number of signals evicted.
        """
        with self._lock:
            stale_keys = [k for k in self._signals if k.startswith(f"{ticker}|")]
            for k in stale_keys:
                del self._signals[k]
            self._stats["shock_invalidations"] += len(stale_keys)
            if stale_keys:
                log.warning(
                    f"Shock invalidation: evicted {len(stale_keys)} signals "
                    f"for {ticker} — {reason}"
                )
            return len(stale_keys)

    def mark_override_pending(self, ticker: str) -> None:
        """Flag that an emergency re-compute is in flight for this ticker.

        The fast path will refuse to trade this ticker until the override
        completes or times out (60s safety valve).
        """
        with self._lock:
            self._overrides_pending[ticker] = dt.datetime.now(dt.UTC)
            log.info(f"Override pending: {ticker} (fast path blocked)")

    def clear_override_pending(self, ticker: str) -> None:
        """Clear the override-pending flag after re-compute finishes."""
        with self._lock:
            self._overrides_pending.pop(ticker, None)
            log.info(f"Override cleared: {ticker} (fast path unblocked)")

    def is_override_pending(self, ticker: str) -> bool:
        """Check if an emergency override is in flight for this ticker.

        Includes a 60-second safety valve: if the override has been pending
        longer than that, we assume the LLM pipeline failed. We clear the
        flag AND evict any cached signals for the ticker — those signals
        were written by the fast-pass under shock conditions and are now
        stale. The fast path will get a cache miss and fall back to the
        rule-based orchestrator, which will use live data.
        """
        with self._lock:
            started = self._overrides_pending.get(ticker)
            if started is None:
                return False
            age = (dt.datetime.now(dt.UTC) - started).total_seconds()
            if age > 60:
                del self._overrides_pending[ticker]
                stale_keys = [k for k in self._signals if k.startswith(f"{ticker}|")]
                for k in stale_keys:
                    del self._signals[k]
                log.warning(
                    f"Override timeout: {ticker} was pending for {age:.0f}s — "
                    f"cleared flag and evicted {len(stale_keys)} stale signals"
                )
                return False
            return True

    def get_pending_overrides(self) -> list[str]:
        """Return list of tickers with active override-in-flight flags."""
        with self._lock:
            return list(self._overrides_pending.keys())

    # ── Batch-2/C: thesis-invalidation open-position tracking ───────

    def register_open_thesis(
        self,
        intent_id: str,
        ticker: str,
        thesis_signature: str,
    ) -> None:
        """Record that position `intent_id` on `ticker` was opened
        under `thesis_signature`. Call when the order fires.

        The signature is typically produced by
        ``compose_thesis_signature(causal_intel)`` so the invalidation
        check is string-equality — cheap, deterministic, and easy to
        serialise into the mmap kill-switch.
        """
        if not intent_id or not ticker:
            return
        with self._lock:
            self._open_theses[intent_id] = (ticker, thesis_signature or "")

    def clear_open_thesis(self, intent_id: str) -> None:
        """Drop the thesis pin after the position closes."""
        with self._lock:
            self._open_theses.pop(intent_id, None)

    def invalidated_intents(
        self,
        current_theses: dict[str, str],
    ) -> list[tuple[str, str, str, str]]:
        """Return open intents whose pinned thesis has changed.

        ``current_theses`` is a per-ticker snapshot of the live thesis
        signature produced by the ingestion cycle. This function returns
        a list of ``(intent_id, ticker, pinned_sig, current_sig)``
        tuples for every open position whose pinned signature differs
        from its ticker's current signature. Callers (the continuous
        worker or the live runner) must then close those positions
        immediately — they've lost their causal justification.

        An empty string on the current side is treated as "no current
        thesis" and does NOT trigger invalidation — we only invalidate
        on an explicit, non-empty, conflicting new signature. This
        guards against spurious closes during quiet news cycles where
        no causal intel has been refreshed.
        """
        with self._lock:
            out: list[tuple[str, str, str, str]] = []
            for intent_id, (ticker, pinned) in self._open_theses.items():
                cur = current_theses.get(ticker, "")
                if cur and pinned and cur != pinned:
                    self._stats["thesis_invalidations"] += 1
                    out.append((intent_id, ticker, pinned, cur))
            return out

    def lookup(self, ticker: str, regime: str) -> PrecomputedSignal | None:
        """Look up a pre-computed signal. Returns None if stale, missing,
        the regime has rotated since compute, or if an override is in
        flight for this ticker.

        Strict `(ticker, regime)` matching is a P0-C invariant: returning
        a different-regime signal — a vol or trend state that no longer
        applies — is a silent correctness bug, not a graceful fallback.
        Regime mismatch → miss → upstream must recompute.

        Override-timeout eviction is symmetric with `is_override_pending`:
        a stuck override (>60s) is treated as a failed recompute, and all
        cached per-ticker signals are flushed to force the caller into a
        live recompute rather than trading on pre-shock cache.
        """
        with self._lock:
            self._stats["lookups"] += 1

            # Block fast path while override is running — or evict if stuck
            started = self._overrides_pending.get(ticker)
            if started is not None:
                age = (dt.datetime.now(dt.UTC) - started).total_seconds()
                if age <= 60:
                    self._stats["blocked_by_override"] += 1
                    log.debug(f"Lookup blocked: {ticker} has override in-flight ({age:.0f}s ago)")
                    return None
                # Override timed out. Mirror `is_override_pending` cleanup:
                # drop the pending flag AND evict any cached signals for
                # this ticker — they were computed under shock conditions
                # and are now stale. Forcing a miss sends the caller back
                # to the rule-based orchestrator with live data.
                del self._overrides_pending[ticker]
                stale_keys = [k for k in self._signals if k.startswith(f"{ticker}|")]
                for k in stale_keys:
                    del self._signals[k]
                self._stats["stale_evictions"] += len(stale_keys)
                log.warning(
                    f"Lookup override timeout: {ticker} pending {age:.0f}s — "
                    f"flag cleared and {len(stale_keys)} cached signals evicted"
                )
                self._stats["misses"] += 1
                return None

            key = self._key(ticker, regime)
            sig = self._signals.get(key)

            if sig is None:
                # P0-C: strict regime match — NO cross-regime fallback.
                # A lookup under a rotated regime is a miss, not a hit
                # against a stale-regime signal.
                self._stats["misses"] += 1
                return None

            if sig.is_stale:
                self._stats["stale_evictions"] += 1
                self._stats["misses"] += 1
                del self._signals[key]
                return None

            self._stats["hits"] += 1
            return sig

    def upsert(self, signal: PrecomputedSignal) -> None:
        """Insert or update a pre-computed signal."""
        with self._lock:
            key = self._key(signal.ticker, signal.regime_fingerprint)
            self._signals[key] = signal
            self._stats["upserts"] += 1

    def upsert_batch(self, signals: list[PrecomputedSignal]) -> int:
        """Insert multiple signals atomically."""
        with self._lock:
            for sig in signals:
                key = self._key(sig.ticker, sig.regime_fingerprint)
                self._signals[key] = sig
                self._stats["upserts"] += 1
            return len(signals)

    def expire_stale(self) -> int:
        """Remove all stale signals. Returns count of evicted entries."""
        with self._lock:
            stale_keys = [k for k, v in self._signals.items() if v.is_stale]
            for k in stale_keys:
                del self._signals[k]
            self._stats["stale_evictions"] += len(stale_keys)
            return len(stale_keys)

    def get_all_active(self) -> list[PrecomputedSignal]:
        """Return all non-stale signals."""
        with self._lock:
            return [s for s in self._signals.values() if not s.is_stale]

    def size(self) -> int:
        with self._lock:
            return len(self._signals)

    def get_stats(self) -> dict:
        with self._lock:
            return {
                **self._stats,
                "active_signals": sum(1 for s in self._signals.values() if not s.is_stale),
                "total_cached": len(self._signals),
                "overrides_in_flight": len(self._overrides_pending),
                "hit_rate": (self._stats["hits"] / max(self._stats["lookups"], 1) * 100),
            }

    def record_precompute(self, duration_ms: float) -> None:
        """Record timing of the last pre-compute cycle."""
        with self._lock:
            self._stats["last_precompute_at"] = dt.datetime.now(dt.UTC).isoformat()
            self._stats["last_precompute_duration_ms"] = round(duration_ms, 1)

    def save_to_disk(self, path: Path | None = None) -> None:
        """Persist the signal book to JSON for crash recovery.

        Uses atomic write-then-rename so a crash mid-write never
        corrupts the existing file. The C++ watchdog can SIGKILL us
        at any instant; a half-written .tmp is harmless.
        """
        path = path or _BOOK_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {k: asdict(v) for k, v in self._signals.items()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(path)
        log.info(f"Signal book saved: {len(data)} signals -> {path}")

    def load_from_disk(self, path: Path | None = None) -> int:
        """Load signal book from disk. Returns count of loaded signals."""
        path = path or _BOOK_PATH
        if not path.exists():
            return 0
        try:
            raw = json.loads(path.read_text())
            with self._lock:
                for key, val in raw.items():
                    sig = PrecomputedSignal(
                        **{
                            k: v
                            for k, v in val.items()
                            if k in PrecomputedSignal.__dataclass_fields__
                        }
                    )
                    if not sig.is_stale:
                        self._signals[key] = sig
            loaded = len(self._signals)
            log.info(f"Signal book loaded: {loaded} active signals from {path}")
            return loaded
        except Exception as exc:
            log.warning(f"Signal book load failed: {exc}")
            return 0

    def clear(self) -> None:
        with self._lock:
            self._signals.clear()


# Module-level singleton
_signal_book = SignalBook()


def get_signal_book() -> SignalBook:
    """Return the global signal book singleton."""
    return _signal_book
