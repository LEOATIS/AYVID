---
name: equity-research/morning-note
description: Morning briefing — overnight macro, key prints scheduled today, active position updates, top-of-mind risks. Sent to the operator at 06:30 ET. Not a trade recommendation.
version: 1.0.0
owner: ayvid
upstream: anthropics/financial-services (equity-research/morning-note)
prompts:
  - system.md
  - user.md
inputs:
  - overnight_market_summary
  - scheduled_prints_today
  - active_positions_status
  - shock_alerts_overnight
outputs:
  - MorningNote (headline, top_3_items, position_callouts, risk_callouts)
---

# Morning note

06:30 ET briefing for the operator. Concise: headline, top-3 items,
position callouts, risk callouts. NOT a trade recommendation — it's a
situational-awareness artifact.

Routed to:
- CLI dashboard banner.
- Console UI top-of-page card.
- (Optional) Slack/email webhook when configured.

## Discipline rules

- Headline is one sentence.
- Top-3 items: bullets, ≤140 chars each.
- Position callouts: only mention positions where something changed
  overnight.
- Risk callouts: name explicit risks (event, level, magnitude). Vague
  risks rejected.
