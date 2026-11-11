from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


REASSESSMENT_DISPOSITIONS = {
    "retain_candidate", "superseded_framework_assumption", "already_addressed_in_new_design", "needs_domain_review",
}
NATIVE_MODULES = {"cn_speech", "navigation", "music", "climate", "window_control", "vehicle_apps", "settings", "phone"}
ACTION_FIELDS = {
    "speak_text": "text", "input_text": "text", "click_text": "text", "click_control": "control",
}
VALIDATION_TYPES = {"asr", "ocr", "diagnostic", "llm", "asr_then_llm", "ocr_then_llm", "diagnostic_then_llm"}


def attach_prior_audit(library: dict[str, Any], audit_library: dict[str, Any]) -> dict[str, Any]:
    by_key = {case["case_key"]: case for case in audit_library["cases"]}
    reports = {record["case_key"]: record for record in audit_library["business_audits"]}
    result = copy.deepcopy(library)
    for case in result["cases"]:
        prior = by_key.get(case["case_key"])
        if prior is None or prior["octane_script"] != case["octane_script"]:
            raise ValueError("The prior audit source does not match frozen case " + case["case_key"])
        record = reports.get(case["case_key"])
        if record is None or record["status"] != "audit_validated":
            raise ValueError("The prior audit report is not complete for " + case["case_key"])
        case["prior_audit_findings"] = copy.deepcopy(record["audit"]["findings"])
    return result


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_output_location(output: Path, target: Path, source_hash: str, prior_hash: str) -> None:
    if output.resolve().is_relative_to(target.resolve()):
        raise ValueError("Draft output must remain outside the target repository")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if any(re.fullmatch(r"\d{8}", part) and part < today for part in output.parts):
        raise ValueError("Use a fresh dated release; do not overwrite a past dated output")
    if output.is_dir() and any(output.iterdir()):
        manifest = output / "regeneration_manifest.json"
        if not manifest.is_file():
            raise ValueError("Refuse to overwrite an existing unrelated release")
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if (saved.get("target_framework") != "AgenticAutomation"
                or saved.get("frozen_original_source_sha256") != source_hash
                or saved.get("prior_audit_sha256") != prior_hash):
            raise ValueError("The existing release source/framework provenance does not match this run")


def validate_review_sources(library: dict[str, Any], frozen: dict[str, Any]) -> None:
    originals = {case["case_key"]: case for case in frozen["cases"]}
    for record in library["model_reviews"]:
        if record["status"] != "validated_design":
            raise ValueError("A native export source review is not validated: " + record["case_key"])
        source = {key: value for key, value in record["review"]["source_case"].items()
                  if key != "reference_case_ids_available"}
        if source != originals.get(record["case_key"]):
            raise ValueError("The reviewed source does not match the supplied frozen input: " + record["case_key"])


def _check_numbered_commands(source: dict[str, Any], design: dict[str, Any]) -> None:
    pattern = r'(?im)^\s*\d+[.)]\s*(?:say|speak)\s*:\s*["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]'
    for number, original in enumerate(source["procedure_steps"], start=1):
        wanted = [_normalized(value) for value in re.findall(pattern, original["action"])]
        actual = [_normalized(action["text"]) for step in design["steps"] if number in step["source_step_numbers"]
                  for action in step["native_actions"] if action["action"] == "speak_text"]
        if wanted and actual and actual != wanted:
            raise ValueError("Incomplete or reordered numbered source commands for paragraph " + str(number)
                             + ": required " + json.dumps(wanted, ensure_ascii=False)
                             + "; mapped " + json.dumps(actual, ensure_ascii=False)
                             + ". Map every numbered command in order or leave its native actions empty with explicit missing capabilities.")


