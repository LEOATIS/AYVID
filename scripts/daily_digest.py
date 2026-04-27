"""Daily digest generator for the live paper-trading window.

Reads every live/*.jsonl artifact produced by the paper runner + backend,
slices it for today (UTC), and writes ``live/daily/YYYY-MM-DD.md`` — a
one-pager you can skim every morning during the 10-day window and that
aggregates into the final YC artifact at the end of the run.

Run manually::

    python scripts/daily_digest.py
    python scripts/daily_digest.py --date 2026-04-21
    python scripts/daily_digest.py --all     # regenerate all past days

Or schedule it with launchd to run at 23:55 PT every day (see
``scripts/ayvid_daily_digest.plist``).

Exit codes::
    0  digest written OK
    1  no runner data yet
    2  date requested has no data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = REPO_ROOT / "live"
DAILY_DIR = LIVE_DIR / "daily"
EQUITY_CURVE = LIVE_DIR / "equity_curve.jsonl"
TRADES = LIVE_DIR / "trades.jsonl"
POSITIONS = LIVE_DIR / "positions.json"
RUN_META = LIVE_DIR / "run_meta.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _iso_to_date(ts: str) -> dt.date:
    """Parse an ISO timestamp to a UTC date, tolerant of trailing Z."""
    ts = ts.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(ts).astimezone(dt.UTC).date()
    except Exception:
        # Fall back to naive parse if fromisoformat chokes on legacy rows
        return dt.datetime.strptime(ts[:10], "%Y-%m-%d").date()


def _filter_by_date(rows: list[dict[str, Any]], day: dt.date) -> list[dict[str, Any]]:
    """Keep only rows whose timestamp field is on ``day`` (UTC)."""
    return [r for r in rows if _iso_to_date(r.get("timestamp", "")) == day]


def _nearest_before(
    rows: list[dict[str, Any]],
    day: dt.date,
) -> dict[str, Any] | None:
    """Return the latest row dated < ``day``, for computing intraday delta."""
    past = [r for r in rows if _iso_to_date(r.get("timestamp", "")) < day]
    return past[-1] if past else None


def _fmt_money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _fmt_pct(x: float, digits: int = 2) -> str:
    return f"{x:+.{digits}f}%"


def _render_curve_png(day: dt.date) -> Path | None:
    """Render cumulative equity curve up to ``day`` as a PNG for the digest."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    rows = _load_jsonl(EQUITY_CURVE)
    rows = [r for r in rows if _iso_to_date(r.get("timestamp", "")) <= day]
    if len(rows) < 2:
        return None

    ts = [dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) for r in rows]
    eq = [float(r["equity"]) for r in rows]
    initial = eq[0]
    final = eq[-1]
    total = (final / initial - 1) * 100 if initial else 0.0
    max_eq = max(eq)
    dd = (final / max_eq - 1) * 100 if max_eq else 0.0

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(ts, eq, linewidth=1.6, color="#0a84ff")
    ax.fill_between(ts, eq, min(eq), alpha=0.10, color="#0a84ff")
    ax.axhline(initial, color="#888", linewidth=0.6, linestyle="--", alpha=0.5)
    ax.set_title(
        f"AYVID Live Paper Equity — {ts[0]:%Y-%m-%d} → {ts[-1]:%Y-%m-%d}",
        fontsize=11,
        weight="bold",
    )
    ax.set_ylabel("Net Liquidation ($)")
    ax.grid(True, alpha=0.25)
    span = ts[-1] - ts[0]
    if span.total_seconds() < 86400:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, span.days // 8)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=20, ha="right")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    stats_color = "#16a34a" if total >= 0 else "#dc2626"
    stats = (
        f"Initial: ${initial:,.0f}\n"
        f"Final:   ${final:,.0f}\n"
        f"Return:  {total:+.2f}%\n"
        f"DD:      {dd:.2f}%"
    )
    ax.text(
        0.99,
        0.02,
        stats,
        transform=ax.transAxes,
        fontsize=9,
        family="monospace",
        va="bottom",
        ha="right",
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor="#f7f7f7",
            edgecolor=stats_color,
            linewidth=1.0,
            alpha=0.9,
        ),
    )

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    out = DAILY_DIR / f"{day.isoformat()}_curve.png"
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out


