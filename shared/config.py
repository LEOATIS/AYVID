from __future__ import annotations

from enum import Enum

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, Enum):
    """Where orders go when ``AUTO_EXECUTE`` is True.

    SHADOW   — no broker. Fills simulated locally; safe for any environment.
    IB_PAPER — IB paper account (DU* / DUQ*). Real-market sim, no real money.
    IB_LIVE  — real IB account. Requires ``LIVE_TRADING_CONFIRMED=True``.
    """

    SHADOW = "shadow"
    IB_PAPER = "ib_paper"
    IB_LIVE = "ib_live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM Providers ---
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o"
    # Cheap/fast model used for high-volume per-NewsItem sentiment scoring.
    # Overridable via env; kept separate from OPENAI_MODEL so the heavier
    # model can still be used for causal reasoning and daily compression.
    OPENAI_SENTIMENT_MODEL: str = "gpt-4o-mini"
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    LLM_PROVIDER: str = "openai"

    # --- OpenBB Data ---
    OPENBB_FMP_API_KEY: str | None = None
    OPENBB_FRED_API_KEY: str | None = None
    OPENBB_PAT: str | None = None
    OPENBB_BENZINGA_API_KEY: str | None = None
    OPENBB_TIINGO_API_KEY: str | None = None
    OPENBB_INTRINIO_API_KEY: str | None = None

    # --- Finnhub ---
    FINNHUB_API_KEY: str | None = None

    # --- MCP connectors (anthropic/financial-services integration) -----
    # Each URL is opt-in: when None (default), the matching wrapper in
    # data_primitives/mcp/ returns None/[] and AYVID falls back to its
    # existing extractor paths (news_scraper, openbb_client, etc.).
    # Set the URL + API key when you have a vendor subscription; the
    # continuous worker will then prefer the vendor feed.
    # Endpoints mirror anthropic/financial-services/.../financial-analysis/.mcp.json.
    MCP_MT_NEWSWIRES_URL: str | None = None
    MCP_MT_NEWSWIRES_API_KEY: str | None = None
    MCP_AIERA_URL: str | None = None
    MCP_AIERA_API_KEY: str | None = None
    MCP_FACTSET_URL: str | None = None
    MCP_FACTSET_API_KEY: str | None = None
    MCP_MOODYS_URL: str | None = None
    MCP_MOODYS_API_KEY: str | None = None
    MCP_SP_GLOBAL_URL: str | None = None
    MCP_SP_GLOBAL_API_KEY: str | None = None

    # --- SEC EDGAR ---
    SEC_EDGAR_EMAIL: str = "research@ayvid.ai"

    # --- Neo4j (supports both NEO4J_USER and NEO4J_USERNAME) ---
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str | None = None
    NEO4J_USERNAME: str | None = None
    NEO4J_PASSWORD: str | None = None
    NEO4J_DATABASE: str = "neo4j"

    @model_validator(mode="after")
    def _resolve_neo4j_user(self) -> Settings:
        if not self.NEO4J_USER and self.NEO4J_USERNAME:
            self.NEO4J_USER = self.NEO4J_USERNAME
        elif not self.NEO4J_USER:
            self.NEO4J_USER = "neo4j"
        return self

    @model_validator(mode="after")
    def _validate_live_routing(self) -> Settings:
        # Safety: if we're about to route orders to IB, the account must be a
        # paper account (DU* / DUQ*) unless LIVE_TRADING is explicitly enabled.
        # This prevents a silent foot-gun where someone flips PAPER_TRADING=false
        # with a live IB_ACCOUNT and AUTO_EXECUTE starts blasting real money.
        if not self.PAPER_TRADING and self.AUTO_EXECUTE and self.IB_ACCOUNT:
            acct = self.IB_ACCOUNT.strip().upper()
            is_paper = acct.startswith("DU") or acct.startswith("DUQ")
            if not is_paper and not self.LIVE_TRADING_CONFIRMED:
                raise ValueError(
                    f"Refusing to start: AUTO_EXECUTE=true + PAPER_TRADING=false "
                    f"+ IB_ACCOUNT={self.IB_ACCOUNT!r} is NOT a paper account "
                    f"(no DU/DUQ prefix). Set LIVE_TRADING_CONFIRMED=true in .env "
                    f"to explicitly opt-in to real-money trading."
                )
        return self

    # --- Qdrant ---
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "ayvid_events"

    # --- Embeddings ---
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # --- QuestDB ---
    QUESTDB_HOST: str = "localhost"
    QUESTDB_PORT: int = 9009
    QUESTDB_HTTP_PORT: int = 9000

    # --- Hopsworks ---
    HOPSWORKS_API_KEY: str | None = None
    HOPSWORKS_PROJECT: str = "ayvid"

    # --- Interactive Brokers ---
    IB_HOST: str = "127.0.0.1"
    IB_PORT: int = 7497
    IB_CLIENT_ID: int = 1
    IB_ACCOUNT: str | None = None
    # Set true ONLY when monitoring a non-primary sub-account. The primary
    # account is auto-subscribed on connect; calling reqAccountUpdates again
    # for the same account hangs forever (IB never resends accountDownloadEnd).
    IB_SUBSCRIBE_ACCOUNT_UPDATES: bool = False
    # Market-data feed type passed to ``reqMarketDataType`` on connect.
    # Paper accounts default to delayed (3); set to 1 if a live data
    # subscription is purchased so position marks are real-time.
    #   1 = live, 2 = frozen, 3 = delayed (15min), 4 = delayed-frozen
    IB_MARKET_DATA_TYPE: int = 3

    # --- IB Client Portal REST (independent kill-switch flatten path) ---
    # The C++ watchdog calls these endpoints DIRECTLY when the kill
    # switch fires, so a broker-side disaster (Python alive but
    # mis-behaving, bracket stops cancelled) still results in a
    # flattened book. See execution_engine/watchdog/kill_switch.cpp.
    IB_CLIENT_PORTAL_URL: str = "https://localhost:5000/v1/api"
    IB_CLIENT_PORTAL_CAFILE: str | None = (
        None  # PEM bundle; unset → skip peer verify (self-signed default)
    )
    IB_CLIENT_PORTAL_COOKIE: str | None = None  # "IBKR=...; SESSION=..." cookie header

    # --- Risk Limits ---
    MAX_DRAWDOWN_PCT: float = 3.0
    KELLY_FRACTION: float = 0.5
    MAX_POSITION_PCT: float = 5.0
    MAX_PORTFOLIO_RISK_PCT: float = 0.30  # total F&O / leveraged capital cap
    DAILY_PNL_HALT_PCT: float = -2.0  # halt new trades when daily PnL <= this
    MAX_VAR_PCT: float = 5.0  # VaR kill-switch threshold
    MAX_GROSS_EXPOSURE: float = 1.5  # max total notional / equity (>1 = leverage)
    PRE_MARKET_DELAY_MINUTES: int = 15
    # Stop-loss / take-profit percentages applied by the bracket-order
    # builder in execution_engine/broker/orders.py. Take-profit is
    # bumped 2× for 3rd/4th-order signals where the alpha is slower
    # to materialise. Override per-deployment via env if a different
    # market regime needs tighter or wider stops.
    # Batch-1/B: the fixed-pct bracket knobs below are now the FALLBACK
    # only — used when no OHLCV history is available for ATR
    # computation (e.g. pre-market submissions for a freshly-listed
    # ticker). Under normal operation the bracket is ATR-scaled via the
    # `compute_bracket_levels` helper in `execution_engine.broker.orders`.
    BRACKET_STOP_LOSS_PCT: float = 0.03
    BRACKET_TAKE_PROFIT_PCT: float = 0.03
    BRACKET_TAKE_PROFIT_PCT_LONG_HORIZON: float = 0.06

    # Batch-1/B ATR-scaled bracket parameters.
    # stop_distance = clamp(ATR_STOP_MULTIPLIER * ATR(14) / entry,
    #                       MIN_STOP_PCT, MAX_STOP_PCT)
    # tp_distance   = BRACKET_TARGET_R * stop_distance
    # Tier-3 shocks take a tighter stop (faster invalidation) and a
    # lower target R because the alpha decays quickly; 3rd/4th order
    # structural trades get a more generous target R to let the
    # thesis play out through compounding noise.
    ATR_STOP_MULTIPLIER: float = 1.75  # K_stop — 1.5-2.0 standard Chandelier
    MIN_STOP_PCT: float = 0.005  # 50 bps absolute floor
    MAX_STOP_PCT: float = 0.06  # 6% hard cap (noise = 2.5σ day)
    BRACKET_TARGET_R: float = 2.0  # default R-ratio for TP
    BRACKET_TARGET_R_LONG_HORIZON: float = 3.0  # 3rd/4th order: more room
    BRACKET_TARGET_R_TIER3_SHOCK: float = 1.5  # take liquidity fast

    # --- Conviction ladder ----------------------------------------
    # Single source of truth for every "is this opinion strong enough
    # to trade?" gate. The previous code had 5+ different floors
    # scattered across files (0.55 / 0.62 / 0.72 / 0.78 / 0.90) with
    # no documented hierarchy. These four fields are that hierarchy,
    # in strictly increasing order of required belief:
    #
    #   SUPERVISOR_MIN_CONFIDENCE < BACKTEST_MIN_CONVICTION < MIN_FACTOR_SCORE·
    #     < MIN_CONVICTION_AUDITOR < MIN_CONVICTION_SUPERMAJORITY
    #     < MIN_CONVICTION_CAUSAL_OVERRIDE
    #
    # MIN_FACTOR_SCORE is orthogonal (independent-factor confirmation
    # rather than Bayesian conviction) but must be cleared on every
    # trade path.
    SUPERVISOR_MIN_CONFIDENCE: float = 0.55  # LangGraph should_continue retry gate
    SUPERVISOR_MAX_ITERATIONS: int = 4  # P2-30: hard cap on retry loops (was a magic const)
    BACKTEST_MIN_CONVICTION: float = 0.60  # run_backtest / walk-forward default (raised from 0.55)
    MIN_CONVICTION_TRADE: float = 0.65  # orchestrator unanimous trade (raised from 0.62)
    MIN_CONVICTION_AUDITOR: float = 0.72  # auditor approval floor
    MIN_CONVICTION_SUPERMAJORITY: float = 0.78  # 3-of-4 analyst supermajority
    MIN_CONVICTION_CAUSAL_OVERRIDE: float = 0.90  # long-vol / grind-up override

    MIN_FACTOR_SCORE: float = 0.30  # independent-factor confirmation (legacy/shock)
    MIN_FACTOR_SCORE_DIRECTIONAL: float = 0.60  # 6/10 factors for non-shock directional
    MIN_FACTOR_SCORE_SHOCK: float = 0.30  # keep existing for shock-driven
    MIN_FACTOR_SCORE_PREMIUM: float = 0.22  # premium strategies only need regime check
    MIN_R_GLOBAL: float = 1.5  # minimum expected reward-to-risk
    EPISTEMIC_DIVERGENCE_THRESHOLD: float = 0.25  # JSD gate on analyst debate
    VRP_RICH_THRESHOLD: float = 0.06  # IV-RV "rich premium" block
    CAUSAL_OVERRIDE_CONVICTION: float = 0.90  # causal_conviction that overrides blocklists
    EV_HURDLE_MULTIPLE: float = 4.0  # trade must clear EV_HURDLE × round-trip cost

    # --- Regime thresholds (volatility / macro buckets) -----------
    VIX_CRISIS_THRESHOLD: float = 30.0  # VIX above → crisis regime (protective puts/calls)
    VIX_ELEVATED_THRESHOLD: float = 25.0  # VIX above → mean-reversion-favoured
    VIX_CALM_THRESHOLD: float = 15.0  # VIX below → trend-favoured

    # --- Exit targets --------------------------------------------
    ATR_STOP_LOSS_MULTIPLIER: float = 2.0
    ATR_TAKE_PROFIT_MULTIPLIER: float = 1.5  # balanced TP/SL — see signals.score_signal

    # --- IB session windows (America/New_York) -------------------
    RTH_OPEN_HOUR: int = 9
    RTH_OPEN_MINUTE: int = 30
    RTH_CLOSE_HOUR: int = 16
    RTH_CLOSE_MINUTE: int = 0
    SAFE_OPEN_HOUR: int = 9  # skip the first 15 min of overnight-gap noise
    SAFE_OPEN_MINUTE: int = 45
    PRE_MARKET_LIMIT_OPEN_HOUR: int = 7  # earliest pre-market entry window
    PRE_MARKET_LIMIT_OPEN_MINUTE: int = 0

    # --- Passive-limit routing -----------------------------------
    PASSIVE_PEG_TIMEOUT_SEC: int = 30  # cancel passive REL if unfilled
    PASSIVE_PEG_POLL_INTERVAL_SEC: int = 2

    # --- Ingestion cadence ---------------------------------------
    NER_BATCH_SIZE: int = 200  # news items per LLM NER batch
    NER_CONCURRENCY: int = 25  # parallel LLM calls within a batch
    TIER2_CYCLE_INTERVAL: int = 4  # run non-critical tickers every N cycles

    # --- Universe / screener -------------------------------------
    SCREEN_TOP_N: int = 50  # equities retained by multi-factor screen
    SCREEN_LOOKBACK_DAYS: int = 120  # OHLCV history used by the screener

    # --- Application ---
    LOG_LEVEL: str = "INFO"
    PAPER_TRADING: bool = True
    API_KEY: str | None = None
    LLM_LIVE_ORCHESTRATOR: bool = False
    # AUTO_EXECUTE flips signal-book consumption from "advisory only" to
    # "actually fill". Default True so paper / shadow modes trade
    # automatically; the IB-LIVE path is gated separately via
    # LIVE_TRADING_CONFIRMED in `_validate_live_routing` above, so flipping
    # this on for paper/shadow cannot leak real-money orders.
    AUTO_EXECUTE: bool = True
    # Execution venue. Default SHADOW so a fresh checkout never reaches
    # for IB; flip to IB_PAPER (paper account) or IB_LIVE (real money,
    # requires LIVE_TRADING_CONFIRMED=true) explicitly.
    EXECUTION_MODE: ExecutionMode = ExecutionMode.SHADOW
    # Backend lifespan toggle: when False, API startup skips the continuous
    # ingestion worker so local dashboard/API requests stay responsive.
    CONTINUOUS_PIPELINE_ENABLED: bool = False
    # Hard opt-in to real-money (live, non-paper) IB trading. Keep False.
    LIVE_TRADING_CONFIRMED: bool = False
    # Max single-order notional for IB_LIVE — second tripwire on top of
    # LIVE_TRADING_CONFIRMED. Paper / shadow ignore this entirely.
    MAX_SINGLE_ORDER_NOTIONAL: float = 50_000.0

    # --- LLM orchestration control (LangGraph swarm vs rule-based) ---
    # When True (default), the 15-min ingestion cycle calls the full
    # LangGraph LLM swarm (`orchestrate_trade`) per ticker, with the
    # deterministic `rule_based_orchestrate` as a timeout / error
    # fallback. Set False to skip the LLM entirely (faster, cheaper,
    # no LLM API cost).
    LLM_ORCHESTRATION_ENABLED: bool = True
    # Per-ticker timeout for the LLM orchestrator call. On timeout we
    # fall back to rule-based — never block the cycle on a slow LLM.
    LLM_SIGNAL_TIMEOUT_SECONDS: int = 45
    # Cap on concurrent LLM calls to avoid exhausting provider rate
    # limits when the cycle iterates over 100+ tickers.
    LLM_MAX_CONCURRENT_SIGNALS: int = 5
    # Vision-LLM PatternAgent toggle. Default OFF so the hot ingestion
    # path never pays the chart-render + vision-LLM cost. When True,
    # ``classify_pattern`` renders an SVG and (in the future, when an
    # SVG-capable vision model is wired) asks for a pattern label.
    # Leaving this off keeps backend startup fast and avoids the
    # multi-second Matplotlib font-cache build that used to block
    # every subprocess the pattern module touched.
    PATTERN_AGENT_ENABLED: bool = False

    # --- CLI → backend routing (P0-B) ---
    # Base URL for the running FastAPI app. `ayvid trade execute` POSTs
    # to {AYVID_BACKEND_URL}/api/v1/trade/manual so manual trades hit
    # the exact same graph-freshness gate + seq registry + kill-switch
    # lifespan as the autopilot path. Override via env to talk to a
    # staging or production deployment.
    AYVID_BACKEND_URL: str = "http://127.0.0.1:8001"
    # Optional shared live-artifact store. When set, the execution VM
    # mirrors `live/*` artifacts into GCS and Cloud Run reads from the
    # same location so dashboard state stays in sync across hosts.
    AYVID_LIVE_GCS_BUCKET: str | None = None
    AYVID_LIVE_GCS_PREFIX: str = "live_state"

    # --- Auth + CORS (P1-15) ---
    # When True, `verify_api_key` NEVER degrades to a dev-mode pass-
    # through: requests without a valid X-API-Key header are rejected
    # with 403. Production deployments MUST set this. Tests and local
    # dev leave it False so they work without registering keys.
    AYVID_REQUIRE_AUTH: bool = False
    # Comma-separated list of allowed origins for CORS. Default `*`
    # preserves existing dev behaviour; production should set this to
    # the explicit frontend origin (and remove the wildcard).
    AYVID_ALLOWED_ORIGINS: str = "*"
    # When False (default) browsers are NOT sent `credentials: include`
    # on CORS requests — this is the safer posture because `*` origin
    # + credentials is rejected by the CORS spec anyway.
    AYVID_CORS_ALLOW_CREDENTIALS: bool = False


settings = Settings()
