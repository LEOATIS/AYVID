"""SQLite (WAL) persistence for the AYVID research workspace.

Zero external services: the workspace boots on Python's built-in ``sqlite3``.
Schema migrations are an ordered list applied once each on startup.

Authorization model: every thesis / session / watchlist row carries a
``workspace_id``. Reads and writes go through ``_assert_owner`` which raises
``ForbiddenError`` on mismatch — the route layer maps that to HTTP 403.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE = "ws_local"
DEFAULT_USER = "user_local"


class NotFoundError(LookupError):
    pass


class ForbiddenError(PermissionError):
    pass


@dataclass(frozen=True)
class Actor:
    workspace_id: str = DEFAULT_WORKSPACE
    user_id: str = DEFAULT_USER


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_JSON_COLUMNS = {
    "instrument_ids",
    "assumptions",
    "catalysts",
    "open_questions",
    "linked_session_ids",
    "payload",
    "snapshot",
    "last_result",
    "inputs",
}

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE users (user_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces,
            display_name TEXT NOT NULL);
        CREATE TABLE instruments (
            instrument_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, local_symbol TEXT, name TEXT NOT NULL,
            exchange TEXT NOT NULL, currency TEXT NOT NULL, country TEXT NOT NULL, sector TEXT,
            industry TEXT, cik TEXT, data_tier TEXT NOT NULL, first_bar TEXT, last_bar TEXT);
        CREATE INDEX ix_instruments_symbol ON instruments(symbol);
        CREATE TABLE source_documents (
            source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT,
            canonical_url TEXT, published_at TEXT, retrieved_at TEXT NOT NULL, doc_type TEXT NOT NULL,
            instrument_id TEXT, accession_no TEXT, locator TEXT, excerpt TEXT, content_hash TEXT,
            access_status TEXT NOT NULL DEFAULT 'AVAILABLE', is_fixture INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX ix_sources_instrument ON source_documents(instrument_id);
        CREATE TABLE document_chunks (
            chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_documents,
            ordinal INTEGER NOT NULL, locator TEXT, text TEXT NOT NULL);
        CREATE TABLE financial_observations (
            observation_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, metric_code TEXT NOT NULL,
            metric_label TEXT NOT NULL, value TEXT NOT NULL, currency TEXT, scale TEXT NOT NULL,
            fiscal_period TEXT NOT NULL, period_start TEXT, period_end TEXT, published_at TEXT,
            as_of TEXT NOT NULL, provider TEXT NOT NULL, source_id TEXT, basis TEXT NOT NULL,
            status TEXT NOT NULL, formula TEXT, inputs TEXT);
        CREATE INDEX ix_obs_instrument_metric ON financial_observations(instrument_id, metric_code);
        CREATE TABLE supply_chain_edges (
            edge_id TEXT PRIMARY KEY, src_instrument_id TEXT, dst_instrument_id TEXT,
            src_symbol TEXT NOT NULL, dst_symbol TEXT NOT NULL, relationship_type TEXT NOT NULL,
            revenue_dependency_pct REAL, criticality_score REAL, source_id TEXT,
            evidence_status TEXT NOT NULL, uncertainty_note TEXT);
        CREATE INDEX ix_edges_src ON supply_chain_edges(src_symbol);
        CREATE INDEX ix_edges_dst ON supply_chain_edges(dst_symbol);
        CREATE TABLE research_sessions (
            session_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
            query TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL, data_mode TEXT NOT NULL,
            title TEXT, created_at TEXT NOT NULL);
        CREATE INDEX ix_sessions_ws ON research_sessions(workspace_id, created_at);
        CREATE TABLE research_artifacts (
            artifact_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES research_sessions,
            kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE claim_evidence_links (
            link_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            ref_id TEXT NOT NULL, ref_kind TEXT NOT NULL, verified INTEGER NOT NULL);
        CREATE TABLE theses (
            thesis_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
            title TEXT NOT NULL, instrument_ids TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, last_reviewed_at TEXT, horizon TEXT, conviction_label TEXT NOT NULL,
            summary TEXT, assumptions TEXT NOT NULL, catalysts TEXT NOT NULL, open_questions TEXT NOT NULL,
            linked_session_ids TEXT NOT NULL);
        CREATE INDEX ix_theses_ws ON theses(workspace_id);
        CREATE TABLE thesis_evidence (
            evidence_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL REFERENCES theses, source_id TEXT NOT NULL,
            stance TEXT NOT NULL, note TEXT, added_at TEXT NOT NULL);
        CREATE TABLE thesis_conditions (
            condition_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL REFERENCES theses,
            metric_code TEXT NOT NULL, instrument_id TEXT NOT NULL, operator TEXT NOT NULL,
            threshold_value REAL, description TEXT NOT NULL, current_status TEXT NOT NULL,
            observed_value TEXT, last_evaluated_at TEXT, evaluation_explanation TEXT);
        CREATE TABLE thesis_revisions (
            revision_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL REFERENCES theses,
            revision_no INTEGER NOT NULL, created_at TEXT NOT NULL, author TEXT NOT NULL,
            change_note TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE TRIGGER thesis_revisions_immutable_u BEFORE UPDATE ON thesis_revisions
            BEGIN SELECT RAISE(ABORT, 'thesis_revisions is append-only'); END;
        CREATE TRIGGER thesis_revisions_immutable_d BEFORE DELETE ON thesis_revisions
            BEGIN SELECT RAISE(ABORT, 'thesis_revisions is append-only'); END;
        CREATE TABLE watchlists (
            watchlist_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE watchlist_items (
            watchlist_id TEXT NOT NULL REFERENCES watchlists, instrument_id TEXT NOT NULL,
            added_at TEXT NOT NULL, PRIMARY KEY (watchlist_id, instrument_id));
        CREATE TABLE monitoring_jobs (
            job_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, target_type TEXT NOT NULL,
            target_id TEXT NOT NULL, opt_in INTEGER NOT NULL, last_run_at TEXT, last_result TEXT);
        CREATE TABLE notifications (
            notification_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, created_at TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT, ref_type TEXT, ref_id TEXT,
            is_read INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE seed_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """,
    ),
]


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():
        val = row[key]
        if key in _JSON_COLUMNS and isinstance(val, str):
            val = json.loads(val)
        elif key == "is_fixture":
            val = bool(val)
        out[key] = val
    return out


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (json.dumps(v) if k in _JSON_COLUMNS and not isinstance(v, str) else v)
        for k, v in values.items()
    }


