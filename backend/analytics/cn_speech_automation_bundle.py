from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


FAMILIES = (
    ("activation_dialog", "Activation and dialog lifecycle", "1695733 1696609 1711408 1806111 1884894 1913397", "Timestamped dialog-state sequence", "functional"),
    ("hotword_swys", "Hot words and say-what-you-see", "1694952 1694953 1887334 1915245", "Visible target association and resulting application/domain state", "functional"),
    ("media_apps", "Media and application commands", "1697189 1707998 1806100 1883947 1913396", "Track, source, playback mode and application foreground readback", "functional"),
    ("navigation", "Navigation and dictation", "1711409 1914405", "Recognized text plus destination/search/route state", "functional"),
    ("phone_messaging", "Phone and messaging", "1709598 1808034 1884902", "Synthetic call/message sink state, never live delivery", "controlled_fixture"),
    ("settings_modes", "System settings and My Modes", "1696611 1707999 1805206 1884899", "Independent setting/mode readback and restoration", "functional"),
    ("vehicle_control", "Vehicle control", "1709597 1913398 2528864 2698157 2698159 2730687 2731607 2733237 2735052", "Zone-specific physical/ECU state, not TTS confirmation", "supervised_fixture"),
    ("gui_privacy_variant", "GUI, consent and product variants", "1884895 1884901 2138183 2299090 2776304 2857261", "Accessibility/CJK OCR, consent state and variant-specific UI", "functional"),
    ("multi_seat", "Multi-seat interaction and handover", "1884900 2232952", "Seat attribution plus target change and non-target invariance", "spatial_fixture"),
    ("compound_arbitration", "Compound intents and arbitration", "1886871 2325120", "One independent result oracle per intended action", "controlled_fixture"),
    ("cloud_knowledge_llm", "Cloud knowledge and LLM answers", "1697190 1709599 1883943 2326422 2356140 2594559 2773162", "Deterministic UI/service facts separated from expert-reviewed answer quality", "hybrid_quality"),
    ("tts_voice", "TTS, voice and dialog presentation", "1808035 2328449 2734158", "Voice configuration/UI separately from calibrated audible quality", "hybrid_quality"),
    ("just_talk", "Just Talk and implicit activation", "2327249 2857253", "Eligible activation, transcript and non-target speech rejection", "acoustic_fixture"),
    ("customer_feedback", "Customer feedback capture", "2250356", "Non-delivering feedback sink and consent evidence", "controlled_fixture"),
    ("dialect_mixed_language", "Mixed-language and dialect commands", "2773506", "Approved speaker corpus, transcript and domain result", "acoustic_fixture"),
    ("parking_restriction", "Parking-view restrictions", "2771714", "Expected refusal, unchanged parking view and no prohibited action", "supervised_fixture"),
    ("unreleased", "Unreleased scope", "1692087", "No inferred tests for a Feature without coverage", "not_assessed"),
)


def parse_manual_script(script: str) -> dict[str, Any]:
    preconditions: list[str] = []
    precondition_lines: list[str] = []
    steps: list[dict[str, str]] = []
    unpaired_expected: list[str] = []
    context: list[str] = []
    in_precondition = False
    in_expected = False
    for raw_line in script.splitlines():
        value = re.sub(r"^-\s*", "", raw_line.strip()).strip()
        if not value:
            continue
        if re.search(r"\[\s*preconditions?\s+start\s*\]", value, re.I):
            in_precondition = True
            precondition_lines = []
            continue
        if re.search(r"\[\s*preconditions?\s+end\s*\]", value, re.I):
            preconditions.append("\n".join(precondition_lines))
            precondition_lines = []
            in_precondition = False
            continue
        if in_precondition:
            precondition_lines.append(value)
        elif value.startswith("?"):
            expected = value[1:].lstrip()
            if steps:
                steps[-1]["expected"] += ("\n" if steps[-1]["expected"] else "") + expected
            else:
                unpaired_expected.append(expected)
            in_expected = True
        elif raw_line.strip().startswith("-"):
            steps.append({"action": value, "expected": ""})
            in_expected = False
        elif steps:
            field = "expected" if in_expected else "action"
            steps[-1][field] += "\n" + value
        else:
            context.append(value)
    if precondition_lines:
        preconditions.append("\n".join(precondition_lines))
    utterances = []
    quote_pattern = r'"([^"\n]+)"|\u201c([^\u201d\n]+)\u201d|\u300c([^\u300d\n]+)\u300d|\u2018([^\u2019\n]+)\u2019'
    for step in steps:
        if re.search(r"\b(say|speak|utterance|command|speech|wakeup)\b", step["action"], re.I):
            for match in re.finditer(quote_pattern, step["action"]):
                text = next(group for group in match.groups() if group is not None)
                if text not in utterances:
                    utterances.append(text)
    return {
        "preconditions": preconditions,
        "steps": steps,
        "utterance_candidates": utterances,
        "utterance_candidates_require_curation": True,
        "unpaired_expected": unpaired_expected,
        "unclassified_context": context,
        "unclosed_preconditions": in_precondition,
    }


