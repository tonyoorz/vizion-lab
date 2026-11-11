from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import csv
import re
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest


def automation_module():
    specification = importlib.util.find_spec("backend.analytics.octane_epic_automation")
    assert specification is not None, "The read-only Epic evidence exporter is missing"
    return importlib.import_module("backend.analytics.octane_epic_automation")


class EvidenceClient:
    def __init__(self, *, broken_script: bool = False, backlog_alias: bool = False):
        self.broken_script = broken_script
        self.backlog_alias = backlog_alias
        self.script_calls = []
        self.queries = []
        self.field_requests = []
        self._api_base = "https://octane.invalid/api"
        self._session = SimpleNamespace(get=self._get_metadata)

    def _get_metadata(self, url, *, params, timeout, verify):
        assert url.endswith("/metadata/fields")
        assert "fields" not in params
        self.queries.append(("metadata/fields", params["query"]))
        rows = [{"name": name, "selectable": True}
            for name in ("id", "name", "subtype", "parent", "covered_content", "description")]
        return SimpleNamespace(raise_for_status=lambda: None,
                       json=lambda: {"data": rows, "total_count": len(rows)})

    def fetch_work_item(self, work_item_id):
        return {"id": work_item_id, "subtype": "epic", "name": "CN Speech"}

    def fetch_rows(self, endpoint, *, fields, query, limit=200):
        self.queries.append((endpoint, query))
        self.field_requests.append((endpoint, fields))
        identifiers = set(re.findall(r"'(\d+)'", query))
        if endpoint == "work_items":
            if "parent=" not in query:
                return [{"id": "1", "subtype": "epic", "name": "CN Speech",
                         "description": "Complete root metadata"}]
            rows = [
                {"id": "2", "subtype": "feature", "name": "Speech", "parent": {"id": "1"}},
                {"id": "3", "subtype": "story", "name": "Dialog", "parent": {"id": "2"}},
            ]
            return [row for row in rows if row["parent"]["id"] in identifiers]
        assert endpoint == "tests"
        rows = [
            {
                "id": "10", "subtype": "test_manual", "name": "Speech dialog",
                "covered_content": {"data": [{"id": "2"}, {"id": "3"}]},
                "description": "<p>Evidence, not an inferred expected result.</p>",
                "last_modified": "2026-10-28T07:00:00Z",
            },
            {"id": "11", "subtype": "test_auto", "name": "Automatic check",
             "covered_content": {"data": [{"id": "1"}]}},
        ]
        selected = [row for row in rows if identifiers.intersection(
            item["id"] for item in row["covered_content"]["data"]
        )]
        if self.backlog_alias:
            for row in selected:
                row["backlog_coverage"] = row.pop("covered_content")
        return selected

    def fetch_test_steps(self, *, test_id):
        self.script_calls.append(test_id)
        if self.broken_script:
            raise RuntimeError("Script endpoint unavailable")
        return {"script": "- Wake assistant\n? Dialog is visible", "test_version": 7,
                "version_stamp": 42}


def test_collects_descendants_and_deduplicates_tests():
    module = automation_module()
    client = EvidenceClient()
    result = module.collect_epic_evidence(client, epic_id="1", batch_size=1, workers=1)

    assert {row["id"] for row in result["work_items"]} == {"1", "2", "3"}
    assert [row["id"] for row in result["tests"]] == ["10", "11"]
    assert client.script_calls == ["10"]
    assert result["completeness"]["complete"] is True
    assert result["completeness"]["manual_tests"] == 1
    assert result["completeness"]["scripts_fetched"] == 1


def test_preserves_raw_test_metadata_and_script_version():
    module = automation_module()
    result = module.collect_epic_evidence(EvidenceClient(), epic_id="1", workers=1)
    test = result["tests"][0]

    assert test["metadata"]["description"].startswith("<p>Evidence")
    assert test["script"]["test_version"] == 7
    assert test["script"]["version_stamp"] == 42
    assert test["covered_work_item_ids"] == ["2", "3"]


def test_uses_discovered_selectable_fields_instead_of_unsupported_wildcard():
    module = automation_module()
    client = EvidenceClient()
    result = module.collect_epic_evidence(client, epic_id="1", workers=1)

    assert all(fields != "*" for _endpoint, fields in client.field_requests)
    assert result["metadata_schema"]["test"]
    assert any(endpoint == "metadata/fields" for endpoint, _query in client.queries)


def test_script_failure_keeps_metadata_but_marks_export_incomplete():
    module = automation_module()
    result = module.collect_epic_evidence(
        EvidenceClient(broken_script=True), epic_id="1", workers=1
    )

    assert len(result["tests"]) == 2
    assert result["completeness"]["complete"] is False
    assert result["completeness"]["script_failures"] == 1
    assert result["tests"][0]["script"] is None
    assert result["tests"][0]["script_error"] == "RuntimeError"


def test_accepts_the_live_backlog_coverage_field_alias():
    module = automation_module()
    result = module.collect_epic_evidence(
        EvidenceClient(backlog_alias=True), epic_id="1", workers=1
    )

    assert result["tests"][0]["covered_work_item_ids"] == ["2", "3"]
    assert result["completeness"]["unresolved_coverage"] == 0


@pytest.mark.parametrize("epic_id", ["", "1' OR id='2", "not-an-id"])
def test_rejects_non_numeric_epic_ids(epic_id):
    module = automation_module()
    with pytest.raises(ValueError, match="numeric"):
        module.collect_epic_evidence(EvidenceClient(), epic_id=epic_id)


def bundle_module():
    specification = importlib.util.find_spec("backend.analytics.cn_speech_automation_bundle")
    assert specification is not None, "The evidence-grounded Speech bundle builder is missing"
    return importlib.import_module("backend.analytics.cn_speech_automation_bundle")


def test_parser_separates_preconditions_actions_and_expected_speech():
    module = bundle_module()
    result = module.parse_manual_script(
        '- [PreCondition Start]\nNetwork available\n[PreCondition End]\n'
        '- Say "\u6253\u5f00\u7a7a\u8c03"\n- ?TTS "\u597d\u7684"\nClimate is on\n'
        '- Press PTT\n- ?Dialog disappears'
    )

    assert result["preconditions"] == ["Network available"]
    assert len(result["steps"]) == 2
    assert "Climate is on" in result["steps"][0]["expected"]
    assert result["utterance_candidates"] == ["\u6253\u5f00\u7a7a\u8c03"]
    assert "\u597d\u7684" not in result["utterance_candidates"]


def test_catalog_keeps_every_manual_case_and_separates_suites():
    module = bundle_module()
    snapshot = {
        "epic": {"id": "1685240"},
        "work_items": [{"id": "1694953", "subtype": "feature", "name": "Hot words"}],
        "tests": [
            {"id": "10", "metadata": {"name": "Command", "subtype": "test_manual"},
             "covered_work_item_ids": ["1694953", "987654"],
             "script": {"script": '- Say "next"\n- ?Track changes', "version_stamp": 42}},
            {"id": "11", "metadata": {"name": "Suite", "subtype": "test_suite"},
             "covered_work_item_ids": ["1694953"], "script": None},
        ],
    }
    result = module.build_case_catalog(snapshot)

    assert [case["octane_test_id"] for case in result["cases"]] == ["10"]
    assert result["cases"][0]["feature_ids"] == ["1694953"]
    assert result["cases"][0]["source_script"]["version_stamp"] == 42
    assert result["cases"][0]["readiness"] == "requires_review_and_adapters"
    assert result["non_manual_records"][0]["id"] == "11"


def test_native_draft_defers_without_importing_framework(tmp_path):
    module = bundle_module()
    source = module.native_runner_source()
    namespace = {"__name__": "speech_draft", "__file__": str(tmp_path / "draft.py")}
    exec(compile(source, "draft.py", "exec"), namespace)

    assert namespace["run_test"]() is None
    path = tmp_path / "draft.py"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                            timeout=10, check=False)
    assert result.returncode == 2
    assert "INCONCLUSIVE" in result.stdout
    assert "framework" not in result.stderr


