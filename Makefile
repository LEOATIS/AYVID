.PHONY: install dev verify status dashboard serve seed watchdog clean demo test backtest backtest-fresh pipeline

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

watchdog:
	cd execution_engine/kill_switch && \
	g++ -O2 -o watchdog watchdog.cpp && \
	echo "Watchdog compiled: execution_engine/kill_switch/watchdog"

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