def build_case_catalog(snapshot: dict[str, Any]) -> dict[str, Any]:
    features = {str(item["id"]): item for item in snapshot["work_items"]
                if item.get("subtype") == "feature"}
    family_by_feature = {
        feature_id: family[0] for family in FAMILIES for feature_id in family[2].split()
    }
    cases = []
    non_manual = []
    for test in snapshot["tests"]:
        metadata = test["metadata"]
        if metadata.get("subtype") != "test_manual":
            non_manual.append({"id": test["id"], "title": metadata.get("name"),
                               "subtype": metadata.get("subtype"),
                               "metadata_source": "evidence_snapshot.json",
                               "executable_speech_case": False})
            continue
        script = test.get("script")
        if not isinstance(script, dict) or not isinstance(script.get("script"), str):
            raise ValueError(f'Missing script evidence for test {test["id"]}')
        feature_ids = [identifier for identifier in test["covered_work_item_ids"]
                       if identifier in features]
        parsed = parse_manual_script(script["script"])
        cases.append({
            "octane_test_id": test["id"],
            "title": metadata.get("name"),
            "description_html": metadata.get("description") or "",
            "feature_ids": feature_ids,
            "family_candidates": sorted({family_by_feature[identifier]
                                         for identifier in feature_ids
                                         if identifier in family_by_feature}),
            "classification_basis": "Feature routing plus full lossless script parsing; semantic review required",
            "readiness": "requires_review_and_adapters",
            "source_last_modified": metadata.get("last_modified"),
            "source_script": script,
            "parsed": parsed,
            "external_evidence_references": sorted(set(re.findall(
                r'https?://[^\s<>"\']+', script["script"]
            ))),
            "audio_assets_approved": False,
            "target_device_validated": False,
        })
    feature_coverage = [{
        "id": identifier,
        "name": feature["name"],
        "manual_cases": sum(identifier in case["feature_ids"] for case in cases),
        "all_covered_records": sum(identifier in test["covered_work_item_ids"]
                                   for test in snapshot["tests"]),
    } for identifier, feature in features.items()]
    return {
        "schema_version": 1,
        "document_type": "non_executable_candidate_specification",
        "epic_id": str(snapshot["epic"]["id"]),
        "automation_ready_count": 0,
        "families": [{"id": identifier, "name": name,
                      "feature_ids": identifiers.split(), "oracle": oracle, "lane": lane,
                      "case_count": sum(identifier in case["family_candidates"] for case in cases)}
                     for identifier, name, identifiers, oracle, lane in FAMILIES],
        "feature_coverage": feature_coverage,
        "cases": cases,
        "non_manual_records": non_manual,
        "unmapped_case_ids": [case["octane_test_id"] for case in cases
                              if not case["family_candidates"]],
    }


def _step(action: str, parameters: dict[str, Any], *assertions: str) -> dict[str, Any]:
    return {"action": action, "parameters": parameters,
            "assertions": [{"name": name, "parameters": {}} for name in assertions]}