def test_native_draft_empty_case_list_cannot_pass(tmp_path):
    module = bundle_module()
    namespace = {"__name__": "speech_draft", "__file__": str(tmp_path / "draft.py")}
    exec(compile(module.native_runner_source(), "draft.py", "exec"), namespace)

    class NotReadyAdapter:
        def preflight(self, cases, device_ip):
            raise AssertionError("An empty plan must stop before contacting an adapter")

    assert namespace["run_test"](adapter=NotReadyAdapter(), cases=[]) is None


@pytest.mark.parametrize("failure", [None, "perform", "verify", "restore", "verify_restoration"])
def test_native_adapter_checks_oracles_and_restoration_before_summary(tmp_path, failure):
    module = bundle_module()
    namespace = {"__name__": "speech_draft", "__file__": str(tmp_path / "draft.py")}
    exec(compile(module.native_runner_source(), "draft.py", "exec"), namespace)
    events = []

    class OfflineAdapter:
        device = object()

        def preflight(self, cases, device_ip):
            return True

        def prepare(self, case):
            return True

        def perform(self, case, action, parameters):
            events.append("perform")
            return failure != "perform"

        def verify(self, case, assertion, parameters):
            events.append(assertion)
            return assertion == "fixture_ready" or failure != "verify"

        def restore(self):
            events.append("restore")
            return failure != "restore"

        def verify_restoration(self):
            events.append("verify_restoration")
            return failure != "verify_restoration"

    class OfflineExecutor:
        def __init__(self, device, **kwargs):
            self.results = []

        def execute_step(self, name, description, action_fn, *, expected, verify_fn):
            result = bool(action_fn() and verify_fn())
            self.results.append(result)
            return result

        def print_summary(self):
            events.append("summary")
            return bool(self.results) and all(self.results)

    cases = [{"octane_test_id": "10", "fixture": "Offline fixture", "steps": [
        {"action": "offline_action", "parameters": {},
         "assertions": [{"name": "independent_oracle", "parameters": {}}]},
    ]}]
    result = namespace["run_test"](
        adapter=OfflineAdapter(), cases=cases, executor_factory=OfflineExecutor
    )

    assert result is (failure is None)
    assert events.index("restore") < events.index("summary")
    if failure != "restore":
        assert events.index("verify_restoration") < events.index("summary")
    if failure == "perform":
        assert "independent_oracle" not in events


@pytest.mark.parametrize("steps", [[], [{"action": "offline", "parameters": {}, "assertions": []}]])
def test_native_draft_rejects_missing_actions_or_oracles_before_preflight(tmp_path, steps):
    module = bundle_module()
    namespace = {"__name__": "speech_draft", "__file__": str(tmp_path / "draft.py")}
    exec(compile(module.native_runner_source(), "draft.py", "exec"), namespace)
    events = []
    adapter = SimpleNamespace(preflight=lambda *arguments: events.append("preflight") or False)

    result = namespace["run_test"](
        adapter=adapter, cases=[{"octane_test_id": "10", "fixture": "Offline", "steps": steps}]
    )

    assert result is None
    assert events == []


@pytest.mark.parametrize("cache_kind", ["broken_json", "missing_payload", "invalid_script", "valid"])
def test_invalid_cache_falls_back_to_live_script_read(tmp_path, cache_kind):
    module = automation_module()
    client = EvidenceClient()
    metadata = client.fetch_rows("tests", fields="id", query='"(id=\'2\')"')[0]
    fingerprint = hashlib.sha256(module._json_text(metadata).encode("utf-8")).hexdigest()
    cached = {"metadata_sha256": fingerprint}
    if cache_kind == "invalid_script":
        cached["payload"] = {"script": 123}
    elif cache_kind == "valid":
        cached["payload"] = {"script": "- Cached action\n- ?Cached expected result"}
    path = tmp_path / "10.json"
    path.write_text("{" if cache_kind == "broken_json" else json.dumps(cached), encoding="utf-8")

    result = module.collect_epic_evidence(
        client, epic_id="1", workers=1, cache_directory=tmp_path
    )

    assert result["completeness"]["complete"] is True
    assert isinstance(result["tests"][0]["script"]["script"], str)
    assert client.script_calls == ([] if cache_kind == "valid" else ["10"])


def test_root_epic_is_refetched_with_discovered_full_metadata_fields():
    module = automation_module()
    result = module.collect_epic_evidence(EvidenceClient(), epic_id="1", workers=1)

    assert result["epic"].get("description") == "Complete root metadata"
    root = next(item for item in result["work_items"] if item["id"] == "1")
    assert root == result["epic"]


def design_module():
    specification = importlib.util.find_spec("backend.analytics.cn_speech_test_design")
    assert specification is not None, "The defect-grounded Excel design builder is missing"
    return importlib.import_module("backend.analytics.cn_speech_test_design")


def design_catalog():
    return {"epic_id": "1685240", "cases": [{
        "octane_test_id": "1218449", "title": "Playback modes", "feature_ids": ["1697189"],
        "family_candidates": ["media_apps"], "source_last_modified": "2026-10-28",
        "source_script": {"script": '- [PreCondition Start]\nMusic playing\n[PreCondition End]\n- Say shuffle\n- ?Playback mode is shuffle', "version_stamp": 42},
        "parsed": {"preconditions": ["Music playing"],
                   "steps": [{"action": "Say shuffle", "expected": "Playback mode is shuffle"}],
                   "utterance_candidates": []},
    }]}


def design_defect(defect_id="9001"):
    return {"defect_id": defect_id, "name": "Shuffle fails after call", "project": "IDCEVO",
            "description": '<p>[Preconditions]</p><p>Music playing; phone paired</p>'
                           '<p>[Action Step]</p><p>1. Answer a test call</p><p>2. End call</p>'
                           '<p>3. Say shuffle</p><p>[Expected behavior]</p><p>Playback mode is shuffle</p>'
                           '<p>[Actual results]</p><p>Playback mode stays repeat one</p>',
            "phase": "09-Concluded without action", "last_modified": "2026-10-28"}


def test_defect_design_never_uses_observed_failure_as_expected():
    module = design_module()
    result = module.build_design_library(design_catalog(), [design_defect()], [])
    regression = next(case for case in result["cases"] if case["source_kind"] == "defect_regression")

    assert "Playback mode is shuffle" in regression["octane_script"]
    assert "stays repeat one" not in regression["octane_script"]
    assert "stays repeat one" in result["defects"][0]["observed"]
    assert regression["approval_status"] == "draft_needs_domain_review"
    assert regression["automation_ready"] is False


def test_identical_defect_procedures_share_one_candidate_with_all_sources():
    module = design_module()
    result = module.build_design_library(
        design_catalog(), [design_defect("9001"), design_defect("9002")], []
    )
    regression = [case for case in result["cases"] if case["source_kind"] == "defect_regression"]

    assert len(regression) == 1
    assert regression[0]["source_defect_ids"] == ["9001", "9002"]
    assert len(result["cases"]) == 2


def test_explicit_relations_are_not_claimed_as_confirmed_failure_mode_coverage():
    module = design_module()
    relationships = [{"test_id": "1218449", "defect_id": "9001",
                      "relation_kind": "manual_run", "evidence_id": "77"}]
    result = module.build_design_library(design_catalog(), [design_defect()], relationships)

    assert result["cases"][0]["source_defect_ids"] == ["9001"]
    assert result["traceability"][0]["relation_kind"] == "manual_run"
    assert result["traceability"][0]["failure_mode_coverage_confirmed"] is False
    assert result["cases"][0]["source_test_id"] == "1218449"
    assert result["cases"][0]["octane_script"] == design_catalog()["cases"][0]["source_script"]["script"]


def test_defect_without_expected_result_is_retained_but_not_turned_into_a_test():
    module = design_module()
    defect = design_defect()
    defect["description"] = '<p>[Action Step]</p><p>Say shuffle</p><p>[Actual results]</p><p>No response</p>'
    result = module.build_design_library(design_catalog(), [defect], [])

    assert len(result["cases"]) == 1
    assert result["defects"][0]["disposition"] == "insufficient_evidence"
    assert result["defects"][0]["expected"] == ""


