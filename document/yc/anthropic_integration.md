# AYVID × `anthropic/financial-services` Integration

> AYVID is the buy-side production of Anthropic's
> [Claude for Financial Services](https://github.com/anthropics/financial-services)
> stack. This document is the canonical map of what was integrated,
> what is AYVID-native, and how the pieces compose at runtime.

## Why this integration exists

Anthropic ships an opinionated FSI stack: ~50 reusable
[Claude Skills](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins)
across nine financial verticals, plus 11 [MCP](https://modelcontextprotocol.io/)
data connectors (Aiera, MT Newswires, FactSet, Moody's, S&P Global,
PitchBook, Daloopa, Morningstar, LSEG, Chronograph, Egnyte).

Most of that stack is sell-side (investment banking pitch decks, PE IC
memos, fund admin GL recon, wealth-management client reviews). The
buy-side slice — equity research, fundamental analysis, real-time
news — is exactly what an AI-native hedge fund needs.

We integrated that buy-side slice, kept the Anthropic file-based
conventions (no build step), and added five AYVID-native skills that
encode the parts of the AYVID stack that have no analogue in the
upstream repo (shock-driven thesis, causal validation, Kelly sizing
explanation, post-mortem, bull/bear/facilitator debate).

## What was integrated (and what wasn't)

### Integrated — buy-side relevant

| Source | What | Where |
|---|---|---|
| `vertical-plugins/equity-research/` | 8 skills: earnings-analysis, earnings-preview, model-update, morning-note, sector-overview, thesis-tracker, catalyst-calendar, idea-generation | `.claude/skills/equity-research/` |
| `vertical-plugins/financial-analysis/` | DCF, comps (fundamental anchor) | `.claude/skills/financial-analysis/` |
| `vertical-plugins/financial-analysis/.mcp.json` | 5 MCP connectors: Aiera, MT Newswires, FactSet, Moody's, S&P Global | `.mcp.json` (root) + `data_primitives/mcp/` |
| File-based skill convention | `SKILL.md` + sibling `.md` prompt files | `.claude/skills/**/*.md` |

### Not integrated — out of scope for a buy-side fund

- `agent-plugins/{pitch-agent, gl-reconciler, kyc-screener, ...}` — sell-side / fund-admin / KYC. AYVID is buy-side.
- `vertical-plugins/{investment-banking, private-equity, wealth-management, fund-admin, operations}` — wrong vertical.
- `claude-for-msft-365-install/` — AYVID has no Microsoft 365 surface.
- `managed-agent-cookbooks/` — AYVID's runtime is Cloud Run + Compute Engine, not `/v1/agents`. Considered for a future cookbook entry; deliberately out of scope today.
- MCP connectors for PitchBook (private markets), Chronograph (PE), Egnyte (doc storage), Daloopa, Morningstar, LSEG — buy-side fund doesn't need them yet.

## AYVID-native skill bundle

Five skills in `.claude/skills/hedge-fund/` have no analogue in the
upstream repo. They encode AYVID's specific machinery:

| Skill | Encodes | Used by |
|---|---|---|
| `bull-bear-debate` | Bull / Bear / Facilitator trio with empirical-priors adjudication | `graph_intelligence/agents/researchers/` |
| `shock-thesis` | Shock-driven directional thesis with causal-chain anchoring | `graph_intelligence/agents/strategist/` |
| `causal-validation` | LLM-edge-proposer adjudication against conditional-independence p-values | `graph_intelligence/causal_falsification/` |
| `kelly-sizing` | Plain-English explanation of the deterministic Half-Kelly output | `graph_intelligence/agents/analyst/` + operator UI |
| `post-mortem` | Post-trade rationale entry for the outcomes log | `graph_intelligence/agents/auditor/` |

## Runtime topology

```
                          ┌──────────────────────────────────┐
                          │  .claude/skills/**/*.md          │
                          │  (single source of truth         │
                          │   for prompts; file-based)       │
                          └────────────┬─────────────────────┘
                                       │  parse YAML + read
                                       │  sibling .md files
                                       ▼
                          ┌──────────────────────────────────┐
                          │  shared/skills_loader.py         │
                          │  load_skill("hedge-fund/...")    │
                          └────────────┬─────────────────────┘
                                       │  prompts dict
                                       ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  graph_intelligence/agents/{researchers, ...}/prompts.py    │
       │  Thin shim that re-exports BULL_SYSTEM / BEAR_SYSTEM / ...  │
       └─────────────────────────────────────────────────────────────┘

                          ┌──────────────────────────────────┐
                          │  .mcp.json (root)                │
                          │   - mt-newswires                 │
                          │   - aiera                        │
                          │   - factset                      │
                          │   - moodys                       │
                          │   - sp-global                    │
                          └────────────┬─────────────────────┘
                                       │  endpoint URLs
                                       ▼
                          ┌──────────────────────────────────┐
                          │  data_primitives/mcp/            │
                          │   - client.py (async HTTP RPC)   │
                          │   - mt_newswires.py              │
                          │   - aiera.py                     │
                          │   - factset.py / moodys.py /     │
                          │     sp_global.py                 │
                          └────────────┬─────────────────────┘
                                       │  NewsItem[] / dict
                                       ▼
                          ┌──────────────────────────────────┐
                          │  Existing AYVID pipeline:        │
                          │   data_primitives/pipeline/      │
                          │   continuous_worker.py           │
                          │   (now prefers MCP feed when     │
                          │    MCP_MT_NEWSWIRES_URL is set;  │
                          │    falls back to scraper else)   │
                          └──────────────────────────────────┘
```

## Opt-in matrix

Every MCP connector is opt-in via env var. AYVID runs identically to
its pre-integration state when no MCP vars are set.

| Env var | Off (default) | On |
|---|---|---|
| `MCP_MT_NEWSWIRES_URL` | Worker uses existing news_scraper + OpenBB + Finnhub | Worker adds MCP feed alongside, NER pipeline ingests both |
| `MCP_AIERA_URL` | Earnings transcripts via scraper / SEC EDGAR | Earnings transcripts via Aiera MCP |
| `MCP_FACTSET_URL` | Consensus + IV via OpenBB | Consensus + IV via FactSet MCP |
| `MCP_MOODYS_URL` | ETFs & bonds rating-uniform BBB | Rating-aware bond sizing |
| `MCP_SP_GLOBAL_URL` | Sector overview via screener output | Sector overview via Capital IQ tear sheets |

All keys belong in `.env`. `.env.example` has commented templates.
Production keys live in GCP Secret Manager (see `infra/`).

## How an agent uses a skill

Today, only `graph_intelligence/agents/researchers/prompts.py` is
migrated through `skills_loader`. The pattern:

```python
# graph_intelligence/agents/researchers/prompts.py
from shared.skills_loader import load_skill

_SKILL = load_skill("hedge-fund/bull-bear-debate")
BULL_SYSTEM = _SKILL.prompt("bull_system")
BEAR_SYSTEM = _SKILL.prompt("bear_system")
FACILITATOR_SYSTEM = _SKILL.prompt("facilitator_system")
# ... + user templates
```

The Python module is now a shim; the markdown files in
`.claude/skills/hedge-fund/bull-bear-debate/` are the canonical
prompts. Editing the markdown moves the agent's behaviour on the next
import.

To migrate another agent (mapper, strategist, analyst, auditor):

1. Author the matching skill files under `.claude/skills/`.
2. Replace the Python prompt constants with `load_skill(...).prompt(...)`.
3. Add a byte-identicality test (see `tests/test_skills_loader.py`).

This is deliberately incremental. Researchers is the proof-of-pattern;
the others can be migrated as their prompts evolve.

## Provider posture

Per current AYVID config (`LLM_PROVIDER=openai`), prompts emitted from
the skill loader are dispatched against OpenAI GPT-4o by default. The
unified dispatch in `shared/llm_client.py` is provider-agnostic; flip
the env var to `anthropic` (and set `ANTHROPIC_API_KEY`) when the
Claude Console key is provisioned. No further code changes are
required to run the same skills on Claude.

## Verification

```bash
# Loader can find every skill and they parse
python -c "from shared.skills_loader import list_skills; print(list_skills())"

# Specific skill exposes the expected prompts
python -c "from shared.skills_loader import load_skill; \
  s = load_skill('hedge-fund/bull-bear-debate'); \
  print(sorted(s.prompts))"

# Researchers' Python constants come from the loader and are
# byte-identical to the pre-migration values
pytest tests/test_skills_loader.py -v

# MCP client fails closed when keys absent
pytest tests/test_mcp_client.py -v

# Full backtest still passes (no regression on the researcher debate)
ayvid data shock-walkforward --only covid_crash_2020 --name claude_fsi_smoke
```

## See also

- [`.claude/skills/`](../../.claude/skills/) — the skill bundles.
- [`.mcp.json`](../../.mcp.json) — connector registry.
- [`shared/skills_loader.py`](../../shared/skills_loader.py) — loader implementation.
- [`data_primitives/mcp/`](../../data_primitives/mcp/) — MCP runtime client.
- Upstream: <https://github.com/anthropics/financial-services>.
