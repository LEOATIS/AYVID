"""Structured logging for AYVID with Rich console output."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from shared.config import settings

_AYVID_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "critical": "bold white on red",
        "signal.long": "bold green",
        "signal.short": "bold red",
        "signal.hold": "dim",
        "graph": "magenta",
        "execution": "blue",
        "risk": "yellow",
    }
)

console = Console(theme=_AYVID_THEME, stderr=True)

_LOG_FORMAT = "%(message)s"

_handler = RichHandler(
    console=console,
    show_time=True,
    show_path=False,
    markup=True,
    rich_tracebacks=True,
    tracebacks_show_locals=False,
)
_handler.setFormatter(logging.Formatter(_LOG_FORMAT))


def get_logger(name: str) -> logging.Logger:
    """Return a named logger with Rich formatting attached."""
    logger = logging.getLogger(f"ayvid.{name}")
    if not logger.handlers:
        logger.addHandler(_handler)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
        logger.propagate = False
    return logger


log = get_logger("core")
