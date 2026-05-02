"""Pure quantitative primitives — leaf math, no orchestration deps.

This package owns:
  - ``_primitives``      : low-level math helpers (ATR, RSI, etc.)
  - ``regime``           : trend / volatility regime classifier
  - ``signals``          : composite scoring + Bayesian update
  - ``options_pricing``  : Black-Scholes, IV solver, multi-leg helpers
  - ``alpha_scorer``     : unified AlphaScore producer
  - ``seasonal_calendar``: calendar-effect schedule

These modules MUST NOT import from ``execution_engine``,
``graph_intelligence``, ``backtesting``, or ``backend``. They are the
pure-math leaf the rest of the stack depends on. Backwards-compat
shims under ``execution_engine.math.*`` keep legacy import sites
working — see ``execution_engine/math/__init__.py``.
"""