def test_defect_design_preserves_each_numbered_action_without_putting_failure_in_steps():
    module = design_module()
    defect = design_defect()
    defect["description"] = '<h3>Test Steps:</h3><p>1. Wake speech</p><p>2. Ask a question</p>'
    defect["description"] += '<h3>Test Result:</h3><p>Wrong brand label</p>'
    defect["description"] += '<h3>Expected Result:</h3><p>Correct product label</p>'
    result = module.build_design_library(design_catalog(), [defect], [])
    regression = next(case for case in result["cases"] if case["source_kind"] == "defect_regression")
    steps = [step for step in result["steps"] if step["case_key"] == regression["case_key"]]

    assert [step["action"] for step in steps] == ["Wake speech", "Ask a question"]
    assert steps[-1]["expected_result"] == "Correct product label"
    assert "Wrong brand label" not in regression["octane_script"]


def test_different_vehicle_configuration_is_not_deduplicated_as_same_regression():
    module = design_module()
    first, second = design_defect("9001"), design_defect("9002")
    first["lead_model"], second["lead_model"] = "BMW", "Rolls-Royce"
    result = module.build_design_library(design_catalog(), [first, second], [])

    assert len([case for case in result["cases"] if case["source_kind"] == "defect_regression"]) == 2


def test_local_source_is_read_only_and_distinguishes_direct_links_from_topic_candidates(tmp_path):
        module = design_module()
        path = tmp_path / "source.db"
        connection = sqlite3.connect(path)
        connection.executescript('''
                CREATE TABLE octane_defects (defect_id TEXT, name TEXT, description TEXT, project TEXT,
                    phase TEXT, severity TEXT, assigned_ecu TEXT, software_version TEXT, lead_model TEXT,
                    last_modified TEXT, fetched_at TEXT, aida_english TEXT, product_areas TEXT);
                CREATE TABLE octane_manual_runs (mr_id TEXT, test_id TEXT, defect_id TEXT,
                    execution_sw_version TEXT, test_version TEXT, fetched_at TEXT);
                INSERT INTO octane_defects VALUES ('9001','Linked defect','','IDCEVO','','','','','','','','','');
                INSERT INTO octane_defects VALUES ('9002','[CN Speech] Topic candidate','','IDCEVO','','','','','','','','','');
                INSERT INTO octane_defects VALUES ('9003','[CN Speech] Another project','','IDC','','','','','','','','','');
                INSERT INTO octane_manual_runs VALUES ('77','1218449','9001','','','2026-10-20');
        ''')
        connection.commit()
        connection.close()
        before = path.read_bytes()

        result = module.read_local_defect_source(path, design_catalog())

        assert {row["defect_id"] for row in result["defects"]} == {"9001", "9002"}
        assert result["relationships"][0]["evidence_id"] == "77"
        assert result["relationships"][0]["relation_kind"] == "manual_run"
        assert result["read_only"] is True
        assert path.read_bytes() == before


def test_excel_preserves_ids_and_stores_source_text_as_text_not_formulas(tmp_path):
        from openpyxl import load_workbook

        module = design_module()
        library = module.build_design_library(design_catalog(), [], [])
        library["cases"][0]["name"] = '=HYPERLINK("https://invalid.invalid","not a formula")'
        path = tmp_path / "design.xlsx"
        module.write_design_workbook(library, path)
        book = load_workbook(path, data_only=False)

        assert {"Cases", "Steps", "Automation", "Traceability", "Defect Review", "Octane Drafts"}.issubset(book.sheetnames)
        headers = [cell.value for cell in book["Cases"][1]]
        name = book["Cases"].cell(2, headers.index("name") + 1)
        assert name.data_type == "s"
        assert name.value == library["cases"][0]["name"]
        assert book["Cases"].freeze_panes == "A2"
        assert book["Cases"].max_row == 2
        book.close()


def test_excel_long_script_has_lossless_chunks_instead_of_silent_truncation(tmp_path):
        from openpyxl import load_workbook

        module = design_module()
        library = module.build_design_library(design_catalog(), [], [])
        script = "- " + "a" * 40000
        library["cases"][0]["octane_script"] = script
        path = tmp_path / "long.xlsx"
        module.write_design_workbook(library, path)
        book = load_workbook(path, data_only=False)
        rows = list(book["Long Text"].iter_rows(min_row=2, values_only=True))
        chunks = [row for row in rows if row[0] == "CNS-TC-1218449:octane_script"]

        assert "".join(row[2] for row in sorted(chunks, key=lambda row: row[1])) == script
        assert all(len(row[2]) <= 15000 for row in chunks)
        book.close()


def test_plain_action_and_actual_observed_headings_exclude_footer_from_expected():
        module = design_module()
        defect = {"description": 'Precondition\nMusic playing\nAction\n# Say shuffle\n'
                  'Actual/Observed Result\nRepeat one remains\nExpected Result\nShuffle is active\n'
                  'Time stamp\n2026-10-28\nError Occurrence Rate\n5/5'}
        parsed = module.parse_defect_evidence(defect)

        assert parsed["actions"] == "# Say shuffle"
        assert parsed["observed"] == "Repeat one remains"
        assert parsed["expected"] == "Shuffle is active"


def test_mixed_action_and_failure_section_is_retained_without_generating_a_procedure():
        module = design_module()
        defect = design_defect()
        defect["description"] = ('Precondition\nNew lifecycle\nAction & Observed Result\n'
                                 'Cannot select a navigation result; receives an apology\n'
                                 'Expected Result\nSelection should work')
        result = module.build_design_library(design_catalog(), [defect], [])

        assert len(result["cases"]) == 1
        assert "Cannot select" in result["defects"][0]["mixed_actions_observed"]
        assert result["defects"][0]["extraction_status"] == "mixed_procedure_requires_review"


def test_regression_candidate_has_explicit_source_edges_and_similarity_does_not_assign_feature():
    module = design_module()
    matches = [{"test_id": "1218449", "defect_id": "9001", "similarity_score": 0.9}]
    result = module.build_design_library(design_catalog(), [design_defect()], [], matches)
    regression = next(case for case in result["cases"] if case["source_kind"] == "defect_regression")

    assert regression["feature_ids"] == []
    assert regression["suggested_test_ids"] == ["1218449"]
    edge = next(row for row in result["traceability"] if row["relation_kind"] == "derived_regression_candidate")
    assert edge["case_key"] == regression["case_key"]
    assert edge["defect_id"] == "9001"
    assert edge["failure_mode_coverage_confirmed"] is False


def test_long_actions_of_different_steps_have_distinct_lossless_references(tmp_path):
    from openpyxl import load_workbook

    module = design_module()
    library = module.build_design_library(design_catalog(), [], [])
    first = library["steps"][0]
    first["action"] = "a" * 40000
    library["steps"].append(dict(first, step_number=2, action="b" * 40000))
    path = tmp_path / "steps.xlsx"
    module.write_design_workbook(library, path)
    book = load_workbook(path, data_only=False)
    texts = {}
    for reference, chunk_number, text in book["Long Text"].iter_rows(min_row=2, values_only=True):
        if reference.endswith(":action"):
            texts.setdefault(reference, []).append((chunk_number, text))

    assert len(texts) == 2
    assert {"".join(text for number, text in sorted(chunks)) for chunks in texts.values()} == {"a" * 40000, "b" * 40000}
    book.close()


def test_regression_deduplication_preserves_different_step_boundaries():
    module = design_module()
    first, second = design_defect("9001"), design_defect("9002")
    template = "Precondition\nMusic playing\nAction\n{}\nExpected Result\nMode changes"
    first["description"] = template.format("1. Say shuffle\n2. Say repeat")
    second["description"] = template.format("1. Say shuffle 2. Say repeat")
    result = module.build_design_library(design_catalog(), [first, second], [])

    assert len([case for case in result["cases"] if case["source_kind"] == "defect_regression"]) == 2


def test_baseline_script_source_ids_are_not_replaced_by_linked_defect_ids(tmp_path):
    from openpyxl import load_workbook

    module = design_module()
    relationships = [{"test_id": "1218449", "defect_id": "9001", "relation_kind": "manual_run", "evidence_id": "77"}]
    library = module.build_design_library(design_catalog(), [design_defect()], relationships)
    path = tmp_path / "provenance.xlsx"
    module.write_design_workbook(library, path)
    book = load_workbook(path, data_only=False)
    rows = list(book["Source Evidence"].iter_rows(values_only=True))
    source_ids_column = rows[0].index("source_ids")

    assert json.loads(rows[1][source_ids_column]) == ["1218449"]
    book.close()