class ResearchStore:
    """Repository over the research SQLite database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ── plumbing ──────────────────────────────────────────────────────
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)", (version, utcnow())
                )
            conn.execute(
                "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)",
                (DEFAULT_WORKSPACE, "Local workspace", utcnow()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO users VALUES (?, ?, ?)",
                (DEFAULT_USER, DEFAULT_WORKSPACE, "Local analyst"),
            )

    def insert(self, table: str, values: dict[str, Any], *, replace: bool = False) -> None:
        enc = _encode(values)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        with self.connect() as conn:
            conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", tuple(enc.values()))

    def insert_many(self, table: str, rows: list[dict[str, Any]], *, replace: bool = True) -> None:
        if not rows:
            return
        cols = list(rows[0])
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        sql = f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        with self.connect() as conn:
            conn.executemany(sql, [tuple(_encode(r)[c] for c in cols) for r in rows])

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [_decode(r) for r in conn.execute(sql, params).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def seed_value(self, key: str) -> str | None:
        row = self.one("SELECT value FROM seed_state WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_seed_value(self, key: str, value: str) -> None:
        self.insert("seed_state", {"key": key, "value": value}, replace=True)

    # ── instruments / sources / observations ─────────────────────────
    def list_instruments(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM instruments ORDER BY symbol")

    def get_instrument(self, instrument_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM instruments WHERE instrument_id = ?", (instrument_id,))
        if row is None:
            raise NotFoundError(f"instrument {instrument_id} not found")
        return row

    def instruments_by_symbol(self, symbol: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM instruments WHERE symbol = ? OR local_symbol = ? ORDER BY data_tier",
            (symbol, symbol),
        )

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM source_documents WHERE source_id = ?", (source_id,))

    def sources_for(self, instrument_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM source_documents WHERE instrument_id = ? ORDER BY published_at DESC",
            (instrument_id,),
        )

    def observations(
        self, instrument_id: str, metric_code: str | None = None
    ) -> list[dict[str, Any]]:
        if metric_code:
            return self.query(
                "SELECT * FROM financial_observations WHERE instrument_id = ? AND metric_code = ? "
                "ORDER BY period_end",
                (instrument_id, metric_code),
            )
        return self.query(
            "SELECT * FROM financial_observations WHERE instrument_id = ? ORDER BY metric_code, period_end",
            (instrument_id,),
        )

    def latest_observation(self, instrument_id: str, metric_code: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM financial_observations WHERE instrument_id = ? AND metric_code = ? "
            "ORDER BY period_end DESC LIMIT 1",
            (instrument_id, metric_code),
        )

    def edges_touching(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not symbols:
            return []
        marks = ",".join("?" for _ in symbols)
        return self.query(
            f"SELECT * FROM supply_chain_edges WHERE src_symbol IN ({marks}) OR dst_symbol IN ({marks})",
            tuple(symbols) * 2,
        )

    def edges_from(self, symbol: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM supply_chain_edges WHERE src_symbol = ?", (symbol,))

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM supply_chain_edges WHERE edge_id = ?", (edge_id,))

    # ── authorization ─────────────────────────────────────────────────
    @staticmethod
    def _assert_owner(row: dict[str, Any], actor: Actor, kind: str) -> None:
        if row["workspace_id"] != actor.workspace_id:
            raise ForbiddenError(f"{kind} belongs to another workspace")

    # ── research sessions ────────────────────────────────────────────
    def save_session(self, actor: Actor, session: dict[str, Any], artifact: dict[str, Any]) -> None:
        now = utcnow()
        self.insert(
            "research_sessions",
            {
                "session_id": session["session_id"],
                "workspace_id": actor.workspace_id,
                "user_id": actor.user_id,
                "query": session["query"],
                "intent": session["intent"],
                "status": session["status"],
                "data_mode": session["data_mode"],
                "title": session.get("title"),
                "created_at": now,
            },
        )
        self.insert(
            "research_artifacts",
            {
                "artifact_id": new_id("art"),
                "session_id": session["session_id"],
                "kind": "ANSWER",
                "payload": artifact,
                "created_at": now,
            },
        )
        self.insert_many(
            "claim_evidence_links",
            [
                {
                    "link_id": new_id("cel"),
                    "session_id": session["session_id"],
                    "claim_id": link["claim_id"],
                    "ref_id": link["ref_id"],
                    "ref_kind": link["ref_kind"],
                    "verified": int(link["verified"]),
                }
                for link in artifact.get("claim_links", [])
            ],
            replace=False,
        )

    def get_session(self, actor: Actor, session_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,))
        if row is None:
            raise NotFoundError("research session not found")
        self._assert_owner(row, actor, "research session")
        art = self.one(
            "SELECT payload FROM research_artifacts WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        return {**row, "artifact": art["payload"] if art else None}

    def list_sessions(self, actor: Actor, limit: int = 20) -> list[dict[str, Any]]:
        return self.query(
            "SELECT session_id, query, title, intent, status, data_mode, created_at "
            "FROM research_sessions WHERE workspace_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (actor.workspace_id, limit),
        )

    # ── theses ────────────────────────────────────────────────────────
    def _thesis_row(self, thesis_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM theses WHERE thesis_id = ?", (thesis_id,))
        if row is None:
            raise NotFoundError("thesis not found")
        return row

    def get_thesis(self, actor: Actor, thesis_id: str) -> dict[str, Any]:
        row = self._thesis_row(thesis_id)
        self._assert_owner(row, actor, "thesis")
        return self._hydrate_thesis(row)

    def _hydrate_thesis(self, row: dict[str, Any]) -> dict[str, Any]:
        tid = row["thesis_id"]
        evidence = self.query(
            "SELECT e.*, s.title AS source_title, s.provider, s.doc_type, s.is_fixture "
            "FROM thesis_evidence e LEFT JOIN source_documents s ON s.source_id = e.source_id "
            "WHERE e.thesis_id = ? ORDER BY e.added_at",
            (tid,),
        )
        return {
            **row,
            "supporting_evidence": [e for e in evidence if e["stance"] == "SUPPORTING"],
            "contradicting_evidence": [e for e in evidence if e["stance"] == "CONTRADICTING"],
            "conditions": self.query(
                "SELECT * FROM thesis_conditions WHERE thesis_id = ? ORDER BY rowid", (tid,)
            ),
            "revisions": self.query(
                "SELECT revision_id, revision_no, created_at, author, change_note "
                "FROM thesis_revisions WHERE thesis_id = ? ORDER BY revision_no DESC",
                (tid,),
            ),
        }

    def list_theses(self, actor: Actor) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM theses WHERE workspace_id = ? ORDER BY updated_at DESC", (actor.workspace_id,)
        )
        statuses: dict[str, list[str]] = {}
        for c in self.query(
            "SELECT c.thesis_id, c.current_status FROM thesis_conditions c "
            "JOIN theses t ON t.thesis_id = c.thesis_id WHERE t.workspace_id = ? ORDER BY c.rowid",
            (actor.workspace_id,),
        ):
            statuses.setdefault(c["thesis_id"], []).append(c["current_status"])
        return [
            {**row, "condition_statuses": statuses.get(row["thesis_id"], []),
             "health": health_of(statuses.get(row["thesis_id"], []))}
            for row in rows
        ]

    def create_thesis(self, actor: Actor, data: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        thesis_id = data.get("thesis_id") or new_id("th")
        session_ids = [data["session_id"]] if data.get("session_id") else []
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO theses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    thesis_id, actor.workspace_id, actor.user_id, data["title"],
                    json.dumps(data["instrument_ids"]), now, now, None, data.get("horizon"),
                    data.get("conviction_label", "EXPLORATORY"), data.get("summary", ""),
                    json.dumps(data.get("assumptions", [])), json.dumps(data.get("catalysts", [])),
                    json.dumps(data.get("open_questions", [])), json.dumps(session_ids),
                ),
            )
            for ev in data.get("evidence", []):
                conn.execute(
                    "INSERT INTO thesis_evidence VALUES (?,?,?,?,?,?)",
                    (new_id("ev"), thesis_id, ev["source_id"], ev["stance"], ev.get("note", ""), now),
                )
            for cond in data.get("conditions", []):
                conn.execute(
                    "INSERT INTO thesis_conditions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("cond"), thesis_id, cond["metric_code"], cond["instrument_id"],
                        cond["operator"], cond.get("threshold_value"), cond["description"],
                        "UNKNOWN_MISSING_DATA", None, None, "Not yet evaluated.",
                    ),
                )
        self.append_revision(actor, thesis_id, "Created")
        return self.get_thesis(actor, thesis_id)

    def update_thesis(self, actor: Actor, thesis_id: str, changes: dict[str, Any], note: str) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        if changes:
            enc = _encode({**changes, "updated_at": utcnow()})
            sets = ", ".join(f"{k} = ?" for k in enc)
            with self.connect() as conn:
                conn.execute(f"UPDATE theses SET {sets} WHERE thesis_id = ?", (*enc.values(), thesis_id))
        self.append_revision(actor, thesis_id, note)
        return self.get_thesis(actor, thesis_id)

    def add_evidence(self, actor: Actor, thesis_id: str, ev: dict[str, Any]) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        if self.get_source(ev["source_id"]) is None:
            raise NotFoundError("source document not found")
        self.insert(
            "thesis_evidence",
            {
                "evidence_id": new_id("ev"), "thesis_id": thesis_id, "source_id": ev["source_id"],
                "stance": ev["stance"], "note": ev.get("note", ""), "added_at": utcnow(),
            },
        )
        return self.update_thesis(actor, thesis_id, {}, f"Added {ev['stance'].lower()} evidence")

    def remove_evidence(self, actor: Actor, thesis_id: str, evidence_id: str) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM thesis_evidence WHERE evidence_id = ? AND thesis_id = ?",
                (evidence_id, thesis_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("evidence link not found")
        return self.update_thesis(actor, thesis_id, {}, "Removed evidence link")

    def add_condition(self, actor: Actor, thesis_id: str, cond: dict[str, Any]) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        self.insert(
            "thesis_conditions",
            {
                "condition_id": new_id("cond"), "thesis_id": thesis_id,
                "metric_code": cond["metric_code"], "instrument_id": cond["instrument_id"],
                "operator": cond["operator"], "threshold_value": cond.get("threshold_value"),
                "description": cond["description"], "current_status": "UNKNOWN_MISSING_DATA",
                "observed_value": None, "last_evaluated_at": None,
                "evaluation_explanation": "Not yet evaluated.",
            },
        )
        return self.update_thesis(actor, thesis_id, {}, f"Added condition: {cond['description']}")

    def remove_condition(self, actor: Actor, thesis_id: str, condition_id: str) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM thesis_conditions WHERE condition_id = ? AND thesis_id = ?",
                (condition_id, thesis_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("condition not found")
        return self.update_thesis(actor, thesis_id, {}, "Removed condition")

    def write_condition_results(self, thesis_id: str, results: list[dict[str, Any]]) -> None:
        now = utcnow()
        with self.connect() as conn:
            for r in results:
                conn.execute(
                    "UPDATE thesis_conditions SET current_status = ?, observed_value = ?, "
                    "last_evaluated_at = ?, evaluation_explanation = ? WHERE condition_id = ?",
                    (r["status"], r.get("observed_value"), now, r["explanation"], r["condition_id"]),
                )
            conn.execute("UPDATE theses SET last_reviewed_at = ? WHERE thesis_id = ?", (now, thesis_id))

    def append_revision(self, actor: Actor, thesis_id: str, note: str) -> None:
        row = self._thesis_row(thesis_id)
        snapshot = self._hydrate_thesis(row)
        snapshot.pop("revisions", None)
        with self.connect() as conn:
            nxt = conn.execute(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM thesis_revisions WHERE thesis_id = ?",
                (thesis_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO thesis_revisions VALUES (?,?,?,?,?,?,?)",
                (new_id("rev"), thesis_id, nxt, utcnow(), actor.user_id, note, json.dumps(snapshot)),
            )

    def get_revision(self, actor: Actor, thesis_id: str, revision_no: int) -> dict[str, Any]:
        self._assert_owner(self._thesis_row(thesis_id), actor, "thesis")
        row = self.one(
            "SELECT * FROM thesis_revisions WHERE thesis_id = ? AND revision_no = ?",
            (thesis_id, revision_no),
        )
        if row is None:
            raise NotFoundError("revision not found")
        return row

    # ── watchlists / monitoring / notifications ──────────────────────
    def list_watchlists(self, actor: Actor) -> list[dict[str, Any]]:
        lists = self.query(
            "SELECT * FROM watchlists WHERE workspace_id = ? ORDER BY created_at", (actor.workspace_id,)
        )
        for wl in lists:
            wl["items"] = self.query(
                "SELECT w.instrument_id, w.added_at, i.symbol, i.name, i.last_bar, i.data_tier "
                "FROM watchlist_items w LEFT JOIN instruments i ON i.instrument_id = w.instrument_id "
                "WHERE w.watchlist_id = ? ORDER BY w.added_at",
                (wl["watchlist_id"],),
            )
        return lists

    def _watchlist_row(self, actor: Actor, watchlist_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM watchlists WHERE watchlist_id = ?", (watchlist_id,))
        if row is None:
            raise NotFoundError("watchlist not found")
        self._assert_owner(row, actor, "watchlist")
        return row

    def create_watchlist(self, actor: Actor, name: str, instrument_ids: list[str]) -> str:
        for iid in instrument_ids:  # validate first so a bad id never leaves an empty orphan list
            self.get_instrument(iid)
        wid = new_id("wl")
        self.insert(
            "watchlists",
            {"watchlist_id": wid, "workspace_id": actor.workspace_id, "user_id": actor.user_id,
             "name": name, "created_at": utcnow()},
        )
        for iid in instrument_ids:
            self.add_watchlist_item(actor, wid, iid)
        return wid

    def add_watchlist_item(self, actor: Actor, watchlist_id: str, instrument_id: str) -> None:
        self._watchlist_row(actor, watchlist_id)
        self.get_instrument(instrument_id)
        self.insert(
            "watchlist_items",
            {"watchlist_id": watchlist_id, "instrument_id": instrument_id, "added_at": utcnow()},
            replace=True,
        )

    def remove_watchlist_item(self, actor: Actor, watchlist_id: str, instrument_id: str) -> None:
        self._watchlist_row(actor, watchlist_id)
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id = ? AND instrument_id = ?",
                (watchlist_id, instrument_id),
            )

    def record_monitoring_run(self, actor: Actor, target_type: str, target_id: str, result: Any) -> None:
        existing = self.one(
            "SELECT job_id FROM monitoring_jobs WHERE workspace_id = ? AND target_type = ? AND target_id = ?",
            (actor.workspace_id, target_type, target_id),
        )
        self.insert(
            "monitoring_jobs",
            {
                "job_id": existing["job_id"] if existing else new_id("job"),
                "workspace_id": actor.workspace_id, "target_type": target_type,
                "target_id": target_id, "opt_in": 1, "last_run_at": utcnow(), "last_result": result,
            },
            replace=True,
        )

    def notify(self, actor: Actor, kind: str, title: str, body: str, ref_type: str, ref_id: str) -> None:
        self.insert(
            "notifications",
            {
                "notification_id": new_id("ntf"), "workspace_id": actor.workspace_id,
                "created_at": utcnow(), "kind": kind, "title": title, "body": body,
                "ref_type": ref_type, "ref_id": ref_id, "is_read": 0,
            },
        )

    def list_notifications(self, actor: Actor, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM notifications WHERE workspace_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (actor.workspace_id, limit),
        )


def health_of(statuses: list[str]) -> str:
    """Roll condition statuses up to one thesis health label."""
    if "TRIGGERED" in statuses:
        return "TRIGGERED"
    if not statuses or "UNKNOWN_MISSING_DATA" in statuses:
        return "UNKNOWN"
    return "HEALTHY"