def build_mvp_cases(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = [
        {"octane_test_id": "1899553", "fixture": "Stationary rig, Mandarin, network, verified PTT actuator and hidden dialog",
         "steps": [_step("press_ptt", {}, "dialog_appearing_then_listening"),
                   _step("press_ptt", {}, "dialog_listening_then_disappearing")]},
        {"octane_test_id": "1220071", "fixture": "SWYS and Snoozing ON, All apps, navigation icon visible, approved animation tolerance",
         "steps": [_step("speak", {"text": "\u5bfc\u822a"},
                         "navigation_icon_animation_one_second_with_reviewed_tolerance", "navigation_app_foreground")]},
        {"octane_test_id": "1217000", "fixture": "Seeded favorites, USB media and paired Bluetooth entertainment, reversible sources",
         "steps": [_step("select_media_source", {"source": "usb"}, "usb_source_active"),
                   _step("speak", {"text": "\u6536\u85cf\u8fd9\u9996\u6b4c"},
                         "unsupported_operation_tts", "favorites_unchanged"),
                   _step("select_media_source", {"source": "bluetooth"}, "bluetooth_source_active"),
                   _step("speak", {"text": "\u6536\u85cf\u8fd9\u9996\u6b4c"},
                         "unsupported_operation_tts", "favorites_unchanged")]},
        {"octane_test_id": "1218449", "fixture": "QQMusic playing a known seeded queue, recorded baseline mode",
         "steps": [_step("speak", {"text": "\u4f60\u597d\u5b9d\u9a6c\uff0c\u5355\u66f2\u5faa\u73af"}, "playback_mode_repeat_one"),
                   _step("speak", {"text": "\u4f60\u597d\u5b9d\u9a6c\uff0c\u968f\u673a\u64ad\u653e"}, "playback_mode_shuffle"),
                   _step("speak", {"text": "\u4f60\u597d\u5b9d\u9a6c\uff0c\u5217\u8868\u5faa\u73af"}, "playback_mode_repeat_all")]},
        {"octane_test_id": "1522678", "fixture": "Supervised stationary rig, climate OFF, independent climate-state reader, network",
         "steps": [_step("speak", {"text": "\u6253\u5f00\u7a7a\u8c03"},
                         "climate_enabled_independent_readback", "no_bmw_ai_generation_banner")]},
    ]
    by_id = {case["octane_test_id"]: case for case in catalog["cases"]}
    for definition in definitions:
        identifier = definition["octane_test_id"]
        if identifier not in by_id:
            raise ValueError(f"MVP source test {identifier} is absent from the snapshot")
        source = by_id[identifier]
        definition["title"] = source["title"]
        definition["source_version_stamp"] = source["source_script"].get("version_stamp")
        definition["readiness"] = "requires_review_and_adapters"
        definition["assertions_require_review"] = True
        definition["strengthened_assertions"] = (
            ["favorites_unchanged"] if identifier == "1217000" else []
        )
        for step in definition["steps"]:
            utterance = step["parameters"].get("text")
            if utterance and utterance not in source["source_script"]["script"]:
                raise ValueError(f"MVP utterance is not verbatim source evidence: {identifier}")
    return definitions


def native_runner_source() -> str:
    return '''r"""Deferred, adapter-driven Octane CN Speech MVP; no default device access.

Run (Windows PowerShell):
    uv run python drafts\\cn_speech_mvp.py

Run (macOS/Linux):
    uv run python drafts/cn_speech_mvp.py
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path


def _checked(callback):
    try:
        return callback() is True
    except Exception as error:
        print("Adapter operation failed: " + type(error).__name__)
        return False


def _cases_have_oracles(cases):
    if not isinstance(cases, list) or not cases:
        return False
    for case in cases:
        if not isinstance(case, dict) or not case.get("steps"):
            return False
        for step in case["steps"]:
            if not isinstance(step, dict) or not step.get("action"):
                return False
            assertions = step.get("assertions")
            if not isinstance(assertions, list) or not assertions:
                return False
            if any(not isinstance(item, dict) or not item.get("name") for item in assertions):
                return False
    return True


def run_test(device_ip=None, test_execution_key=None, *, adapter=None,
             cases=None, executor_factory=None):
    if adapter is None or test_execution_key is not None:
        print("INCONCLUSIVE: reviewed adapters are absent; external reporting is disabled.")
        return None
    if cases is None:
        path = Path(__file__).with_name("mvp_cases.json")
        if not path.is_file():
            print("INCONCLUSIVE: MVP data is missing.")
            return None
        cases = json.loads(path.read_text(encoding="utf-8"))
    if not _cases_have_oracles(cases):
        print("INCONCLUSIVE: MVP actions or independent result oracles are missing.")
        return None
    if not _checked(lambda: adapter.preflight(cases, device_ip)):
        print("INCONCLUSIVE: audio, observation, interlock or fixture preflight is not ready.")
        return None
    executor = None
    succeeded = True
    summary = False
    restored = False
    try:
        if executor_factory is None:
            try:
                from framework.test_executor import TestExecutor
            except ImportError:
                from aigent.framework.test_executor import TestExecutor
            executor_factory = TestExecutor
        executor = executor_factory(adapter.device, test_key=None, test_execution_key=None,
                                    log_test_id="octane_1685240_cn_speech_mvp")
        for case in cases:
            identifier = case["octane_test_id"]
            prepared = executor.execute_step(
                identifier + " preconditions", case["fixture"],
                lambda case=case: _checked(lambda: adapter.prepare(case)),
                expected="Independent fixture checks match the reviewed case",
                verify_fn=lambda case=case: _checked(
                    lambda: adapter.verify(case, "fixture_ready", {})),
            )
            if not prepared:
                succeeded = False
                break
            for index, step in enumerate(case["steps"], start=1):
                result = executor.execute_step(
                    identifier + " step " + str(index), step["action"],
                    lambda case=case, step=step: _checked(
                        lambda: adapter.perform(case, step["action"], step["parameters"])),
                    expected=", ".join(item["name"] for item in step["assertions"]),
                    verify_fn=lambda case=case, step=step: all(
                        _checked(lambda item=item: adapter.verify(
                            case, item["name"], item["parameters"]))
                        for item in step["assertions"]),
                )
                if not result:
                    succeeded = False
                    break
            if not succeeded:
                break
    except Exception as error:
        print("Execution failed: " + type(error).__name__)
        succeeded = False
    finally:
        restore_action = _checked(adapter.restore)
        restore_verified = restore_action and _checked(adapter.verify_restoration)
        restored = restore_action and restore_verified
        if executor is not None:
            recorded = _checked(lambda: bool(executor.execute_step(
                "Fixture restoration", "Restore and independently verify the baseline",
                lambda: restore_action,
                expected="All modified fixture state matches its recorded baseline",
                verify_fn=lambda: restore_verified,
            )))
            restored = restored and recorded
            summary = _checked(executor.print_summary)
        if not restored:
            print("FAIL: fixture restoration was not verified.")
    return succeeded and summary and restored


def main():
    parser = argparse.ArgumentParser(description="Deferred CN Speech MVP; no implicit device")
    parser.add_argument("device_ip", nargs="?", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--adapter", help="Reviewed local module:factory, loaded only with --execute")
    arguments = parser.parse_args()
    if not arguments.execute or not arguments.adapter:
        result = run_test()
    else:
        module_name, separator, factory_name = arguments.adapter.partition(":")
        if not separator or not module_name or not factory_name:
            parser.error("--adapter must be module:factory")
        factory = getattr(importlib.import_module(module_name), factory_name)
        result = run_test(arguments.device_ip, adapter=factory())
    return 0 if result is True else 1 if result is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def write_case_inventory(catalog: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "octane_test_id", "title", "feature_ids", "family_candidates", "readiness",
            "version_stamp", "action_count", "expected_step_count", "utterance_candidate_count",
            "description_html", "preconditions", "actions", "expected_results", "full_script",
            "test_version", "source_last_modified",
        ])
        writer.writeheader()
        for case in catalog["cases"]:
            steps = case["parsed"]["steps"]
            test_version = case["source_script"].get("test_version", "")
            if isinstance(test_version, (dict, list)):
                test_version = json.dumps(test_version, ensure_ascii=False)
            writer.writerow({
                "octane_test_id": case["octane_test_id"], "title": case["title"],
                "feature_ids": ";".join(case["feature_ids"]),
                "family_candidates": ";".join(case["family_candidates"]),
                "readiness": case["readiness"],
                "version_stamp": case["source_script"].get("version_stamp"),
                "action_count": len(steps),
                "expected_step_count": sum(bool(step["expected"]) for step in steps),
                "utterance_candidate_count": len(case["parsed"]["utterance_candidates"]),
                "description_html": case.get("description_html") or "",
                "preconditions": "\n\n".join(case["parsed"]["preconditions"]),
                "actions": "\n".join(f'{index}. {step["action"]}'
                                     for index, step in enumerate(steps, start=1)),
                "expected_results": "\n".join(f'{index}. {step["expected"]}'
                                              for index, step in enumerate(steps, start=1)
                                              if step["expected"]),
                "full_script": case["source_script"]["script"],
                "test_version": test_version,
                "source_last_modified": case.get("source_last_modified") or "",
            })


def write_bundle(snapshot_path: Path, output_directory: Path) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not snapshot.get("completeness", {}).get("complete"):
        raise ValueError("An incomplete evidence snapshot cannot produce this bundle")
    if str(snapshot["epic"]["id"]) != "1685240":
        raise ValueError("This curated family mapping is specific to Epic 1685240")
    catalog = build_case_catalog(snapshot)
    if catalog["unmapped_case_ids"]:
        raise ValueError("Some manual cases have no curated Feature routing")
    output_directory.mkdir(parents=True, exist_ok=True)
    drafts = output_directory / "drafts"
    drafts.mkdir(exist_ok=True)
    (output_directory / "case_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    (output_directory / "scenario_catalog.yaml").write_text(
        yaml.safe_dump(catalog, allow_unicode=False, sort_keys=False, width=110), encoding="utf-8"
    )
    write_case_inventory(catalog, output_directory / "case_inventory.csv")
    (output_directory / "local_test_plan.draft.yaml").write_text(yaml.safe_dump({
        "name": "Deferred Octane 1685240 CN Speech MVP - all SKIP is NOT coverage",
        "tests": ["drafts/cn_speech_mvp.py"],
    }, sort_keys=False), encoding="utf-8")
    (drafts / "cn_speech_mvp.py").write_text(native_runner_source(), encoding="utf-8")
    (drafts / "mvp_cases.json").write_text(
        json.dumps(build_mvp_cases(catalog), ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "epic_id": "1685240", "manual_cases": len(catalog["cases"]),
        "non_manual_records": len(catalog["non_manual_records"]),
        "covered_features": sum(feature["all_covered_records"] > 0 for feature in catalog["feature_coverage"]),
        "zero_coverage_features": [feature for feature in catalog["feature_coverage"]
                                   if not feature["all_covered_records"]],
        "active_family_candidates": sum(family["case_count"] > 0 for family in catalog["families"]),
        "automation_ready_count": 0, "mvp_case_ids": ["1899553", "1220071", "1217000", "1218449", "1522678"],
        "unmapped_case_ids": catalog["unmapped_case_ids"],
        "full_script_characters_preserved": sum(len(case["source_script"]["script"]) for case in catalog["cases"]),
        "parsed_actions": sum(len(case["parsed"]["steps"]) for case in catalog["cases"]),
        "cases_without_parsed_actions": [case["octane_test_id"] for case in catalog["cases"] if not case["parsed"]["steps"]],
        "cases_with_unclosed_preconditions": [case["octane_test_id"] for case in catalog["cases"] if case["parsed"]["unclosed_preconditions"]],
        "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "semantic_review": "Whole-set structural parsing and Feature routing; representative semantic review, not certified per-case execution design",
        "device_execution_validated": False,
    }
    (output_directory / "bundle_summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-grounded CN Speech automation draft")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(write_bundle(arguments.snapshot, arguments.output_directory),
                     ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())