def _summarize_day(day: dt.date) -> dict[str, Any]:
    curve_rows = _load_jsonl(EQUITY_CURVE)
    trade_rows = _load_jsonl(TRADES)

    today_curve = _filter_by_date(curve_rows, day)
    today_trades = _filter_by_date(trade_rows, day)
    prev_close_row = _nearest_before(curve_rows, day)

    opening_eq = float(today_curve[0]["equity"]) if today_curve else None
    closing_eq = float(today_curve[-1]["equity"]) if today_curve else None
    prev_eq = float(prev_close_row["equity"]) if prev_close_row else None

    daily_pnl = (
        (closing_eq - (prev_eq or opening_eq or closing_eq or 0))
        if closing_eq is not None
        else 0.0
    )
    daily_pct = (
        ((closing_eq / (prev_eq or opening_eq or closing_eq)) - 1) * 100 if closing_eq else 0.0
    )

    first_row = curve_rows[0] if curve_rows else None
    run_start_eq = float(first_row["equity"]) if first_row else None
    cum_pnl = (
        (closing_eq - run_start_eq)
        if (closing_eq is not None and run_start_eq is not None)
        else 0.0
    )
    cum_pct = ((closing_eq / run_start_eq) - 1) * 100 if (closing_eq and run_start_eq) else 0.0

    # Mode distribution — did we run ib_paper today, or shadow?
    modes: dict[str, int] = defaultdict(int)
    for r in today_curve:
        modes[r.get("mode", "?")] += 1

    # Trade breakdown
    buys = [t for t in today_trades if (t.get("side") or "").upper() in ("BUY", "LONG")]
    sells = [t for t in today_trades if (t.get("side") or "").upper() in ("SELL", "SHORT")]
    tickers = sorted({t.get("ticker", "") for t in today_trades})

    # Cycle count (if we embed cycle in the equity row)
    cycles = max((int(r.get("cycle", 0)) for r in today_curve), default=0)

    return {
        "day": day.isoformat(),
        "opening_equity": opening_eq,
        "closing_equity": closing_eq,
        "prev_close_equity": prev_eq,
        "daily_pnl": daily_pnl,
        "daily_return_pct": daily_pct,
        "run_start_equity": run_start_eq,
        "cumulative_pnl": cum_pnl,
        "cumulative_return_pct": cum_pct,
        "cycles": cycles,
        "modes": dict(modes),
        "trades_today": len(today_trades),
        "buys_today": len(buys),
        "sells_today": len(sells),
        "tickers_today": tickers,
        "snapshots_today": len(today_curve),
        "has_data": bool(today_curve),
    }


