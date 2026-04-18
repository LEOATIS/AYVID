from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM Providers ---
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o"
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

    # --- SEC EDGAR ---
    SEC_EDGAR_EMAIL: str = "research@ayvid.ai"

    # --- Neo4j (supports both NEO4J_USER and NEO4J_USERNAME) ---
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str | None = None
    NEO4J_USERNAME: str | None = None
    NEO4J_PASSWORD: str | None = None
    NEO4J_DATABASE: str = "neo4j"

    @model_validator(mode="after")
    def _resolve_neo4j_user(self) -> "Settings":
        if not self.NEO4J_USER and self.NEO4J_USERNAME:
            self.NEO4J_USER = self.NEO4J_USERNAME
        elif not self.NEO4J_USER:
            self.NEO4J_USER = "neo4j"
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

    # --- Risk Limits ---
    MAX_DRAWDOWN_PCT: float = 3.0
    KELLY_FRACTION: float = 0.5
    MAX_POSITION_PCT: float = 5.0
    PRE_MARKET_DELAY_MINUTES: int = 15

    # --- Application ---
    LOG_LEVEL: str = "INFO"
    PAPER_TRADING: bool = True
    API_KEY: str | None = None
    LLM_LIVE_ORCHESTRATOR: bool = False
    AUTO_EXECUTE: bool = False


settings = Settings()
