"""Atomic file I/O helpers.

Crash-safety is load-bearing in this codebase. The C++ watchdog can
SIGKILL the runner at any instant; a half-written `positions.json` or
`priors.json` corrupts state across a restart. These helpers write to a
sibling `.tmp` file and then `os.replace()` (atomic on POSIX), and
optionally keep a `.bak` of the previous file so a corrupt primary can
be recovered from backup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str, *, keep_backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    if keep_backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            os.replace(path, bak)
        except OSError:
            pass
    os.replace(tmp, path)


def atomic_write_json(
    path: Path,
    obj: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
    default: Any = str,
    keep_backup: bool = False,
) -> None:
    payload = json.dumps(obj, indent=indent, sort_keys=sort_keys, default=default)
    atomic_write_text(path, payload, keep_backup=keep_backup)


def safe_load_json(path: Path, *, fallback_to_backup: bool = False) -> Any | None:
    """Load JSON; on parse failure (with fallback_to_backup) try `.bak`.

    Returns ``None`` if neither primary nor backup is loadable. Callers
    distinguish "no priors yet" (None) from a corrupt primary by also
    checking ``path.exists()`` if the recovery story matters.
    """
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    if fallback_to_backup:
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            try:
                return json.loads(bak.read_text())
            except (json.JSONDecodeError, ValueError, OSError):
                pass
    return None
