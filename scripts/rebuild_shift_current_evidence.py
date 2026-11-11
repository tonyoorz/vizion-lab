from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_shift_scenario_triple import (
    build_merged_shift_tolerate_artifact_triple,
    load_open_shift_raw_evidence,
)
from backend.analytics.config import get_full_picture_source_db_path
from backend.analytics.shift_current_evidence import (
    apply_current_evidence,
    load_current_evidence_inputs,
)
from backend.analytics.shift_database_workflow import build_database_workflow, prepare_database_workflow


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare/build Shift/Tolerate analysis from a scoped database snapshot or an approved repaired KB.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--knowledge-base", type=Path)
    source.add_argument("--from-database", action="store_true")
    parser.add_argument("--repair-directory", "--run-directory", dest="repair_directory", required=True, type=Path)
    parser.add_argument("--project")
    parser.add_argument("--created-from")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--db-path", type=Path, default=get_full_picture_source_db_path())
    parser.add_argument("--summaries", type=Path)
    parser.add_argument("--maximum-age-hours", type=int, default=24)
    parser.add_argument("--priority-ticket", action="append", default=[])
    args = parser.parse_args()
    if args.from_database:
        if args.priority_ticket:
            parser.error("--priority-ticket is only supported by the repaired-KB preparation mode")
        if args.summaries:
            manifest = json.loads((args.repair_directory / "database_workflow_manifest.json").read_text(encoding="utf-8"))
            if (args.project is not None and args.project != manifest["scope"]["project"]) or (args.created_from is not None and args.created_from != manifest["scope"]["created_from"]):
                raise ValueError("Build arguments differ from the prepared database scope")
            outputs = build_database_workflow(args.repair_directory, summaries_path=args.summaries, output_directory=args.output_directory)
            print(json.dumps({"source_mode": "database_snapshot", "outputs": {key: str(path.resolve()) for key, path in outputs.items()}}, indent=2), flush=True)
        else:
            if not args.project:
                parser.error("--from-database preparation requires an explicit --project")
            manifest = prepare_database_workflow(args.db_path, args.repair_directory, project=args.project, created_from=args.created_from or "")
            print(json.dumps({
                "source_mode": "database_snapshot", "scope": manifest["scope"],
                "ticket_count": len(manifest["ticket_ids"]),
                "database_scope_ticket_count": manifest["source_audit"]["database_scope_ticket_count"],
                "inputs_path": manifest["inputs_path"], "snapshot_path": manifest["snapshot_path"],
                "live_source_freshness_verified": False,
            }, indent=2), flush=True)
        return 0
    if args.project is not None or args.created_from is not None:
        parser.error("Project/date source selection requires --from-database; approved-KB scope is frozen")
    baseline = json.loads(args.knowledge_base.read_text(encoding="utf-8"))
    manifest = json.loads((args.repair_directory / "ticket_scope.json").read_text(encoding="utf-8"))
    refresh = json.loads((args.repair_directory / "source_refresh_summary.json").read_text(encoding="utf-8"))
    policy = json.loads((args.repair_directory / "source_refresh_policy.json").read_text(encoding="utf-8"))
    if refresh.get("expected_unavailable_ticket_ids") != policy["expected_unavailable_ticket_ids"] or not set(refresh["unavailable_ticket_ids"]) <= set(policy["expected_unavailable_ticket_ids"]):
        raise ValueError("Unavailable coverage does not match the reviewed source refresh policy")
    if str(args.db_path.resolve()) != manifest["source_database"] or str(args.db_path.resolve()) != refresh["source_database"]:
        raise ValueError("Resolved source database does not match the approved repair manifest")
    records = baseline["scenarios"]
    ticket_ids = [str(record["ticket_id"]) for record in records]
    if len(set(ticket_ids)) != len(ticket_ids) or set(ticket_ids) != set(manifest["ticket_ids"]):
        raise ValueError("Baseline scenarios do not match the approved exact ticket scope")
    unavailable = set(refresh["unavailable_ticket_ids"])
    inputs, current, audit = load_current_evidence_inputs(
        args.db_path, ticket_ids, unavailable, maximum_age_hours=args.maximum_age_hours,
    )
    priority = {ticket_id: index for index, ticket_id in enumerate(args.priority_ticket)}
    inputs.sort(key=lambda item: (priority.get(item["ticket_id"], len(priority)), item["ticket_id"]))
    input_path = args.repair_directory / "current_state_llm_inputs.jsonl"
    input_content = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in inputs)
    if args.summaries:
        previous = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if {item["ticket_id"]: item["input_fingerprint"] for item in previous} != {item["ticket_id"]: item["input_fingerprint"] for item in inputs}:
            raise ValueError("Source changed after LLM input preparation; prepare and reanalyse changed evidence first")
    else:
        input_path.write_text(input_content, encoding="utf-8")
    (args.repair_directory / "current_source_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)
    print(f"Current-state inputs: {input_path}", flush=True)
    if not args.summaries:
        return 0
    summaries = {
        str(item["ticket_id"]): item
        for line in args.summaries.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    enriched = apply_current_evidence(records, summaries, inputs, current, unavailable)
    counts = {field: dict(Counter(str(record[field]) for record in enriched)) for field in ("source_evidence_status", "historical_shift_status", "current_postponement_status", "repair_status", "delivery_status")}
    output_directory = args.output_directory or args.repair_directory / "candidate_v12"
    print("Loading raw local evidence for FullLLM source...", flush=True)
    raw_evidence = load_open_shift_raw_evidence(args.db_path, ticket_ids)
    print(f"Building synchronized source and triple: {len(enriched)} tickets, {len(raw_evidence)} evidence rows", flush=True)
    outputs = build_merged_shift_tolerate_artifact_triple(
        enriched, output_directory=output_directory,
        source_snapshot=audit["earliest_evidence_refresh"], raw_evidence=raw_evidence,
        source_evidence_audit=audit,
    )
    result = {"source_audit": audit, "assessment_counts": counts, "raw_evidence_rows": len(raw_evidence), "outputs": {key: str(value.resolve()) for key, value in outputs.items()}}
    (args.repair_directory / "candidate_build_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())