def test_nested_list_markers_are_not_empty_actions_and_order_is_preserved():
    module = design_module()
    defect = design_defect()
    defect["description"] = ('Action\n# Prepare lifecycle\n*\n** Lock vehicle\n'
                             '** Wait for approved interval\n** Unlock vehicle\nExpected Result\nUI restored')
    result = module.build_design_library(design_catalog(), [defect], [])
    case = next(case for case in result["cases"] if case["source_kind"] == "defect_regression")

    assert [step["action"] for step in case["procedure_steps"]] == [
        "Prepare lifecycle", "Lock vehicle", "Wait for approved interval", "Unlock vehicle"
    ]


def test_only_list_markers_do_not_count_as_reproduction_evidence():
    module = design_module()
    defect = design_defect()
    defect["description"] = 'Action\n*\n**\nExpected Result\nUI restored'
    result = module.build_design_library(design_catalog(), [defect], [])

    assert len(result["cases"]) == 1
    assert result["defects"][0]["extraction_status"] == "insufficient_evidence"


def test_compact_design_has_one_tab_and_one_row_per_case_with_regressions_first(tmp_path):
    from openpyxl import load_workbook

    module = design_module()
    library = module.build_design_library(design_catalog(), [design_defect()], [])
    original = json.dumps(library, sort_keys=True)
    path = tmp_path / "compact.xlsx"
    assert hasattr(module, "write_compact_workbook"), "The single-tab review export is missing"
    module.write_compact_workbook(library, path)
    book = load_workbook(path, data_only=False)
    sheet = book.active

    assert len(book.sheetnames) == 1
    assert sheet.max_row == len(library["cases"]) + 1
    assert sheet.max_column == 10
    assert {sheet.cell(row, 1).value for row in range(2, sheet.max_row + 1)} == {
        case["case_key"] for case in library["cases"]
    }
    assert sheet.cell(2, 1).value.startswith("CNS-REG-")
    assert sheet.cell(3, 1).value == "CNS-TC-1218449"
    assert sheet.freeze_panes == "D2"
    assert sheet.auto_filter.ref == sheet.dimensions
    assert all(sheet.sheet_state == "visible" for sheet in book)
    assert json.dumps(library, sort_keys=True) == original
    book.close()


def test_compact_design_keeps_actions_and_expected_in_the_same_case_row(tmp_path):
    from openpyxl import load_workbook

    module = design_module()
    library = module.build_design_library(design_catalog(), [design_defect()], [])
    path = tmp_path / "compact.xlsx"
    assert hasattr(module, "write_compact_workbook"), "The single-tab review export is missing"
    module.write_compact_workbook(library, path)
    book = load_workbook(path, data_only=False)
    sheet = book.active

    assert "Answer a test call" in sheet.cell(2, 5).value
    assert "End call" in sheet.cell(2, 5).value
    assert "Say shuffle" in sheet.cell(2, 5).value
    assert "Playback mode is shuffle" in sheet.cell(2, 6).value
    assert "stays repeat one" not in sheet.cell(2, 6).value
    assert sheet.cell(2, 7).value
    assert "9001" in sheet.cell(2, 10).value
    book.close()


def test_compact_design_formula_strings_remain_literal_and_overlong_cells_are_rejected(tmp_path):
    from openpyxl import load_workbook

    module = design_module()
    library = module.build_design_library(design_catalog(), [], [])
    library["cases"][0]["name"] = '=HYPERLINK("https://invalid.invalid","literal")'
    path = tmp_path / "compact.xlsx"
    assert hasattr(module, "write_compact_workbook"), "The single-tab review export is missing"
    module.write_compact_workbook(library, path)
    book = load_workbook(path, data_only=False)

    assert book.active.cell(2, 3).data_type == "s"
    assert book.active.cell(2, 3).value == library["cases"][0]["name"]
    book.close()
    library["cases"][0]["name"] = "a" * 40000
    with pytest.raises(ValueError, match="cell limit"):
        module.write_compact_workbook(library, tmp_path / "overlong.xlsx")


