from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests

from backend.analytics.ingest import pipeline as ingest_pipeline
from backend.analytics.ingest.client import OctaneApiClient
from backend.analytics.ingest.pipeline import IngestRequest, refresh_octane_manual_runs_only, refresh_octane_source
from backend.analytics.ingest.source_store import OctaneSourceStore


class FakeOctaneClient:
    def __init__(self) -> None:
        self.defect_calls: list[dict[str, object]] = []
        self.manual_run_calls: list[dict[str, object]] = []
        self.comment_calls: list[list[str]] = []
        self.history_calls: list[list[str]] = []
        self.history_access_checks = 0

    def check_history_access(self) -> None:
        self.history_access_checks += 1

    def list_teams(self) -> list[dict[str, str]]:
        return [
            {"id": "1", "name": "DTSV_China"},
            {"id": "2", "name": "[AT]CoC_EI_IuK"},
        ]

    def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
        self.defect_calls.append({"team_id": team_id, "year": year, "modified_since": modified_since})
        return [
            {
                "id": f"D-{team_id}-{year}",
                "name": "Wake defect",
                "last_modified": f"{year}-01-02T00:00:00Z",
                "phase": {"name": "06"},
            }
        ]

    def fetch_comments_for_defects(self, defect_ids: list[str]) -> list[dict[str, object]]:
        self.comment_calls.append(list(defect_ids))
        return [{"defect_id": defect_ids[0], "text": "comment one"}]

    def fetch_history(self, *, defect_id: str) -> dict[str, object]:
        self.history_calls.append([defect_id])
        return {
            "data": [
                {
                    "timestamp": "2026-01-02T00:00:00Z",
                    "change_set": [
                        {
                            "field_name": "phase",
                            "old_value": "08",
                            "value": "06",
                        }
                    ],
                }
            ]
        }

    def fetch_histories_for_defects(
        self,
        defect_ids: list[str],
        *,
        max_workers: int = 1,
        modified_since_by_defect: dict[str, str | None] | None = None,
    ) -> dict[str, dict[str, object]]:
        self.history_calls.append(list(defect_ids))
        return {defect_id: self.fetch_history(defect_id=defect_id) for defect_id in defect_ids}

    def fetch_manual_runs(
        self,
        *,
        team_id: str,
        year: int,
        modified_since: str | None = None,
        include_related_work_items: bool = True,
        releases: tuple[str, ...] = (),
        related_work_item_workers: int = 1,
        progress=None,
    ) -> list[dict[str, object]]:
        self.manual_run_calls.append(
            {
                "team_id": team_id,
                "year": year,
                "modified_since": modified_since,
                "include_related_work_items": include_related_work_items,
                "releases": releases,
                "related_work_item_workers": related_work_item_workers,
            }
        )
        if progress is not None:
            progress(f"manual-runs page year={year}")
        row = {
            "id": f"MR-{team_id}-{year}",
            "defect": {"id": f"D-{team_id}-{year}"},
            "test": {"id": f"T-{team_id}-{year}"},
            "test_name": "Wake test",
            "status": "Passed",
            "release": {"name": f"R-{str(year)[-2:]}-01"},
        }
        if include_related_work_items:
            row["related_work_items"] = [
                {
                    "id": f"F-{team_id}-{year}",
                    "name": "Wake feature",
                    "subtype": "feature",
                    "path": "epic/wake-feature",
                },
                {
                    "id": f"S-{team_id}-{year}",
                    "name": "Wake story",
                    "subtype": "story",
                    "path": "epic/wake-feature/wake-story",
                },
            ]
        return [
            {
                **row,
            }
        ]


def test_refresh_octane_source_checks_history_access_before_opening_source(tmp_path: Path) -> None:
    class ForbiddenHistoryClient(FakeOctaneClient):
        def check_history_access(self) -> None:
            self.history_access_checks += 1
            response = requests.Response()
            response.status_code = 403
            response.url = "https://octane.example.com/api/shared_spaces/1002/workspaces/2001/history_logs"
            response.raise_for_status()

    client = ForbiddenHistoryClient()
    db_path = tmp_path / "source" / "qgate_raw.db"

    with pytest.raises(requests.HTTPError, match="403"):
        refresh_octane_source(
            request=IngestRequest(
                source_db_path=db_path,
                teams=("all",),
                years=(2026,),
                include_history=True,
                include_comments=True,
                include_testing=False,
            ),
            client=client,
        )

    assert client.history_access_checks == 1
    assert client.defect_calls == []
    assert client.comment_calls == []
    assert not db_path.exists()


def test_refresh_octane_source_skips_history_access_check_when_history_is_disabled(tmp_path: Path) -> None:
    client = FakeOctaneClient()

    refresh_octane_source(
        request=IngestRequest(
            source_db_path=tmp_path / "qgate_raw.db",
            teams=("DTSV_China",),
            years=(2026,),
            include_history=False,
            include_comments=False,
            include_testing=False,
        ),
        client=client,
    )

    assert client.history_access_checks == 0
    assert len(client.defect_calls) == 1


