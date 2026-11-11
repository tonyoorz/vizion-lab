from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from backend.analytics.ingest.source_store import OctaneSourceStore


def _source_database(path: Path) -> None:
    store = OctaneSourceStore(path)
    store.create_tables()
    try:
        for ticket_id, project, phase, description, created in (
            ("D-OPEN", "IDCEVO", "04-In Progress", "Navigation guidance disappears.", "2026-01-01T00:00:00Z"),
            ("D-PROCESS", "IDCEVO", "09-Concluded without action", "Shift to 27-07 is agreed; fix planned in that release.", "2026-02-01T00:00:00Z"),
            ("D-NONE", "IDCEVO", "04-In Progress", "No future planning evidence.", "2026-03-01T00:00:00Z"),
            ("D-OTHER", "OTHER", "04-In Progress", "Shift to 27-07 agreed.", "2026-04-01T00:00:00Z"),
            ("D-OLD", "IDCEVO", "09-Concluded without action", "Shift to 27-07 agreed.", "2024-01-01T00:00:00Z"),
        ):
            store.upsert_defects([{
                "id": ticket_id, "name": "Navigation guidance disappears after restart",
                "tproject": {"name": project}, "phase": {"name": phase},
                "description": description, "creation_time": created,
                "last_modified": "2026-10-01T00:00:00Z",
                "comments": [{"id": "1", "creation_time": "2026-10-01T00:00:00Z", "text": "Retest and delivery confirmation pending."}],
                "software_version_udf": "idcevo-pu2607-26w01.1-1",
            }], team="fixture")
            store._conn.execute("UPDATE octane_defects SET tproject=? WHERE defect_id=?", (project, ticket_id))
            store.commit()
        store.upsert_defect_history_events(
            defect_id="D-OPEN",
            payload={"data": [{"timestamp": "2026-10-01T00:00:00Z", "change_set": [{"field_name": "target_i_step_udf", "value": "NA05-27-07-460"}]}], "total_count": 1},
        )
    finally:
        store.close()