def _render_markdown(day: dt.date, summary: dict[str, Any], curve_png: Path | None) -> str:
    lines: list[str] = []
    lines.append(f"# AYVID Live Paper — Daily Digest · {day.isoformat()}")
    lines.append("")
    lines.append(f"*Generated {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M UTC}*")
    lines.append("")

    if not summary["has_data"]:
        lines.append("> No equity snapshots recorded for this date.")
        lines.append("> Either the runner was offline, or the market was closed and")
        lines.append("> the runner correctly skipped polling.")
        lines.append("")
        return "\n".join(lines)

    modes = ", ".join(f"{m}×{c}" for m, c in summary["modes"].items()) or "—"

    lines.append("## Headline")
    lines.append("")
    lines.append(
        f"- **Closing NLV:** {_fmt_money(summary['closing_equity'])}  "
        f"({_fmt_pct(summary['daily_return_pct'])} day · "
        f"{_fmt_pct(summary['cumulative_return_pct'])} since start)"
    )
    lines.append(f"- **Daily P&L:** {_fmt_money(summary['daily_pnl'])}")
    lines.append(
        f"- **Cumulative P&L:** {_fmt_money(summary['cumulative_pnl'])} on "
        f"{_fmt_money(summary['run_start_equity'])} initial"
    )
    lines.append(
        f"- **Activity:** {summary['trades_today']} fills today "
        f"({summary['buys_today']} buy / {summary['sells_today']} sell), "
        f"{summary['cycles']} runner cycles, {summary['snapshots_today']} snapshots ({modes})"
    )
    lines.append("")

    if curve_png:
        rel = curve_png.relative_to(REPO_ROOT)
        lines.append("## Equity Curve")
        lines.append("")
        lines.append(f"![Equity curve through {day}](/{rel.as_posix()})")
        lines.append("")

    if summary["trades_today"]:
        lines.append("## Fills")
        lines.append("")
        lines.append("| Time (UTC) | Ticker | Side | Shares | Fill | Rationale |")
        lines.append("|---|---|---|---:|---:|---|")
        trade_rows = _load_jsonl(TRADES)
        for t in _filter_by_date(trade_rows, day):
            ts = t.get("timestamp", "")[:19].replace("T", " ")
            tick = t.get("ticker", "?")
            side = t.get("side", "?")
            qty = t.get("shares") or t.get("quantity") or "?"
            px = t.get("fill_price") or t.get("limit_price") or t.get("price") or "?"
            rationale = (t.get("rationale") or "").replace("|", "/").strip()
            if len(rationale) > 72:
                rationale = rationale[:72] + "…"
            px_str = f"${px:,.2f}" if isinstance(px, (int, float)) else str(px)
            lines.append(f"| {ts} | `{tick}` | {side} | {qty} | {px_str} | {rationale} |")
        lines.append("")

    # Current positions snapshot (not date-filtered — shows end-of-day state)
    if POSITIONS.exists():
        try:
            pos = json.loads(POSITIONS.read_text())
            positions = pos.get("positions", {}) or {}
            if positions:
                lines.append("## Open Positions (end of day)")
                lines.append("")
                lines.append(
                    f"Cash: {_fmt_money(float(pos.get('cash', 0)))}  ·  "
                    f"As of: {pos.get('as_of', '?')}"
                )
                lines.append("")
                lines.append("| Ticker | Shares | Entry | Entered |")
                lines.append("|---|---:|---:|---|")
                for tick, p in positions.items():
                    lines.append(
                        f"| `{tick}` | {p.get('shares', '?')} | "
                        f"${float(p.get('entry_price', 0)):,.2f} | "
                        f"{p.get('entered_at', '?')[:19]} |"
                    )
                lines.append("")
        except Exception:
            pass

    # Health footer
    if RUN_META.exists():
        try:
            meta = json.loads(RUN_META.read_text())
            lines.append("## Run Meta")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(meta, indent=2))
            lines.append("```")
            lines.append("")
        except Exception:
            pass

    return "\n".join(lines)


def generate_for(day: dt.date) -> Path | None:
    summary = _summarize_day(day)
    curve_png = _render_curve_png(day) if summary["has_data"] else None
    md = _render_markdown(day, summary, curve_png)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    out = DAILY_DIR / f"{day.isoformat()}.md"
    out.write_text(md)
    if summary["has_data"]:
        # Append one-liner to run_log.txt for at-a-glance history.
        log_line = (
            f"{day.isoformat()}  "
            f"NLV=${summary['closing_equity']:,.0f}  "
            f"day={summary['daily_return_pct']:+.2f}%  "
            f"cum={summary['cumulative_return_pct']:+.2f}%  "
            f"fills={summary['trades_today']}  "
            f"cycles={summary['cycles']}\n"
        )
        (LIVE_DIR / "run_log.txt").open("a").write(log_line)
    return out


def _distinct_days_in_curve() -> list[dt.date]:
    rows = _load_jsonl(EQUITY_CURVE)
    days = sorted({_iso_to_date(r.get("timestamp", "")) for r in rows if r.get("timestamp")})
    return days


def main() -> int:
    p = argparse.ArgumentParser(description="AYVID daily digest generator")
    p.add_argument("--date", help="UTC date YYYY-MM-DD (default: today)")
    p.add_argument("--all", action="store_true", help="Regenerate every day with data")
    args = p.parse_args()

    if not EQUITY_CURVE.exists() or EQUITY_CURVE.stat().st_size == 0:
        print("no live/equity_curve.jsonl yet — start the paper runner first", file=sys.stderr)
        return 1

    if args.all:
        days = _distinct_days_in_curve()
        if not days:
            print("no days with data", file=sys.stderr)
            return 2
        for d in days:
            out = generate_for(d)
            print(f"  wrote {out.relative_to(REPO_ROOT)}")
        return 0

    target = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.UTC).date()
    out = generate_for(target)
    if out is None:
        return 2
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