def test_refresh_octane_source_writes_all_core_entities(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    summary = refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("all",),
            years=(2026,),
            include_history=True,
            include_comments=True,
            include_testing=True,
        ),
        client=FakeOctaneClient(),
    )

    assert summary == {
        "defect_rows": 2,
        "defect_ids": ["D-1-2026", "D-2-2026"],
        "comment_refreshed_defects": 2,
        "comment_reused_defects": 0,
        "history_queued_defects": 2,
        "history_skipped_defects": 0,
        "history_event_rows": 2,
        "manual_run_rows": 2,
        "manual_run_ids": ["MR-1-2026", "MR-2-2026"],
        "testcase_rows": 2,
        "testcase_relation_rows": 6,
    }

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM octane_defects").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM octane_defect_history_events").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM octane_manual_runs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM octane_testcases").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM octane_testcase_relations").fetchone()[0] == 6

        testcase_row = conn.execute(
            "SELECT scope_team, scope_release, defect_ids_json, feature_ids_json, story_ids_json FROM octane_testcases WHERE test_id = ?",
            ("T-1-2026",),
        ).fetchone()
        assert testcase_row[0] == "DTSV_China"
        assert testcase_row[1] == "R-26-01"
        assert json.loads(testcase_row[2]) == ["D-1-2026"]
        assert json.loads(testcase_row[3]) == ["F-1-2026"]
        assert json.loads(testcase_row[4]) == ["S-1-2026"]

        relation_types = conn.execute(
            "SELECT relation_type, related_id FROM octane_testcase_relations WHERE test_id = ? ORDER BY relation_type, related_id",
            ("T-1-2026",),
        ).fetchall()
        assert relation_types == [
            ("defect", "D-1-2026"),
            ("feature", "F-1-2026"),
            ("story", "S-1-2026"),
        ]
    finally:
        conn.close()


def test_refresh_octane_source_passes_defect_modified_since_watermark(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                fvp TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                problem_finder_team TEXT,
                year TEXT,
                last_modified TEXT
            );
            INSERT INTO octane_defects(
                defect_id, name, raw_json, fetched_at, problem_finder_team, year, last_modified
            ) VALUES (
                'D-EXISTING', 'Existing', '{}', '2026-06-12T00:00:00Z', 'DTSV_China', '2026', '2026-06-12T12:00:00Z'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = FakeOctaneClient()
    refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=False,
            include_comments=False,
            include_testing=False,
        ),
        client=client,
    )

    assert client.defect_calls == [
        {"team_id": "1", "year": 2026, "modified_since": "2026-06-09T12:00:00Z"}
    ]


def test_incremental_defect_watermarks_batch_teams_with_one_indexed_query(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    store = OctaneSourceStore(db_path)
    try:
        store.create_tables()
    finally:
        store.close()

    connection = sqlite3.connect(db_path)
    try:
        connection.executemany(
            """
            INSERT INTO octane_defects(
                defect_id, problem_finder_team, team, year, last_modified, fetched_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, '{}')
            """,
            [
                ("D-1", " DTSV_China ", "Other", "2026", "2026-06-12T12:00:00Z", "2026-06-13T00:00:00Z"),
                ("D-2", "", " [AT]CoC_EI_IuK ", "2026", "", "2026-06-10T09:00:00Z"),
                ("D-3", None, "DTSV_China", "2025", None, "2026-06-09T08:00:00Z"),
                ("D-4", "Other", "DTSV_China", "2026", "2099-01-01T00:00:00Z", "2026-06-13T00:00:00Z"),
                ("D-5", "DTSV_China", "DTSV_China", "2024", "2099-01-01T00:00:00Z", "2026-06-13T00:00:00Z"),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    statements: list[str] = []
    original_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(ingest_pipeline.sqlite3, "connect", tracked_connect)

    watermarks = ingest_pipeline._load_incremental_defect_since_by_team(
        source_db_path=db_path,
        team_names=("DTSV_China", "[AT]CoC_EI_IuK", "Missing"),
        years=(2025, 2026),
    )

    assert watermarks == {
        "DTSV_China": {2025: "2026-06-06T08:00:00Z", 2026: "2026-06-09T12:00:00Z"},
        "[AT]CoC_EI_IuK": {2025: None, 2026: "2026-06-07T09:00:00Z"},
        "Missing": {2025: None, 2026: None},
    }
    watermark_selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "FROM OCTANE_DEFECTS" in statement.upper()
    ]
    assert len(watermark_selects) == 1

    connection = original_connect(db_path)
    try:
        query_plan = connection.execute("EXPLAIN QUERY PLAN " + watermark_selects[0]).fetchall()
    finally:
        connection.close()

    assert any("idx_octane_defects_incremental_watermark" in str(row[3]) for row in query_plan)
    assert all("SCAN octane_defects" not in str(row[3]) for row in query_plan)


def test_refresh_octane_source_loads_all_team_watermarks_once(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    db_path = tmp_path / "qgate_raw.db"

    def load_watermarks(*, source_db_path, team_names, years):
        calls.append({"source_db_path": source_db_path, "team_names": team_names, "years": years})
        return {
            "DTSV_China": {2026: "2026-06-09T12:00:00Z"},
            "[AT]CoC_EI_IuK": {2026: None},
        }

    monkeypatch.setattr(ingest_pipeline, "_load_incremental_defect_since_by_team", load_watermarks, raising=False)
    client = FakeOctaneClient()

    refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("all",),
            years=(2026,),
            include_history=False,
            include_comments=False,
            include_testing=False,
        ),
        client=client,
    )

    assert calls == [{
        "source_db_path": db_path,
        "team_names": ("DTSV_China", "[AT]CoC_EI_IuK"),
        "years": (2026,),
    }]
    assert client.defect_calls == [
        {"team_id": "1", "year": 2026, "modified_since": "2026-06-09T12:00:00Z"},
        {"team_id": "2", "year": 2026, "modified_since": None},
    ]


def test_refresh_octane_source_force_defect_refresh_ignores_watermark(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                fvp TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                problem_finder_team TEXT,
                year TEXT,
                last_modified TEXT
            );
            INSERT INTO octane_defects(
                defect_id, name, raw_json, fetched_at, problem_finder_team, year, last_modified
            ) VALUES (
                'D-EXISTING', 'Existing', '{}', '2026-06-12T00:00:00Z', 'DTSV_China', '2026', '2026-06-12T12:00:00Z'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = FakeOctaneClient()
    refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=False,
            include_comments=False,
            include_testing=False,
            force_defect_refresh=True,
        ),
        client=client,
    )

    assert client.defect_calls == [
        {"team_id": "1", "year": 2026, "modified_since": None}
    ]


