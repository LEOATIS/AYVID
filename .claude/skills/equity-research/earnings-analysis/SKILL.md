---
name: equity-research/earnings-analysis
description: Read an earnings call transcript + the latest 10-Q/10-K filing and emit a structured analyst note — beat/miss, guidance delta, segment trends, model deltas, and the AYVID-specific call-out of any line item that could rerate the supply-chain edges in Neo4j.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/earnings-analysis)
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - reporting_period
  - transcript
  - filing_excerpt
  - consensus_estimates
  - prior_model_assumptions
outputs:
  - EarningsNote (headline, beat_miss_table, guidance_delta, model_updates, supply_chain_implications)
data_sources:
  - mcp:aiera (transcript fetch)
  - mcp:factset (consensus + filing data)
  - mcp:sp_global (Capital IQ tear sheet for context)
---

# Earnings analysis (AYVID profile)

Adapted from anthropic/financial-services equity-research vertical.
AYVID extends the upstream skill with one buy-side-specific section:
**supply-chain implications**, which calls out any line item, customer
concentration, or geographic exposure that should rerate a Neo4j edge.

The Mapper agent reads that section verbatim and, if a rerate is
warranted, schedules an out-of-band refinery pass on the affected
edges. This is how earnings move the graph, not just the price.

## Inputs (via MCP connectors when configured)

- **Aiera MCP** → transcript fetch by ticker + reporting period.
- **FactSet MCP** → consensus estimates + filing excerpts.
- **S&P Global MCP** → Capital IQ tear sheet for context numbers.

When MCPs are unconfigured, falls back to the existing
`data_primitives/extractors/sec_edgar.py` + scraped transcript path.

## Discipline rules

- Beat/miss must be quantified (`reported - consensus`, both nominal
  and pct).
- Guidance delta is `new_guidance - prior_guidance` in matching units.
- Model updates name specific assumption (revenue mix, gross margin,
  capex) and the magnitude of the change.
- Supply-chain implications: at most 3 callouts, each naming the
  Neo4j edge type (e.g. `CUSTOMER_OF`, `SUPPLIER_TO`) and the
  direction of the implied confidence update.
