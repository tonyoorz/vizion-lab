from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.analytics.ingest.source_store import (
    DEFECT_WATERMARK_TEAM_SQL,
    DEFECT_WATERMARK_VALUE_SQL,
    DEFECT_WATERMARK_YEAR_SQL,
    OctaneSourceStore,
)
from backend.analytics.ingest.client import DEFECT_FIELDS


MANUAL_RUN_OVERLAP_DAYS = 3
DEFECT_OVERLAP_DAYS = 3
ProgressCallback = Callable[[str], None]


def _elapsed_seconds(started_at: float) -> float:
    return round(time.perf_counter() - started_at, 1)


@dataclass(frozen=True)
class IngestRequest:
    source_db_path: Path
    teams: tuple[str, ...]
    years: tuple[int, ...]
    include_history: bool
    include_comments: bool
    include_testing: bool
    history_max_workers: int = 1
    team_max_workers: int = 1
    force_defect_refresh: bool = False


@dataclass(frozen=True)
class _TeamYearTarget:
    team_id: str
    team_name: str
    year: int
    modified_since: str | None


@dataclass(frozen=True)
class _TeamYearFetchResult:
    target: _TeamYearTarget
    defects: list[dict[str, Any]]
    comment_summary: dict[str, int]
    comment_state_rows: list[tuple[str, str, str]]
    history_refresh_since: dict[str, str | None]
    skipped_history_count: int
    history_payloads: dict[str, dict[str, Any]]
    manual_runs: list[dict[str, Any]]