def test_refresh_octane_source_fetches_teams_with_limited_parallelism(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    class ConcurrentDefectClient(FakeOctaneClient):
        def __init__(self) -> None:
            super().__init__()
            self._lock = threading.Lock()
            self._active_fetches = 0
            self.max_active_fetches = 0

        def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
            with self._lock:
                self._active_fetches += 1
                self.max_active_fetches = max(self.max_active_fetches, self._active_fetches)
            try:
                time.sleep(0.05)
                return super().fetch_defects(team_id=team_id, year=year, modified_since=modified_since)
            finally:
                with self._lock:
                    self._active_fetches -= 1

    client = ConcurrentDefectClient()
    summary = refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("all",),
            years=(2026,),
            include_history=False,
            include_comments=False,
            include_testing=False,
            team_max_workers=2,
        ),
        client=client,
    )

    assert client.max_active_fetches == 2
    assert summary["defect_rows"] == 2


def test_refresh_octane_source_fetches_only_stale_histories(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    class HistoryIncrementalClient(FakeOctaneClient):
        def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
            return [
                {"id": "D-FRESH", "name": "Fresh", "last_modified": "2026-06-10T00:00:00Z", "phase": {"name": "06"}},
                {"id": "D-STALE", "name": "Stale", "last_modified": "2026-06-12T00:00:00Z", "phase": {"name": "06"}},
                {"id": "D-MISSING", "name": "Missing", "last_modified": "2026-06-13T00:00:00Z", "phase": {"name": "06"}},
            ]

        def fetch_histories_for_defects(
            self,
            defect_ids: list[str],
            *,
            max_workers: int = 1,
            modified_since_by_defect: dict[str, str | None] | None = None,
        ) -> dict[str, dict[str, object]]:
            self.history_calls.append(list(defect_ids))
            return {
                defect_id: {
                    "data": [
                        {
                            "timestamp": "2026-06-14T00:00:00Z",
                            "change_set": [{"field_name": "phase", "old_value": "08", "value": "06"}],
                        }
                    ]
                }
                for defect_id in defect_ids
            }

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                fvp TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                comments TEXT,
                last_modified TEXT
            );
            CREATE TABLE octane_defect_history_events (
                defect_id TEXT NOT NULL,
                event_timestamp TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                fetched_at TEXT NOT NULL
            );
            INSERT INTO octane_defects(defect_id, name, raw_json, fetched_at, last_modified)
            VALUES
                ('D-FRESH', 'old fresh', '{}', '2026-06-10T01:00:00Z', '2026-06-10T00:00:00Z'),
                ('D-STALE', 'old stale', '{}', '2026-06-10T01:00:00Z', '2026-06-10T00:00:00Z');
            INSERT INTO octane_defect_history_events(defect_id, event_timestamp, field_name, old_value, new_value, fetched_at)
            VALUES
                ('D-FRESH', '2026-06-10T00:00:00Z', 'phase', '08', '06', '2026-06-11T00:00:00Z'),
                ('D-STALE', '2026-06-10T00:00:00Z', 'phase', '08', '06', '2026-06-11T00:00:00Z');
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = HistoryIncrementalClient()
    summary = refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=True,
            include_comments=False,
            include_testing=False,
        ),
        client=client,
    )

    assert client.history_calls == [["D-MISSING", "D-STALE"]]
    assert summary["history_queued_defects"] == 2
    assert summary["history_skipped_defects"] == 1
    assert summary["history_event_rows"] == 2