def test_case_inventory_csv_includes_complete_original_content_in_one_record(tmp_path):
    module = bundle_module()
    script = ('- [PreCondition Start]\nNetwork available, account ready\n[PreCondition End]\n'
              '- Say "\u6253\u5f00\u7a7a\u8c03"\n- ?Climate is on\nNo unwanted banner\n'
              '- Press PTT\n- ?Dialog disappears')
    description = '<p>Complete objective, with "quotes" and a newline</p>\n<p>Keep this overview.</p>'
    snapshot = {
        "epic": {"id": "1685240"},
        "work_items": [{"id": "1694953", "subtype": "feature", "name": "Hot words"}],
        "tests": [{"id": "10", "metadata": {
            "name": "\u4e2d\u6587\u7528\u4f8b, with quotes", "subtype": "test_manual",
            "description": description, "last_modified": "2026-10-28T07:00:00Z"},
            "covered_work_item_ids": ["1694953"],
            "script": {"script": script, "version_stamp": 42,
                       "test_version": '{"id":123,"type":"test_version"}'}}],
    }
    catalog = module.build_case_catalog(snapshot)
    path = tmp_path / "case_inventory.csv"
    assert hasattr(module, "write_case_inventory"), "The complete case inventory CSV exporter is missing"
    module.write_case_inventory(catalog, path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    row = rows[0]
    assert row["full_script"] == script
    assert row["description_html"] == description
    assert row["preconditions"] == "Network available, account ready"
    assert '1. Say "\u6253\u5f00\u7a7a\u8c03"' in row["actions"]
    assert "2. Press PTT" in row["actions"]
    assert "1. Climate is on\nNo unwanted banner" in row["expected_results"]
    assert "2. Dialog disappears" in row["expected_results"]
    assert row["test_version"] == '{"id":123,"type":"test_version"}'
    assert row["source_last_modified"] == "2026-10-28T07:00:00Z"
    assert row["action_count"] == "2"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def regeneration_module():
    specification = importlib.util.find_spec("backend.analytics.cn_speech_ta_regeneration")
    assert specification is not None, "The capability-grounded TA regeneration stage is missing"
    return importlib.import_module("backend.analytics.cn_speech_ta_regeneration")


def source_for_regeneration():
    return design_module().build_design_library(design_catalog(), [], [])["cases"][0]


def valid_regenerated_design():
    return {
        "case_key": "CNS-TC-1218449", "test_name": "Playback mode changes to shuffle",
        "objective": "Verify actual playback mode after a spoken request",
        "preconditions": "Music playing; establish a known non-shuffle baseline before the action",
        "steps": [{"source_step_numbers": [1], "action": "Speak the source request",
                   "expected_result": "Playback mode is shuffle",
                   "verify_method": "Read a fresh independent playback-mode observation",
                   "action_capability_ids": [], "verify_capability_ids": ["known.capture"],
                   "missing_capabilities": ["real microphone audio input", "playback-mode state reader"],
                   "source_quotes": ["Say shuffle", "Playback mode is shuffle"],
                   "wait_condition": "Wait for the observed mode change; timeout requires approval"}],
        "restoration": "Restore and independently verify the recorded playback mode",
        "feasibility": "requires_adapter", "feasibility_reason": "Audio and state readers are absent",
        "reference_test_ids": [], "handoff": "test-recorder: record the UI observation path after authorization",
        "unresolved_questions": ["Approve timeout and audio asset"],
    }


def test_regenerated_design_rejects_invented_capability_and_source_omissions():
    module = regeneration_module()
    source = source_for_regeneration()
    result = valid_regenerated_design()
    result["steps"][0]["action_capability_ids"] = ["fictional.speech_sdk"]

    with pytest.raises(ValueError, match="capability"):
        module.validate_design(source, result, {"known.capture"})
    result = valid_regenerated_design()
    result["steps"][0]["source_step_numbers"] = []
    with pytest.raises(ValueError, match="source step"):
        module.validate_design(source, result, {"known.capture"})


def test_regenerated_design_preserves_evidence_and_never_approves_execution():
    module = regeneration_module()
    source = source_for_regeneration()
    validated = module.validate_design(source, valid_regenerated_design(), {"known.capture"})

    assert validated["source_case"]["octane_script"] == source["octane_script"]
    assert validated["design"]["feasibility"] == "requires_adapter"
    assert validated["automation_ready"] is False
    assert validated["device_validated"] is False
    assert validated["domain_approved"] is False


def test_regenerated_design_rejects_unsupported_source_quotes():
    module = regeneration_module()
    result = valid_regenerated_design()
    result["steps"][0]["source_quotes"] = ["A threshold or requirement not in source evidence"]

    with pytest.raises(ValueError, match="source quote"):
        module.validate_design(source_for_regeneration(), result, {"known.capture"})


def test_regeneration_prompt_provides_explicit_source_indexes_without_mutating_source():
    module = regeneration_module()
    source = source_for_regeneration()
    original = json.dumps(source, sort_keys=True)
    messages = module.build_design_prompt(source, {"capabilities": []}, [])
    prompt = json.loads(messages[1]["content"])

    assert prompt["source_case"]["source_step_index"] == [
        {"source_step_number": 1, **source["procedure_steps"][0]}
    ]
    assert json.dumps(source, sort_keys=True) == original


@pytest.mark.parametrize("condition", ["Wait at most 5 seconds", "\u6700\u591a\u7b49\u5f855\u79d2", "\u6210\u529f\u7387\u81f3\u5c1195%"])
def test_regenerated_design_rejects_unsourced_numeric_acceptance_in_any_output_language(condition):
    module = regeneration_module()
    design = valid_regenerated_design()
    design["steps"][0]["wait_condition"] = condition

    with pytest.raises(ValueError, match="numeric acceptance"):
        module.validate_design(source_for_regeneration(), design, {"known.capture"})


def test_reference_retrieval_builds_one_index_for_the_whole_batch(monkeypatch):
    from sklearn.feature_extraction.text import TfidfVectorizer

    module = regeneration_module()
    first = source_for_regeneration()
    second = dict(first, case_key="CNS-TC-11", source_test_id="11")
    third = dict(first, case_key="CNS-REG-review", source_kind="defect_regression", source_test_id=None)
    library = {"cases": [first, second, third]}
    calls = []
    original = TfidfVectorizer.fit_transform

    def counted_fit(self, *arguments, **keywords):
        calls.append(True)
        return original(self, *arguments, **keywords)

    monkeypatch.setattr(TfidfVectorizer, "fit_transform", counted_fit)
    assert hasattr(module, "prepare_reference_cases"), "Reference indexing still repeats for every case"
    prepared = module.prepare_reference_cases(library)

    assert set(prepared) == {case["case_key"] for case in library["cases"]}
    assert len(calls) == 1
    assert prepared[first["case_key"]][0]["test_id"] == "11"
    assert {row["test_id"] for row in prepared[third["case_key"]]} == {"1218449", "11"}


def test_glm_review_uses_injected_transport_and_model_isolated_cache(tmp_path):
    module = regeneration_module()
    source = source_for_regeneration()
    inventory = {"capabilities": [{"id": "known.capture", "role": "observation"}],
                 "missing_domain_adapters": ["real microphone audio input"],
                 "recorder_policy": "Hand off after authorized bounded checks",
                 "qamule_reference": {"reused_principles": ["Independent verification"]}}
    calls = []

    def model_call(job):
        calls.append(job["model"])
        return {"ok": True, "design": valid_regenerated_design(), "model": job["model"], "usage": {}}

    assert "model_call" in __import__("inspect").signature(module.review_case).parameters
    first = module.review_case(source, {"cases": [source]}, inventory,
                               model="glm-5.3-flash", cache_directory=tmp_path,
                               references=[], model_call=model_call)
    second = module.review_case(source, {"cases": [source]}, inventory,
                                model="glm-5.3-flash", cache_directory=tmp_path,
                                references=[], model_call=model_call)
    other = module.review_case(source, {"cases": [source]}, inventory,
                               model="previous-model", cache_directory=tmp_path,
                               references=[], model_call=model_call)

    assert calls == ["glm-5.3-flash", "previous-model"]
    assert first["status"] == "validated_design"
    assert second["cache_hit"] is True
    assert first["input_sha256"] != other["input_sha256"]


def test_persistent_model_bridge_routes_out_of_order_results_from_one_process():
    from concurrent.futures import ThreadPoolExecutor

    module = regeneration_module()
    code = (
        'import json, os, sys\npending=[]\n'
        'for line in sys.stdin:\n'
        '    pending.append(json.loads(line))\n'
        '    if len(pending)==2:\n'
        '        for job in reversed(pending):\n'
        '            print(json.dumps({"request_id":job["request_id"],"ok":True,"key":job["key"],"pid":os.getpid()}),flush=True)\n'
        '        pending=[]\n'
    )
    assert hasattr(module, "PersistentModelBridge"), "The batch still launches a process for each request"
    with module.PersistentModelBridge(command=[sys.executable, "-u", "-c", code]) as bridge:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(bridge.call, {"key": "first"})
            second = executor.submit(bridge.call, {"key": "second"})
            first_result, second_result = first.result(timeout=10), second.result(timeout=10)

    assert first_result["key"] == "first"
    assert second_result["key"] == "second"
    assert first_result["pid"] == second_result["pid"]


def test_fast_prompt_omits_duplicate_metadata_but_preserves_full_script_and_indexes():
    module = regeneration_module()
    source = source_for_regeneration()
    source["description_html"] = "Repeated display metadata " * 1000
    messages = module.build_design_prompt(source, {"capabilities": []}, [])
    evidence = json.loads(messages[1]["content"])["source_case"]

    assert evidence["octane_script"] == source["octane_script"]
    assert evidence["source_step_index"] == [{"source_step_number": 1, **source["procedure_steps"][0]}]
    assert "description_html" not in evidence
    assert "procedure_steps" not in evidence


def test_model_timeout_without_design_does_not_repeat_the_whole_timeout(tmp_path):
    module = regeneration_module()
    source = source_for_regeneration()
    inventory = {"capabilities": [{"id": "known.capture", "role": "observation"}],
                 "missing_domain_adapters": [], "recorder_policy": "Bounded handoff",
                 "qamule_reference": {"reused_principles": []}}
    calls = []

    def timed_out(job):
        calls.append(True)
        return {"ok": False, "error": "The operation was aborted due to timeout"}

    result = module.review_case(source, {"cases": [source]}, inventory,
                                model="glm-5.3-flash", cache_directory=tmp_path,
                                references=[], model_call=timed_out)

    assert len(calls) == 1
    assert result["status"] == "design_validation_failed"
    assert "timeout" in result["error"]


def test_numeric_validation_feedback_identifies_field_and_unsupported_value():
    module = regeneration_module()
    design = valid_regenerated_design()
    design["steps"][0]["wait_condition"] = "Wait at most 5 seconds"

    with pytest.raises(ValueError) as error:
        module.validate_design(source_for_regeneration(), design, {"known.capture"})

    assert "wait_condition" in str(error.value)
    assert "5000" in str(error.value)
    assert "pending" in str(error.value).lower()


def business_audit_module():
    specification = importlib.util.find_spec("backend.analytics.cn_speech_business_audit")
    assert specification is not None, "The independent business-consistency audit is missing"
    return importlib.import_module("backend.analytics.cn_speech_business_audit")


def business_audit_fixture():
    module = business_audit_module()
    context = module.build_audit_context(
        source_for_regeneration(), valid_regenerated_design(), [],
        {"capabilities": [{"id": "known.capture", "role": "observation", "limitation": "No domain-state proof"}]},
    )
    report = {
        "case_key": context["case_key"], "summary": "Source flow is retained; missing readers remain explicit",
        "checks": [{"id": identifier, "status": "not_applicable" if identifier == "defect_coverage" else "consistent",
                    "reason": "No linked defects" if identifier == "defect_coverage" else "Matches supplied source evidence"}
                   for identifier in module.CHECK_IDS],
        "findings": [], "questions": ["Confirm current product applicability"],
    }
    return module, context, report


def test_business_audit_can_confirm_source_consistency_but_never_domain_approval():
    module, context, report = business_audit_fixture()
    result = module.validate_business_audit(context, report)

    assert result["verdict"] == "source_consistent_candidate"
    assert result["domain_approved"] is False
    assert result["device_validated"] is False
    assert result["human_review_required"] is True
    report["domain_approved"] = True
    with pytest.raises(ValueError, match="approval"):
        module.validate_business_audit(context, report)


def test_business_audit_rejects_an_invented_source_quote():
    module, context, report = business_audit_fixture()
    report["checks"][0]["status"] = "needs_revision"
    report["findings"] = [{
        "check_id": module.CHECK_IDS[0], "severity": "medium", "issue_type": "contradiction",
        "issue": "The generated request changes the source", "recommendation": "Preserve the source request",
        "source_evidence": [{"source_id": context["evidence"][0]["source_id"], "quote": "Never present in source"}],
        "design_quote": "Speak the source request",
    }]
    with pytest.raises(ValueError, match="source quote"):
        module.validate_business_audit(context, report)


def test_business_audit_requires_evidence_for_revision_and_complete_checklist():
    module, context, report = business_audit_fixture()
    report["checks"][0]["status"] = "needs_revision"
    with pytest.raises(ValueError, match="finding"):
        module.validate_business_audit(context, report)
    report["checks"] = report["checks"][:-1]
    with pytest.raises(ValueError, match="checklist"):
        module.validate_business_audit(context, report)


def test_business_audit_forces_missing_defect_body_to_remain_insufficient():
    module, context, report = business_audit_fixture()
    context["source_case"]["source_defect_ids"] = ["missing-defect"]
    context["missing_defect_ids"] = ["missing-defect"]
    result = module.validate_business_audit(context, report)

    assert result["verdict"] == "insufficient_evidence"
    assert result["domain_approved"] is False


def test_business_audit_output_keeps_one_tab_original_design_and_pending_approval(tmp_path):
    from openpyxl import load_workbook

    module, context, report = business_audit_fixture()
    library = design_module().build_design_library(design_catalog(), [], [])
    source = library["cases"][0]
    generation_record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
                         "input_sha256": "fixture", "review": {"source_case": source, "design": valid_regenerated_design()}}
    library = regeneration_module().build_regenerated_library(library, [generation_record])
    original = json.dumps(library, sort_keys=True)
    audit = module.validate_business_audit(context, report)
    records = [{"case_key": context["case_key"], "status": "audit_validated", "audit": audit,
                "model": "deepseek-v4-pro", "cache_hit": False}]
    assert hasattr(module, "write_audited_outputs"), "The single-tab audited deliverable is missing"
    summary = module.write_audited_outputs(library, records, tmp_path, {"audit_model": "deepseek-v4-pro"})
    book = load_workbook(tmp_path / "CN_Speech_Business_Audit.xlsx", data_only=False)

    assert len(book.sheetnames) == 1 and book.active.max_row == 2
    assert report["summary"] in book.active.cell(2, 9).value
    assert summary["audit_validated"] == 1
    assert summary["domain_approved"] == 0
    assert json.dumps(library, sort_keys=True) == original
    book.close()


