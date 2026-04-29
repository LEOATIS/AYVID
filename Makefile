.PHONY: install dev verify status dashboard serve seed watchdog clean demo test backtest backtest-fresh pipeline graph-init scan analyze ingest universe world-scan clean-data lint format sync

# ── Setup ────────────────────────────────────────────────────

install:
	pip install -e ".[dev]" 2>/dev/null || pip install -e .

dev:
	pip install -e .
	@echo "\nAYVID installed. Run 'python -m cli.app --help' to get started.\n"

# ── Backtesting (primary workflow) ──────────────────────────

backtest:
	python -m cli.app data backtest --days 60

backtest-fresh:
	python -m cli.app data backtest --days 60 --fresh

# ── CLI Shortcuts ────────────────────────────────────────────

status:
	python -m cli.app system status

verify:
	python -m cli.app data verify

dashboard:
	python -m cli.app dashboard

serve:
	python -m cli.app system serve

pipeline:
	python -m cli.app data pipeline

# ── Graph ────────────────────────────────────────────────────

graph-init:
	python -m cli.app graph init

seed:
	python -m cli.app graph seed

scan:
	@read -p "Shock ticker: " ticker; \
	python -m cli.app graph scan $$ticker

analyze:
	@read -p "Shock ticker: " ticker; \
	python -m cli.app graph analyze $$ticker

# ── Data ─────────────────────────────────────────────────────

ingest:
	python -m cli.app data ingest AAPL TSLA NVDA MSFT TSM

universe:
	python -m cli.app data universe

world-scan:
	python -m cli.app data world-scan

# ── Kill Switch ──────────────────────────────────────────────

# Compiles the production kill switch with libcurl so the independent
# IB Client Portal REST flatten path is available (step-2 of the
# watchdog contract: Python SIGKILL AND broker-side flatten).
#
# Falls back gracefully at compile time if libcurl is absent (step-2
# is skipped at runtime; step-1 still fires).
watchdog:
	@if command -v curl-config >/dev/null 2>&1; then \
		CFLAGS=$$(curl-config --cflags); \
		LIBS=$$(curl-config --libs); \
	else \
		CFLAGS=""; LIBS="-lcurl"; \
	fi; \
	cd execution_engine/watchdog && \
	g++ -O2 -std=c++17 $$CFLAGS -o kill_switch kill_switch.cpp $$LIBS -lpthread && \
	echo "[OK] Production watchdog compiled: execution_engine/watchdog/kill_switch (libcurl=$$LIBS)"

# P2-22: the `watchdog-legacy` target pointed at
# `execution_engine/kill_switch/` which was deleted during the Phase-2
# consolidation. Leaving a stale make target around tricks operators
# into thinking there's a fallback path; removing it keeps the
# contract honest — there is one canonical kill_switch and it lives
# under `execution_engine/watchdog/`.

# ── GCS Sync ────────────────────────────────────────────────

sync:
	AYVID_LIVE_GCS_BUCKET=$${AYVID_LIVE_GCS_BUCKET:-ayvid-production-tfstate} \
	AYVID_LIVE_GCS_PREFIX=$${AYVID_LIVE_GCS_PREFIX:-live_state} \
	python -m scripts.gcs_sync

sync-watch:
	AYVID_LIVE_GCS_BUCKET=$${AYVID_LIVE_GCS_BUCKET:-ayvid-production-tfstate} \
	AYVID_LIVE_GCS_PREFIX=$${AYVID_LIVE_GCS_PREFIX:-live_state} \
	python -m scripts.gcs_sync --watch --interval 60

# ── Demo & Test ──────────────────────────────────────────────

demo:
	python -m scripts.demo

test:
	python -m pytest tests/ -v

# ── Maintenance ──────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache/ .pytest_cache/

clean-data:
	rm -rf data_store/

lint:
	ruff check .

format:
	ruff format .
