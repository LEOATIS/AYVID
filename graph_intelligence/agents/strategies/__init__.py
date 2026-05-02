"""Per-asset-class orchestrator modules.

This subpackage is the H5 decomposition of the legacy 2,788-line
``strategy_orchestrator.py`` god object. Each module here owns one
asset class's decision logic and imports only what it needs from the
parent package's public types.

Extracted modules (each exposes ``orchestrate(ctx) -> TradeDecision``):
  * ``fx``         — FX pairs (was ``_fx_orchestrate``)
  * ``commodity``  — commodity futures (was ``_commodity_orchestrate``)
  * ``crypto``     — crypto assets (was ``_crypto_orchestrate``)
  * ``index_fno``  — index futures + options (was ``index_fno_orchestrate``)
  * ``equity``     — long equity (was ``_equity_orchestrate_core``)

After extraction the parent ``strategy_orchestrator.py`` shrank from
2,890 → 1,696 lines; ``rule_based_orchestrate`` is the asset-class
dispatcher that routes into these modules. Each is byte-identical
to its legacy implementation; the next iteration can split
``index_fno`` further by REGIME (shock / crisis / trend / mean-rev)
without touching the parent module.
"""

from graph_intelligence.agents.strategies.commodity import orchestrate as commodity_orchestrate
from graph_intelligence.agents.strategies.crypto import orchestrate as crypto_orchestrate
from graph_intelligence.agents.strategies.equity import orchestrate as equity_orchestrate
from graph_intelligence.agents.strategies.fx import orchestrate as fx_orchestrate
from graph_intelligence.agents.strategies.index_fno import orchestrate as index_fno_orchestrate

__all__ = [
    "fx_orchestrate",
    "commodity_orchestrate",
    "crypto_orchestrate",
    "index_fno_orchestrate",
    "equity_orchestrate",
]
