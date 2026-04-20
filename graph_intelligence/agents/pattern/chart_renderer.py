"""Deterministic candlestick chart renderer for the PatternAgent.

Matplotlib-based OHLCV → PNG renderer. Design goals:
  • Zero external chart-library dependencies (mpl-finance unnecessary)
  • Deterministic output — same input ≡ same bytes, so unit tests can
    fingerprint the PNG and the production cache keys on the hash
  • Non-interactive backend — safe in subprocesses, async contexts,
    and CI runners with no display

Public API
──────────
    render_candlestick_chart(ohlcv: OHLCVSeries, *, width=10, height=5,
                              lookback=50) -> bytes  (PNG bytes)
    chart_fingerprint(png_bytes: bytes) -> str       (sha256[:16])
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, crucial for async/tests
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle


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
            len(arr) == n
            for arr in (self.opens, self.highs, self.lows, self.closes, self.volumes)
        ):
            raise ValueError("OHLCVSeries arrays must have equal length")


def _draw_candlestick(ax, xs: np.ndarray, o, h, l, c) -> None:
    """Draw one OHLC bar per x index."""
    for i in range(len(xs)):
        body_bottom = min(o[i], c[i])
        body_top = max(o[i], c[i])
        body_height = max(body_top - body_bottom, 1e-9)
        body_color = "#26a69a" if c[i] >= o[i] else "#ef5350"

        # wick
        ax.vlines(xs[i], l[i], h[i], color="#37474f", linewidth=0.8, zorder=2)
        # body
        ax.add_patch(Rectangle(
            (xs[i] - 0.35, body_bottom),
            0.70,
            body_height,
            facecolor=body_color,
            edgecolor="#212121",
            linewidth=0.4,
            zorder=3,
        ))


def render_candlestick_chart(
    series: OHLCVSeries,
    *,
    width: float = 10.0,
    height: float = 5.5,
    lookback: int = 50,
    title: str | None = None,
    dpi: int = 110,
) -> bytes:
    """Render a candlestick chart to a PNG byte buffer.

    The figure is plotted with:
      • Last ``lookback`` bars
      • Candlesticks + volume sub-plot
      • Simple 20-bar SMA overlay (no fancy indicators — the vision
        model is supposed to read the price geometry itself)

    Returns raw PNG bytes ready for base64 encoding into an LLM
    message payload.
    """
    n = len(series.closes)
    if n == 0:
        raise ValueError("render_candlestick_chart: empty OHLCV series")

    start = max(0, n - lookback)
    o = series.opens[start:]
    h = series.highs[start:]
    l = series.lows[start:]
    c = series.closes[start:]
    v = series.volumes[start:]
    xs = np.arange(len(c))

    # 20-bar SMA as a faint reference line.
    sma_window = min(20, len(c))
    if sma_window >= 2:
        sma = np.convolve(c, np.ones(sma_window) / sma_window, mode="valid")
        sma_xs = xs[sma_window - 1:]
    else:
        sma = None
        sma_xs = None

    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, sharex=True,
        figsize=(width, height),
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.05},
        dpi=dpi,
    )

    _draw_candlestick(ax_price, xs, o, h, l, c)
    if sma is not None:
        ax_price.plot(sma_xs, sma, color="#1976d2", linewidth=1.2, label="SMA20",
                      zorder=4)
        ax_price.legend(loc="upper left", fontsize=8, frameon=False)

    ax_price.set_ylabel("Price", fontsize=9)
    ax_price.set_title(title or "Price action", fontsize=10)
    ax_price.grid(True, alpha=0.25, linestyle=":")
    ax_price.set_xlim(-1, len(xs))

    # volume bars coloured up/down by close vs open
    vol_colors = ["#26a69a" if c[i] >= o[i] else "#ef5350" for i in range(len(c))]
    ax_vol.bar(xs, v, width=0.7, color=vol_colors, alpha=0.6)
    ax_vol.set_ylabel("Volume", fontsize=9)
    ax_vol.grid(True, alpha=0.20, linestyle=":")
    ax_vol.set_xlabel("Bar index", fontsize=9)

    for ax in (ax_price, ax_vol):
        ax.tick_params(axis="both", labelsize=8)
        for spine in ax.spines.values():
            spine.set_alpha(0.5)

    # Deterministic output: no timestamp metadata in the PNG.
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", metadata={"CreationTime": None})
    plt.close(fig)
    return buf.getvalue()


def chart_fingerprint(png_bytes: bytes) -> str:
    """Short, stable hash of a chart PNG — used as a cache key."""
    return hashlib.sha256(png_bytes).hexdigest()[:16]