def test_refresh_octane_source_fetches_incremental_history_window(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    class IncrementalHistoryClient(FakeOctaneClient):
        def __init__(self) -> None:
            super().__init__()
            self.history_since_calls: list[dict[str, str | None]] = []

        def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
            return [
                {
                    "id": "D-STALE",
                    "name": "Stale defect",
                    "last_modified": "2026-06-12T00:00:00Z",
                    "phase": {"name": "06"},
                }
            ]

        def fetch_histories_for_defects(
            self,
            defect_ids: list[str],
            *,
            max_workers: int = 1,
            modified_since_by_defect: dict[str, str | None] | None = None,
        ) -> dict[str, dict[str, object]]:
            self.history_calls.append(list(defect_ids))
            self.history_since_calls.append(dict(modified_since_by_defect or {}))
            return {
                "D-STALE": {
                    "data": [
                        {
                            "timestamp": "2026-06-12T00:00:00Z",
                            "change_set": [{"field_name": "phase", "old_value": "08", "value": "06"}],
                        },
                    ]
                }
            }

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                fvp TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                comments TEXT,
                last_modified TEXT
            );
            CREATE TABLE octane_defect_history_events (
                defect_id TEXT NOT NULL,
                event_timestamp TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                fetched_at TEXT NOT NULL,
                action TEXT,
                user_name TEXT,
                team TEXT,
                entry_index INTEGER,
                change_index INTEGER,
                old_value_text TEXT,
                new_value_text TEXT,
                UNIQUE(defect_id, entry_index, change_index)
            );
            INSERT INTO octane_defects(defect_id, name, raw_json, fetched_at, last_modified)
            VALUES ('D-STALE', 'old stale', '{}', '2026-06-10T01:00:00Z', '2026-06-10T00:00:00Z');
            INSERT INTO octane_defect_history_events(
                defect_id, event_timestamp, field_name, old_value, new_value, fetched_at, entry_index, change_index
            ) VALUES ('D-STALE', '2026-06-10T00:00:00Z', 'phase', '10', '08', '2026-06-10T01:00:00Z', 0, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = IncrementalHistoryClient()
    summary = refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=True,
            include_comments=False,
            include_testing=False,
        ),
        client=client,
    )

    assert client.history_calls == [["D-STALE"]]
    assert client.history_since_calls == [{"D-STALE": "2026-06-10T00:00:00Z"}]
    assert summary["history_event_rows"] == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT event_timestamp, old_value, new_value
            FROM octane_defect_history_events
            WHERE defect_id = 'D-STALE'
            ORDER BY event_timestamp
            """
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("2026-06-10T00:00:00Z", "10", "08"),
        ("2026-06-12T00:00:00Z", "08", "06"),
    ]


def test_refresh_octane_source_reuses_comments_for_unchanged_defects(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    class CommentIncrementalClient(FakeOctaneClient):
        def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
            return [
                {
                    "id": "D-UNCHANGED",
                    "name": "Unchanged defect",
                    "last_modified": "2026-06-10T00:00:00Z",
                    "phase": {"name": "06"},
                },
                {
                    "id": "D-CHANGED",
                    "name": "Changed defect",
                    "last_modified": "2026-06-12T00:00:00Z",
                    "phase": {"name": "06"},
                },
            ]

        def fetch_comments_for_defects(self, defect_ids: list[str]) -> list[dict[str, object]]:
            self.comment_calls.append(list(defect_ids))
            return [{"defect_id": defect_id, "text": f"fresh {defect_id}"} for defect_id in defect_ids]

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                fvp TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                comments TEXT,
                last_modified TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO octane_defects(defect_id, name, raw_json, fetched_at, comments, last_modified) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "D-UNCHANGED",
                    "old unchanged",
                    "{}",
                    "2026-06-10T01:00:00Z",
                    json.dumps([{"text": "cached unchanged"}], ensure_ascii=False),
                    "2026-06-10T00:00:00Z",
                ),
                (
                    "D-CHANGED",
                    "old changed",
                    "{}",
                    "2026-06-10T01:00:00Z",
                    json.dumps([{"text": "stale changed"}], ensure_ascii=False),
                    "2026-06-10T00:00:00Z",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    client = CommentIncrementalClient()
    summary = refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=False,
            include_comments=True,
            include_testing=False,
        ),
        client=client,
    )

    assert client.comment_calls == [["D-CHANGED"]]
    assert summary["comment_refreshed_defects"] == 1
    assert summary["comment_reused_defects"] == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = dict(conn.execute("SELECT defect_id, comments FROM octane_defects ORDER BY defect_id").fetchall())
    finally:
        conn.close()

    assert "fresh D-CHANGED" in rows["D-CHANGED"]
    assert "cached unchanged" in rows["D-UNCHANGED"]


def test_comment_refresh_state_updates_canonical_ticket_comments(tmp_path: Path) -> None:
    from backend.analytics.ingest.source_store import OctaneSourceStore

    database_path = tmp_path / "comments.db"
    store = OctaneSourceStore(database_path)
    store.create_tables()
    try:
        store.upsert_defects(
            [{"id": "D-CACHE", "name": "Fixture", "last_modified": "2026-10-01T00:00:00Z"}],
            team="fixture",
            year=2026,
        )
        comments = [{"id": "C-1", "text": "New evidence"}]
        store.record_comment_refresh_state(
            [("D-CACHE", "2026-10-01T00:00:00Z", json.dumps(comments))]
        )
        store.commit()
    finally:
        store.close()
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT comments, raw_json FROM octane_defects WHERE defect_id='D-CACHE'"
        ).fetchone()
        assert json.loads(row[0]) == comments
        assert json.loads(row[1])["comments"] == comments
    finally:
        connection.close()


def test_defect_refresh_without_comments_preserves_existing_evidence(tmp_path: Path) -> None:
    from backend.analytics.ingest.source_store import OctaneSourceStore

    database_path = tmp_path / "preserve.db"
    store = OctaneSourceStore(database_path)
    store.create_tables()
    comments = [{"id": "C-1", "text": "Recorded resolution"}]
    try:
        store.upsert_defects(
            [{"id": "D-PRESERVE", "name": "Before", "comments": comments}],
            team="fixture", year=2026,
        )
        store.upsert_defects(
            [{"id": "D-PRESERVE", "name": "After"}],
            team="fixture", year=2026,
        )
        store.commit()
    finally:
        store.close()
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT comments, raw_json FROM octane_defects WHERE defect_id='D-PRESERVE'"
        ).fetchone()
        assert json.loads(row[0]) == comments
        assert json.loads(row[1])["comments"] == comments
    finally:
        connection.close()


def test_exact_id_refresh_requires_all_sources_before_applying(tmp_path: Path) -> None:
    import pytest
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids

    class ScopedClient(FakeOctaneClient):
        def fetch_rows(self, endpoint, *, fields, query=None, limit=1000):
            return [{"id": "2531406", "name": "Fixture", "last_modified": "2026-10-20T00:00:00Z", "phase": {"name": "05-In Testing"}}]

        def fetch_comments_for_defects(self, defect_ids):
            return [{"id": "C-1", "defect_id": "2531406", "text": "Fix in testing"}]

        def fetch_histories_for_defects(self, defect_ids, **kwargs):
            return {}

    database_path = tmp_path / "scoped.db"
    with pytest.raises(ValueError, match="history coverage"):
        refresh_octane_tickets_by_ids(
            source_db_path=database_path, ticket_ids=["2531406"],
            client=ScopedClient(), evidence_directory=tmp_path / "evidence",
        )
    if database_path.exists():
        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT count(*) FROM octane_defects").fetchone()[0] == 0
        finally:
            connection.close()


def test_exact_id_refresh_verifies_canonical_comments_and_empty_history(tmp_path: Path) -> None:
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids

    class ScopedClient(FakeOctaneClient):
        def fetch_rows(self, endpoint, *, fields, query=None, limit=1000):
            return [{"id": "2531406", "name": "Fixture", "last_modified": "2026-10-20T00:00:00Z", "phase": {"name": "05-In Testing"}}]

        def fetch_comments_for_defects(self, defect_ids):
            return [{"id": "C-1", "defect_id": "2531406", "text": "Fix in testing"}]

        def fetch_histories_for_defects(self, defect_ids, **kwargs):
            return {"2531406": {"data": [], "total_count": 0}}

    database_path = tmp_path / "scoped.db"
    summary = refresh_octane_tickets_by_ids(
        source_db_path=database_path, ticket_ids=["2531406"],
        client=ScopedClient(), evidence_directory=tmp_path / "evidence",
    )
    assert summary["verified_ticket_count"] == 1
    connection = sqlite3.connect(database_path)
    try:
        comments, raw_json = connection.execute("SELECT comments, raw_json FROM octane_defects").fetchone()
        assert len(json.loads(comments)) == 1
        assert json.loads(raw_json)["comments"] == json.loads(comments)
        state = connection.execute("SELECT comments_count, history_count FROM octane_ticket_evidence_refresh_state").fetchone()
        assert state == (1, 0)
    finally:
        connection.close()


def test_exact_id_refresh_reports_unavailable_without_deleting_local_evidence(tmp_path: Path) -> None:
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids
    from backend.analytics.ingest.source_store import OctaneSourceStore

    class UnavailableClient(FakeOctaneClient):
        def fetch_rows(self, endpoint, **kwargs):
            return []

    database_path = tmp_path / "unavailable.db"
    store = OctaneSourceStore(database_path)
    store.create_tables()
    store.upsert_defects(
        [{"id": "2111699", "name": "Historical", "comments": [{"text": "retained"}]}],
        team="fixture", year=2026,
    )
    store.close()
    summary = refresh_octane_tickets_by_ids(
        source_db_path=database_path, ticket_ids=["2111699"],
        client=UnavailableClient(), evidence_directory=tmp_path / "evidence",
        allow_unavailable=True, expected_unavailable_ids=["2111699"],
    )
    assert summary["verified_ticket_count"] == 0
    assert summary["unavailable_ticket_ids"] == ["2111699"]
    connection = sqlite3.connect(database_path)
    try:
        assert json.loads(connection.execute("SELECT comments FROM octane_defects").fetchone()[0]) == [{"text": "retained"}]
    finally:
        connection.close()


def test_exact_id_refresh_rejects_unlisted_unavailable_ids(tmp_path: Path) -> None:
    import pytest
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids

    class MissingClient(FakeOctaneClient):
        def fetch_rows(self, endpoint, **kwargs):
            return []

    with pytest.raises(ValueError, match="Unapproved unavailable"):
        refresh_octane_tickets_by_ids(
            source_db_path=tmp_path / "missing.db", ticket_ids=["D-APPROVED", "D-UNEXPECTED"],
            client=MissingClient(), evidence_directory=tmp_path / "evidence",
            allow_unavailable=True, expected_unavailable_ids=["D-APPROVED"],
        )


def test_exact_id_refresh_rejects_unlisted_cached_unavailable_ids(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from hashlib import sha256
    import pytest
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids

    ticket_ids = ["D-APPROVED", "D-UNEXPECTED"]
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    batch_key = sha256(json.dumps(ticket_ids).encode("utf-8")).hexdigest()[:16]
    snapshot = {
        "ticket_ids": ticket_ids, "unavailable_ticket_ids": ticket_ids,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "defects": [], "comments": [], "histories": {},
    }
    (evidence / f"batch_00000_{batch_key}.json").write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="Unapproved unavailable"):
        refresh_octane_tickets_by_ids(
            source_db_path=tmp_path / "cached.db", ticket_ids=ticket_ids,
            client=FakeOctaneClient(), evidence_directory=evidence,
            allow_unavailable=True, expected_unavailable_ids=["D-APPROVED"],
        )


def test_refresh_octane_source_reuses_empty_comment_refresh_state(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"

    class EmptyCommentClient(FakeOctaneClient):
        def fetch_defects(self, *, team_id: str, year: int, modified_since: str | None = None) -> list[dict[str, object]]:
            return [
                {
                    "id": "D-EMPTY",
                    "name": "Empty comment defect",
                    "last_modified": "2026-06-10T00:00:00Z",
                    "phase": {"name": "06"},
                }
            ]

        def fetch_comments_for_defects(self, defect_ids: list[str]) -> list[dict[str, object]]:
            self.comment_calls.append(list(defect_ids))
            return []

    client = EmptyCommentClient()
    for _run in range(2):
        refresh_octane_source(
            request=IngestRequest(
                source_db_path=db_path,
                teams=("DTSV_China",),
                years=(2026,),
                include_history=False,
                include_comments=True,
                include_testing=False,
            ),
            client=client,
        )

    assert client.comment_calls == [["D-EMPTY"]]


def test_refresh_octane_source_updates_legacy_comment_state_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE octane_defect_comment_refresh_state (
                defect_id TEXT PRIMARY KEY,
                last_defect_modified TEXT NOT NULL,
                last_synced_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = FakeOctaneClient()
    refresh_octane_source(
        request=IngestRequest(
            source_db_path=db_path,
            teams=("DTSV_China",),
            years=(2026,),
            include_history=False,
            include_comments=True,
            include_testing=False,
        ),
        client=client,
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT last_defect_modified, last_synced_at, defect_last_modified, refreshed_at, comments_json
            FROM octane_defect_comment_refresh_state
            WHERE defect_id = ?
            """,
            ("D-1-2026",),
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == row[2]
    assert row[1] == row[3]
    assert "comment one" in row[4]