def refresh_octane_tickets_by_ids(
    *,
    source_db_path: Path,
    ticket_ids: list[str],
    client: Any,
    evidence_directory: Path,
    batch_size: int = 50,
    allow_unavailable: bool = False,
    expected_unavailable_ids: list[str] | None = None,
) -> dict[str, object]:
    normalized_ids = sorted({str(ticket_id).strip() for ticket_id in ticket_ids if str(ticket_id).strip()})
    if not normalized_ids:
        raise ValueError("Exact ticket scope must not be empty")
    allowed_unavailable = {str(ticket_id).strip() for ticket_id in (expected_unavailable_ids or [])}
    if not allowed_unavailable <= set(normalized_ids):
        raise ValueError("Expected unavailable IDs fall outside the approved ticket scope")
    if allow_unavailable and not allowed_unavailable:
        raise ValueError("Unavailable exceptions must be explicitly enumerated")
    if allowed_unavailable and not allow_unavailable:
        raise ValueError("Explicit unavailable exceptions require allow_unavailable")
    evidence_directory.mkdir(parents=True, exist_ok=True)
    store = OctaneSourceStore(source_db_path)
    store.create_tables()
    store._conn.execute(
        "CREATE TABLE IF NOT EXISTS octane_ticket_evidence_refresh_state ("
        "defect_id TEXT PRIMARY KEY, defect_last_modified TEXT NOT NULL, "
        "refreshed_at TEXT NOT NULL, comments_count INTEGER NOT NULL, "
        "history_count INTEGER NOT NULL, evidence_fingerprint TEXT NOT NULL, "
        "snapshot_file TEXT NOT NULL)"
    )
    store.commit()
    verified_count = 0
    total_comments = 0
    total_history_rows = 0
    unavailable_ticket_ids: list[str] = []
    try:
        for start in range(0, len(normalized_ids), max(1, batch_size)):
            batch = normalized_ids[start:start + max(1, batch_size)]
            batch_key = hashlib.sha256(json.dumps(batch).encode("utf-8")).hexdigest()[:16]
            checkpoint = evidence_directory / f"batch_{start:05d}_{batch_key}.json"
            if checkpoint.exists():
                snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
            else:
                id_expression = ",".join("'" + ticket_id.replace("'", "\\'") + "'" for ticket_id in batch)
                defects = client.fetch_rows(
                    "defects", fields=DEFECT_FIELDS,
                    query='"(id IN ' + id_expression + ')"', limit=max(100, len(batch) + 1),
                )
                returned_ids = {str(row.get("id")) for row in defects}
                if len(returned_ids) != len(defects) or not returned_ids <= set(batch):
                    raise ValueError("Unexpected metadata IDs in exact ticket scope")
                missing_ids = sorted(set(batch) - returned_ids)
                if missing_ids and not allow_unavailable:
                    raise ValueError("Incomplete metadata coverage for exact ticket scope")
                if set(missing_ids) - allowed_unavailable:
                    raise ValueError(f"Unapproved unavailable ticket IDs: {sorted(set(missing_ids) - allowed_unavailable)}")
                available_ids = sorted(returned_ids)
                comments = client.fetch_comments_for_defects(available_ids) if available_ids else []
                histories = client.fetch_histories_for_defects(available_ids, max_workers=2) if available_ids else {}
                snapshot = {
                    "ticket_ids": batch,
                    "unavailable_ticket_ids": missing_ids,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "defects": defects, "comments": comments, "histories": histories,
                }
            if snapshot.get("ticket_ids") != batch:
                raise ValueError("Checkpoint ticket scope mismatch")
            defects = snapshot["defects"]
            histories = snapshot["histories"]
            available_ids = {str(row["id"]) for row in defects}
            missing_ids = sorted(set(batch) - available_ids)
            if len(available_ids) != len(defects) or not available_ids <= set(batch) or missing_ids != sorted(snapshot.get("unavailable_ticket_ids", [])):
                raise ValueError("Invalid metadata/unavailable coverage in source checkpoint")
            fetched_at = datetime.fromisoformat(str(snapshot["fetched_at"]).replace("Z", "+00:00"))
            if fetched_at.tzinfo is None or fetched_at < datetime.now(timezone.utc) - timedelta(hours=24):
                raise ValueError("Stale source checkpoint; start a new approved repair directory")
            if missing_ids and not allow_unavailable:
                raise ValueError("Incomplete metadata coverage for exact ticket scope")
            if set(missing_ids) - allowed_unavailable:
                raise ValueError(f"Unapproved unavailable ticket IDs: {sorted(set(missing_ids) - allowed_unavailable)}")
            if set(histories) != available_ids:
                raise ValueError("Incomplete history coverage for exact ticket scope")
            comments_by_ticket: dict[str, list[dict[str, object]]] = {ticket_id: [] for ticket_id in available_ids}
            for comment in snapshot["comments"]:
                ticket_id = str(comment.get("defect_id") or (comment.get("owner_work_item") or {}).get("id") or "")
                if ticket_id not in comments_by_ticket:
                    raise ValueError("Comment owner does not match requested ticket scope")
                comments_by_ticket[ticket_id].append(comment)
            for ticket_id, history in histories.items():
                if not isinstance(history.get("data"), list) or history.get("total_count") != len(history["data"]):
                    raise ValueError(f"Incomplete history coverage for {ticket_id}")
            if not checkpoint.exists():
                checkpoint.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            try:
                for defect in defects:
                    ticket_id = str(defect["id"])
                    defect = {**defect, "comments": comments_by_ticket[ticket_id]}
                    last_modified = str(defect.get("last_modified") or "")
                    store.upsert_defects([defect], team="", commit=False)
                    store.record_comment_refresh_state(
                        [(ticket_id, last_modified, json.dumps(defect["comments"], ensure_ascii=False))]
                    )
                    history_count = store.upsert_defect_history_events(
                        defect_id=ticket_id, payload=histories[ticket_id],
                        defect_last_modified=last_modified, commit=False,
                    )
                    fingerprint = hashlib.sha256(
                        json.dumps({"defect": defect, "history": histories[ticket_id]}, sort_keys=True, ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    stored = store._conn.execute(
                        "SELECT comments, raw_json FROM octane_defects WHERE defect_id=?", (ticket_id,)
                    ).fetchone()
                    if json.loads(stored["comments"]) != defect["comments"] or json.loads(stored["raw_json"])["comments"] != defect["comments"]:
                        raise ValueError(f"Canonical comment verification failed for {ticket_id}")
                    store._conn.execute(
                        "INSERT INTO octane_ticket_evidence_refresh_state VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(defect_id) DO UPDATE SET defect_last_modified=excluded.defect_last_modified, "
                        "refreshed_at=excluded.refreshed_at, comments_count=excluded.comments_count, "
                        "history_count=excluded.history_count, evidence_fingerprint=excluded.evidence_fingerprint, snapshot_file=excluded.snapshot_file",
                        (ticket_id, last_modified, snapshot["fetched_at"], len(defect["comments"]), history_count, fingerprint, str(checkpoint.resolve())),
                    )
                    total_comments += len(defect["comments"])
                    total_history_rows += history_count
                store.commit()
            except Exception:
                store.rollback()
                raise
            verified_count += len(available_ids)
            unavailable_ticket_ids.extend(missing_ids)
            print(f"Exact-ID source refresh processed={start + len(batch)}/{len(normalized_ids)} verified={verified_count} unavailable={len(unavailable_ticket_ids)} comments={total_comments} history_rows={total_history_rows}", flush=True)
    finally:
        store.close()
    return {
        "requested_ticket_count": len(normalized_ids),
        "verified_ticket_count": verified_count,
        "comment_count": total_comments,
        "history_row_count": total_history_rows,
        "unavailable_ticket_ids": sorted(unavailable_ticket_ids),
        "expected_unavailable_ticket_ids": sorted(allowed_unavailable),
        "evidence_directory": str(evidence_directory),
    }


def _select_teams(request: IngestRequest, client: Any) -> list[dict[str, str]]:
    available = client.list_teams()
    if request.teams == ("all",) or any(str(value).strip().lower() == "all" for value in request.teams):
        return available
    allowed = {str(value).strip() for value in request.teams}
    return [
        row
        for row in available
        if str(row.get("name") or "").strip() in allowed or str(row.get("id") or "").strip() in allowed
    ]


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_incremental_manual_run_since_by_year(
    *,
    source_db_path: Path,
    team_name: str,
    years: tuple[int, ...],
    overlap_days: int = MANUAL_RUN_OVERLAP_DAYS,
) -> dict[int, str | None]:
    watermarks: dict[int, str | None] = {int(year): None for year in years}
    if not source_db_path.exists() or not years:
        return watermarks

    placeholders_years = ", ".join("?" for _ in years)
    query = f"""
        SELECT
            CAST(year AS INTEGER) AS run_year,
            MAX(COALESCE(NULLIF(TRIM(CAST(last_modified AS TEXT)), ''), NULLIF(TRIM(CAST(fetched_at AS TEXT)), ''))) AS watermark
        FROM octane_manual_runs
        WHERE CAST(year AS INTEGER) IN ({placeholders_years})
          AND COALESCE(NULLIF(TRIM(CAST(run_team AS TEXT)), ''), NULLIF(TRIM(CAST(team AS TEXT)), '')) = ?
        GROUP BY CAST(year AS INTEGER)
    """
    conn = sqlite3.connect(str(source_db_path))
    try:
        rows = conn.execute(query, [*years, team_name]).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    for run_year, watermark in rows:
        parsed = _parse_iso_datetime(watermark)
        if parsed is None:
            continue
        watermarks[int(run_year)] = (
            parsed - timedelta(days=max(0, int(overlap_days)))
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return watermarks


def _load_traceability_refresh_since_by_year(
    *,
    source_db_path: Path,
    team_name: str,
    years: tuple[int, ...],
) -> dict[int, str | None]:
    watermarks = _load_incremental_manual_run_since_by_year(
        source_db_path=source_db_path,
        team_name=team_name,
        years=years,
    )
    if not source_db_path.exists() or not years:
        return watermarks

    placeholders_years = ", ".join("?" for _ in years)
    query = f"""
        SELECT CAST(year AS INTEGER) AS run_year, COUNT(*) AS relation_count
        FROM octane_run_traceability
        WHERE CAST(year AS INTEGER) IN ({placeholders_years})
          AND TRIM(COALESCE(CAST(scope_team AS TEXT), '')) = ?
        GROUP BY CAST(year AS INTEGER)
    """
    conn = sqlite3.connect(str(source_db_path))
    try:
        rows = conn.execute(query, [*years, team_name]).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    traced_years = {int(run_year) for run_year, relation_count in rows if int(relation_count or 0) > 0}
    return {
        int(year): watermarks.get(int(year)) if int(year) in traced_years else None
        for year in years
    }


def _load_incremental_defect_since_by_year(
    *,
    source_db_path: Path,
    team_name: str,
    years: tuple[int, ...],
    overlap_days: int = DEFECT_OVERLAP_DAYS,
) -> dict[int, str | None]:
    return _load_incremental_defect_since_by_team(
        source_db_path=source_db_path,
        team_names=(team_name,),
        years=years,
        overlap_days=overlap_days,
    )[team_name]


def _load_incremental_defect_since_by_team(
    *,
    source_db_path: Path,
    team_names: tuple[str, ...],
    years: tuple[int, ...],
    overlap_days: int = DEFECT_OVERLAP_DAYS,
) -> dict[str, dict[int, str | None]]:
    watermarks: dict[str, dict[int, str | None]] = {
        team_name: {int(year): None for year in years}
        for team_name in team_names
    }
    if not source_db_path.exists() or not team_names or not years:
        return watermarks

    placeholders_teams = ", ".join("?" for _ in team_names)
    placeholders_years = ", ".join("?" for _ in years)
    query = f"""
        SELECT
            {DEFECT_WATERMARK_TEAM_SQL} AS scope_team,
            {DEFECT_WATERMARK_YEAR_SQL} AS defect_year,
            MAX({DEFECT_WATERMARK_VALUE_SQL}) AS watermark
        FROM octane_defects
        WHERE {DEFECT_WATERMARK_TEAM_SQL} IN ({placeholders_teams})
          AND {DEFECT_WATERMARK_YEAR_SQL} IN ({placeholders_years})
        GROUP BY {DEFECT_WATERMARK_TEAM_SQL}, {DEFECT_WATERMARK_YEAR_SQL}
    """
    conn = sqlite3.connect(str(source_db_path))
    try:
        rows = conn.execute(query, [*team_names, *years]).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    for team_name, defect_year, watermark in rows:
        parsed = _parse_iso_datetime(watermark)
        if parsed is None:
            continue
        watermarks[str(team_name)][int(defect_year)] = (
            parsed - timedelta(days=max(0, int(overlap_days)))
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return watermarks


def _has_cached_comments(raw_comments: str) -> bool:
    text = str(raw_comments or "").strip()
    return bool(text and text not in {"[]", "{}", "null"})


def _attach_incremental_comments(
    *,
    store: OctaneSourceStore,
    client: Any,
    defects: list[dict[str, Any]],
) -> dict[str, int]:
    defect_ids = [str(row.get("id") or "").strip() for row in defects if str(row.get("id") or "").strip()]
    snapshots = store.load_comment_snapshots(defect_ids)
    plan = _build_incremental_comment_plan(snapshots=snapshots, client=client, defects=defects)
    state_rows = list(plan["state_rows"])
    if state_rows:
        store.record_comment_refresh_state(state_rows)
    return {
        "comment_refreshed_defects": int(plan["comment_refreshed_defects"]),
        "comment_reused_defects": int(plan["comment_reused_defects"]),
    }


def _build_incremental_comment_plan(
    *,
    snapshots: dict[str, dict[str, str]],
    client: Any,
    defects: list[dict[str, Any]],
) -> dict[str, object]:
    ids_to_refresh: list[str] = []
    reused = 0

    for row in defects:
        defect_id = str(row.get("id") or "").strip()
        if not defect_id:
            continue
        incoming_last_modified = str(row.get("last_modified") or "").strip()
        snapshot = snapshots.get(defect_id)
        if (
            snapshot is not None
            and (str(snapshot.get("has_refresh_state") or "") == "1" or _has_cached_comments(snapshot.get("comments_json", "")))
            and incoming_last_modified
            and str(snapshot.get("defect_last_modified") or "").strip() == incoming_last_modified
        ):
            try:
                row["comments"] = json.loads(str(snapshot.get("comments_json") or "[]"))
            except json.JSONDecodeError:
                row["comments"] = []
            reused += 1
            continue
        ids_to_refresh.append(defect_id)

    comment_rows = client.fetch_comments_for_defects(ids_to_refresh) if ids_to_refresh else []
    comments_by_defect: dict[str, list[dict[str, object]]] = {}
    for comment in comment_rows:
        comments_by_defect.setdefault(str(comment.get("defect_id") or "").strip(), []).append(comment)

    state_rows: list[tuple[str, str, str]] = []
    for row in defects:
        defect_id = str(row.get("id") or "").strip()
        if not defect_id or defect_id not in ids_to_refresh:
            continue
        comments = comments_by_defect.get(defect_id, [])
        row["comments"] = comments
        state_rows.append((defect_id, str(row.get("last_modified") or "").strip(), json.dumps(comments, ensure_ascii=False)))

    return {
        "comment_refreshed_defects": len(ids_to_refresh),
        "comment_reused_defects": reused,
        "state_rows": state_rows,
    }


def _load_comment_snapshots(source_db_path: Path, defect_ids: list[str]) -> dict[str, dict[str, str]]:
    store = OctaneSourceStore(source_db_path)
    try:
        return store.load_comment_snapshots(defect_ids)
    finally:
        store.close()


def _plan_history_refreshes(source_db_path: Path, defects: list[dict[str, Any]]) -> tuple[dict[str, str | None], int]:
    store = OctaneSourceStore(source_db_path)
    try:
        return store.plan_history_refreshes(defects)
    finally:
        store.close()


def _fetch_team_year_source(
    *,
    request: IngestRequest,
    client: Any,
    target: _TeamYearTarget,
) -> _TeamYearFetchResult:
    team_year_started_at = time.perf_counter()
    if target.modified_since:
        print(f"Source refresh {target.team_name} {target.year}: fetching defects since {target.modified_since}", flush=True)
    else:
        print(f"Source refresh {target.team_name} {target.year}: fetching defects with full year scope", flush=True)
    defect_fetch_started_at = time.perf_counter()
    defects = list(client.fetch_defects(team_id=target.team_id, year=target.year, modified_since=target.modified_since))
    print(
        f"Source refresh {target.team_name} {target.year}: fetched {len(defects)} defects duration_seconds={_elapsed_seconds(defect_fetch_started_at)}",
        flush=True,
    )

    comment_summary = {"comment_refreshed_defects": 0, "comment_reused_defects": 0}
    comment_state_rows: list[tuple[str, str, str]] = []
    if request.include_comments and defects:
        comments_started_at = time.perf_counter()
        defect_ids = [str(row.get("id") or "").strip() for row in defects if str(row.get("id") or "").strip()]
        snapshots = _load_comment_snapshots(request.source_db_path, defect_ids)
        comment_plan = _build_incremental_comment_plan(snapshots=snapshots, client=client, defects=defects)
        comment_state_rows = list(comment_plan["state_rows"])
        comment_summary = {
            "comment_refreshed_defects": int(comment_plan["comment_refreshed_defects"]),
            "comment_reused_defects": int(comment_plan["comment_reused_defects"]),
        }
        print(
            f"Source refresh {target.team_name} {target.year}: comments refreshed={comment_summary['comment_refreshed_defects']} reused={comment_summary['comment_reused_defects']} duration_seconds={_elapsed_seconds(comments_started_at)}",
            flush=True,
        )

    history_refresh_since: dict[str, str | None] = {}
    skipped_history_count = 0
    history_payloads: dict[str, dict[str, Any]] = {}
    if request.include_history:
        history_refresh_since, skipped_history_count = _plan_history_refreshes(request.source_db_path, defects)
        stale_history_ids = sorted(history_refresh_since)
        incremental_history_count = sum(1 for value in history_refresh_since.values() if value)
        print(
            f"Source refresh {target.team_name} {target.year}: history queued={len(stale_history_ids)} skipped={skipped_history_count} incremental={incremental_history_count} full={len(stale_history_ids) - incremental_history_count}",
            flush=True,
        )
        history_fetch_started_at = time.perf_counter()
        history_payloads = client.fetch_histories_for_defects(
            stale_history_ids,
            max_workers=max(1, int(request.history_max_workers or 1)),
            modified_since_by_defect=history_refresh_since,
        )
        fetched_history_rows = sum(len(list(payload.get("data") or [])) for payload in history_payloads.values())
        print(
            f"Source refresh {target.team_name} {target.year}: history fetched payloads={len(history_payloads)} entries={fetched_history_rows} duration_seconds={_elapsed_seconds(history_fetch_started_at)}",
            flush=True,
        )

    manual_runs: list[dict[str, Any]] = []
    if request.include_testing:
        manual_runs = list(client.fetch_manual_runs(team_id=target.team_id, year=target.year))

    print(
        f"Source refresh {target.team_name} {target.year}: fetch duration_seconds={_elapsed_seconds(team_year_started_at)}",
        flush=True,
    )
    return _TeamYearFetchResult(
        target=target,
        defects=defects,
        comment_summary=comment_summary,
        comment_state_rows=comment_state_rows,
        history_refresh_since=history_refresh_since,
        skipped_history_count=skipped_history_count,
        history_payloads=history_payloads,
        manual_runs=manual_runs,
    )


def refresh_octane_source(*, request: IngestRequest, client: Any) -> dict[str, int]:
    if request.include_history:
        preflight_started_at = time.perf_counter()
        print("Source refresh history access preflight...", flush=True)
        try:
            client.check_history_access()
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", "unknown")
            print(
                "Source refresh history access preflight failed: "
                f"status={status_code} duration_seconds={_elapsed_seconds(preflight_started_at)}",
                flush=True,
            )
            raise
        print(
            "Source refresh history access preflight passed: "
            f"duration_seconds={_elapsed_seconds(preflight_started_at)}",
            flush=True,
        )

    store_started_at = time.perf_counter()
    print("Source refresh initializing local store...", flush=True)
    store = OctaneSourceStore(request.source_db_path)
    store.create_tables()
    print(
        f"Source refresh local store ready duration_seconds={_elapsed_seconds(store_started_at)}",
        flush=True,
    )

    defect_rows = 0
    history_rows = 0
    manual_run_rows = 0
    manual_run_ids: list[str] = []
    testcase_rows = 0
    testcase_relation_rows = 0
    defect_ids: list[str] = []
    comment_refreshed_defects = 0
    comment_reused_defects = 0
    history_queued_defects = 0
    history_skipped_defects = 0

    try:
        selected_teams = _select_teams(request, client)
        incremental_defect_since_by_team: dict[str, dict[int, str | None]] = {}
        if not request.force_defect_refresh:
            watermark_started_at = time.perf_counter()
            print(f"Source refresh loading defect watermarks teams={len(selected_teams)}...", flush=True)
            incremental_defect_since_by_team = _load_incremental_defect_since_by_team(
                source_db_path=request.source_db_path,
                team_names=tuple(str(team.get("name") or "").strip() for team in selected_teams),
                years=request.years,
            )
            print(
                "Source refresh defect watermarks loaded: "
                f"teams={len(selected_teams)} duration_seconds={_elapsed_seconds(watermark_started_at)}",
                flush=True,
            )
        targets: list[_TeamYearTarget] = []
        for team in selected_teams:
            team_id = str(team.get("id") or "").strip()
            team_name = str(team.get("name") or "").strip()
            incremental_defect_since = incremental_defect_since_by_team.get(team_name, {})
            for year in request.years:
                targets.append(
                    _TeamYearTarget(
                        team_id=team_id,
                        team_name=team_name,
                        year=int(year),
                        modified_since=incremental_defect_since.get(int(year)),
                    )
                )

        effective_team_workers = max(1, min(int(request.team_max_workers or 1), 3, len(targets) or 1))
        print(f"Source refresh team workers={effective_team_workers}", flush=True)
        if effective_team_workers == 1:
            fetch_results = [
                _fetch_team_year_source(request=request, client=client, target=target)
                for target in targets
            ]
        else:
            fetch_results_by_target: dict[_TeamYearTarget, _TeamYearFetchResult] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=effective_team_workers) as executor:
                future_to_target = {
                    executor.submit(_fetch_team_year_source, request=request, client=client, target=target): target
                    for target in targets
                }
                for future in concurrent.futures.as_completed(future_to_target):
                    target = future_to_target[future]
                    fetch_results_by_target[target] = future.result()
            fetch_results = [fetch_results_by_target[target] for target in targets]

        for result in fetch_results:
            target = result.target
            team_year_store_started_at = time.perf_counter()
            if result.comment_state_rows:
                store.record_comment_refresh_state(result.comment_state_rows)
            comment_refreshed_defects += int(result.comment_summary["comment_refreshed_defects"])
            comment_reused_defects += int(result.comment_summary["comment_reused_defects"])

            defect_rows += store.upsert_defects(result.defects, team=target.team_name, year=target.year)
            defect_ids.extend(
                str(row.get("id") or "").strip()
                for row in result.defects
                if str(row.get("id") or "").strip()
            )

            stale_history_ids = sorted(result.history_refresh_since)
            history_queued_defects += len(stale_history_ids)
            history_skipped_defects += result.skipped_history_count
            history_store_started_at = time.perf_counter()
            defect_last_modified = {
                str(row.get("id") or "").strip(): str(row.get("last_modified") or "").strip()
                for row in result.defects
                if str(row.get("id") or "").strip()
            }
            for defect_id in stale_history_ids:
                history_payload = result.history_payloads.get(defect_id, {"data": []})
                history_rows += store.upsert_defect_history_events(
                    defect_id=defect_id,
                    payload=history_payload,
                    team=target.team_name,
                    modified_since=result.history_refresh_since.get(defect_id),
                    defect_last_modified=defect_last_modified.get(defect_id, ""),
                )
            if request.include_history:
                print(
                    f"Source refresh {target.team_name} {target.year}: stored {history_rows} history event rows total duration_seconds={_elapsed_seconds(history_store_started_at)}",
                    flush=True,
                )

            if request.include_testing:
                manual_run_rows += store.upsert_manual_runs(result.manual_runs, team=target.team_name, year=target.year)
                manual_run_ids.extend(
                    str(row.get("id") or "").strip()
                    for row in result.manual_runs
                    if str(row.get("id") or "").strip()
                )
                testcase_summary = store.rebuild_testcases_from_runs(result.manual_runs, team=target.team_name)
                testcase_rows += testcase_summary["testcase_rows"]
                testcase_relation_rows += testcase_summary["relation_rows"]
            print(
                f"Source refresh {target.team_name} {target.year}: store duration_seconds={_elapsed_seconds(team_year_store_started_at)}",
                flush=True,
            )
    finally:
        store.close()

    return {
        "defect_rows": defect_rows,
        "defect_ids": sorted(set(defect_ids)),
        "comment_refreshed_defects": comment_refreshed_defects,
        "comment_reused_defects": comment_reused_defects,
        "history_queued_defects": history_queued_defects,
        "history_skipped_defects": history_skipped_defects,
        "history_event_rows": history_rows,
        "manual_run_rows": manual_run_rows,
        "manual_run_ids": sorted(set(manual_run_ids)),
        "testcase_rows": testcase_rows,
        "testcase_relation_rows": testcase_relation_rows,
    }


def refresh_octane_manual_runs_only(
    *,
    source_db_path: Path,
    team_name: str,
    years: tuple[int, ...],
    client: Any,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    if progress is not None:
        progress(f"Resolving Octane team: {team_name}")
    available_teams = client.list_teams()
    selected_team = next(
        (
            row
            for row in available_teams
            if str(row.get("name") or "").strip() == str(team_name).strip()
            or str(row.get("id") or "").strip() == str(team_name).strip()
        ),
        None,
    )
    if selected_team is None:
        raise ValueError(f"Octane team not found: {team_name}")

    effective_team_name = str(selected_team.get("name") or team_name).strip()
    team_id = str(selected_team.get("id") or "").strip()
    if progress is not None:
        progress(f"Resolved Octane team: {effective_team_name} ({team_id})")
    incremental_since = _load_incremental_manual_run_since_by_year(
        source_db_path=source_db_path,
        team_name=effective_team_name,
        years=years,
    )

    store = OctaneSourceStore(source_db_path)
    store.create_tables()
    manual_run_rows = 0
    manual_run_ids: list[str] = []
    try:
        for year in years:
            modified_since = incremental_since.get(int(year))
            if progress is not None:
                if modified_since:
                    progress(f"Refreshing manual runs for {effective_team_name} {year} since {modified_since}")
                else:
                    progress(f"Refreshing manual runs for {effective_team_name} {year} with full fetch")
            runs = list(
                client.fetch_manual_runs(
                    team_id=team_id,
                    year=year,
                    modified_since=modified_since,
                    include_related_work_items=False,
                    progress=progress,
                )
            )
            written_rows = store.upsert_manual_runs(runs, team=effective_team_name, year=year)
            manual_run_rows += written_rows
            manual_run_ids.extend(
                str(row.get("id") or "").strip()
                for row in runs
                if str(row.get("id") or "").strip()
            )
            if progress is not None:
                progress(f"Stored {written_rows} manual runs for {effective_team_name} {year}")
    finally:
        store.close()

    return {"manual_run_rows": manual_run_rows, "manual_run_ids": sorted(set(manual_run_ids))}


def refresh_octane_traceability_source(
    *,
    source_db_path: Path,
    team_name: str,
    years: tuple[int, ...],
    releases: tuple[str, ...] = (),
    force: bool = False,
    workers: int = 24,
    client: Any,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    if progress is not None:
        progress(f"Resolving Octane team: {team_name}")
    available_teams = client.list_teams()
    selected_team = next(
        (
            row
            for row in available_teams
            if str(row.get("name") or "").strip() == str(team_name).strip()
            or str(row.get("id") or "").strip() == str(team_name).strip()
        ),
        None,
    )
    if selected_team is None:
        raise ValueError(f"Octane team not found: {team_name}")

    effective_team_name = str(selected_team.get("name") or team_name).strip()
    team_id = str(selected_team.get("id") or "").strip()
    if progress is not None:
        progress(f"Resolved Octane team: {effective_team_name} ({team_id})")
    if force:
        store = OctaneSourceStore(source_db_path)
        try:
            store.create_tables()
            store.delete_run_traceability_scope(team=effective_team_name, years=years, releases=releases)
        finally:
            store.close()
    incremental_since = _load_traceability_refresh_since_by_year(
        source_db_path=source_db_path,
        team_name=effective_team_name,
        years=years,
    )

    store = OctaneSourceStore(source_db_path)
    store.create_tables()
    manual_run_rows = 0
    traceability_rows = 0
    manual_run_ids: list[str] = []
    try:
        for year in years:
            modified_since = incremental_since.get(int(year))
            if progress is not None:
                if modified_since:
                    progress(f"Refreshing traceability runs for {effective_team_name} {year} since {modified_since}")
                else:
                    progress(f"Refreshing traceability runs for {effective_team_name} {year} with full fetch")
            runs = list(
                client.fetch_manual_runs(
                    team_id=team_id,
                    year=year,
                    modified_since=modified_since,
                    include_related_work_items=True,
                    releases=releases,
                    related_work_item_workers=workers,
                    progress=progress,
                )
            )
            manual_run_rows += store.upsert_manual_runs(runs, team=effective_team_name, year=year)
            traceability_rows += store.replace_run_traceability_from_runs(runs, team=effective_team_name)
            manual_run_ids.extend(
                str(row.get("id") or "").strip()
                for row in runs
                if str(row.get("id") or "").strip()
            )
            if progress is not None:
                progress(f"Stored traceability for {len(runs)} manual runs in {effective_team_name} {year}")
    finally:
        store.close()

    return {
        "manual_run_rows": manual_run_rows,
        "traceability_rows": traceability_rows,
        "manual_run_ids": sorted(set(manual_run_ids)),
    }