def test_business_audit_feedback_lists_allowed_issue_types_and_rejects_extra_check_fields():
    module, context, report = business_audit_fixture()
    report["checks"][0]["status"] = "needs_revision"
    report["findings"] = [{"check_id": module.CHECK_IDS[0], "severity": "medium",
                           "issue_type": "missing_verification"}]
    with pytest.raises(ValueError) as error:
        module.validate_business_audit(context, report)
    assert "missing_verification" in str(error.value)
    assert "insufficient_oracle" in str(error.value)

    module, context, report = business_audit_fixture()
    report["checks"][0]["unexpected_text"] = "Malformed JSON field split"
    with pytest.raises(ValueError, match="checklist fields"):
        module.validate_business_audit(context, report)


def test_business_audit_feedback_identifies_check_and_invalid_severity():
    module, context, report = business_audit_fixture()
    report["checks"][0]["status"] = "needs_revision"
    with pytest.raises(ValueError) as error:
        module.validate_business_audit(context, report)
    assert module.CHECK_IDS[0] in str(error.value)

    report["findings"] = [{"check_id": module.CHECK_IDS[0], "severity": "critical"}]
    with pytest.raises(ValueError) as error:
        module.validate_business_audit(context, report)
    assert "critical" in str(error.value)
    assert "high" in str(error.value)

    report["checks"][0]["status"] = "insufficient_evidence"
    report["findings"] = [{
        "check_id": module.CHECK_IDS[0], "severity": "medium", "issue_type": "omission",
        "issue": "A proposed source omission", "recommendation": "Confirm this interpretation",
        "source_evidence": [{"source_id": context["evidence"][0]["source_id"],
                             "quote": context["evidence"][0]["text"]}], "design_quote": "",
    }]
    with pytest.raises(ValueError) as error:
        module.validate_business_audit(context, report)
    assert module.CHECK_IDS[0] in str(error.value)


def test_business_audit_metadata_is_not_a_source_body_quote():
    module, context, report = business_audit_fixture()
    context["evidence"][0]["phase"] = "09-Concluded without action"
    report["checks"][0]["status"] = "needs_revision"
    report["findings"] = [{
        "check_id": module.CHECK_IDS[0], "severity": "medium", "issue_type": "omission",
        "issue": "Current applicability is not established", "recommendation": "Ask the domain owner",
        "source_evidence": [{"source_id": context["evidence"][0]["source_id"],
                             "quote": "09-Concluded without action"}], "design_quote": "",
    }]
    with pytest.raises(ValueError) as error:
        module.validate_business_audit(context, report)
    assert "metadata" in str(error.value)


def test_business_audit_findings_stay_unconfirmed_model_opinions():
    module, context, report = business_audit_fixture()
    report["checks"][0]["status"] = "needs_revision"
    report["findings"] = [{
        "check_id": module.CHECK_IDS[0], "severity": "medium", "issue_type": "omission",
        "issue": "A proposed source omission, not a confirmed defect",
        "recommendation": "Confirm this interpretation with the domain owner",
        "source_evidence": [{"source_id": context["evidence"][0]["source_id"],
                             "quote": context["evidence"][0]["text"]}],
        "design_quote": "",
    }]
    result = module.validate_business_audit(context, report)
    assert result["findings_status"] == "unconfirmed_model_opinions"
    assert result["findings"][0]["domain_confirmed"] is False


def test_business_audit_wrong_model_response_is_a_controlled_failure(tmp_path):
    module, context, report = business_audit_fixture()
    calls = []

    def wrong_model(job):
        calls.append(job)
        return {"ok": True, "model": "other-model", "design": report}

    result = module.audit_case(context, model="deepseek-v4-pro", cache_directory=tmp_path,
                               model_call=wrong_model)
    assert result["status"] == "audit_failed"
    assert "model mismatch" in result["error"]
    assert len(calls) == 1


def test_business_audit_missing_body_does_not_erase_a_supported_finding():
    module, context, report = business_audit_fixture()
    context["source_case"]["source_defect_ids"] = ["available", "missing"]
    context["missing_defect_ids"] = ["missing"]
    context["evidence"].append({"source_id": "defect:available", "kind": "historical_defect",
                                "text": "An expected voice response is omitted from the design"})
    report["checks"][-1]["status"] = "needs_revision"
    report["findings"] = [{
        "check_id": "defect_coverage", "severity": "medium", "issue_type": "omission",
        "issue": "The available defect supports an omitted observation",
        "recommendation": "Retain the observation and confirm the missing defect separately",
        "source_evidence": [{"source_id": "defect:available", "quote": "An expected voice response"}],
        "design_quote": "",
    }]
    result = module.validate_business_audit(context, report)
    assert result["verdict"] == "needs_revision"
    assert result["checks"][-1]["status"] == "needs_revision"
    assert result["missing_defect_ids"] == ["missing"]


@pytest.mark.parametrize("field,value", [
    ("procedure_steps", [{"action": "A different action", "expected": "A different expected result"}]),
    ("verification_methods", ["A different observation"]),
    ("restoration_requirement", "A different restoration"),
    ("preconditions", ["A different initial state"]),
    ("octane_script", "A different original script"),
])
def test_business_audit_rejects_display_that_differs_from_reviewed_design(field, value, tmp_path):
    module = business_audit_module()
    source = source_for_regeneration()
    record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
              "input_sha256": "fixture", "review": {"source_case": source, "design": valid_regenerated_design()}}
    library = regeneration_module().build_regenerated_library({"cases": [source]}, [record])
    assert hasattr(module, "validate_case_binding"), "The displayed case is not bound to the reviewed design"
    module.validate_case_binding(library["cases"][0], record)
    library["cases"][0][field] = value
    with pytest.raises(ValueError, match="does not match"):
        module.validate_case_binding(library["cases"][0], record)
    with pytest.raises(ValueError, match="does not match"):
        module.write_audited_outputs(library, [], tmp_path, {})