def test_prepare_from_database_needs_no_legacy_artifacts_and_preserves_source(tmp_path: Path) -> None:
    from backend.analytics.shift_database_workflow import prepare_database_workflow

    database = tmp_path / "source.db"
    _source_database(database)
    before = sha256(database.read_bytes()).hexdigest()
    run = tmp_path / "brand_new_run"
    manifest = prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")
    assert manifest["ticket_ids"] == ["D-OPEN", "D-PROCESS"]
    assert manifest["source_mode"] == "database_snapshot"
    assert Path(manifest["snapshot_path"]).exists()
    inputs = [json.loads(line) for line in Path(manifest["inputs_path"]).read_text(encoding="utf-8").splitlines()]
    assert {item["ticket_id"] for item in inputs} == {"D-OPEN", "D-PROCESS"}
    assert all(item["ticket"]["source_mode"] == "database_snapshot" for item in inputs)
    assert manifest["source_audit"]["current_verified_ticket_count"] == 0
    assert manifest["source_audit"]["database_snapshot_ticket_count"] == 2
    refresh_scope = json.loads((run / "selected_ticket_scope.json").read_text(encoding="utf-8"))
    assert refresh_scope["artifact_type"] == "ingestion_scope_only"
    assert [item["ticket_id"] for item in refresh_scope["ticket_mappings"]] == manifest["ticket_ids"]
    assert sha256(database.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.xlsx"))
    connection = sqlite3.connect(database)
    try:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='octane_ticket_evidence_refresh_state'").fetchone()
    finally:
        connection.close()
    assert prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")["ticket_ids"] == manifest["ticket_ids"]
    with pytest.raises(ValueError, match="scope"):
        prepare_database_workflow(database, run, project="OTHER", created_from="2025-01-01")


def test_database_analysis_fingerprints_reuse_unchanged_evidence_across_refreshes(tmp_path: Path) -> None:
    from backend.analytics.shift_database_workflow import build_database_workflow, prepare_database_workflow

    database = tmp_path / "source.db"
    _source_database(database)
    first = prepare_database_workflow(database, tmp_path / "first", project="IDCEVO", created_from="2025-01-01")
    before = {item["ticket_id"]: item for line in Path(first["inputs_path"]).read_text(encoding="utf-8").splitlines() for item in [json.loads(line)]}
    connection = sqlite3.connect(database)
    connection.execute("UPDATE octane_defects SET fetched_at='2026-10-22T09:00:00Z'")
    connection.execute("UPDATE octane_defect_history_events SET fetched_at='2026-10-22T09:00:00Z'")
    connection.commit()
    connection.close()
    second = prepare_database_workflow(database, tmp_path / "second", project="IDCEVO", created_from="2025-01-01")
    after = {item["ticket_id"]: item for line in Path(second["inputs_path"]).read_text(encoding="utf-8").splitlines() for item in [json.loads(line)]}
    assert first["snapshot_sha256"] != second["snapshot_sha256"]
    assert first["source_audit"]["earliest_evidence_refresh"] != second["source_audit"]["earliest_evidence_refresh"]
    assert before == after
    shared_cache = tmp_path / "persistent_model_cache.jsonl"
    _fixture_summaries(first, shared_cache)
    outputs = build_database_workflow(tmp_path / "second", summaries_path=shared_cache)
    rebuilt = json.loads(outputs["knowledge_base_json"].read_text(encoding="utf-8"))
    assert rebuilt["summary"]["ticket_count"] == 2
    assert rebuilt["source_evidence_audit"]["earliest_evidence_refresh"] == second["source_audit"]["earliest_evidence_refresh"]
    connection = sqlite3.connect(database)
    raw = json.loads(connection.execute("SELECT raw_json FROM octane_defects WHERE defect_id='D-OPEN'").fetchone()[0])
    raw["comments"].append({"id": "2", "creation_time": "2026-10-22T10:00:00Z", "text": "A new recurrence was reported."})
    connection.execute("UPDATE octane_defects SET comments=?, raw_json=? WHERE defect_id='D-OPEN'", (json.dumps(raw["comments"]), json.dumps(raw)))
    connection.commit()
    connection.close()
    third = prepare_database_workflow(database, tmp_path / "third", project="IDCEVO", created_from="2025-01-01")
    changed = {item["ticket_id"]: item for line in Path(third["inputs_path"]).read_text(encoding="utf-8").splitlines() for item in [json.loads(line)]}
    assert changed["D-OPEN"]["input_fingerprint"] != before["D-OPEN"]["input_fingerprint"]
    assert changed["D-PROCESS"]["input_fingerprint"] == before["D-PROCESS"]["input_fingerprint"]


def test_resume_rejects_previous_fingerprint_policy_before_model_stage(tmp_path: Path) -> None:
    from backend.analytics.shift_database_workflow import prepare_database_workflow

    database = tmp_path / "source.db"
    _source_database(database)
    run = tmp_path / "old_run"
    manifest = prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")
    manifest["source_audit"].pop("analysis_fingerprint_policy")
    (run / "database_workflow_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint policy"):
        prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")


def _fixture_summaries(manifest: dict, output: Path) -> None:
    from backend.analytics.shift_current_evidence import ANALYSIS_FIELDS, STATUS_VALUES

    summaries = []
    for line in Path(manifest["inputs_path"]).read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        summaries.append({
            **{field: "Offline fixture analysis; source freshness requires review." for field in ANALYSIS_FIELDS},
            "issue_summary": "Navigation guidance disappears after restart.",
            "ticket_id": item["ticket_id"], "input_version": item["input_version"],
            "input_fingerprint": item["input_fingerprint"], "source": "llm", "model": "fixture-no-network-call",
            "historical_shift_status": "uncertain", "current_postponement_status": "unclear",
            "repair_status": "unknown", "delivery_status": "unknown",
            "status_evidence": {field: [] for field in STATUS_VALUES},
        })
    output.write_text("".join(json.dumps(item) + "\n" for item in summaries), encoding="utf-8")


def test_database_workflow_builds_all_native_outputs_from_snapshot_and_matching_summaries(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    from backend.analytics.shift_database_workflow import build_database_workflow, prepare_database_workflow

    database = tmp_path / "source.db"
    _source_database(database)
    run = tmp_path / "run"
    manifest = prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")
    summaries = run / "fixture_summaries.jsonl"
    _fixture_summaries(manifest, summaries)
    before = sha256(database.read_bytes()).hexdigest()
    outputs = build_database_workflow(run, summaries_path=summaries, output_directory=run / "reports")
    assert all(path.is_file() for path in outputs.values())
    payload = json.loads(outputs["knowledge_base_json"].read_text(encoding="utf-8"))
    assert payload["summary"]["ticket_count"] == 2
    assert payload["summary"]["current_evidence_verified_count"] == 0
    assert payload["summary"]["database_snapshot_ticket_count"] == 2
    assert {item["source_evidence_status"] for item in payload["scenarios"]} == {"database_snapshot"}
    assert payload["source_evidence_audit"]["live_source_freshness_verified"] is False
    assert payload["catalog_filters"]["excluded_phases"] == ["05", "08"]
    assert "database snapshot" in outputs["catalog_html"].read_text(encoding="utf-8").lower()
    workbook = load_workbook(outputs["triple_xlsx"], read_only=True, data_only=True)
    rows = workbook["01_Scenario_Library"].iter_rows(values_only=False)
    headers = [cell.value for cell in next(rows)]
    detail = list(rows)
    assert len(detail) == 2
    assert all(row[headers.index("Ticket Creation Time")].is_date for row in detail)
    assert {row[headers.index("Source Evidence Status")].value for row in detail} == {"database_snapshot"}
    workbook.close()
    assert sha256(database.read_bytes()).hexdigest() == before
    original_output_hash = sha256(outputs["knowledge_base_json"].read_bytes()).hexdigest()
    snapshot = sqlite3.connect(manifest["snapshot_path"])
    snapshot.execute("UPDATE octane_defects SET description='changed after model input'")
    snapshot.commit()
    snapshot.close()
    with pytest.raises(ValueError, match="snapshot.*fingerprint"):
        build_database_workflow(run, summaries_path=summaries, output_directory=run / "reports")
    assert sha256(outputs["knowledge_base_json"].read_bytes()).hexdigest() == original_output_hash


def test_database_build_rejects_missing_or_stale_model_coverage_before_outputs(tmp_path: Path) -> None:
    from backend.analytics.shift_database_workflow import build_database_workflow, prepare_database_workflow

    database = tmp_path / "source.db"
    _source_database(database)
    run = tmp_path / "run"
    manifest = prepare_database_workflow(database, run, project="IDCEVO", created_from="2025-01-01")
    summaries = run / "fixture_summaries.jsonl"
    _fixture_summaries(manifest, summaries)
    entries = [json.loads(line) for line in summaries.read_text(encoding="utf-8").splitlines()]
    entries[0]["input_fingerprint"] = "stale"
    summaries.write_text("".join(json.dumps(item) + "\n" for item in entries), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        build_database_workflow(run, summaries_path=summaries, output_directory=run / "must_not_exist")
    assert not (run / "must_not_exist").exists()


def test_database_mode_cli_prepares_and_builds_without_legacy_arguments(tmp_path: Path) -> None:
    import subprocess
    import sys

    database = tmp_path / "source.db"
    _source_database(database)
    run = tmp_path / "new_run"
    repository = Path(__file__).resolve().parents[2]
    script = repository / "scripts/rebuild_shift_current_evidence.py"
    prepared = subprocess.run(
        [sys.executable, str(script), "--from-database", "--project", "IDCEVO", "--created-from", "2025-01-01", "--db-path", str(database), "--run-directory", str(run)],
        cwd=repository, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert prepared.returncode == 0, prepared.stderr
    manifest = json.loads((run / "database_workflow_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ticket_ids"] == ["D-OPEN", "D-PROCESS"]
    summaries = run / "fixture_summaries.jsonl"
    _fixture_summaries(manifest, summaries)
    built = subprocess.run(
        [sys.executable, str(script), "--from-database", "--run-directory", str(run), "--summaries", str(summaries), "--output-directory", str(run / "published_candidate")],
        cwd=repository, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert built.returncode == 0, built.stderr
    result = json.loads((run / "candidate_build_summary.json").read_text(encoding="utf-8"))
    assert result["ticket_count"] == 2
    assert all(Path(path).is_file() for path in result["outputs"].values())
    assert not (run / "ticket_scope.json").exists()
    assert not (run / "source_refresh_summary.json").exists()
