"""AYVID banner — Gemini-CLI-inspired splash with live status footer.

The banner is *responsive*: it picks one of four layout tiers based on the
current terminal width so it always looks intentional, whether you're on a
50-column phone-sized SSH window or a 200-column ultrawide.

Layout tiers (current width → tier):

    >= 84 cols    WIDE     full block ASCII logo, 3-col footer
    66 – 83       MEDIUM   full block ASCII logo, 3-col footer (tighter)
    48 – 65       NARROW   half-block "AYVID" logo, 2-row stacked footer
    < 48          TINY     single-line styled "AYVID", single-column stack

The panel always fits inside ``console.width`` with a 1-col breathing margin
on either side so it can never be clipped by the terminal frame.

Two public helpers are exposed:

    print_banner()        – the big splash shown at REPL launch
    print_status_bar()    – the slim 1-line bar reprinted before every prompt
                            so cwd / mode / model / clock stay live.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()


# ──────────────────────────────────────────────────────────────────
# Logos at three sizes.  Each row is paired with one entry from the
# matching gradient so we get a fake top-to-bottom cyan→magenta fade.
# ──────────────────────────────────────────────────────────────────
_LOGO_BIG: list[str] = [
    "  █████╗ ██╗   ██╗██╗   ██╗██╗██████╗ ",
    " ██╔══██╗╚██╗ ██╔╝██║   ██║██║██╔══██╗",
    " ███████║ ╚████╔╝ ██║   ██║██║██║  ██║",
    " ██╔══██║  ╚██╔╝  ╚██╗ ██╔╝██║██║  ██║",
    " ██║  ██║   ██║    ╚████╔╝ ██║██████╔╝",
    " ╚═╝  ╚═╝   ╚═╝     ╚═══╝  ╚═╝╚═════╝ ",
]
_LOGO_BIG_W = max(len(line) for line in _LOGO_BIG)  # ≈ 39

# Extra-large variant for ultrawide terminals — same ANSI Shadow style,
# scaled ~2x horizontally with extra letter spacing so it reads as a
# proper hero banner on 140+ col displays.  10 rows tall, ≈ 84 cols wide.
_LOGO_XL: list[str] = [
    "    █████╗   ██╗   ██╗  ██╗   ██╗  ██╗  ██████╗ ",
    "   ██╔══██╗  ╚██╗ ██╔╝  ██║   ██║  ██║  ██╔══██╗",
    "   ██║  ██║   ╚████╔╝   ██║   ██║  ██║  ██║  ██║",
    "   ███████║    ╚██╔╝    ██║   ██║  ██║  ██║  ██║",
    "   ██╔══██║     ██║     ╚██╗ ██╔╝  ██║  ██║  ██║",
    "   ██║  ██║     ██║      ╚████╔╝   ██║  ██║  ██║",
    "   ██║  ██║     ██║       ╚██╔╝    ██║  ██████╔╝",
    "   ╚═╝  ╚═╝     ╚═╝        ╚═╝     ╚═╝  ╚═════╝ ",
]
_LOGO_XL_W = max(len(line) for line in _LOGO_XL)  # ≈ 57

_LOGO_MID: list[str] = [
    " ▄▀█ █▄█ █░█ █ █▀▄ ",
    " █▀█ ░█░ ▀▄▀ █ █▄▀ ",
]
_LOGO_MID_W = max(len(line) for line in _LOGO_MID)  # ≈ 19

_GRADIENT_BIG = [
    "bold #4ec9ff",
    "bold #5aa9ff",
    "bold #7d8df3",
    "bold #9b7ce8",
    "bold #b86adb",
    "bold #d05bcf",
]
# 8-stop gradient for the taller XL logo so every row gets its own hue.
_GRADIENT_XL = [
    "bold #4ec9ff",
    "bold #5aa9ff",
    "bold #6e9bf7",
    "bold #7d8df3",
    "bold #8c80ed",
    "bold #9b7ce8",
    "bold #b86adb",
    "bold #d05bcf",
]
_GRADIENT_MID = ["bold #4ec9ff", "bold #b86adb"]


def _styled_logo(lines: list[str], gradient: list[str]) -> Text:
    out = Text()
    for line, style in zip(lines, gradient):
        out.append(line, style=style)
        out.append("\n")
    # strip the trailing newline so Padding can manage spacing
    if out.plain.endswith("\n"):
        out = out[:-1]
    return out


# ──────────────────────────────────────────────────────────────────
# Acronym expansion — printed under the logo in every tier so it's
# always clear what AYVID stands for.
# ──────────────────────────────────────────────────────────────────
_ACRONYM_PARTS: list[tuple[str, str]] = [
    ("A", "symmetric"),
    ("Y", "ield"),
    ("V", "ia"),
    ("I", "ntelligent"),
    ("D", "ecisions"),
]
# Re-use logo gradient endpoints so the initials feel "lit" by the logo.
_ACRONYM_GRADIENT = [
    "bold #4ec9ff",
    "bold #7d8df3",
    "bold #9b7ce8",
    "bold #b86adb",
    "bold #d05bcf",
]


def _styled_acronym(*, compact: bool = False) -> Text:
    """Render the AYVID acronym expansion.

    ``compact=False`` → full words      e.g.  ``Asymmetric Yield Via …``
    ``compact=True``  → initials only   e.g.  ``A · Y · V · I · D``
    In both modes the initials use the logo gradient.
    """
    out = Text()
    for i, (initial, rest) in enumerate(_ACRONYM_PARTS):
        if i:
            out.append("  " if compact is False else " · ", style="dim #4a4a5a")
        out.append(initial, style=_ACRONYM_GRADIENT[i])
        if not compact:
            out.append(rest, style="#c8c8d4")
    return out


# ──────────────────────────────────────────────────────────────────
# Tips
# ──────────────────────────────────────────────────────────────────
_TIPS_FULL = [
    ("1. ", "Type any command at the ", "ayvid ›  prompt — e.g. trade paper-status."),
    ("2. ", "Be specific. ", "Name the ticker, scenario id, or date range."),
    ("3. ", "Type ", "help to see the full command tree, or clear to redraw."),
    ("4. ", "Type ", "exit  (or Ctrl+D) to leave the AYVID shell."),
]

_TIPS_SHORT = [
    ("1. ", "Type ", "help for commands."),
    ("2. ", "Try ", "trade paper-status."),
    ("3. ", "Type ", "exit to leave."),
]


def _tips(items: list[tuple[str, str, str]]) -> Text:
    txt = Text()
    txt.append("Tips for getting started:\n", style="bold #c8c8d4")
    for marker, lead, rest in items:
        txt.append(marker, style="dim #8a8aa0")
        txt.append(lead, style="#c8c8d4")
        txt.append(rest, style="dim #8a8aa0")
        txt.append("\n")
    if txt.plain.endswith("\n"):
        txt = txt[:-1]
    return txt


# ──────────────────────────────────────────────────────────────────
# Status rows
# ──────────────────────────────────────────────────────────────────
def _meta_row_full() -> Table:
    """Two-column 'Using N AYVID.md  ·  K commands · M modules' row."""
    ayvid_md = Path.cwd() / "AYVID.md"
    md_label = "1 AYVID.md file" if ayvid_md.exists() else "no AYVID.md file"

    tbl = Table.grid(expand=True, padding=(0, 0))
    tbl.add_column(justify="left", ratio=1, overflow="ellipsis")
    tbl.add_column(justify="right", ratio=1, overflow="ellipsis")
    tbl.add_row(
        Text(f"Using {md_label}", style="dim #6e7080"),
        Text(f"{_command_count()} commands · 4 modules", style="dim #6e7080"),
    )
    return tbl


def _meta_row_compact() -> Text:
    """Single-line stacked variant for narrow terminals."""
    ayvid_md = Path.cwd() / "AYVID.md"
    md_label = "1 AYVID.md" if ayvid_md.exists() else "no AYVID.md"
    txt = Text()
    txt.append(f"{md_label}", style="dim #6e7080")
    txt.append("  ·  ", style="dim #4a4a5a")
    txt.append(f"{_command_count()} cmds", style="dim #6e7080")
    return txt


def _footer_row_full() -> Table:
    """Four-column footer: cwd · mode · model · clock (wide terminals only)."""
    tbl = Table.grid(expand=True, padding=(0, 0))
    tbl.add_column(justify="left", ratio=3, overflow="ellipsis")
    tbl.add_column(justify="center", ratio=2, overflow="ellipsis")
    tbl.add_column(justify="center", ratio=2, overflow="ellipsis")
    tbl.add_column(justify="right", ratio=2, overflow="ellipsis")
    tbl.add_row(
        Text(_short_cwd(), style="#7eb0d0"),
        Text(_mode_string(), style="#c8a06a"),
        Text(_model_string(), style="#a08acf"),
        Text(_clock_string(), style="dim #6e7080"),
    )
    return tbl


def _footer_stacked() -> Text:
    """Four-line stack for very narrow terminals."""
    txt = Text()
    txt.append(f"cwd    {_short_cwd()}\n", style="#7eb0d0")
    txt.append(f"mode   {_mode_string()}\n", style="#c8a06a")
    txt.append(f"model  {_model_string()}\n", style="#a08acf")
    txt.append(f"time   {_clock_string()}", style="dim #6e7080")
    return txt


def _footer_two_line() -> Text:
    """cwd on top, 'mode · model · clock' on bottom — fits 48-65 col terminals."""
    txt = Text()
    txt.append(f"{_short_cwd()}\n", style="#7eb0d0")
    txt.append(_mode_string(), style="#c8a06a")
    txt.append("  ·  ", style="dim #4a4a5a")
    txt.append(_model_string(), style="#a08acf")
    txt.append("  ·  ", style="dim #4a4a5a")
    txt.append(_clock_string(), style="dim #6e7080")
    return txt


# ──────────────────────────────────────────────────────────────────
# Dynamic data helpers
# ──────────────────────────────────────────────────────────────────
def _short_cwd() -> str:
    cwd = Path.cwd()
    home = Path.home()
    try:
        rel = cwd.relative_to(home)
        return f"~/{rel}" if str(rel) != "." else "~"
    except ValueError:
        return str(cwd)


def _mode_string() -> str:
    try:
        from shared.config import settings

        prefix = "paper" if settings.PAPER_TRADING else "live"
        return f"{prefix} · IB:{settings.IB_PORT}"
    except Exception:
        return "paper · IB:7497"


def _model_string() -> str:
    try:
        from shared.config import settings

        if str(settings.LLM_PROVIDER).lower().startswith("anthropic"):
            return settings.ANTHROPIC_MODEL
        return settings.OPENAI_MODEL
    except Exception:
        return os.environ.get("AYVID_MODEL", "gpt-4o")


def _clock_string() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def _command_count() -> int:
    try:
        from cli.app import app

        total = 0
        for grp in app.registered_groups:
            sub = grp.typer_instance
            if sub is not None:
                total += len(getattr(sub, "registered_commands", []))
        total += len(getattr(app, "registered_commands", []))
        return total
    except Exception:
        return 50


# ──────────────────────────────────────────────────────────────────
# Layout selection
# ──────────────────────────────────────────────────────────────────
def _pick_layout(term_w: int) -> dict:
    """Return a dict describing which renderables to stitch together for
    the given terminal width.  We size the panel so it always stays inside
    the terminal with at least a 1-col gutter on each side.

    Tiers:
        TINY   < 48   single-line text logo, stacked footer
        NARROW 48-65  small block logo, stacked footer
        MEDIUM 66-83  big logo, two-line footer
        WIDE   >= 84  big logo, three-column footer
    """
    # Always leave a 1-col gutter on each side so we can never spill over.
    safe_w = max(term_w - 2, 24)
    if term_w >= 140:
        # ULTRAWIDE — hero-sized logo, generous panel that scales with the
        # terminal up to 180 cols so the splash actually feels like a banner.
        return {
            "tier": "ultrawide",
            "panel_w": min(safe_w, 180),
            "logo": _styled_logo(_LOGO_XL, _GRADIENT_XL),
            "acronym": _styled_acronym(compact=False),
            "tips": _tips(_TIPS_FULL),
            "meta": _meta_row_full(),
            "footer": _footer_row_full(),
        }
    if term_w >= 84:
        # WIDE — keep the original block logo but let the panel grow with
        # the terminal up to 130 cols (was 96, which felt cramped).
        return {
            "tier": "wide",
            "panel_w": min(safe_w, 130),
            "logo": _styled_logo(_LOGO_BIG, _GRADIENT_BIG),
            "acronym": _styled_acronym(compact=False),
            "tips": _tips(_TIPS_FULL),
            "meta": _meta_row_full(),
            "footer": _footer_row_full(),
        }
    if term_w >= 66:
        return {
            "tier": "medium",
            "panel_w": safe_w,
            "logo": _styled_logo(_LOGO_BIG, _GRADIENT_BIG),
            "acronym": _styled_acronym(compact=False),
            "tips": _tips(_TIPS_FULL),
            "meta": _meta_row_full(),
            "footer": _footer_two_line(),
        }
    if term_w >= 48:
        return {
            "tier": "narrow",
            "panel_w": safe_w,
            "logo": _styled_logo(_LOGO_MID, _GRADIENT_MID),
            "acronym": _styled_acronym(compact=False),
            "tips": _tips(_TIPS_SHORT),
            "meta": _meta_row_compact(),
            "footer": _footer_two_line(),
        }
    # TINY
    tiny_logo = Text("AYVID", style="bold #b86adb")
    return {
        "tier": "tiny",
        "panel_w": safe_w,
        "logo": tiny_logo,
        # Compact "A · Y · V · I · D" so we don't wrap on a 40-col terminal.
        "acronym": _styled_acronym(compact=True),
        "tips": _tips(_TIPS_SHORT[:2]),
        "meta": _meta_row_compact(),
        "footer": _footer_stacked(),
    }


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────
def _build_panel(layout: dict) -> Panel:
    big_tier = layout["tier"] in {"ultrawide", "wide", "medium"}
    left_pad = 3 if layout["tier"] == "ultrawide" else (2 if big_tier else 1)

    body_parts: list[RenderableType] = [
        Padding(Align.left(layout["logo"]), (0, 0, 0, left_pad)),
        Padding(Align.left(layout["acronym"]), (0, 0, 1, left_pad)),
        Padding(layout["tips"], (0, 0, 1, left_pad)),
        Padding(layout["meta"], (0, left_pad)),
        Padding(Rule(style="#2a2a3a"), (0, left_pad)),
        Padding(layout["footer"], (0, left_pad)),
    ]

    panel_padding = (1, 3) if layout["tier"] == "ultrawide" else (
        (1, 2) if big_tier else (1, 1)
    )
    # NOTE: ``expand=False`` makes Rich's Panel SILENTLY IGNORE the
    # ``width=`` argument and shrink-wrap to content — that's how the
    # banner ended up at ~80 cols on a 190-col terminal.  Leaving expand
    # at its default (True) lets ``width=panel_w`` actually take effect.
    return Panel(
        Group(*body_parts),
        border_style="#3a3f55",
        padding=panel_padding,
        width=layout["panel_w"],
    )


def print_banner() -> None:
    """Splash banner — printed once when the AYVID shell starts."""
    layout = _pick_layout(console.width)

    console.print()
    console.print(_build_panel(layout))
    console.print()

    if console.width >= 70:
        console.print(
            "  [dim]Type[/dim] [#4ec9ff]help[/#4ec9ff] [dim]for the full command tree, "
            "or[/dim] [#4ec9ff]dashboard[/#4ec9ff] [dim]for the live monitor.[/dim]\n"
        )
    else:
        console.print("  [dim]Type[/dim] [#4ec9ff]help[/#4ec9ff] [dim]for commands.[/dim]")
        console.print(
            "  [dim]Or[/dim] [#4ec9ff]dashboard[/#4ec9ff] [dim]for the live monitor.[/dim]\n"
        )


def print_status_bar() -> None:
    """One-line live status reprinted before every REPL prompt.

    Compact, dynamic, and always re-evaluated — so cwd / mode / model /
    clock reflect the *current* state without re-rendering the splash.
    """
    parts = Text()
    parts.append(" ⏵ ", style="bold #4ec9ff")
    parts.append(_short_cwd(), style="#7eb0d0")
    parts.append("  ·  ", style="dim #4a4a5a")
    parts.append(_mode_string(), style="#c8a06a")
    parts.append("  ·  ", style="dim #4a4a5a")
    parts.append(_model_string(), style="#a08acf")
    parts.append("  ·  ", style="dim #4a4a5a")
    parts.append(_clock_string(), style="dim #6e7080")
    console.print(parts)
