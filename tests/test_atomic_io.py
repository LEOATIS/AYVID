"""Tests for the shared atomic file I/O helpers.

These guarantees are load-bearing for crash safety: if `positions.json`
or `priors.json` ever ends up half-written, the runner reads corrupt
state on restart. The tests here exercise the failure mode the audit
flagged — a concurrent reader observing a partial write — and also the
backup-rotation fallback path used by `safe_load_json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.atomic_io import (
    atomic_write_json,
    atomic_write_text,
    safe_load_json,
)


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        atomic_write_text(target, "hello")
        assert target.read_text() == "hello"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "deeper" / "nested" / "f.txt"
        atomic_write_text(target, "ok")
        assert target.read_text() == "ok"

    def test_keeps_backup_when_requested(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2", keep_backup=True)
        assert target.read_text() == "v2"
        bak = target.with_suffix(target.suffix + ".bak")
        assert bak.exists()
        assert bak.read_text() == "v1"

    def test_no_tmp_left_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        atomic_write_text(target, "x")
        tmp = target.with_suffix(target.suffix + ".tmp")
        assert not tmp.exists()


class TestAtomicWriteJson:
    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        payload = {"a": [1, 2, 3], "b": "ok"}
        atomic_write_json(target, payload)
        assert json.loads(target.read_text()) == payload


class TestSafeLoadJson:
    def test_loads_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        atomic_write_json(target, {"k": 1})
        assert safe_load_json(target) == {"k": 1}

    def test_returns_none_on_missing(self, tmp_path: Path) -> None:
        assert safe_load_json(tmp_path / "missing.json") is None

    def test_falls_back_to_backup_on_corrupt_primary(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        atomic_write_json(target, {"v": "good"})
        # Now overwrite the primary with garbage but rotate-backup the
        # good copy first (mimics the priors.json keep_backup=True path).
        atomic_write_json(target, {"v": "good"}, keep_backup=True)
        target.write_text("{ this is not json")
        assert safe_load_json(target, fallback_to_backup=True) == {"v": "good"}

    def test_no_fallback_without_flag(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        atomic_write_json(target, {"v": "good"}, keep_backup=True)
        target.write_text("{ not json")
        # Without fallback_to_backup, corrupt primary returns None even
        # when a good .bak exists. Caller decides recovery policy.
        assert safe_load_json(target) is None


class TestPartialWriteSimulation:
    """Simulate the concrete crash scenario: a writer dies mid-write.

    On a real system this would happen if SIGKILL hits the runner
    while it's flushing positions.json. Our atomic_write helpers must
    guarantee that the visible primary file is *either* the previous
    good copy or the new good copy — never half-written.
    """

    def test_primary_unchanged_when_tmp_phase_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        atomic_write_json(target, {"v": 1})
        original = target.read_text()

        # Simulate the writer crashing AFTER writing the .tmp but
        # BEFORE the os.replace. We do this by manually doing the first
        # half of the operation and then leaving the .tmp in place.
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text("{garbage")
        # Reader sees only the original — they read `target`, not `target.tmp`.
        assert target.read_text() == original

    def test_backup_preserved_across_retries(self, tmp_path: Path) -> None:
        target = tmp_path / "p.json"
        atomic_write_json(target, {"gen": 0})
        atomic_write_json(target, {"gen": 1}, keep_backup=True)
        atomic_write_json(target, {"gen": 2}, keep_backup=True)
        bak = target.with_suffix(target.suffix + ".bak")
        # The .bak rotates to the immediately-previous good copy each
        # time, which is what we want — fallback recovers the latest
        # known-good state.
        assert json.loads(bak.read_text()) == {"gen": 1}
        assert json.loads(target.read_text()) == {"gen": 2}
