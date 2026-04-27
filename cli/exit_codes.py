"""Canonical CLI exit codes — operator-facing, CI-parseable.

Rationale: every long-lived CLI eventually grows piecemeal exit codes
scattered across commands (``raise typer.Exit(1)`` here, ``sys.exit(2)``
there, silent successes on failure paths elsewhere). That makes shell
pipelines and CI scripts brittle. This module is the one place that
enumerates every code AYVID can exit with, and the surrounding CLI
commands import from it so any two uses of "connection refused" produce
the same integer.

Convention mirrors POSIX / sysexits.h loosely — zero is success, 1 is
generic failure, 2 is usage error, 3-9 are transport / auth / local
environment, 10+ are domain-specific (data-plane desync, backtest
validation failure, …). 130 is reserved for SIGINT.
"""

from __future__ import annotations

# ── Successful execution ─────────────────────────────────────────────
EXIT_OK: int = 0

# ── Generic / usage ──────────────────────────────────────────────────
EXIT_GENERIC_ERROR: int = 1
EXIT_USAGE_ERROR: int = 2

# ── Connectivity / transport ────────────────────────────────────────
EXIT_BACKEND_UNREACHABLE: int = 3
EXIT_BACKEND_AUTH_FAILED: int = 4
EXIT_BACKEND_TIMEOUT: int = 5

# ── Domain-specific (AYVID) ─────────────────────────────────────────
EXIT_DATA_PLANE_DESYNC: int = 10  # validate_graph_freshness / seq-sync fail
EXIT_BACKTEST_VALIDATION_FAIL: int = 11
EXIT_IB_GATEWAY_DOWN: int = 12

# ── Signal-driven ───────────────────────────────────────────────────
# 128 + SIGINT(2). This is what bash reports by convention; we mirror
# it so shell pipelines see the same code whether the process was
# ^C-ed or exited via our handler.
EXIT_KEYBOARD_INTERRUPT: int = 130
