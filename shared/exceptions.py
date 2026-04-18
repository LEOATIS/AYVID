"""Custom exception hierarchy for AYVID.

All exceptions that cross module boundaries are defined here.
"""


class AYVIDError(Exception):
    """Base exception for all AYVID errors."""


# ── Data Pipeline ────────────────────────────────────────────

class DataPipelineError(AYVIDError):
    """Raised when a data ingestion step fails."""


class OpenBBFetchError(DataPipelineError):
    """OpenBB SDK returned an error or empty dataset."""


class QuestDBWriteError(DataPipelineError):
    """Failed to write time-series data to QuestDB."""


class HopsworksSyncError(DataPipelineError):
    """Feature store sync failed."""


# ── Graph Intelligence ───────────────────────────────────────

class GraphStaleError(AYVIDError):
    """Neo4j topology is behind the latest sequence ID.

    Fires when the FastAPI sequence validator detects that the graph
    has not yet ingested the event that triggered the trade signal.
    """

    def __init__(self, expected_seq: int, actual_seq: int = 0) -> None:
        self.expected_seq = expected_seq
        self.actual_seq = actual_seq
        super().__init__(
            f"Graph stale: expected seq >= {expected_seq}, got {actual_seq}"
        )


class CausalPathNotFoundError(AYVIDError):
    """No valid N-th order causal path found for the target."""


class Neo4jConnectionError(AYVIDError):
    """Cannot connect to the Neo4j database."""


class QdrantConnectionError(AYVIDError):
    """Cannot connect to the Qdrant vector store."""


# ── Execution Engine ─────────────────────────────────────────

class IBKRDisconnectError(AYVIDError):
    """IB Gateway connection lost (Error 1100).

    Triggers the exponential backoff reconnection loop.
    """


class IBKROrderRejectedError(AYVIDError):
    """Interactive Brokers rejected the submitted order."""

    def __init__(self, order_id: int, reason: str) -> None:
        self.order_id = order_id
        self.reason = reason
        super().__init__(f"Order {order_id} rejected: {reason}")


class PreMarketTrapError(AYVIDError):
    """Signal generated outside RTH — queued for delayed execution."""


class KillSwitchTriggered(AYVIDError):
    """The C++ watchdog or software VaR limit was breached.

    When raised, ALL positions must be flattened immediately.
    """

    def __init__(self, drawdown_pct: float, threshold_pct: float) -> None:
        self.drawdown_pct = drawdown_pct
        self.threshold_pct = threshold_pct
        super().__init__(
            f"KILL SWITCH: drawdown {drawdown_pct:.2f}% exceeded "
            f"threshold {threshold_pct:.2f}%"
        )


# ── Sequence Validation ──────────────────────────────────────

class SequenceValidationError(AYVIDError):
    """Event sourcing sequence ID mismatch across databases.

    Prevents trading on asymmetric information where QuestDB has
    ingested a shock but Neo4j has not yet updated its topology.
    """

    def __init__(self, source: str, expected: int, actual: int) -> None:
        self.source = source
        super().__init__(
            f"Sequence desync in {source}: expected {expected}, got {actual}"
        )


# ── LLM Errors ───────────────────────────────────────────────

class LLMProviderError(AYVIDError):
    """LLM API call failed after retries."""


class LLMRateLimitError(LLMProviderError):
    """Hit rate limit on the LLM provider."""