def test_business_audit_binding_excludes_generation_request_only_reference_index():
    module = business_audit_module()
    source = source_for_regeneration()
    record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
              "input_sha256": "fixture", "review": {"source_case": source, "design": valid_regenerated_design()}}
    library = regeneration_module().build_regenerated_library({"cases": [source]}, [record])
    record["review"]["source_case"] = {**source, "reference_case_ids_available": ["1220071"]}
    module.validate_case_binding(library["cases"][0], record)


def test_agentic_inventory_uses_its_own_symbols_and_preserves_observation_limits(tmp_path, monkeypatch):
    module = regeneration_module()
    sources = (
        ("agentic.voice.speak.v1", "tools.py", "Bus.speak_text", "action", "Playback requires calibrated microphone path"),
        ("agentic.voice.feedback.v1", "tools.py", "Bus.capture_feedback", "observation", "generated_wav is input only, not vehicle response"),
    )
    monkeypatch.setattr(module, "AGENTIC_CAPABILITY_SOURCES", sources, raising=False)
    (tmp_path / "tools.py").write_text("class Bus:\n    def speak_text(self, text):\n        pass\n    def capture_feedback(self):\n        pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Native review-only fixture", encoding="utf-8")
    inventory = module.build_capability_inventory(tmp_path, framework="agentic")

    assert inventory["framework"] == "AgenticAutomation"
    assert {item["id"] for item in inventory["capabilities"]} == {item[0] for item in sources}
    assert all(item["device_validated"] is False for item in inventory["capabilities"])
    assert "generated_wav" in json.dumps(inventory)
    assert "disabled" in json.dumps(inventory).lower()


def test_agentic_prompt_does_not_describe_the_target_as_aigent():
    module = regeneration_module()
    inventory = {"framework": "AgenticAutomation", "capabilities": [], "missing_domain_adapters": [],
                 "verification_guards": ["generated_wav is input only", "disabled LLM is not a business PASS"]}
    messages = module.build_design_prompt(source_for_regeneration(), inventory, [])
    assert "AgenticAutomation" in messages[0]["content"]
    assert "verified AIGENT capability inventory" not in messages[0]["content"]
    assert "generated_wav" in messages[0]["content"]


def test_agentic_review_retains_profile_without_requiring_qamule_metadata(tmp_path):
    module = regeneration_module()
    source = source_for_regeneration()
    inventory = {"framework": "AgenticAutomation", "prompt_version": "agentic-fixture",
                 "capabilities": [{"id": "known.capture", "role": "observation"}],
                 "missing_domain_adapters": [], "recorder_policy": "No device execution"}
    jobs = []

    def complete(job):
        jobs.append(job)
        design = valid_regenerated_design()
        design["steps"][0]["native_actions"] = []
        design["steps"][0]["native_validation"] = {}
        design["steps"][0]["missing_capabilities"] = ["Fixture requires manual binding"]
        design["native_module"] = "cn_speech"
        design["prior_audit_reassessment"] = []
        return {"ok": True, "model": job["model"], "design": design}

    result = module.review_case(source, {"cases": [source]}, inventory, model="deepseek-v4-flash",
                                cache_directory=tmp_path, references=[], model_call=complete)
    assert result["status"] == "validated_design"
    assert result["review"]["prompt_version"] == "agentic-fixture"
    assert "AgenticAutomation" in jobs[0]["messages"][0]["content"]


def agentic_native_fixture():
    specification = importlib.util.find_spec("backend.analytics.cn_speech_agentic_automation")
    assert specification is not None, "The native AgenticAutomation mapping/export layer is missing"
    module = importlib.import_module("backend.analytics.cn_speech_agentic_automation")
    source = source_for_regeneration()
    design = valid_regenerated_design()
    design["native_module"] = "cn_speech"
    design["prior_audit_reassessment"] = []
    design["steps"][0]["native_actions"] = [{"action": "speak_text", "text": "shuffle"}]
    design["steps"][0]["native_validation"] = {"type": "ocr", "ocr_contains": ["shuffle"]}
    design["steps"][0]["missing_capabilities"] = []
    inventory = {"framework": "AgenticAutomation", "control_ids": [],
                 "diagnostic_keys": ["ediabas.climate_status.status", "ediabas.climate_status.values.ac_power_row1"]}
    return module, source, design, inventory


@pytest.mark.parametrize("change,match", [
    ({"native_actions": [{"action": "speak_text", "text": "Never in original action"}]}, "source action"),
    ({"native_actions": [{"action": "click_control", "control": "invented.control"}]}, "control"),
    ({"native_validation": {"type": "asr", "asr_contains": ["shuffle"], "source": "generated_wav"}}, "input"),
    ({"native_validation": {"type": "llm", "llm_query": "Check state", "enabled": False}}, "disabled"),
    ({"native_validation": {"type": "diagnostic", "diagnostic": {"expect": {"ediabas.climate_status.status": {"equals": "ok"}}}}}, "target"),
])
def test_agentic_native_mapping_rejects_invented_actions_and_pseudo_oracles(change, match):
    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0].update(change)
    with pytest.raises(ValueError, match=match):
        module.validate_agentic_design(source, design, inventory)


def test_agentic_native_export_is_disabled_and_keeps_full_source(tmp_path):
    import yaml

    module, source, design, inventory = agentic_native_fixture()
    record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
              "input_sha256": "fixture", "review": {"source_case": source, "design": design}}
    library = regeneration_module().build_regenerated_library({"cases": [source]}, [record])
    summary = module.write_native_bundle(library, inventory, tmp_path)
    suite = yaml.safe_load((tmp_path / "suite_yaml/CN_Speech_Review_Drafts.yaml").read_text(encoding="utf-8"))

    assert summary["cases"] == 1 and summary["enabled_cases"] == 0
    assert suite["runner"] == "hybrid_runner"
    case = suite["cases"][0]
    assert case["enabled"] is False
    assert case["source_meta"]["source_full_script"] == source["octane_script"]
    assert case["source_meta"]["domain_approved"] is False
    assert case["utterance"] == "shuffle"
    assert case["validation"]["ocr_contains"] == ["shuffle"]
    assert suite["module_llm_enabled"]["cn_speech"] is True
    assert suite["bench_asr"]["source"] != "generated_wav"


def test_agentic_reassessment_covers_every_prior_opinion_without_approval():
    module, source, design, inventory = agentic_native_fixture()
    source["prior_audit_findings"] = [{"issue": "Missing ASR adapter", "domain_confirmed": False}]
    with pytest.raises(ValueError, match="prior"):
        module.validate_agentic_design(source, design, inventory)
    design["prior_audit_reassessment"] = [{"finding_index": 1, "disposition": "superseded_framework_assumption",
                                           "reason": "Actual recorded-feedback ASR exists; device validation remains pending"}]
    module.validate_agentic_design(source, design, inventory)


def test_agentic_review_rejects_invalid_native_mapping_before_cache(tmp_path):
    _, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["native_actions"] = [{"action": "speak_text", "text": "invented source input"}]
    inventory.update({"capabilities": [{"id": "known.capture", "role": "observation"}],
                      "missing_domain_adapters": [], "recorder_policy": "No hardware"})

    def complete(job):
        return {"ok": True, "model": job["model"], "design": design}

    result = regeneration_module().review_case(source, {"cases": [source]}, inventory,
                                               model="deepseek-v4-flash", cache_directory=tmp_path,
                                               references=[], model_call=complete)
    assert result["status"] == "design_validation_failed"
    assert "source action" in result["error"]
    assert list(tmp_path.glob("*.json")) == []


def test_agentic_prompt_includes_old_opinions_and_native_schema():
    _, source, _, inventory = agentic_native_fixture()
    source["prior_audit_findings"] = [{"issue": "Old framework adapter assumption"}]
    messages = regeneration_module().build_design_prompt(source, inventory, [])
    payload = json.loads(messages[1]["content"])
    assert payload["source_case"]["prior_audit_findings"] == source["prior_audit_findings"]
    assert "native_actions" in payload["required_output_shape"]["steps"][0]
    assert "prior_audit_reassessment" in payload["required_output_shape"]


