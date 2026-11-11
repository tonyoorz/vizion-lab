from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DUPLICATE_SEARCH_SOURCE_STATE_TABLE = "duplicate_search_source_state"
DUPLICATE_SEARCH_SOURCE_STATE_KEY = "octane_defects"

DUPLICATE_SEARCH_RELEVANT_DEFECT_COLUMNS = (
    "name",
    "description",
    "comments",
    "project",
    "pu",
    "software_version",
    "status_phase",
    "assigned_ecu",
    "lead_model",
    "detected_in_release",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_duplicate_search_source_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DUPLICATE_SEARCH_SOURCE_STATE_TABLE} (
            store_name TEXT PRIMARY KEY,
            source_revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {DUPLICATE_SEARCH_SOURCE_STATE_TABLE}(
            store_name, source_revision, updated_at
        ) VALUES (?, 0, ?)
        """,
        (DUPLICATE_SEARCH_SOURCE_STATE_KEY, _utc_now()),
    )


def advance_duplicate_search_source_revision(conn: sqlite3.Connection) -> int:
    ensure_duplicate_search_source_state(conn)
    conn.execute(
        f"""
        UPDATE {DUPLICATE_SEARCH_SOURCE_STATE_TABLE}
        SET source_revision = source_revision + 1,
            updated_at = ?
        WHERE store_name = ?
        """,
        (_utc_now(), DUPLICATE_SEARCH_SOURCE_STATE_KEY),
    )
    row = conn.execute(
        f"""
        SELECT source_revision
        FROM {DUPLICATE_SEARCH_SOURCE_STATE_TABLE}
        WHERE store_name = ?
        """,
        (DUPLICATE_SEARCH_SOURCE_STATE_KEY,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def read_duplicate_search_source_revision(db_path: Path | str) -> int | None:
    resolved_path = Path(db_path).resolve()
    if not resolved_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"{resolved_path.as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                f"""
                SELECT source_revision
                FROM {DUPLICATE_SEARCH_SOURCE_STATE_TABLE}
                WHERE store_name = ?
                """,
                (DUPLICATE_SEARCH_SOURCE_STATE_KEY,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    return int(row[0] or 0) if row else None