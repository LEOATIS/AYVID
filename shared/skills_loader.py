"""Skill loader — markdown-based prompt registry.

Walks ``.claude/skills/**/SKILL.md`` from the repo root, parses YAML
frontmatter, and reads every sibling ``.md`` file as a named prompt.

Pattern adopted from anthropic/financial-services: file-based, no
build. The markdown files are the single source of truth for prompts;
Python modules in graph_intelligence/ become thin shims that import
from here. This module is in ``shared/`` (the leaf layer of AYVID's
dependency graph) and must not import from graph_intelligence,
backtesting, execution_engine, or backend.

Usage::

    from shared.skills_loader import load_skill
    skill = load_skill("hedge-fund/bull-bear-debate")
    bull_system_prompt = skill.prompts["bull_system"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Project root is two levels up from this file (shared/skills_loader.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / ".claude" / "skills"

# Frontmatter keys consumed directly into typed Skill fields. Everything
# else lands in ``Skill.metadata`` so the format remains extensible
# without code changes here.
_RESERVED_KEYS = frozenset(
    {
        "name",
        "description",
        "version",
        "owner",
        "upstream",
        "inputs",
        "outputs",
        "prompts",
    }
)


@dataclass(frozen=True)
class Skill:
    """A loaded skill bundle.

    `prompts` is the dict of {filename_stem -> file_content} for every
    .md file in the skill directory except SKILL.md itself. So a skill
    at ``.claude/skills/foo/bar/SKILL.md`` with siblings ``system.md``
    and ``user.md`` exposes ``prompts == {"system": "...", "user": "..."}``.
    """

    name: str
    description: str
    version: str
    owner: str
    upstream: str | None
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    prompts: dict[str, str]
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)

    def prompt(self, key: str) -> str:
        """Get a named prompt; raises ``KeyError`` with helpful list on miss."""
        try:
            return self.prompts[key]
        except KeyError as exc:
            raise KeyError(
                f"Skill {self.name!r} has no prompt {key!r}. "
                f"Available: {sorted(self.prompts)}"
            ) from exc


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from a markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return fm, body


def _load_skill_dir(skill_md: Path) -> Skill:
    """Build a ``Skill`` from one SKILL.md and its sibling .md files."""
    text = skill_md.read_text(encoding="utf-8")
    fm, _body = _split_frontmatter(text)

    prompts: dict[str, str] = {}
    for sibling in sorted(skill_md.parent.iterdir()):
        if (
            sibling.is_file()
            and sibling.suffix == ".md"
            and sibling.name != "SKILL.md"
        ):
            prompts[sibling.stem] = sibling.read_text(encoding="utf-8")

    return Skill(
        name=str(fm.get("name") or skill_md.parent.name),
        description=str(fm.get("description", "")),
        version=str(fm.get("version", "0.0.0")),
        owner=str(fm.get("owner", "ayvid")),
        upstream=fm.get("upstream"),
        inputs=tuple(fm.get("inputs") or ()),
        outputs=tuple(fm.get("outputs") or ()),
        prompts=prompts,
        path=skill_md.parent,
        metadata={k: v for k, v in fm.items() if k not in _RESERVED_KEYS},
    )


@lru_cache(maxsize=1)
def _index() -> dict[str, Skill]:
    """Build and cache the ``{skill_name -> Skill}`` index on first call."""
    out: dict[str, Skill] = {}
    if not _SKILLS_ROOT.exists():
        return out
    for skill_md in _SKILLS_ROOT.glob("**/SKILL.md"):
        skill = _load_skill_dir(skill_md)
        out[skill.name] = skill
    return out


def load_skill(name: str) -> Skill:
    """Return the loaded ``Skill`` for ``name`` (e.g. ``"hedge-fund/bull-bear-debate"``)."""
    idx = _index()
    if name not in idx:
        raise KeyError(
            f"Unknown skill {name!r}. Available: {sorted(idx)}"
        )
    return idx[name]


def list_skills() -> list[str]:
    """Return the list of all loaded skill names, sorted."""
    return sorted(_index())


def refresh_index() -> None:
    """Drop the cache and force a re-walk on next access. Used by tests."""
    _index.cache_clear()
