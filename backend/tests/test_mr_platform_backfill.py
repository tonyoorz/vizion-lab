import importlib
import json
import sqlite3

import pytest


def module():
    return importlib.import_module("backend.analytics.mr_platform_backfill")


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE octane_manual_runs (
            mr_id TEXT PRIMARY KEY, test_id TEXT, team TEXT, testplatformid TEXT,
            raw_json TEXT, status TEXT, started TEXT, finished TEXT, creation_time TEXT
        )""")
        connection.executemany("INSERT INTO octane_manual_runs VALUES (?,?,?,?,?,?,?,?,?)", [
            ("1", "11", "DTSV_China", "", "{}", "Passed", "2025-01-01", "2025-01-02", "2025-01-01"),
            ("2", "12", None, "", json.dumps({"run_team_000_udf": {"name": "DTSV_China"}}), "Failed", "", "2026-01-02", "2026-01-01"),
            ("3", "13", "DTSV_Germany", "", "{}", "Passed", "", "2026-01-02", "2026-01-01"),
            ("4", "14", "DTSV_China", "KNOWN", "{}", "Passed", "", "2026-01-02", "2026-01-01"),
            ("5", "15", "DTSV_China", "", "{}", "Planned", "", "", "2026-01-01"),
        ])
    return path


def native_batch():
    return {"observed_at": "2026-11-10T09:00:00Z", "records": [
        {"id": "1", "test": {"id": "11"}, "testplatformid_udf": "WBY21CF02PCN18309", "last_modified": "2026-02-04T06:54:15Z"},
        {"id": "2", "test": {"id": "12"}, "testplatformid_udf": None, "last_modified": "2026-01-01T00:00:00Z"},
    ]}


def test_plan_selects_exact_team_missing_identifiers_without_source_mutation(source_db):
    before = source_db.read_bytes()
    plan = module().build_plan(source_db, batch_size=2)
    assert [(row["mr_id"], row["test_id"]) for row in plan["targets"]] == [("1", "11"), ("2", "12"), ("5", "15")]
    assert [row["ids"] for row in plan["batches"]] == [["1", "2"], ["5"]]
    assert source_db.read_bytes() == before


def test_batch_validation_distinguishes_native_empty_and_testcase_mismatch(source_db):
    plan = module().build_plan(source_db, batch_size=2)
    result = module().validate_batch(plan, "000000", native_batch())
    assert result["records"][0]["capture_state"] == "native_populated"
    assert result["records"][1]["capture_state"] == "native_empty"
    assert result["records"][0]["observed_at"] == "2026-11-10T09:00:00Z"
    bad = native_batch()
    bad["records"][0]["test"]["id"] = "OTHER"
    result = module().validate_batch(plan, "000000", bad)
    assert [row["id"] for row in result["records"]] == ["2"]
    assert result["errors"][0]["reason"] == "testcase_mismatch"


@pytest.mark.parametrize("invalid", ["unexpected_id", "duplicate_id", "missing_timestamp", "missing_platform_field"])
def test_batch_rejects_unscoped_or_unobserved_evidence(source_db, invalid):
    plan = module().build_plan(source_db, batch_size=2)
    batch = native_batch()
    if invalid == "unexpected_id":
        batch["records"][0]["id"] = "999"
    elif invalid == "duplicate_id":
        batch["records"].append(batch["records"][0])
    elif invalid == "missing_timestamp":
        batch.pop("observed_at")
    else:
        batch["records"][0].pop("testplatformid_udf")
    with pytest.raises(ValueError):
        module().validate_batch(plan, "000000", batch)


def test_checkpoint_resume_and_snapshot_preserve_original_observation(source_db, tmp_path):
    job = module().BackfillJob(source_db, tmp_path / "job", batch_size=2)
    job.save_batch("000000", native_batch())
    resumed = module().BackfillJob(source_db, tmp_path / "job", batch_size=2)
    assert [row["ids"] for row in resumed.pending_batches()] == [["5"]]
    resumed.save_batch("000001", {"observed_at": "2026-11-10T09:01:00Z", "records": [
        {"id": "5", "test": {"id": "15"}, "testplatformid_udf": ""},
    ]})
    snapshot = resumed.finalize(apply=False)
    assert len(snapshot["records"]) == 3
    assert snapshot["fetched_at"] == "2026-11-10T09:01:00Z"
    assert snapshot["records"][0]["observed_at"] == "2026-11-10T09:00:00Z"
    assert snapshot["source_database_updated"] is False


def test_apply_updates_only_platform_column_and_preserves_a_recovery_journal(source_db, tmp_path):
    plan = module().build_plan(source_db, batch_size=2)
    batch = module().validate_batch(plan, "000000", native_batch())
    snapshot = {"fetched_at": "2026-11-10T09:00:00Z", "records": batch["records"], "team_name": "DTSV_China"}
    with sqlite3.connect(source_db) as connection:
        before = connection.execute("SELECT mr_id,test_id,team,raw_json,status,started,finished,creation_time FROM octane_manual_runs ORDER BY mr_id").fetchall()
    backup = tmp_path / "before_platform_values.json"
    result = module().apply_platform_snapshot(source_db, snapshot, backup)
    assert result["updated_nonempty"] == 1
    assert result["native_empty"] == 1
    assert backup.exists()
    with sqlite3.connect(source_db) as connection:
        after = connection.execute("SELECT mr_id,test_id,team,raw_json,status,started,finished,creation_time FROM octane_manual_runs ORDER BY mr_id").fetchall()
        assert connection.execute("SELECT testplatformid FROM octane_manual_runs WHERE mr_id='1'").fetchone()[0] == "WBY21CF02PCN18309"
        assert connection.execute("SELECT COUNT(*) FROM octane_mr_platform_observations").fetchone()[0] == 2
    assert after == before
    assert json.loads(backup.read_text(encoding="utf-8"))["rows"][0]["testplatformid"] == ""


def test_loopback_bridge_rejects_other_origins_and_authentication_headers(source_db, tmp_path):
    import threading

    import requests

    job = module().BackfillJob(source_db, tmp_path / "bridge", batch_size=2)
    server = module().make_server(job, api_path="/api/shared_spaces/1002/workspaces/2001/manual_runs")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        assert requests.get(url + "/plan", timeout=5).status_code == 403
        assert requests.get(url + "/plan", headers={"Origin": "https://example.com"}, timeout=5).status_code == 403
        assert requests.get(url + "/plan", headers={"Origin": "https://octane-prod.bmwgroup.net", "Authorization": "synthetic-only"}, timeout=5).status_code == 403
        response = requests.get(url + "/plan", headers={"Origin": "https://octane-prod.bmwgroup.net"}, timeout=5)
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://octane-prod.bmwgroup.net"
        assert response.json()["batches"][0]["ids"] == ["1", "2"]
        assert "Cookie" not in response.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_bridge_checkpoints_validated_records_and_reports_progress(source_db, tmp_path):
    import threading

    import requests

    job = module().BackfillJob(source_db, tmp_path / "bridge_capture", batch_size=2)
    server = module().make_server(job, api_path="/api/shared_spaces/1002/workspaces/2001/manual_runs")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    headers = {"Origin": "https://octane-prod.bmwgroup.net"}
    try:
        response = requests.post(url + "/batch/000000", headers=headers, json=native_batch(), timeout=5)
        assert response.status_code == 200
        assert response.json()["native_populated"] == 1
        assert response.json()["native_empty"] == 1
        assert requests.get(url + "/plan", headers=headers, timeout=5).json()["batches"][0]["ids"] == ["5"]
        assert requests.post(url + "/finish", headers=headers, json={}, timeout=5).status_code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)