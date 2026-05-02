"""Back-compat shim — module moved to `shared.quant._primitives`.

The pure-math primitives have been relocated to the leaf
`shared/quant/` package so `graph_intelligence` can depend on them
without forming a cycle with `execution_engine`. This file re-exports
the public surface so legacy `from execution_engine.math._primitives import …`
call sites keep working unchanged. New code should import directly
from `shared.quant._primitives`.
"""

from shared.quant._primitives import *  # noqa: F401,F403,E402

