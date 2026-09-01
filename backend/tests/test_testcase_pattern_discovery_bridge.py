from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
from pathlib import Path

import numpy as np
from openpyxl import load_workbook


def _load_testcase_bridge():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "testcase_bridge.py"
    spec = importlib.util.spec_from_file_location("testcase_pattern_bridge_for_tests", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                comments TEXT,
                project TEXT,
                assigned_ecu TEXT,
                lead_model TEXT,
                software_version TEXT,
                detected_in_release TEXT,
                phase TEXT,
                status_phase TEXT,
                severity TEXT,
                last_modified TEXT,
                creation_time TEXT
            );
            CREATE TABLE octane_testcases (defect_ids_json TEXT);
            CREATE TABLE octane_traceability_testcases (defect_ids_json TEXT);
            """
        )
        conn.executemany(
            """
            INSERT INTO octane_defects (
                defect_id, name, description, comments, project, assigned_ecu,
                lead_model, software_version, detected_in_release, phase,
                status_phase, severity, last_modified, creation_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "1",
                    "RSU activation times out after reboot",
                    "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow.",
                    "",
                    "RSU",
                    "RSE",
                    "G68",
                    "25-07",
                    "R-25-07",
                    "In Analysis",
                    "03-In Analysis",
                    "High",
                    "2026-09-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ),
                (
                    "2",
                    "RSU activation timeout during reboot",
                    "Start a remote software upgrade, reboot the vehicle, and observe the RSU activation timeout.",
                    "",
                    "RSU",
                    "RSE",
                    "G68",
                    "25-11",
                    "R-25-11",
                    "In Analysis",
                    "03-In Analysis",
                    "High",
                    "2026-09-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ),
                (
                    "3",
                    "RSU activation times out after reboot",
                    "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow.",
                    "",
                    "RSU",
                    "HU-MGU",
                    "G68",
                    "25-07",
                    "R-25-07",
                    "In Analysis",
                    "03-In Analysis",
                    "High",
                    "2026-09-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ),
                (
                    "4",
                    "RSU activation timeout after reboot",
                    "Start remote software upgrade, reboot the vehicle, and observe a timeout in the RSU activation flow.",
                    "",
                    "RSU",
                    "RSE",
                    "G68",
                    "25-07",
                    "R-25-07",
                    "In Analysis",
                    "03-In Analysis",
                    "High",
                    "2026-09-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ),
            ],
        )
        conn.execute("INSERT INTO octane_testcases (defect_ids_json) VALUES (?)", (json.dumps(["D-4"]),))


def test_discovery_persists_a_selected_unlinked_snapshot_and_clusters_it(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )

    result = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "defect_ids": ["D-1", "2", "3", "D-4"],
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result["success"] is True
    assert result["sourceSnapshot"]["matchedTotal"] == 4
    assert result["sourceSnapshot"]["linkedExcluded"] == 1
    assert result["sourceSnapshot"]["selectedCount"] == 3
    assert result["sourceSnapshot"]["policyVersion"]
    assert Path(result["sourceSnapshot"]["snapshotPath"]).is_file()
    assert [
        {member["defectId"] for member in pattern["members"]}
        for pattern in result["patterns"]
    ] == [{"1", "2"}, {"3"}]


def test_coverage_audit_reads_a_persisted_pattern_snapshot(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    assert snapshot["success"] is True
    monkeypatch.setattr(
        bridge,
        "_retrieve_pattern_testcase_candidates",
        lambda _representative: [{"testId": "900", "name": "Similar RSU testcase", "score": 0.91}],
        raising=False,
    )

    result = bridge._do_audit_defect_pattern_coverage(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": [snapshot["patterns"][0]["patternId"]],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result["success"] is True
    assert result["sourceSnapshot"]["runId"] == snapshot["sourceSnapshot"]["runId"]
    assert result["summary"]["audited_pattern_count"] == 1
    assert result["audits"][0]["semanticCandidates"][0]["testId"] == "900"
    assert Path(result["auditPath"]).is_file()


def test_pattern_preparation_returns_only_the_selected_snapshot_context(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    selected_pattern = snapshot["patterns"][0]
    monkeypatch.setattr(
        bridge,
        "_retrieve_pattern_testcase_candidates",
        lambda _representative: [],
        raising=False,
    )
    audit = bridge._do_audit_defect_pattern_coverage(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": [selected_pattern["patternId"]],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    assert audit["success"] is True
    monkeypatch.setattr(
        bridge,
        "_prepare_pattern_few_shot",
        lambda _defect: ([{"testId": "900", "name": "RSU reference", "score": 0.9}], "reference context"),
        raising=False,
    )

    result = bridge._do_prepare_reusable_testcase_patterns(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": [selected_pattern["patternId"]],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result["success"] is True
    assert result["summary"] == {"total": 1, "succeeded": 1, "failed": 0}
    context = result["contexts"][0]
    assert context["patternId"] == selected_pattern["patternId"]
    assert context["memberDefectIds"] == [member["defectId"] for member in selected_pattern["members"]]
    assert context["defectInfo"]["defect_id"] == selected_pattern["representative"]["defectId"]
    assert context["similarCases"] == [{"testId": "900", "name": "RSU reference", "score": 0.9}]
    assert context["fewShotText"] == "reference context"


def test_pattern_preparation_rejects_a_snapshot_selection_without_coverage_audit(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    monkeypatch.setattr(bridge, "_prepare_pattern_few_shot", lambda _defect: ([], ""), raising=False)

    result = bridge._do_prepare_reusable_testcase_patterns(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": [snapshot["patterns"][0]["patternId"]],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result == {"success": False, "error": "matching coverage audit is required before pattern draft preparation"}


def test_pattern_preparation_accepts_a_subset_of_patterns_from_the_same_coverage_audit(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    pattern_ids = [pattern["patternId"] for pattern in snapshot["patterns"]]
    monkeypatch.setattr(bridge, "_retrieve_pattern_testcase_candidates", lambda _representative: [], raising=False)
    audit = bridge._do_audit_defect_pattern_coverage(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": pattern_ids,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    assert audit["success"] is True
    monkeypatch.setattr(bridge, "_prepare_pattern_few_shot", lambda _defect: ([], ""), raising=False)

    result = bridge._do_prepare_reusable_testcase_patterns(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": [pattern_ids[0]],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result["success"] is True
    assert result["summary"] == {"total": 1, "succeeded": 1, "failed": 0}


def test_pattern_preparation_accepts_patterns_covered_by_separate_audit_batches(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    pattern_ids = [pattern["patternId"] for pattern in snapshot["patterns"]]
    monkeypatch.setattr(bridge, "_retrieve_pattern_testcase_candidates", lambda _representative: [], raising=False)
    for pattern_id in pattern_ids:
        audit = bridge._do_audit_defect_pattern_coverage(
            {
                "run_id": snapshot["sourceSnapshot"]["runId"],
                "pattern_ids": [pattern_id],
                "snapshot_output_dir": str(tmp_path / "snapshots"),
            }
        )
        assert audit["success"] is True
    monkeypatch.setattr(bridge, "_prepare_pattern_few_shot", lambda _defect: ([], ""), raising=False)

    result = bridge._do_prepare_reusable_testcase_patterns(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "pattern_ids": pattern_ids,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )

    assert result["success"] is True
    assert result["summary"] == {"total": 2, "succeeded": 2, "failed": 0}


def test_pattern_verification_includes_pattern_specific_criteria() -> None:
    bridge = _load_testcase_bridge()

    result = bridge._do_verify_reusable_testcase_pattern(
        {
            "defect_info": {
                "defect_id": "1",
                "name": "RSU activation timeout after reboot",
            },
            "pattern_id": "pattern-rsu-1",
            "member_defect_ids": ["1", "2"],
            "parameter_matrix": {
                "softwareVersions": ["25-07", "25-11"],
                "detectedInReleases": ["R-25-07", "R-25-11"],
            },
            "description_html": "Reference D1 Pattern ID: pattern-rsu-1 uses 25-07, 25-11, R-25-07, and R-25-11.",
            "steps_text": "\n".join(
                [
                    "- [PreCon] RSU activation test environment is available.",
                    "- Start RSU activation.",
                    "- Reboot the vehicle.",
                    "- Observe the activation timeout state.",
                    "- ? Activation completes within 30 seconds after reboot.",
                ]
            ),
        }
    )

    assert result["success"] is True
    criteria = {criterion["name"]: criterion for criterion in result["verification"]["criteria"]}
    assert criteria["pattern_traceability"]["passed"] is True
    assert criteria["pattern_parameterization"]["passed"] is True


def test_pattern_draft_export_writes_reviewable_pattern_and_mapping_csvs(monkeypatch, tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()
    db_path = tmp_path / "source.db"
    _create_source_db(db_path)

    from backend.analytics import config

    monkeypatch.setattr(config, "get_full_picture_source_db_path", lambda: db_path)
    bridge._LINKED_DEFECT_CACHE = None
    monkeypatch.setattr(
        bridge,
        "encode_defect_evidence",
        lambda _texts: np.array([[1.0, 0.0], [0.99, 0.1], [1.0, 0.0]], dtype=np.float32),
        raising=False,
    )
    snapshot = bridge._do_discover_defect_patterns(
        {
            "keyword": "RSU",
            "similarity_threshold": 0.8,
            "snapshot_output_dir": str(tmp_path / "snapshots"),
        }
    )
    pattern = snapshot["patterns"][0]
    formula_like_description = "==================================\nIssue Description: RSU evidence"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE octane_defects SET description = ? WHERE defect_id = ?",
            (formula_like_description, "2"),
        )

    result = bridge._do_export_reusable_pattern_drafts(
        {
            "run_id": snapshot["sourceSnapshot"]["runId"],
            "snapshot_output_dir": str(tmp_path / "snapshots"),
            "drafts": [{
                "patternId": pattern["patternId"],
                "representativeDefectId": pattern["representative"]["defectId"],
                "memberDefectIds": [member["defectId"] for member in pattern["members"]],
                "memberCount": len(pattern["members"]),
                "commonAnchorTerms": pattern["commonAnchorTerms"],
                "parameterMatrix": pattern["parameterMatrix"],
                "name": "[RSU] Reusable activation regression",
                "descriptionHtml": "<p>Pattern ID: test</p>",
                "stepsText": "- [PreCon] RSU ready\n- Trigger activation\n- ? Completes within 30 seconds",
                "verification": {"passed": True, "feedback": ""},
                "similarCases": [{"testId": "900", "name": "Reference", "score": 0.9}],
                "generatedAt": "2026-09-01T00:00:00Z",
                "error": None,
            }],
        }
    )

    assert result["success"] is True
    with Path(result["patternDraftsCsvPath"]).open(encoding="utf-8-sig", newline="") as handle:
        draft_row = next(csv.DictReader(handle))
    with Path(result["defectMappingCsvPath"]).open(encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    assert draft_row["pattern_id"] == pattern["patternId"]
    assert draft_row["test_name"] == "[RSU] Reusable activation regression"
    assert {row["defect_id"] for row in mapping_rows} == {member["defectId"] for member in pattern["members"]}
    assert {row["review_status"] for row in mapping_rows} == {"pending"}

    workbook_path = Path(result["testcaseLibraryXlsxPath"])
    assert workbook_path.is_file()
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    assert workbook.sheetnames == [
        "00_总览",
        "01_主题概览",
        "02_标准Testcase库",
        "03_Defect映射",
        "04_评审队列",
        "05_RSU原始数据",
    ]
    raw_data = workbook["05_RSU原始数据"]
    raw_headers = [cell.value for cell in next(raw_data.iter_rows(min_row=1, max_row=1))]
    error_description_index = raw_headers.index("error_description")
    raw_rows = list(raw_data.iter_rows(min_row=2, values_only=True))
    descriptions_by_defect_id = {
        str(row[0]).removeprefix("D"): row[error_description_index]
        for row in raw_rows
    }
    assert descriptions_by_defect_id["1"] == (
        "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow."
    )
    assert descriptions_by_defect_id["2"] == formula_like_description


def test_excel_cell_text_marks_values_beyond_excel_limit() -> None:
    bridge = _load_testcase_bridge()

    value = bridge._excel_cell_text("x" * 32_768)

    assert len(value) == 32_767
    assert value.endswith("[Truncated: source text exceeds Excel's 32,767-character cell limit.]")
