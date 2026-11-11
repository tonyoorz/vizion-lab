from backend.analytics.phase09_process_v12 import build_process_full_records


def test_build_process_full_records_combines_process_evidence_and_existing_llm() -> None:
    records = build_process_full_records(
        [
            {
                "ticket_id": "D-PROCESS",
                "cohort": "Phase 09 FullLLM",
                "current_phase": "09-Concluded without action",
                "title": "Navigation guidance disappears",
                "source_software_version": "idcevo-pu2607-26w15.1-1",
                "function_responsible": "Owner A",
                "solution_cluster": "Navigation CN",
                "process_tier": "Formal Shift/Tolerate process",
                "process_basis": "Recorded decision date.",
                "process_matches": [
                    {
                        "source": "History",
                        "field_name": "postpone_toleratedate_udf",
                        "excerpt": "decision date: 2026-10-10",
                    }
                ],
            }
        ],
        {
            "D-PROCESS": {
                "issue_summary": "重启后导航引导消失。",
                "postponement_reason_analysis": "记录已进入延期容忍流程。",
                "resolution_timing_analysis": "决定日期为 2026-10-10。",
                "resolution_version_analysis": "目标版本为 27-07。",
                "review_focus": "核实该版本验证状态。",
                "llm_model": "deepseek-v4-pro",
                "rca_root_cause": "Recorded navigation state restore failure.",
                "rca_recovery": "Record and validate the state restore update.",
            }
        },
    )

    assert len(records) == 1
    record = records[0]
    assert record["service_function"] == "Navigation"
    assert "Formal Shift/Tolerate process" in record["future_resolution_plan"]
    assert "decision date: 2026-10-10" in record["future_resolution_plan"]
    assert record["postponement_reason_analysis"] == "记录已进入延期容忍流程。"
    assert record["rca_root_cause"] == "Recorded navigation state restore failure."
    assert record["rca_recovery"] == "Record and validate the state restore update."