def _validate_oracle(validation: dict[str, Any], inventory: dict[str, Any]) -> None:
    kind = validation.get("type")
    if kind not in VALIDATION_TYPES:
        raise ValueError("Unknown native validation type " + repr(kind) + "; allowed: " + ", ".join(sorted(VALIDATION_TYPES))
                         + ". Do not invent composite validators. Keep the complete human Verify requirement, "
                         "but use native_validation={} with explicit missing_capabilities when no complete native mapping exists.")
    if validation.get("source") == "generated_wav":
        raise ValueError("generated_wav is input verification, not vehicle feedback; use recorded microphone feedback")
    if validation.get("enabled") is False or validation.get("llm_enabled") is False:
        raise ValueError("A disabled validator cannot supply a business PASS")
    if kind in {"asr", "asr_then_llm", "ocr", "ocr_then_llm"}:
        prefix = "asr" if kind.startswith("asr") else "ocr"
        fields = (prefix + "_contains", prefix + "_any_contains")
        values = [validation.get(field, []) for field in fields]
        if not any(values) or any(not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value) for value in values):
            raise ValueError("A native " + prefix + " oracle needs nonempty " + " or ".join(fields))
    if kind in {"diagnostic", "diagnostic_then_llm"}:
        expect = validation.get("diagnostic", {}).get("expect", {})
        if not isinstance(expect, dict) or not expect:
            raise ValueError("A diagnostic oracle needs specific target-state rules")
        unknown = set(expect) - set(inventory.get("diagnostic_keys", []))
        if unknown:
            raise ValueError("Unknown diagnostic target keys: " + ", ".join(sorted(unknown)))
        targets = {key: rule for key, rule in expect.items()
                   if not key.endswith((".status", ".job_status", ".enabled", ".error"))}
        if not targets or any(not isinstance(rule, dict) or set(rule) != {"equals"} or rule["equals"] is None for rule in targets.values()):
            raise ValueError("A diagnostic oracle needs an equals rule for actual target state, not communication success or field presence. "
                             "If the inventory has no diagnostic key for this requested function, set native_validation={} "
                             "and retain the missing target-state binding in this step's missing_capabilities; "
                             "never substitute climate/window job_status for another function's result.")
    if kind == "llm" or kind.endswith("_then_llm"):
        if not isinstance(validation.get("llm_query"), str) or not validation["llm_query"].strip():
            raise ValueError("An executed LLM oracle needs a top-level case-specific llm_query alongside type, "
                             'e.g. {"type":"llm","llm_query":"source-grounded question"}. '
                             "Do not nest llm_query inside an llm object; the native runner does not read that field.")


def validate_agentic_design(source: dict[str, Any], design: dict[str, Any], inventory: dict[str, Any]) -> None:
    if design.get("native_module") not in NATIVE_MODULES:
        raise ValueError("native_module must be one of: " + ", ".join(sorted(NATIVE_MODULES)))
    previous = 0
    for index, step in enumerate(design["steps"], start=1):
        numbers = step["source_step_numbers"]
        if min(numbers) < previous:
            raise ValueError("Native source action order changed at design step " + str(index))
        previous = min(numbers)
        actions = step.get("native_actions")
        validation = step.get("native_validation")
        if not isinstance(actions, list) or not isinstance(validation, dict):
            raise ValueError("Every step needs native_actions list and native_validation object; use []/{} with explicit missing_capabilities for unresolved mappings")
        if (not actions or not validation) and not step["missing_capabilities"] and design["feasibility"] not in {"manual_review", "insufficient_evidence"}:
            absent = [name for name, value in (("native_actions", actions), ("native_validation", validation)) if not value]
            raise ValueError("Unmapped native design step " + str(index) + ": " + ", ".join(absent)
                             + " is empty but this step's missing_capabilities is also empty. Add the actual unresolved "
                             "input/selector/observer/target-rule gap to THIS step's missing_capabilities list. "
                             "A global feasibility_reason does not record the per-step gap; do not invent a mapping to fill it.")
        source_actions = [_normalized(source["procedure_steps"][number - 1]["action"]) for number in numbers]
        for action in actions:
            if not isinstance(action, dict) or action.get("action") not in ACTION_FIELDS:
                raise ValueError("Unsupported native action; allowed: " + ", ".join(sorted(ACTION_FIELDS)))
            field = ACTION_FIELDS[action["action"]]
            value = action.get(field)
            if set(action) - {"action", field, "name"} or not isinstance(value, str) or not value.strip():
                raise ValueError("Native " + action["action"] + " accepts only action, " + field + " and optional name; do not invent timing or coordinates")
            if field == "control":
                if value not in inventory.get("control_ids", []):
                    raise ValueError("Unknown native control " + repr(value) + "; leave native_actions=[] and record the missing mapping")
            elif not any(_normalized(value) in text for text in source_actions):
                raise ValueError("Native input/text is absent from the referenced source action: " + repr(value)
                                 + "; actual referenced source actions: " + json.dumps(source_actions, ensure_ascii=False)
                                 + ". Copy ONLY these action substrings or leave native_actions=[] with a missing mapping. "
                                 "Do not promote expected-result text into an action. If the original action/expected boundary "
                                 "is mixed or only configuration conditions remain, keep native actions empty and classify "
                                 "the design as insufficient_evidence/manual_review with the source-curation question explicit.")
        if validation:
            _validate_oracle(validation, inventory)
    _check_numbered_commands(source, design)
    reassessment = design.get("prior_audit_reassessment")
    total = len(source.get("prior_audit_findings", []))
    if (not isinstance(reassessment, list) or len(reassessment) != total
            or any(not isinstance(item, dict) for item in reassessment)
            or {item.get("finding_index") for item in reassessment} != set(range(1, total + 1))):
        raise ValueError("The prior audit reassessment must cover every supplied finding_index exactly once: " + str(list(range(1, total + 1))))
    for item in reassessment:
        if item.get("disposition") not in REASSESSMENT_DISPOSITIONS or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError("Invalid prior opinion disposition/reason; allowed: " + ", ".join(sorted(REASSESSMENT_DISPOSITIONS)))
        if item.get("domain_approved") is True or item.get("domain_confirmed") is True:
            raise ValueError("A model reassessment cannot confirm a domain requirement")


