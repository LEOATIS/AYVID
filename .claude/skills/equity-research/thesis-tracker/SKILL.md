---
name: equity-research/thesis-tracker
description: Track whether an active position's thesis still holds — what's intact, what's broken, what catalysts remain. Run nightly on every open position; surface thesis-invalidation closes when it triggers.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/thesis-tracker)
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - entry_thesis
  - invalidation_level
  - entry_date
  - current_price
  - news_since_entry
  - earnings_since_entry
  - macro_since_entry
outputs:
  - ThesisStatus (verdict, components_intact, components_broken, action)
---

# Thesis tracker

Runs nightly across every open position. Compares the entry thesis
against everything that has happened since entry (news, earnings,
macro prints) and emits a verdict of one of:

- **INTACT** — keep position, no action.
- **WEAKENED** — keep but reduce conviction; tighten stop.
- **INVALIDATED** — close position; the thesis no longer holds.
- **TRANSMUTED** — the thesis is now a different thesis; flag for
  human review.

When `verdict == INVALIDATED`, the orchestrator pins a thesis-
invalidation close on the position regardless of current PnL. This is
how AYVID enforces "exit on broken thesis, not on price."

## Discipline rules

- A single piece of contradicting news is NOT automatically
  INVALIDATED. Two independent ones from different domains is.
- TRANSMUTED is a real and useful verdict — flag for review rather
  than over-confidently closing.
- WEAKENED requires naming which thesis component weakened.
