from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def _load_testcase_bridge():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "testcase_bridge.py"
    spec = importlib.util.spec_from_file_location("testcase_bridge_for_tests", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_csv_converts_legacy_drafts_to_plain_text_workflow_columns(tmp_path: Path) -> None:
    bridge = _load_testcase_bridge()

    result = bridge._do_export_csv(
        {
            "keyword": "RSU",
            "filename": "rsu_plain.csv",
            "output_dir": str(tmp_path),
            "cluster": False,
            "drafts": [
                {
                    "defect_id": "2114499",
                    "defect_name": "",
                    "defect_severity": "High",
                    "software_version": "26-07",
                    "assigned_ecu": "RSU-APP",
                    "lead_model": "G68",
                    "project": "RSU",
                    "phase": "Fixed",
                    "test_name": "[RSU][26-07] Regression - INFO-prefixed DLT messages (D2114499)",
                    "description_html": (
                        "<html><body><h3>Objective</h3>"
                        "<p>Verify informational RSU DLT messages have the INFO prefix.</p>"
                        "<h3>Pass Criteria</h3><p>Every matching message contains INFO.</p>"
                        "</body></html>"
                    ),
                    "steps_text": (
                        "- [PreCon] Vehicle is ready for RSU application logging.\\n"
                        "- [PreCon] DLT logging is connected to the RSU application.\\n"
                        "- Trigger an informational RSU application log message.\\n"
                        "- ? Every matching <code>DLT</code> message contains the INFO prefix."
                    ),
                    "verification_passed": True,
                    "verification_feedback": "",
                    "similar_cases": "12345(0.8)",
                    "generated_at": "2026-09-01T00:00:00Z",
                    "error": "",
                }
            ],
        }
    )

    assert result["success"] is True
    with Path(result["csv_path"]).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
        assert reader.fieldnames == [
            "group_id",
            "is_primary",
            "similarity_to_primary",
            "defect_id",
            "defect_name",
            "defect_severity",
            "software_version",
            "assigned_ecu",
            "lead_model",
            "project",
            "phase",
            "test_name",
            "test_priority",
            "test_overview",
            "preconditions",
            "execution_steps",
            "expected_results",
            "verification_passed",
            "verification_feedback",
            "similar_cases",
            "generated_at",
            "error",
        ]

    assert row["defect_id"] == "2114499"
    assert row["defect_name"] == "Source title unavailable (D2114499)"
    assert row["test_priority"] == "P1"
    assert row["test_overview"] == (
        "Objective\nVerify informational RSU DLT messages have the INFO prefix.\n"
        "Pass Criteria\nEvery matching message contains INFO."
    )
    assert row["preconditions"] == (
        "Vehicle is ready for RSU application logging.\n"
        "DLT logging is connected to the RSU application."
    )
    assert row["execution_steps"] == "1. Trigger an informational RSU application log message."
    assert row["expected_results"] == "1. Every matching DLT message contains the INFO prefix."
    assert "description_html" not in row
    assert all("<" not in value and ">" not in value for value in row.values())