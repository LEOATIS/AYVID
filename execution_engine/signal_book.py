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
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.logger import get_logger

log = get_logger("signal_book")

_DEFAULT_TTL_MINUTES = 30
_BOOK_PATH = Path("data_store/signal_book.json")
# Append-only ledger of every signal lifecycle event ever seen. Survives
# expiry, container restart, and the active-book LRU eviction. The
# /signals/history endpoint reads this file; nothing in the runtime
# path ever truncates or rewrites it.
_HISTORY_PATH = Path("data_store/signal_history.jsonl")
# Soft cap on the active book — keeps the in-memory dict / signal_book.json
# bounded. Anything older than this drains into history-only.
_ACTIVE_BOOK_CAP = 500


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

    The pricing/sizing block is the canonical contract every downstream
    consumer (Kelly sizing, IB routing, the readiness checker, the
    frontend Signals page) reads. Absolute prices (``stop_loss_price``,
    ``take_profit_price``, ``entry_zone_low/high``) are auto-derived
    from the pct fields in ``__post_init__`` if not provided, so legacy
    callers that only set the percentages still produce a complete
    record. New code SHOULD set quantity / notional / risk_amount
    explicitly via the ``execution_engine.math.position_sizer`` helper.
    """

    # ── Identity ──────────────────────────────────────────────
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

    # ── Legacy / existing optional ────────────────────────────
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

    # Confirmation entry: wait for price to move in signal direction
    # before placing the order. Filters ~25% of false signals.
    requires_confirmation: bool = False
    confirmation_threshold_pct: float = 0.003  # 0.3% move in signal direction
    confirmation_expires_bars: int = 3  # wait max 3 bars then cancel

    # ── Asset-class metadata ──────────────────────────────────
    # Strings rather than the AssetClass enum so JSON round-trip
    # without a custom encoder is trivial. Valid values:
    # EQUITY | INDEX | FX | FUTURE | CRYPTO | ETF | OPTION |
    # COMMODITY | BOND.
    asset_class: str = "EQUITY"

    # ── Absolute pricing (auto-derived from pct fields) ───────
    entry_price: float = 0.0          # alias of entry_price_at_compute
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    risk_reward_ratio: float = 0.0

    # ── Sizing ────────────────────────────────────────────────
    quantity: float = 0.0
    notional_value: float = 0.0
    risk_amount: float = 0.0           # $ at risk if SL is hit
    kelly_fraction: float = 0.0

    # ── Asset-class-specific sizing context ───────────────────
    lot_size: float | None = None
    contract_multiplier: float | None = None
    pip_value: float | None = None
    margin_required: float | None = None

    # ── Options / spreads ─────────────────────────────────────
    option_type: str | None = None     # "CALL" | "PUT"
    leg_2_strike: float | None = None
    leg_2_expiry: str | None = None

    # ── Quality ───────────────────────────────────────────────
    factor_score: float = 0.0          # 0..1 (N/10 factors confirmed)

    # ── Expiry / timing ───────────────────────────────────────
    expires_at: str = ""               # ISO 8601 — derived from computed_at + ttl
    price_at_compute: float = 0.0      # market price at compute (= entry_price)

    # ── Causal intel snapshot for readiness gate ──────────────
    causal_intel: dict[str, Any] = field(default_factory=dict)

    # ── Readiness state (mutated by TradeReadinessChecker) ────
    # WAITING — newly-cached signal, not yet evaluated against live
    #           market. Default for every new entry.
    # READY   — current price entered the entry zone AND every other
    #           gate (regime, position, capital, risk) is green.
    #           This is the state the dispatcher routes on.
    # EXPIRED — past computed_at + ttl_minutes; no longer tradable.
    # CANCELLED — regime rotated or thesis flipped after compute.
    readiness_status: str = "WAITING"
    readiness_reason: str = ""
    readiness_checked_at: str = ""     # ISO 8601
    current_price: float = 0.0
    distance_to_entry_pct: float = 0.0  # (current - entry) / entry — signed

    def __post_init__(self) -> None:
        """Derive the absolute pricing block from the percentage inputs.

        Every site downstream (UI, readiness checker, broker) consumes
        absolute prices; the orchestrator emits percentages. We close
        the gap here so the dataclass is internally consistent the
        moment it is constructed, regardless of which fields the
        caller populated.
        """
        # Entry-price aliases — entry_price ↔ entry_price_at_compute
        # are kept in sync because legacy callers still set the long
        # name and new callers set the short one.
        if self.entry_price <= 0 and self.entry_price_at_compute > 0:
            self.entry_price = self.entry_price_at_compute
        elif self.entry_price > 0 and self.entry_price_at_compute <= 0:
            self.entry_price_at_compute = self.entry_price
        if self.price_at_compute <= 0 and self.entry_price > 0:
            self.price_at_compute = self.entry_price

        # Entry zone bounds — symmetric ± entry_zone_pct around entry.
        if self.entry_price > 0:
            if self.entry_zone_low <= 0:
                self.entry_zone_low = self.entry_price * (1.0 - self.entry_zone_pct)
            if self.entry_zone_high <= 0:
                self.entry_zone_high = self.entry_price * (1.0 + self.entry_zone_pct)

        # Stop-loss / take-profit prices. Direction-aware: LONG SL is
        # below entry, SHORT SL is above. HOLD signals get no SL/TP
        # because they are signal-only (no execution).
        direction_up = str(self.direction).upper()
        if (
            self.entry_price > 0
            and self.stop_loss_price <= 0
            and self.stop_loss_pct > 0
            and direction_up in ("LONG", "SHORT")
        ):
            sl_sign = -1.0 if direction_up == "LONG" else 1.0
            self.stop_loss_price = self.entry_price * (
                1.0 + sl_sign * self.stop_loss_pct / 100.0
            )
        if (
            self.entry_price > 0
            and self.take_profit_price <= 0
            and self.take_profit_pct > 0
            and direction_up in ("LONG", "SHORT")
        ):
            tp_sign = 1.0 if direction_up == "LONG" else -1.0
            self.take_profit_price = self.entry_price * (
                1.0 + tp_sign * self.take_profit_pct / 100.0
            )

        # Risk/reward ratio: reward / risk in absolute price units.
        if (
            self.risk_reward_ratio <= 0
            and self.entry_price > 0
            and self.stop_loss_price > 0
            and self.take_profit_price > 0
        ):
            risk = abs(self.entry_price - self.stop_loss_price)
            reward = abs(self.take_profit_price - self.entry_price)
            if risk > 0:
                self.risk_reward_ratio = round(reward / risk, 4)

        # Notional value & dollar risk: derived from quantity if set.
        # Asset-class aware — equity-style qty*price is wrong for FX
        # (qty is in lots × 100k base units), futures/commodities (qty is
        # contracts × contract_multiplier), and options (qty × 100). The
        # canonical sizers in execution_engine/math/position_sizer.py
        # already populate notional_value correctly; this fallback fires
        # when a signal lands on disk with notional_value=0 (manual /
        # LLM-emitted signals, restored ledger rows). Pre-fix this path
        # produced USDJPY @ 155.45 → "$15,546.64 notional for 1 lot",
        # which is 100× off — anyone in finance spots it instantly.
        if self.notional_value <= 0 and self.quantity > 0 and self.entry_price > 0:
            ac = (self.asset_class or "EQUITY").upper()
            if ac == "FX":
                lot_units = float(self.lot_size or 100_000.0)
                self.notional_value = round(self.quantity * lot_units * self.entry_price, 2)
            elif ac in ("FUTURE", "COMMODITY"):
                mult = float(self.contract_multiplier or self.lot_size or 1.0)
                self.notional_value = round(self.quantity * mult * self.entry_price, 2)
            elif ac == "OPTION":
                mult = float(self.contract_multiplier or 100.0)
                self.notional_value = round(self.quantity * mult * self.entry_price, 2)
            else:  # EQUITY / ETF / INDEX / CRYPTO / BOND
                self.notional_value = round(self.quantity * self.entry_price, 2)
        if (
            self.risk_amount <= 0
            and self.quantity > 0
            and self.entry_price > 0
            and self.stop_loss_price > 0
        ):
            self.risk_amount = round(
                self.quantity * abs(self.entry_price - self.stop_loss_price), 2
            )

        # Expires-at ISO timestamp. Computed once so the frontend can
        # render a stable "expires in 12m" without re-parsing
        # computed_at + adding ttl.
        if not self.expires_at and self.computed_at:
            try:
                computed = dt.datetime.fromisoformat(self.computed_at)
                if computed.tzinfo is None:
                    computed = computed.replace(tzinfo=dt.UTC)
                self.expires_at = (
                    computed + dt.timedelta(minutes=self.ttl_minutes)
                ).isoformat()
            except (ValueError, TypeError):
                pass

    @property
    def is_stale(self) -> bool:
        try:
            computed = dt.datetime.fromisoformat(self.computed_at)
            age = dt.datetime.now(dt.UTC) - computed.replace(tzinfo=dt.UTC)
            return age.total_seconds() > self.ttl_minutes * 60
        except (ValueError, TypeError):
            return True

    @property
    def is_expired(self) -> bool:
        """Stricter cousin of ``is_stale`` keyed off ``expires_at``."""
        if not self.expires_at:
            return self.is_stale
        try:
            exp = dt.datetime.fromisoformat(self.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=dt.UTC)
            return dt.datetime.now(dt.UTC) >= exp
        except (ValueError, TypeError):
            return self.is_stale

    def price_in_entry_zone(self, current_price: float) -> bool:
        """Check if current price is still within acceptable entry range."""
        if self.entry_price_at_compute <= 0:
            return True
        pct_move = abs(current_price - self.entry_price_at_compute) / self.entry_price_at_compute
        return pct_move <= self.entry_zone_pct

    def is_in_entry_zone(self, current_price: float) -> bool:
        """Use the absolute zone bounds derived in __post_init__."""
        if current_price <= 0 or self.entry_zone_low <= 0 or self.entry_zone_high <= 0:
            return self.price_in_entry_zone(current_price)
        return self.entry_zone_low <= current_price <= self.entry_zone_high

    def compute_distance_to_entry_pct(self, current_price: float) -> float:
        """Signed distance from current to entry as a fraction.

        Negative means current is below entry (good for LONG limits),
        positive means current is above entry (good for SHORT limits).
        """
        if self.entry_price <= 0 or current_price <= 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price


# ── Test-data tripwire ─────────────────────────────────────────────────
# Strings that should never appear in a production signal. Anything
# carrying these names is a fixture / placeholder and must be rejected
# at the SignalBook boundary so it cannot drive a shadow fill.
_BANNED_STRATEGY_NAMES = frozenset(
    s.lower() for s in (
        "", "test", "test_strategy", "dummy", "placeholder",
        "fake", "fake_signal", "sample", "stub",
    )
)
_BANNED_TICKERS = frozenset({"", "TEST", "DUMMY", "FAKE", "SAMPLE"})


def _validate_signal(signal: PrecomputedSignal) -> None:
    """Reject test / debug / zero-conviction entries.

    Run on every write path (upsert / upsert_batch / load_from_disk).
    Raises ``ValueError`` for hard rejects so the caller fails loudly
    instead of silently corrupting the book.
    """
    ticker_norm = (signal.ticker or "").strip().upper()
    if ticker_norm in _BANNED_TICKERS:
        raise ValueError(
            f"Refusing to cache signal with placeholder ticker {signal.ticker!r}"
        )
    strat_norm = (signal.strategy_name or "").strip().lower()
    if strat_norm in _BANNED_STRATEGY_NAMES:
        raise ValueError(
            f"Refusing to cache signal with test/empty strategy_name "
            f"{signal.strategy_name!r} for {signal.ticker}"
        )
    fp_norm = (signal.regime_fingerprint or "").strip().lower()
    if fp_norm in {"test_fp", "test", "dummy", "placeholder"}:
        raise ValueError(
            f"Refusing to cache signal with test regime_fingerprint "
            f"{signal.regime_fingerprint!r} for {signal.ticker}"
        )
    # HOLD signals are allowed at zero conviction (signal-only); EXECUTE
    # paths require both conviction and expected_r > 0 so the shadow
    # consumer never sees a zero-sized order.
    direction_up = (signal.direction or "").upper()
    if direction_up in ("LONG", "SHORT"):
        if signal.conviction <= 0.0:
            raise ValueError(
                f"Refusing to cache {direction_up} signal for "
                f"{signal.ticker} with zero conviction"
            )
        if signal.expected_r <= 0.0:
            raise ValueError(
                f"Refusing to cache {direction_up} signal for "
                f"{signal.ticker} with zero expected_r"
            )


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

    def invalidate_and_mark_override(self, ticker: str, reason: str = "") -> int:
        """Atomic invalidate + mark-override.

        Replaces the legacy two-step ``invalidate(t); mark_override(t)``
        sequence. Without atomicity, the fast path could observe a
        window where the cache has been emptied but the override flag
        is not yet set, allowing a stale-data lookup to slip through.
        Returns the number of signals evicted (same contract as
        ``invalidate``).
        """
        with self._lock:
            evicted = self.invalidate(ticker, reason=reason)
            # RLock is re-entrant so the nested ``with self._lock`` in
            # ``mark_override_pending`` is fine; we keep the call to
            # avoid duplicating the override timestamp logic.
            self.mark_override_pending(ticker)
            return evicted

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

    def open_thesis_tickers(self) -> set[str]:
        """Snapshot of every ticker with at least one open thesis.

        Used by the IB-side reconciliation heartbeat to detect divergence
        between local state and broker positions (e.g. a close order
        that succeeded locally but never confirmed at IB).
        """
        with self._lock:
            return {ticker for (ticker, _sig) in self._open_theses.values()}

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
        """Insert or update a pre-computed signal.

        Hard rejects test/placeholder/zero-conviction entries so a
        debug fixture from a notebook can never end up driving real
        shadow fills. The exact tripwires below are the M3 retro
        findings — the regression that produced the May 1 fake NVDA
        trades was a ``test_strategy`` row landing in the book.

        Also writes a ``CREATED`` row to the immortal history ledger so
        every signal ever generated stays auditable forever, even after
        the active-book LRU evicts it.
        """
        _validate_signal(signal)
        with self._lock:
            key = self._key(signal.ticker, signal.regime_fingerprint)
            self._signals[key] = signal
            self._stats["upserts"] += 1
        # Append-only history is intentionally outside the lock — the
        # file write has its own fcntl lock and the in-memory state
        # has already been committed. Better to risk a missed history
        # row (logged) than to extend the RLock across disk I/O.
        self._append_history(signal, event="CREATED")

    def upsert_batch(self, signals: list[PrecomputedSignal]) -> int:
        """Insert multiple signals atomically."""
        for sig in signals:
            _validate_signal(sig)
        with self._lock:
            for sig in signals:
                key = self._key(sig.ticker, sig.regime_fingerprint)
                self._signals[key] = sig
                self._stats["upserts"] += 1
            count = len(signals)
        for sig in signals:
            self._append_history(sig, event="CREATED")
        return count

    # ── Immortal history ledger ────────────────────────────────────
    @staticmethod
    def _signal_to_record(signal: PrecomputedSignal, event: str) -> dict:
        """Project a PrecomputedSignal + event tag into a JSONL row."""
        try:
            payload = asdict(signal)
        except TypeError:
            payload = {
                k: getattr(signal, k, None)
                for k in PrecomputedSignal.__dataclass_fields__
            }
        record = {
            "event": event,
            "logged_at": dt.datetime.now(dt.UTC).isoformat(),
            "key": SignalBook._key_static(signal.ticker, signal.regime_fingerprint),
            **payload,
        }
        return record

    def _append_history(self, signal: PrecomputedSignal, *, event: str) -> None:
        """Append a single event row to ``signal_history.jsonl``.

        Multi-process-safe via fcntl LOCK_EX. Skipped silently on the
        rare platforms (Windows in CI) without fcntl — the runtime
        target is Linux containers + macOS dev so this is fine.
        """
        record = self._signal_to_record(signal, event=event)
        path = _HISTORY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import json as _json
            try:
                import fcntl  # type: ignore[import-not-found]
            except ImportError:
                fcntl = None  # type: ignore[assignment]
            with open(path, "a", encoding="utf-8") as fh:
                if fcntl is not None:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    except OSError:
                        pass
                fh.write(_json.dumps(record, default=str) + "\n")
                fh.flush()
                try:
                    import os as _os
                    _os.fsync(fh.fileno())
                except OSError:
                    pass
                if fcntl is not None:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        except Exception as exc:
            log.warning(f"signal_history append failed for {signal.ticker}: {exc}")
            return
        try:
            from shared.gcs import sync_artifact
            sync_artifact(path)
        except Exception:
            pass

    @staticmethod
    def _key_static(ticker: str, regime_fingerprint: str) -> str:
        """Mirror of the instance ``_key`` for use without a SignalBook."""
        return f"{ticker}|{regime_fingerprint}"

    def get_history(
        self,
        *,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
        ticker: str | None = None,
        asset_class: str | None = None,
        status: str | None = None,
        event: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Filtered scan over ``signal_history.jsonl``.

        Reads the entire file (it's append-only, capped only by disk).
        For demo-scale (~10K rows / 14 days) this is sub-millisecond.
        Production-scale would page or move to a column store; we are
        not there yet and a pure-file solution is one less moving part.
        """
        path = _HISTORY_PATH
        if not path.exists():
            return []
        out: list[dict] = []
        start_iso = start.isoformat() if start else None
        end_iso = end.isoformat() if end else None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json as _json
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    ts = (
                        rec.get("computed_at")
                        or rec.get("logged_at")
                        or ""
                    )
                    if start_iso and ts and ts < start_iso:
                        continue
                    if end_iso and ts and ts > end_iso:
                        continue
                    if ticker and rec.get("ticker") != ticker:
                        continue
                    if asset_class and rec.get("asset_class") != asset_class:
                        continue
                    if status and rec.get("readiness_status") != status:
                        continue
                    if event and rec.get("event") != event:
                        continue
                    out.append(rec)
        except OSError:
            return []
        out.sort(
            key=lambda r: r.get("computed_at") or r.get("logged_at") or "",
            reverse=True,
        )
        return out[:limit]

    def expire_stale(self) -> int:
        """Mark stale signals EXPIRED (do NOT delete) + LRU-evict only the
        very oldest beyond the active-book cap.

        Behaviour change vs. legacy: signals are immortal in
        ``signal_history.jsonl``. The active book retains the last
        ``_ACTIVE_BOOK_CAP`` (default 500) for live UI rendering;
        anything older drains away but stays queryable via
        ``get_history`` and the /signals/history endpoint.

        Returns the number of signals **flipped to EXPIRED** (the
        callers that historically read this count for monitoring still
        get a meaningful value — only newly-flipped, not LRU-evicted).
        """
        flipped: list[PrecomputedSignal] = []
        with self._lock:
            for sig in self._signals.values():
                if sig.readiness_status in ("EXPIRED", "CANCELLED", "FILLED"):
                    continue
                if sig.is_stale or sig.is_expired:
                    sig.readiness_status = "EXPIRED"
                    sig.readiness_reason = sig.readiness_reason or "ttl_elapsed"
                    sig.readiness_checked_at = dt.datetime.now(dt.UTC).isoformat()
                    flipped.append(sig)
            # LRU-evict the oldest beyond the cap. We do NOT delete from
            # history — the ledger is append-only.
            if len(self._signals) > _ACTIVE_BOOK_CAP:
                ranked = sorted(
                    self._signals.items(),
                    key=lambda kv: kv[1].computed_at or "",
                    reverse=True,
                )
                self._signals = dict(ranked[:_ACTIVE_BOOK_CAP])
            self._stats["stale_evictions"] += len(flipped)
        for sig in flipped:
            self._append_history(sig, event="EXPIRED")
        return len(flipped)

    def get_all_active(self) -> list[PrecomputedSignal]:
        """Return all non-stale signals."""
        with self._lock:
            return [s for s in self._signals.values() if not s.is_stale]

    # ── Readiness query API (consumed by TradeReadinessChecker + /signals) ──

    def get_signals_by_status(self, status: str) -> dict[str, PrecomputedSignal]:
        """Return all signals whose ``readiness_status`` matches.

        Returns a fresh dict so callers can iterate without holding
        the lock. Values are still references to the live cache
        entries (intentional — readiness updates mutate in place).
        """
        with self._lock:
            return {k: v for k, v in self._signals.items() if v.readiness_status == status}

    def get_ready_signals(self) -> list[PrecomputedSignal]:
        """Return READY signals sorted by conviction descending.

        This is the surface the dispatcher reads to decide which
        signals to route to the broker first when capital is finite.
        """
        ready = self.get_signals_by_status("READY")
        return sorted(ready.values(), key=lambda s: s.conviction, reverse=True)

    def get_all_signals_with_readiness(self) -> list[PrecomputedSignal]:
        """Return every signal in the book regardless of stale-ness.

        EXPIRED signals stay visible so the frontend can render the
        most-recent decision even after its TTL elapses; the
        readiness_status field is the authoritative tradability gate.
        """
        with self._lock:
            return list(self._signals.values())

    def update_readiness(
        self,
        key: str,
        *,
        status: str,
        current_price: float = 0.0,
        distance_to_entry_pct: float = 0.0,
        readiness_checked_at: dt.datetime | str | None = None,
        readiness_reason: str = "",
    ) -> bool:
        """Mutate the readiness state of a single signal in place.

        Returns True if the signal was found and updated, False
        otherwise. The book is NOT auto-persisted here — callers
        (typically the readiness checker's main loop) batch many
        updates per cycle and call ``save_to_disk`` once at the end.
        """
        if isinstance(readiness_checked_at, dt.datetime):
            checked_iso = readiness_checked_at.isoformat()
        elif isinstance(readiness_checked_at, str) and readiness_checked_at:
            checked_iso = readiness_checked_at
        else:
            checked_iso = dt.datetime.now(dt.UTC).isoformat()
        prev_status: str | None = None
        sig_snapshot: PrecomputedSignal | None = None
        with self._lock:
            sig = self._signals.get(key)
            if sig is None:
                return False
            prev_status = sig.readiness_status
            sig.readiness_status = status
            sig.readiness_reason = readiness_reason or sig.readiness_reason
            sig.readiness_checked_at = checked_iso
            if current_price > 0:
                sig.current_price = current_price
            sig.distance_to_entry_pct = distance_to_entry_pct
            sig_snapshot = sig
        # Status transition → history event. We only log on transitions
        # (not on every "still WAITING" tick) so the ledger stays
        # readable.
        if sig_snapshot is not None and prev_status != status:
            self._append_history(sig_snapshot, event=f"STATUS_{status}")
        return True

    def mark_all_expired(self) -> int:
        """Sweep — flip every TTL-elapsed WAITING/READY signal to EXPIRED.

        Returns the number of signals transitioned. Called once per
        readiness-checker cycle so the frontend's expiry counter
        moves forward even when no live evaluation hits the signal.
        """
        flipped: list[PrecomputedSignal] = []
        with self._lock:
            for sig in self._signals.values():
                if sig.readiness_status in ("EXPIRED", "CANCELLED"):
                    continue
                if sig.is_expired:
                    sig.readiness_status = "EXPIRED"
                    sig.readiness_reason = "ttl_elapsed"
                    sig.readiness_checked_at = dt.datetime.now(dt.UTC).isoformat()
                    flipped.append(sig)
        for sig in flipped:
            self._append_history(sig, event="EXPIRED")
        return len(flipped)

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

        Uses atomic write-then-rename + a `.bak` rotation so a crash
        mid-write never corrupts the existing file AND a corrupt
        primary can be recovered from the previous good copy.
        """
        from shared.atomic_io import atomic_write_json

        path = path or _BOOK_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {k: asdict(v) for k, v in self._signals.items()}
        atomic_write_json(path, data, indent=2, default=str, keep_backup=True)
        log.info(f"Signal book saved: {len(data)} signals -> {path}")
        try:
            from shared.gcs import sync_artifact
            sync_artifact(path)
        except Exception:
            pass

    def load_from_disk(self, path: Path | None = None) -> int:
        """Load signal book from disk. Returns count of active signals
        loaded into memory.

        Falls back to the `.bak` if the primary fails to parse — this
        is the dual-file rotation written by ``save_to_disk``.

        Stale entries (TTL elapsed) are loaded into the in-memory book
        too — but they're loaded with their on-disk status preserved
        so the readiness checker's next ``mark_all_expired`` sweep can
        flip them to EXPIRED *and persist that to disk*.

        Why we don't just filter stale on load (the prior behaviour):
        the dashboard's signal/* endpoints read the on-disk file
        directly, not the in-memory book. So a stale READY entry
        invisible to the in-memory checker stayed READY on disk
        forever — the May 3 console showed seven weeks-old signals
        still flagged READY because every restart re-loaded them and
        then ignored them. By loading + immediately running
        mark_all_expired + persisting, we guarantee disk converges to
        in-memory state on every boot.
        """
        from shared.atomic_io import safe_load_json

        path = path or _BOOK_PATH
        raw = safe_load_json(path, fallback_to_backup=True)
        if raw is None:
            if path.exists():
                log.error(
                    f"Signal book at {path} (and its .bak) is corrupt; "
                    f"starting empty. Investigate before trading."
                )
            return 0
        try:
            stale_count = 0
            with self._lock:
                for key, val in raw.items():
                    sig = PrecomputedSignal(
                        **{
                            k: v
                            for k, v in val.items()
                            if k in PrecomputedSignal.__dataclass_fields__
                        }
                    )
                    self._signals[key] = sig
                    if sig.is_stale:
                        stale_count += 1
            total = len(self._signals)
        except Exception as exc:
            log.warning(f"Signal book load failed during deserialise: {exc}")
            return 0

        # Sweep + persist so on-disk EXPIRED catches up with reality.
        # Done outside the lock above (mark_all_expired re-acquires
        # internally and so does save_to_disk).
        if stale_count > 0:
            flipped = self.mark_all_expired()
            if flipped > 0:
                try:
                    self.save_to_disk(path)
                    log.info(
                        f"Signal book loaded: {total - flipped} active + "
                        f"{flipped} flipped to EXPIRED (persisted) from {path}"
                    )
                except Exception as exc:
                    log.warning(f"signal_book persist after load-sweep failed: {exc}")
                return total - flipped

        log.info(f"Signal book loaded: {total} active signals from {path}")
        return total

    def clear(self) -> None:
        with self._lock:
            self._signals.clear()


# Module-level singleton
_signal_book = SignalBook()


def get_signal_book() -> SignalBook:
    """Return the global signal book singleton."""
    return _signal_book
