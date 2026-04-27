"""Deterministic candlestick chart renderer — pure SVG.

Historically this file was matplotlib-based and produced PNG bytes.
Matplotlib's first-use font-cache build added a multi-second hang to
the backend's startup path (``Matplotlib is building the font cache;
this may take a moment.``) AND every subprocess that imported pattern
tooling paid the same cost. We've swapped the renderer to a pure-SVG
implementation:

  • No matplotlib → no font cache build, no Agg backend init
  • No runtime dependency beyond the stdlib + numpy (already loaded)
  • Output is raw SVG bytes — perfect for inline-embedding in the
    Next.js frontend, lightweight for caching, and still deterministic
    so the fingerprint / hash path is unchanged
  • Downstream: the vision-LLM path in ``pattern/node.py`` is gated
    behind ``settings.PATTERN_AGENT_ENABLED`` (default off). When the
    flag is on and somebody wires an SVG-capable vision model, the
    same bytes flow straight through.

Public API (unchanged)
──────────────────────
    render_candlestick_chart(ohlcv, *, width, height, lookback) -> bytes
    chart_fingerprint(png_bytes: bytes) -> str  # sha256[:16]
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OHLCVSeries:
    """Minimal typed bundle of bars for chart rendering."""

    dates: list  # list[datetime.date] or datetime-parseable values
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.dates)
        if not all(
            len(arr) == n for arr in (self.opens, self.highs, self.lows, self.closes, self.volumes)
        ):
            raise ValueError("OHLCVSeries arrays must have equal length")


# ── SVG layout constants ───────────────────────────────────────────
# Kept small so the rendered chart stays compact in the DOM / LLM
# payload. The geometry is deterministic: identical input → identical
# bytes → identical fingerprint, same as the old PNG behaviour.
_PRICE_PAD_PCT = 0.04  # 4% headroom/footroom on the price axis
_BAR_BODY_W = 0.70  # width of each candle body (fraction of slot)


def _candle_colour(close: float, open_: float) -> str:
    return "#26a69a" if close >= open_ else "#ef5350"


def _compute_price_range(h: np.ndarray, l: np.ndarray) -> tuple[float, float]:
    hi = float(np.max(h))
    lo = float(np.min(l))
    span = max(hi - lo, 1e-9)
    pad = span * _PRICE_PAD_PCT
    return lo - pad, hi + pad


def _format_float(v: float) -> str:
    """Deterministic stringification — avoids locale drift."""
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def render_candlestick_chart(
    series: OHLCVSeries,
    *,
    width: float = 960.0,
    height: float = 520.0,
    lookback: int = 50,
    title: str | None = None,
    dpi: int | None = None,  # accepted for API compat — unused in SVG
) -> bytes:
    """Render a candlestick + volume chart as SVG bytes.

    Args:
        series: Typed OHLCV bundle.
        width, height: Output viewport in SVG user units (pixels).
        lookback: Number of most-recent bars to render.
        title: Optional title text rendered at the top-left.
        dpi: Retained for signature compatibility with the old PNG
            renderer; SVG is resolution-independent so it is ignored.

    Returns:
        Raw UTF-8 SVG bytes. Can be embedded inline in HTML,
        base64'd for data-URI use, or hashed for caching.
    """
    del dpi  # unused — SVG is vector

    n = len(series.closes)
    if n == 0:
        raise ValueError("render_candlestick_chart: empty OHLCV series")

    start = max(0, n - lookback)
    o = np.asarray(series.opens[start:], dtype=float)
    h = np.asarray(series.highs[start:], dtype=float)
    l = np.asarray(series.lows[start:], dtype=float)
    c = np.asarray(series.closes[start:], dtype=float)
    v = np.asarray(series.volumes[start:], dtype=float)
    bars = len(c)

    # ── Layout: price pane + volume pane, stacked 4:1 ──────────────
    margin = {"top": 28, "right": 16, "bottom": 22, "left": 44}
    inner_w = max(width - margin["left"] - margin["right"], 100)
    inner_h = max(height - margin["top"] - margin["bottom"], 100)
    price_h = inner_h * 0.80
    vol_h = inner_h * 0.18  # 2% gap between panes

    slot_w = inner_w / max(bars, 1)
    body_w = slot_w * _BAR_BODY_W

    lo_p, hi_p = _compute_price_range(h, l)
    price_span = hi_p - lo_p

    def y_price(v_: float) -> float:
        return margin["top"] + price_h - ((v_ - lo_p) / price_span) * price_h

    vol_max = float(np.max(v)) if bars > 0 else 1.0
    vol_max = max(vol_max, 1.0)

    def y_vol(v_: float) -> float:
        top = margin["top"] + price_h + inner_h * 0.02
        return top + vol_h - (v_ / vol_max) * vol_h

    # ── SMA-20 overlay ────────────────────────────────────────────
    sma_window = min(20, bars)
    sma_pts: list[str] = []
    if sma_window >= 2:
        kernel = np.ones(sma_window) / sma_window
        sma = np.convolve(c, kernel, mode="valid")
        sma_x_start = sma_window - 1
        for i, val in enumerate(sma):
            x = margin["left"] + (sma_x_start + i + 0.5) * slot_w
            y = y_price(float(val))
            sma_pts.append(f"{x:.2f},{y:.2f}")

    # ── Build SVG ──────────────────────────────────────────────────
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" '
        f'font-family="Inter, system-ui, sans-serif" font-size="10">'
    )
    # Background
    parts.append(f'<rect x="0" y="0" width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>')
    # Title
    if title:
        parts.append(
            f'<text x="{margin["left"]}" y="{margin["top"] - 10}" '
            f'font-size="11" font-weight="600" fill="#111827">{_esc(title)}</text>'
        )
    # Price grid (5 horizontal lines)
    for i in range(6):
        frac = i / 5
        y = margin["top"] + price_h - frac * price_h
        value = lo_p + frac * price_span
        parts.append(
            f'<line x1="{margin["left"]}" y1="{y:.2f}" '
            f'x2="{margin["left"] + inner_w:.2f}" y2="{y:.2f}" '
            f'stroke="#e5e7eb" stroke-width="0.75" stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{margin["left"] - 5}" y="{y + 3:.2f}" '
            f'text-anchor="end" fill="#6b7280">{_format_float(value)}</text>'
        )
    # Candlesticks
    for i in range(bars):
        xc = margin["left"] + (i + 0.5) * slot_w
        body_top = y_price(max(o[i], c[i]))
        body_bot = y_price(min(o[i], c[i]))
        wick_top = y_price(h[i])
        wick_bot = y_price(l[i])
        colour = _candle_colour(c[i], o[i])
        # Wick
        parts.append(
            f'<line x1="{xc:.2f}" y1="{wick_top:.2f}" '
            f'x2="{xc:.2f}" y2="{wick_bot:.2f}" '
            f'stroke="#37474f" stroke-width="0.9"/>'
        )
        # Body
        body_height = max(body_bot - body_top, 0.6)
        parts.append(
            f'<rect x="{xc - body_w / 2:.2f}" y="{body_top:.2f}" '
            f'width="{body_w:.2f}" height="{body_height:.2f}" '
            f'fill="{colour}" stroke="#212121" stroke-width="0.4"/>'
        )
    # SMA20 polyline
    if sma_pts:
        parts.append(
            f'<polyline fill="none" stroke="#1976d2" stroke-width="1.2" '
            f'points="{" ".join(sma_pts)}"/>'
        )
    # Volume bars
    vol_baseline = margin["top"] + price_h + inner_h * 0.02 + vol_h
    for i in range(bars):
        xc = margin["left"] + (i + 0.5) * slot_w
        top = y_vol(v[i])
        vh = max(vol_baseline - top, 0.6)
        vcol = _candle_colour(c[i], o[i])
        parts.append(
            f'<rect x="{xc - body_w / 2:.2f}" y="{top:.2f}" '
            f'width="{body_w:.2f}" height="{vh:.2f}" '
            f'fill="{vcol}" fill-opacity="0.55"/>'
        )
    # Axis labels
    parts.append(
        f'<text x="{margin["left"] - 32}" y="{margin["top"] + price_h / 2:.2f}" '
        f'transform="rotate(-90 {margin["left"] - 32},{margin["top"] + price_h / 2:.2f})" '
        f'text-anchor="middle" fill="#6b7280">Price</text>'
    )
    parts.append(
        f'<text x="{margin["left"] - 32}" y="{margin["top"] + price_h + inner_h * 0.02 + vol_h / 2:.2f}" '
        f'transform="rotate(-90 {margin["left"] - 32},{margin["top"] + price_h + inner_h * 0.02 + vol_h / 2:.2f})" '
        f'text-anchor="middle" fill="#6b7280">Vol</text>'
    )
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def _esc(s: str) -> str:
    """Minimal SVG text escaping — just the characters that break XML."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def chart_fingerprint(svg_bytes: bytes) -> str:
    """Short, stable hash of a chart — used as a cache key.

    The name still says ``png`` historically in the callers but the
    function is format-agnostic: we hash whatever bytes are produced,
    so the cache remains correct after the PNG → SVG swap.
    """
    return hashlib.sha256(svg_bytes).hexdigest()[:16]