def test_agentic_source_reuses_frozen_cases_and_attaches_only_matching_opinions():
    module, source, _, _ = agentic_native_fixture()
    library = {"cases": [source], "defects": []}
    original = json.dumps(library, sort_keys=True)
    audit = {"cases": [{"case_key": source["case_key"], "octane_script": source["octane_script"]}],
             "business_audits": [{"case_key": source["case_key"], "status": "audit_validated",
                                  "audit": {"findings": [{"issue": "Unconfirmed old-framework gap", "domain_confirmed": False}]}}]}
    adapted = module.attach_prior_audit(library, audit)
    assert adapted["cases"][0]["octane_script"] == source["octane_script"]
    assert len(adapted["cases"][0]["prior_audit_findings"]) == 1
    assert json.dumps(library, sort_keys=True) == original
    audit["cases"][0]["octane_script"] = "Different source text"
    with pytest.raises(ValueError, match="source"):
        module.attach_prior_audit(library, audit)


def test_design_source_index_feedback_lists_actual_allowed_numbers():
    design = valid_regenerated_design()
    design["steps"][0]["source_step_numbers"] = [2]
    with pytest.raises(ValueError) as error:
        regeneration_module().validate_design(source_for_regeneration(), design, {"known.capture"})
    assert "[1]" in str(error.value)
    assert "[2]" in str(error.value)


def test_agentic_invalid_validator_feedback_names_invalid_type():
    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["native_validation"] = {"type": "asr_and_ocr"}
    with pytest.raises(ValueError) as error:
        module.validate_agentic_design(source, design, inventory)
    assert "asr_and_ocr" in str(error.value)


def test_agentic_prompt_gives_a_typed_validator_and_actual_index_set():
    _, source, _, inventory = agentic_native_fixture()
    messages = regeneration_module().build_design_prompt(source, inventory, [])
    schema = json.loads(messages[1]["content"])["required_output_shape"]
    assert schema["steps"][0]["native_validation"]["type"] == "ocr"
    assert "Allowed source_step_numbers for THIS case: [1]" in messages[0]["content"]


def test_agentic_export_retains_declared_gaps_and_demotes_partial_oracles(tmp_path):
    import yaml

    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["missing_capabilities"] = ["Independent vehicle TTS result observation"]
    record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
              "input_sha256": "fixture", "review": {"source_case": source, "design": design}}
    library = regeneration_module().build_regenerated_library({"cases": [source]}, [record])
    summary = module.write_native_bundle(library, inventory, tmp_path)
    case = yaml.safe_load((tmp_path / "suite_yaml/CN_Speech_Review_Drafts.yaml").read_text(encoding="utf-8"))["cases"][0]
    assert summary["cases_with_mapping_gaps"] == 1
    assert case["source_meta"]["mapping_gaps"][0]["missing"] == design["steps"][0]["missing_capabilities"]
    assert case["source_meta"]["proposed_native_validations"][0] == design["steps"][0]["native_validation"]
    assert case["validation"]["type"] == "llm" and "manual_check" in case["validation"]["llm_query"]
    assert not any(step.get("action") == "validate_observation" for step in case["steps"])


def test_agentic_output_location_rejects_target_and_unrelated_existing_release(tmp_path):
    module, _, _, _ = agentic_native_fixture()
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError, match="target"):
        module.validate_output_location(target / "drafts", target, "source", "audit")
    unrelated = tmp_path / "old_release"
    unrelated.mkdir()
    (unrelated / "README.md").write_text("An existing unrelated release", encoding="utf-8")
    with pytest.raises(ValueError, match="existing"):
        module.validate_output_location(unrelated, target, "source", "audit")
    module.validate_output_location(tmp_path / "new_release", target, "source", "audit")


def test_agentic_numbered_spoken_commands_require_complete_ordered_mapping():
    module, source, design, inventory = agentic_native_fixture()
    source["procedure_steps"][0]["action"] = '1.say:"one"\n2.say:"two"\n3.say:"three"'
    design["steps"][0]["native_actions"] = [{"action": "speak_text", "text": "one"}]
    with pytest.raises(ValueError, match="source command"):
        module.validate_agentic_design(source, design, inventory)
    design["steps"][0]["native_actions"] = [{"action": "speak_text", "text": value} for value in ("two", "one", "three")]
    with pytest.raises(ValueError, match="source command"):
        module.validate_agentic_design(source, design, inventory)


def test_agentic_export_binds_cases_and_review_sources_before_write(tmp_path):
    module, source, design, inventory = agentic_native_fixture()
    frozen = {"cases": json.loads(json.dumps([source]))}
    record = {"case_key": source["case_key"], "status": "validated_design", "model": "deepseek-v4-flash",
              "input_sha256": "fixture", "review": {"source_case": source, "design": design}}
    library = regeneration_module().build_regenerated_library({"cases": [source]}, [record])
    module.validate_review_sources(library, frozen)
    library["cases"][0]["procedure_steps"][0]["action"] = "A different displayed action"
    with pytest.raises(ValueError, match="does not match"):
        module.write_native_bundle(library, inventory, tmp_path)
    library["model_reviews"][0]["review"]["source_case"]["octane_script"] = "A different frozen source"
    with pytest.raises(ValueError, match="source"):
        module.validate_review_sources(library, frozen)


def test_agentic_output_location_does_not_overwrite_past_dated_release(tmp_path):
    module, _, _, _ = agentic_native_fixture()
    with pytest.raises(ValueError, match="dated"):
        module.validate_output_location(tmp_path / "20200101" / "release", tmp_path / "target", "source", "audit")


def test_agentic_missing_mapping_feedback_identifies_step_and_required_gap_field():
    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["native_actions"] = []
    with pytest.raises(ValueError) as error:
        module.validate_agentic_design(source, design, inventory)
    assert "step 1" in str(error.value)
    assert "native_actions" in str(error.value)
    assert "missing_capabilities" in str(error.value)


def test_agentic_failed_design_resume_uses_only_matching_evidence_and_error(tmp_path):
    _, source, design, inventory = agentic_native_fixture()
    inventory.update({"capabilities": [{"id": "known.capture", "role": "observation"}],
                      "missing_domain_adapters": [], "recorder_policy": "No hardware"})
    invalid = json.loads(json.dumps(design))
    invalid["steps"][0]["native_actions"][0]["text"] = "Invented text"
    module = regeneration_module()

    def fail(job):
        return {"ok": True, "model": job["model"], "design": invalid}

    failed = module.review_case(source, {"cases": [source]}, inventory, model="deepseek-v4-flash",
                                cache_directory=tmp_path, references=[], model_call=fail)
    jobs = []

    def repair(job):
        jobs.append(job)
        return {"ok": True, "model": job["model"], "design": design}

    result = module.review_case(source, {"cases": [source]}, inventory, model="deepseek-v4-flash",
                                cache_directory=tmp_path, references=[], model_call=repair, previous_record=failed)
    assert result["status"] == "validated_design"
    assert jobs[0]["messages"][-2]["role"] == "assistant"
    assert "source action" in jobs[0]["messages"][-1]["content"]
    assert result["previous_failed_calls"] == failed["calls"]


def test_agentic_nested_llm_and_missing_target_feedback_gives_exact_fix():
    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["native_validation"] = {"type": "llm", "llm": {"llm_query": "A nested query"}}
    with pytest.raises(ValueError) as error:
        module.validate_agentic_design(source, design, inventory)
    assert "top-level" in str(error.value)
    design["steps"][0]["native_validation"] = {"type": "diagnostic", "diagnostic": {"expect": {"ediabas.climate_status.status": {"equals": "ok"}}}}
    with pytest.raises(ValueError) as error:
        module.validate_agentic_design(source, design, inventory)
    assert "native_validation={}" in str(error.value)


def test_agentic_input_feedback_shows_original_actions_not_expected_text():
    module, source, design, inventory = agentic_native_fixture()
    design["steps"][0]["native_actions"] = [{"action": "speak_text", "text": "Invented input"}]
    with pytest.raises(ValueError) as error:
        module.validate_agentic_design(source, design, inventory)
    assert source["procedure_steps"][0]["action"] in str(error.value)
    assert "expected" in str(error.value)