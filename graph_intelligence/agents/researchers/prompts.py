"""Prompts for the bull/bear/facilitator trio.

Single source of truth lives in
``.claude/skills/hedge-fund/bull-bear-debate/`` as markdown files; this
module re-exports them through ``shared.skills_loader`` so the rest of
the codebase keeps the historical import path
(``BULL_SYSTEM`` / ``BEAR_SYSTEM`` / ``FACILITATOR_SYSTEM`` + the user
templates).

The skills-layer migration is described in
``document/anthropic_integration.md`` — pattern adopted from
anthropic/financial-services (file-based, no build).
"""

from __future__ import annotations

from shared.skills_loader import load_skill

_SKILL = load_skill("hedge-fund/bull-bear-debate")

BULL_SYSTEM = _SKILL.prompt("bull_system")
BULL_USER_TEMPLATE = _SKILL.prompt("bull_user")
BEAR_SYSTEM = _SKILL.prompt("bear_system")
BEAR_USER_TEMPLATE = _SKILL.prompt("bear_user")
FACILITATOR_SYSTEM = _SKILL.prompt("facilitator_system")
FACILITATOR_USER_TEMPLATE = _SKILL.prompt("facilitator_user")