def write_native_bundle(library: dict[str, Any], inventory: dict[str, Any], output_directory: Path) -> dict[str, Any]:
    from backend.analytics.cn_speech_business_audit import validate_case_binding

    if inventory.get("framework") != "AgenticAutomation":
        raise ValueError("Native export requires an AgenticAutomation capability inventory")
    if inventory.get("source_root") and output_directory.resolve().is_relative_to(Path(inventory["source_root"]).resolve()):
        raise ValueError("Native drafts must remain outside the target repository")
    records = {record["case_key"]: record for record in library["model_reviews"]}
    cases = []
    reassessments = []
    for case in library["cases"]:
        record = records[case["case_key"]]
        if record["status"] != "validated_design":
            raise ValueError("Native export requires validated design: " + case["case_key"])
        validate_case_binding(case, record)
        source, design = record["review"]["source_case"], record["review"]["design"]
        validate_agentic_design(source, design, inventory)
        native_steps = []
        utterance = ""
        gaps = []
        usable_validations = []
        for index, step in enumerate(design["steps"], start=1):
            actions = copy.deepcopy(step["native_actions"])
            if index == 1 and actions and actions[0]["action"] == "speak_text":
                utterance = actions.pop(0)["text"]
            native_steps.extend(actions)
            validation = step["native_validation"]
            incomplete = bool(step["missing_capabilities"]) or not step["native_actions"] or not validation
            if incomplete:
                gaps.append({"design_step": index, "missing": step["missing_capabilities"], "verify": step["verify_method"]})
            usable_validations.append({} if incomplete else copy.deepcopy(validation))
            if not incomplete and validation["type"] in {"ocr", "ocr_then_llm", "diagnostic", "diagnostic_then_llm", "llm"}:
                native_steps.append({"name": f"verify_{index}", "action": "validate_observation",
                                     "expected": step["expected_result"], "validation": copy.deepcopy(validation)})
            elif not incomplete and validation and len(design["steps"]) > 1:
                gaps.append({"design_step": index, "missing": ["Independent per-turn recorded ASR capture requires verified runner integration"]})
        final_validation = usable_validations[-1] if not gaps else {}
        if not final_validation:
            final_validation = {"type": "llm", "llm_query": "Evidence is incomplete. Return manual_check, never passed."}
        native = {"name": case["case_key"], "case_name": case["name"], "enabled": False,
                  "module": design["native_module"], "suite_kind": "mixed", "group": case.get("family", "cn_speech"),
                  "utterance": utterance, "steps": native_steps, "expected": "\n".join(step["expected_result"] for step in design["steps"]),
                  "validation": final_validation,
                  "source_meta": {"case_key": case["case_key"], "source_test_id": source.get("source_test_id"),
                                  "source_defect_ids": source.get("source_defect_ids", []),
                                  "source_full_script": source["octane_script"], "preconditions": design["preconditions"],
                                  "restoration_requirement": design["restoration"], "design_steps": design["steps"],
                                  "proposed_native_validations": [copy.deepcopy(step["native_validation"]) for step in design["steps"]],
                                  "oracle_coverage_status": "Unvalidated candidate; partial oracles with declared gaps are not active result assertions",
                                  "mapping_gaps": gaps, "domain_approved": False, "device_validated": False,
                                  "approval_status": "draft_needs_domain_review", "automation_ready": False,
                                  "promotion_blockers": ["Domain approval", "Fixture/selector/diagnostic validation", "Verified setup and restoration", "Required observers actually executed"]}}
        if gaps:
            native["source_meta"]["promotion_blockers"].append("Unresolved native step mapping")
        cases.append(native)
        reassessments.append({"case_key": case["case_key"], "original_findings": copy.deepcopy(source.get("prior_audit_findings", [])),
                              "reassessment": copy.deepcopy(design["prior_audit_reassessment"]), "domain_confirmed": False})
    modules = {case["module"] for case in cases} | {"cn_speech"}
    suite = {"suite_name": "CN_Speech_Review_Drafts", "module": "cn_speech", "runner": "hybrid_runner",
             "skill_path": "skill_yaml/cn_speech.skill.yaml", "wake_text": "", "wake_once": False,
             "bench_asr": {"enabled": True, "source": "microphone"},
             "module_bench_asr_enabled": {module: True for module in sorted(modules)},
             "module_llm_enabled": {module: True for module in sorted(modules)}, "llm_async": False,
             "cases": cases}
    skill = {"name": "cn_speech_review_skill", "description": "Draft CN Speech source-grounded judging context, not execution approval",
             "llm_context": ["Use fresh recorded vehicle feedback, never generated input WAV as vehicle response.",
                             "Require exact target-state observations; TTS acknowledgement or diagnostic communication success is insufficient.",
                             "Missing evidence or unexecuted validator means manual_check, never passed.",
                             "Retain source wording, multi-turn ownership, target zone and non-target invariance.",
                             "Domain/device approval and verified restoration are required before promotion."],
             "review_status": "draft_not_installed"}
    output_directory.mkdir(parents=True, exist_ok=True)
    for relative, value in (("suite_yaml/CN_Speech_Review_Drafts.yaml", suite), ("skill_yaml/cn_speech.skill.yaml", skill)):
        path = output_directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    dispositions = Counter(item["disposition"] for case in reassessments for item in case["reassessment"])
    summary = {"cases": len(cases), "enabled_cases": 0,
               "cases_with_mapping_gaps": sum(bool(case["source_meta"]["mapping_gaps"]) for case in cases),
               "prior_findings_reassessed": sum(len(case["reassessment"]) for case in reassessments),
               "reassessment_dispositions": dict(dispositions), "domain_approved": 0, "device_validated": 0,
               "deployment": "Drafts remain outside the target repository. Even disabled suites can initialize services/device tools at runner startup; do not invoke them.",
               "files_sha256": {relative: hashlib.sha256((output_directory / relative).read_bytes()).hexdigest()
                                for relative in ("suite_yaml/CN_Speech_Review_Drafts.yaml", "skill_yaml/cn_speech.skill.yaml")}}
    (output_directory / "prior_audit_reassessment.json").write_text(json.dumps(reassessments, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    (output_directory / "native_manifest.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    from backend.analytics.cn_speech_ta_regeneration import regenerate

    parser = argparse.ArgumentParser(description="Adapt frozen CN Speech evidence to review-gated AgenticAutomation YAML")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--prior-audit", type=Path, required=True)
    parser.add_argument("--framework-root", type=Path, default=Path("../AgenticAutomation"))
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--case-key", action="append")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    output = args.output_directory
    if output.resolve() in {args.source.parent.resolve(), args.prior_audit.parent.resolve()}:
        raise ValueError("Use a new output release; do not overwrite frozen source or prior audit")
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    prior_hash = hashlib.sha256(args.prior_audit.read_bytes()).hexdigest()
    validate_output_location(output, args.framework_root, source_hash, prior_hash)
    if not args.export_only:
        library = json.loads(args.source.read_text(encoding="utf-8"))
        prior = json.loads(args.prior_audit.read_text(encoding="utf-8"))
        prepared = attach_prior_audit(library, prior)
        output.mkdir(parents=True, exist_ok=True)
        input_path = output / "source_with_prior_audit.json"
        input_path.write_text(json.dumps(prepared, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        summary = regenerate(input_path, args.framework_root, output, model=args.model, workers=args.workers,
                             case_keys=args.case_key, framework="agentic")
        summary.update({"frozen_original_source_sha256": source_hash, "prior_audit_sha256": prior_hash,
                        "prior_audit_scope": "Unconfirmed model opinions, reassessed against the new framework"})
        (output / "regeneration_manifest.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if summary["failed_case_keys"]:
            print(json.dumps(summary, ensure_ascii=True, indent=2))
            return 1
    else:
        summary = json.loads((output / "regeneration_manifest.json").read_text(encoding="utf-8"))
    display = json.loads((output / "test_design_library.json").read_text(encoding="utf-8"))
    inventory = json.loads((output / "capability_inventory.json").read_text(encoding="utf-8"))
    validate_review_sources(display, attach_prior_audit(json.loads(args.source.read_text(encoding="utf-8")),
                                                      json.loads(args.prior_audit.read_text(encoding="utf-8"))))
    native = write_native_bundle(display, inventory, output)
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != source_hash or hashlib.sha256(args.prior_audit.read_bytes()).hexdigest() != prior_hash:
        raise ValueError("Frozen source/prior audit changed during the run")
    print(json.dumps({"regeneration": summary, "native": native}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())