"""Back-compat shim — module moved to `shared.quant.signals`.

The pure-math primitives have been relocated to the leaf
`shared/quant/` package so `graph_intelligence` can depend on them
without forming a cycle with `execution_engine`. This file re-exports
the public surface so legacy `from execution_engine.math.signals import …`
call sites keep working unchanged. New code should import directly
from `shared.quant.signals`.
"""

from shared.quant.signals import *  # noqa: F401,F403,E402