def test_refresh_octane_manual_runs_only_skips_testcase_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    client = FakeOctaneClient()

    summary = refresh_octane_manual_runs_only(
        source_db_path=db_path,
        team_name="DTSV_China",
        years=(2026,),
        client=client,
    )

    assert summary == {"manual_run_rows": 1, "manual_run_ids": ["MR-1-2026"]}
    assert client.manual_run_calls == [
        {
            "team_id": "1",
            "year": 2026,
            "modified_since": None,
            "include_related_work_items": False,
            "releases": (),
            "related_work_item_workers": 1,
        }
    ]

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM octane_manual_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM octane_testcases").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM octane_testcase_relations").fetchone()[0] == 0
        run_row = conn.execute(
            "SELECT mr_id, team, run_team, release FROM octane_manual_runs WHERE mr_id = ?",
            ("MR-1-2026",),
        ).fetchone()
        assert run_row == ("MR-1-2026", "DTSV_China", "DTSV_China", "R-26-01")
    finally:
        conn.close()


def test_refresh_octane_manual_runs_only_reports_progress(tmp_path: Path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    client = FakeOctaneClient()
    messages: list[str] = []

    refresh_octane_manual_runs_only(
        source_db_path=db_path,
        team_name="DTSV_China",
        years=(2026,),
        client=client,
        progress=messages.append,
    )

    assert messages == [
        "Resolving Octane team: DTSV_China",
        "Resolved Octane team: DTSV_China (1)",
        "Refreshing manual runs for DTSV_China 2026 with full fetch",
        "manual-runs page year=2026",
        "Stored 1 manual runs for DTSV_China 2026",
    ]


class TraceabilityOctaneClient:
    def __init__(self) -> None:
        self.manual_run_calls: list[dict[str, object]] = []

    def list_teams(self) -> list[dict[str, str]]:
        return [{"id": "1", "name": "DTSV_China"}]

    def fetch_manual_runs(
        self,
        *,
        team_id: str,
        year: int,
        modified_since: str | None = None,
        include_related_work_items: bool = True,
        releases: tuple[str, ...] = (),
        related_work_item_workers: int = 1,
        progress=None,
    ) -> list[dict[str, object]]:
        self.manual_run_calls.append(
            {
                "team_id": team_id,
                "year": year,
                "modified_since": modified_since,
                "include_related_work_items": include_related_work_items,
                "releases": releases,
                "related_work_item_workers": related_work_item_workers,
            }
        )
        if progress is not None:
            progress(f"traceability manual-runs page year={year}")
        return [
            {
                "id": "MR-TRACE-1",
                "defect": {
                    "total_count": 1,
                    "data": [
                        {"id": "D-TRACE-1", "name": "Wake defect", "subtype": "defect"},
                    ],
                },
                "test": {"id": "T-TRACE-1", "name": "Wake trace test", "subtype": "test_manual"},
                "test_name": "Wake trace test",
                "status": {"name": "Passed"},
                "release": {"name": "R-26-06"},
                "run_team_000_udf": {"name": "DTSV_China"},
                "finished_udf": "2026-06-30T08:00:00Z",
                "covered_content": {
                    "total_count": 2,
                    "data": [
                        {
                            "id": "F-HISTORY-1",
                            "name": "Historical testcase feature",
                            "subtype": "feature",
                            "path": "epic/historical-feature",
                            "parent": {"id": "E-HISTORY-1", "name": "Historical epic", "subtype": "epic"},
                        },
                        {
                            "id": "S-HISTORY-1",
                            "name": "Historical testcase story",
                            "subtype": "story",
                            "path": "epic/historical-feature/historical-story",
                            "parent": {"id": "F-HISTORY-1", "name": "Historical testcase feature", "subtype": "feature"},
                        },
                    ],
                },
                "related_work_items": [
                    {
                        "id": "F-TRACE-1",
                        "name": "Wake feature",
                        "subtype": "feature",
                        "path": "epic/wake-feature",
                        "parent": {"id": "E-TRACE-1", "name": "Wake epic", "subtype": "epic"},
                    },
                    {
                        "id": "S-TRACE-1",
                        "name": "Wake story",
                        "subtype": "story",
                        "path": "epic/wake-feature/wake-story",
                        "parent": {"id": "F-TRACE-1", "name": "Wake feature", "subtype": "feature"},
                    },
                ] if include_related_work_items else [],
            }
        ]


def test_refresh_traceability_source_writes_run_level_relations(tmp_path: Path) -> None:
    from backend.analytics.ingest.pipeline import refresh_octane_traceability_source

    db_path = tmp_path / "qgate_raw.db"
    client = TraceabilityOctaneClient()

    summary = refresh_octane_traceability_source(
        source_db_path=db_path,
        team_name="DTSV_China",
        years=(2026,),
        workers=24,
        client=client,
    )

    assert summary == {
        "manual_run_rows": 1,
        "traceability_rows": 3,
        "manual_run_ids": ["MR-TRACE-1"],
    }
    assert client.manual_run_calls == [
        {
            "team_id": "1",
            "year": 2026,
            "modified_since": None,
            "include_related_work_items": True,
            "releases": (),
            "related_work_item_workers": 24,
        }
    ]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT run_id, test_id, scope_team, scope_release, year, status,
                   relation_type, related_id, related_name, parent_id, parent_name
            FROM octane_run_traceability
            ORDER BY relation_type, related_id
            """
        ).fetchall()
        testcase_row = conn.execute(
            """
            SELECT test_id, scope_team, scope_release, source, test_name, run_count,
                   run_ids_json, run_status_distribution_json,
                 defect_ids_json, defect_names_json,
                 feature_ids_json, feature_names_json,
                 story_ids_json, story_names_json,
                 epic_ids_json, epic_names_json
            FROM octane_traceability_testcases
            WHERE test_id='T-TRACE-1'
            """
        ).fetchone()
    finally:
        conn.close()

    assert rows == [
        ("MR-TRACE-1", "T-TRACE-1", "DTSV_China", "R-26-06", "2026", "Passed", "defect", "D-TRACE-1", "Wake defect", "", ""),
        ("MR-TRACE-1", "T-TRACE-1", "DTSV_China", "R-26-06", "2026", "Passed", "feature", "F-TRACE-1", "Wake feature", "E-TRACE-1", "Wake epic"),
        ("MR-TRACE-1", "T-TRACE-1", "DTSV_China", "R-26-06", "2026", "Passed", "story", "S-TRACE-1", "Wake story", "F-TRACE-1", "Wake feature"),
    ]
    assert testcase_row is not None
    assert testcase_row[:6] == ("T-TRACE-1", "DTSV_China", "R-26-06", "manual_runs", "Wake trace test", 1)
    assert json.loads(testcase_row[6]) == ["MR-TRACE-1"]
    assert json.loads(testcase_row[7]) == {"Passed": 1}
    assert json.loads(testcase_row[8]) == ["D-TRACE-1"]
    assert json.loads(testcase_row[9]) == ["Wake defect"]
    assert json.loads(testcase_row[10]) == ["F-TRACE-1"]
    assert json.loads(testcase_row[11]) == ["Wake feature"]
    assert json.loads(testcase_row[12]) == ["S-TRACE-1"]
    assert json.loads(testcase_row[13]) == ["Wake story"]
    assert json.loads(testcase_row[14]) == ["E-TRACE-1"]
    assert json.loads(testcase_row[15]) == ["Wake epic"]


def test_refresh_traceability_source_force_rebuilds_only_traceability_scope(tmp_path: Path) -> None:
    from backend.analytics.ingest.pipeline import refresh_octane_traceability_source
    from backend.analytics.ingest.source_store import OctaneSourceStore

    db_path = tmp_path / "qgate_raw.db"
    store = OctaneSourceStore(db_path)
    try:
        store.create_tables()
        store.upsert_manual_runs(
            [
                {
                    "id": "MR-OLD-1",
                    "test": {"id": "T-OLD-1", "name": "Old trace test"},
                    "test_name": "Old trace test",
                    "status": {"name": "Passed"},
                    "release": {"name": "R-26-06"},
                    "run_team_000_udf": {"name": "DTSV_China"},
                    "year": "2026",
                }
            ],
            team="DTSV_China",
            year=2026,
        )
        store.replace_run_traceability_from_runs(
            [
                {
                    "id": "MR-OLD-1",
                    "test": {"id": "T-OLD-1", "name": "Old trace test"},
                    "test_name": "Old trace test",
                    "status": {"name": "Passed"},
                    "release": {"name": "R-26-06"},
                    "run_team_000_udf": {"name": "DTSV_China"},
                    "year": "2026",
                    "related_work_items": [
                        {"id": "S-OLD-1", "name": "Old story", "subtype": "story"},
                    ],
                }
            ],
            team="DTSV_China",
        )
    finally:
        store.close()

    client = TraceabilityOctaneClient()

    refresh_octane_traceability_source(
        source_db_path=db_path,
        team_name="DTSV_China",
        years=(2026,),
        client=client,
        force=True,
    )

    conn = sqlite3.connect(db_path)
    try:
        manual_count = conn.execute("SELECT COUNT(*) FROM octane_manual_runs WHERE mr_id='MR-OLD-1'").fetchone()[0]
        old_trace_count = conn.execute("SELECT COUNT(*) FROM octane_run_traceability WHERE run_id='MR-OLD-1'").fetchone()[0]
        old_testcase_count = conn.execute("SELECT COUNT(*) FROM octane_traceability_testcases WHERE test_id='T-OLD-1'").fetchone()[0]
        new_trace_count = conn.execute("SELECT COUNT(*) FROM octane_run_traceability WHERE run_id='MR-TRACE-1'").fetchone()[0]
    finally:
        conn.close()

    assert manual_count == 1
    assert old_trace_count == 0
    assert old_testcase_count == 0
    assert new_trace_count == 3


def test_run_traceability_does_not_fallback_to_covered_content_when_related_items_empty(tmp_path: Path) -> None:
    from backend.analytics.ingest.source_store import OctaneSourceStore

    db_path = tmp_path / "qgate_raw.db"
    store = OctaneSourceStore(db_path)
    try:
        store.create_tables()
        written = store.replace_run_traceability_from_runs(
            [
                {
                    "id": "MR-TRACE-EMPTY",
                    "defect": {"total_count": 0, "data": []},
                    "test": {"id": "T-TRACE-EMPTY", "name": "Wake trace test", "subtype": "test_manual"},
                    "test_name": "Wake trace test",
                    "status": {"name": "Passed"},
                    "release": {"name": "R-26-06"},
                    "run_team_000_udf": {"name": "DTSV_China"},
                    "covered_content": {
                        "total_count": 1,
                        "data": [
                            {
                                "id": "S-HISTORY-1",
                                "name": "Historical testcase story",
                                "subtype": "story",
                                "parent": {"id": "F-HISTORY-1", "name": "Historical testcase feature", "subtype": "feature"},
                            }
                        ],
                    },
                    "related_work_items": [],
                }
            ],
            team="DTSV_China",
        )
        rows = store._conn.execute("SELECT relation_type, related_id FROM octane_run_traceability").fetchall()
    finally:
        store.close()

    assert written == 0
    assert rows == []


def test_refresh_traceability_source_full_fetches_when_traceability_table_is_empty(tmp_path: Path) -> None:
    from backend.analytics.ingest.pipeline import refresh_octane_traceability_source
    from backend.analytics.ingest.source_store import OctaneSourceStore

    db_path = tmp_path / "qgate_raw.db"
    store = OctaneSourceStore(db_path)
    try:
        store.create_tables()
        store.upsert_manual_runs(
            [
                {
                    "id": "MR-CACHED-1",
                    "test": {"id": "T-CACHED-1"},
                    "status": "Passed",
                    "release": {"name": "R-26-06"},
                    "run_team_000_udf": {"name": "DTSV_China"},
                    "last_modified": "2026-06-30T08:00:00Z",
                }
            ],
            team="DTSV_China",
            year=2026,
        )
    finally:
        store.close()
    client = TraceabilityOctaneClient()

    refresh_octane_traceability_source(
        source_db_path=db_path,
        team_name="DTSV_China",
        years=(2026,),
        client=client,
    )

    assert client.manual_run_calls[0]["modified_since"] is None


def test_octane_client_list_teams_omits_query_param() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "1", "name": "DTSV_China"}]}

    class FakeSession:
        def get(self, url: str, *, params: dict[str, object], timeout: int, verify: bool):
            captured["url"] = url
            captured["params"] = dict(params)
            captured["timeout"] = timeout
            captured["verify"] = verify
            return FakeResponse()

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    rows = client.list_teams()

    assert rows == [{"id": "1", "name": "DTSV_China"}]
    assert captured["params"] == {"fields": "id,name", "limit": 1000, "offset": 0}


def test_octane_client_fetch_comments_batches_unique_defect_ids_by_100() -> None:
    captured_queries: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": []}

    class FakeSession:
        def get(self, url: str, *, params: dict[str, object], timeout: int, verify: bool):
            captured_queries.append(str(params["query"]))
            return FakeResponse()

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    defect_ids = ["D-050", *[f"D-{index:03d}" for index in range(201)], "D-050"]

    rows = client.fetch_comments_for_defects(defect_ids)

    assert rows == []
    assert len(captured_queries) == 3
    assert captured_queries[0].count("D-") == 100
    assert captured_queries[1].count("D-") == 100
    assert captured_queries[2].count("D-") == 1


def test_octane_client_fetch_related_work_items_filters_by_release() -> None:
    captured_queries: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": "S-1",
                        "name": "Run story",
                        "subtype": "story",
                        "path": "feature/story",
                        "parent": {"id": "F-1", "name": "Run feature", "subtype": "feature"},
                    }
                ]
            }

    class FakeSession:
        def get(self, url: str, *, params: dict[str, object], timeout: int, verify: bool):
            captured_queries.append(str(params["query"]))
            return FakeResponse()

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    rows = client.fetch_related_work_items_for_runs(["MR-001"], releases=("R-26-06",))

    assert rows == {
        "MR-001": [
            {
                "id": "S-1",
                "name": "Run story",
                "subtype": "story",
                "path": "feature/story",
                "parent": {"id": "F-1", "name": "Run feature", "subtype": "feature"},
            }
        ]
    }
    assert captured_queries == [
        '"(subtype IN \'defect\',\'feature\',\'story\';((release={name=\'R-26-06\'}));run_covered_content_relation={id IN \'MR-001\'})"'
    ]


def test_octane_client_fetch_related_work_items_splits_timed_out_batches() -> None:
    captured_queries: list[str] = []

    class FakeResponse:
        def __init__(self, query: str) -> None:
            self.query = query

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            if "MR-001" not in self.query:
                return {"data": []}
            return {
                "data": [
                    {
                        "id": "S-1",
                        "name": "Run story",
                        "subtype": "story",
                        "path": "feature/story",
                        "parent": {"id": "F-1", "name": "Run feature", "subtype": "feature"},
                        "run_covered_content_relation": {"id": "MR-001"},
                    }
                ]
            }

    class FakeSession:
        def get(self, url: str, *, params: dict[str, object], timeout: int, verify: bool):
            query = str(params["query"])
            captured_queries.append(query)
            if query.count("MR-") > 1:
                raise requests.exceptions.ReadTimeout("batch too large")
            return FakeResponse(query)

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    rows = client.fetch_related_work_items_for_runs(["MR-001", "MR-002"], batch_size=2)

    assert rows["MR-001"] == [
        {
            "id": "S-1",
            "name": "Run story",
            "subtype": "story",
            "path": "feature/story",
            "parent": {"id": "F-1", "name": "Run feature", "subtype": "feature"},
        }
    ]
    assert rows["MR-002"] == []
    assert [query.count("MR-") for query in captured_queries] == [2, 1, 1]


def test_octane_client_fetch_history_uses_legacy_history_logs_shape() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": []}

    class FakeSession:
        def get(
            self,
            url: str,
            *,
            params: dict[str, object],
            timeout: int,
            verify: bool,
            headers: dict[str, str] | None = None,
        ):
            captured["url"] = url
            captured["params"] = dict(params)
            captured["timeout"] = timeout
            captured["verify"] = verify
            captured["headers"] = dict(headers or {})
            return FakeResponse()

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    payload = client.fetch_history(defect_id="D-1")

    assert payload == {"data": [], "total_count": 0}
    assert captured["params"] == {
        "limit": 10000,
        "order_by": "-timestamp",
        "offset": 0,
        "query": '"(entity_id=\'D-1\';entity_type=\'defect\')"',
    }
    assert captured["headers"] == {"HPECLIENTTYPE": "HPE_MQM_UI"}


def test_octane_client_history_access_check_is_bounded_and_does_not_read_real_tickets() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": []}

    class FakeSession:
        def get(
            self,
            url: str,
            *,
            params: dict[str, object],
            timeout: int,
            verify: bool,
            headers: dict[str, str] | None = None,
        ):
            captured["url"] = url
            captured["params"] = dict(params)
            captured["timeout"] = timeout
            captured["headers"] = dict(headers or {})
            return FakeResponse()

    client = OctaneApiClient(
        base_url="https://octane.example.com",
        shared_space_id="1002",
        workspace_id="2001",
        session=FakeSession(),
    )

    client.check_history_access()

    assert captured == {
        "url": "https://octane.example.com/api/shared_spaces/1002/workspaces/2001/history_logs",
        "params": {
            "query": '"(entity_id=\'0\';entity_type=\'defect\')"',
            "limit": 1,
            "offset": 0,
            "order_by": "-timestamp",
        },
        "timeout": 30,
        "headers": {"HPECLIENTTYPE": "HPE_MQM_UI"},
    }