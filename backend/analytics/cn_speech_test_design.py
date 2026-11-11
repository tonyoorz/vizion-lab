from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from backend.analytics.cn_speech_automation_bundle import FAMILIES


class _DescriptionText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_description(value: str) -> str:
    parser = _DescriptionText()
    parser.feed(str(value or ""))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


_SECTION = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:\[\s*)?"
    r"(?P<label>pre[- ]?conditions?(?:\s+and\s+actions?)?|"
    r"actions?\s*(?:&|and)\s*observed\s+results?|"
    r"steps?\s+and\s+expected\s+behaviou?r|"
    r"actions?\s*/\s*steps?\s+to\s+reproduce|actions?\s+steps?|"
    r"actions?\s+to\s+reproduce|steps?\s+to\s+reproduce|test\s+steps?|actions?|"
    r"expected\s+(?:results?(?:\s*/\s*behaviou?r)?|behaviou?r)|"
    r"(?:actual(?:\s*/\s*observed)?|observed)\s+(?:results?(?:\s*/\s*behaviou?r)?|behaviou?r)|test\s+results?|"
    r"steps?\s+to\s+recover(?:y)?|recover\s+steps?|recovery|"
    r"corrective\s+action\s*/\s*recovery|possible\s+impact|customer\s+impact|zeitpunkt|traces|logs|"
    r"time\s*stamp|error\s+occurrence\s+rate|occurrence\s+rate\s*&\s*timestamp|"
    r"contact\s+person|context\s+and\s+environment|"
    r"(?:sw|hw)\s+version|(?:english|chinese)\s+version)"
    r"\s*(?:\]\s*:?|\*+\s*:?|:\s*|(?=\n|$))"
)


def parse_defect_evidence(defect: dict[str, Any]) -> dict[str, Any]:
    text = _plain_description(defect.get("description", ""))
    matches = list(_SECTION.finditer(text))
    sections = {"preconditions": [], "actions": [], "expected": [], "observed": [],
                "recovery": [], "mixed_actions_observed": []}
    combined = False
    for index, match in enumerate(matches):
        label = match.group("label").casefold()
        content = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        if label.startswith("action") and "observed" in label:
            kind = "mixed_actions_observed"
        elif label.startswith("steps and expected"):
            kind = "actions"
            combined = True
        elif label.startswith("pre") and "and" in label:
            kind = "actions"
            combined = True
        elif label.startswith("pre"):
            kind = "preconditions"
        elif label.startswith("expected"):
            kind = "expected"
        elif label.startswith(("actual", "observed", "test result")):
            kind = "observed"
        elif label.startswith(("recover", "corrective", "step to recover", "steps to recover")):
            kind = "recovery"
        elif label.startswith(("action", "step", "test step")):
            kind = "actions"
        else:
            continue
        if content:
            sections[kind].append(content)
    has_actions = bool(_action_rows("\n".join(sections["actions"]), "\n".join(sections["expected"])))
    return {**{key: "\n".join(values) for key, values in sections.items()},
            "plain_description": text, "combined_setup_actions": combined,
            "extraction_status": "mixed_procedure_requires_review" if sections["mixed_actions_observed"] else
            "explicit_actions_and_expected" if has_actions and sections["expected"]
            else "insufficient_evidence"}


def _action_rows(actions: str, expected: str) -> list[dict[str, str]]:
    numbered = re.compile(r"^\s*(?:\d+[.)]|[-*#]+)\s*")
    parts: list[str] = []
    for line in actions.splitlines():
        if not line.strip():
            continue
        if numbered.match(line):
            action = numbered.sub("", line).strip()
            if action:
                parts.append(action)
        elif parts:
            parts[-1] += "\n" + line.strip()
        else:
            parts.append(line.strip())
    return [{"action": part, "expected": expected if index == len(parts) - 1 else ""}
            for index, part in enumerate(parts)]


def _design_steps(case_key: str, steps: list[dict[str, str]], families: list[str]) -> list[dict[str, Any]]:
    oracle_by_family = {family[0]: family[3] for family in FAMILIES}
    candidates = "; ".join(dict.fromkeys(oracle_by_family[family] for family in families
                                       if family in oracle_by_family))
    rows = []
    for index, step in enumerate(steps, start=1):
        action = step.get("action", "")
        rows.append({
            "case_key": case_key, "step_number": index, "action": action,
            "expected_result": step.get("expected", ""),
            "action_capability_candidate": "Real microphone-path audio input" if re.search(
                r"\b(say|speak|speech|utterance|wakeup)\b", action, re.I
            ) else "Review and bind to a verified device/fixture action",
            "verify_method_candidate": candidates or "Review an independent, observable result reader",
            "verdict_rule": "All reviewed checkpoints must match fresh independent observations",
            "timeout_seconds": None, "timeout_basis": "Not approved; require requirement or domain review",
            "evidence_required": "Timestamped input, UI/event evidence and independent result readback",
            "action_binding": "unimplemented", "verify_binding": "unimplemented",
            "review_status": "not_reviewed",
        })
    return rows


def _description(case_key: str, title: str, defect_ids: list[str], test_id: str | None) -> str:
    return (
        "<html><body><p><b>Objective:</b> " + escape(title) + "</p>"
        "<p><b>Local design key:</b> " + escape(case_key) + "</p>"
        "<p><b>Source test:</b> " + escape(test_id or "No confirmed existing test") + "</p>"
        "<p><b>Source defects:</b> " + escape(", ".join(defect_ids) or "None linked") + "</p>"
        "<p><b>Status:</b> Draft requiring domain review; not device-validated automation.</p>"
        "<p><b>Approval:</b> Reproduction and expected-result evidence are not approved requirements.</p>"
        "</body></html>"
    )


def build_design_library(
    catalog: dict[str, Any],
    defects: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    candidate_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_cases = {case["octane_test_id"]: case for case in catalog["cases"]}
    valid_defects = {str(defect["defect_id"]): defect for defect in defects}
    relations = [dict(relation, failure_mode_coverage_confirmed=False)
                 for relation in relationships if relation["test_id"] in source_cases]
    proposals = [dict(match, relation_kind="text_similarity_candidate",
                      failure_mode_coverage_confirmed=False)
                 for match in (candidate_matches or [])]
    cases = []
    steps = []
    for source in catalog["cases"]:
        test_id = source["octane_test_id"]
        key = "CNS-TC-" + test_id
        defect_ids = sorted({relation["defect_id"] for relation in relations
                             if relation["test_id"] == test_id})
        case = {
            "case_key": key, "name": source["title"], "source_kind": "existing_testcase",
            "source_test_id": test_id, "source_defect_ids": defect_ids,
            "feature_ids": source["feature_ids"], "suggested_test_ids": [],
            "families": source["family_candidates"],
            "preconditions": "\n".join(source["parsed"]["preconditions"]),
            "source_version_stamp": source["source_script"].get("version_stamp"),
            "source_last_modified": source.get("source_last_modified"),
            "procedure_steps": source["parsed"]["steps"],
            "octane_script": source["source_script"]["script"],
            "approval_status": "draft_needs_domain_review", "automation_ready": False,
            "disposition": "review_existing_failure_coverage" if defect_ids else "baseline_candidate",
            "blockers": ["Domain and expected-result approval", "Action and Verify implementation",
                         "Approved audio/test data", "Timeout and applicability review", "Device validation"],
            "octane_id_for_create": None, "owner_id_for_create": None, "servicepack_id_for_create": None,
        }
        case["description_html"] = _description(key, case["name"], defect_ids, test_id)
        cases.append(case)
        steps.extend(_design_steps(key, source["parsed"]["steps"], case["families"]))

    defect_rows = []
    regressions: dict[str, dict[str, Any]] = {}
    for identifier, defect in sorted(valid_defects.items()):
        parsed = parse_defect_evidence(defect)
        linked_tests = sorted({relation["test_id"] for relation in relations
                               if relation["defect_id"] == identifier})
        suggested_tests = sorted({match["test_id"] for match in proposals
                                  if match["defect_id"] == identifier})
        row = {**defect, **parsed, "explicit_test_ids": linked_tests,
               "suggested_test_ids": suggested_tests,
               "disposition": "insufficient_evidence", "candidate_case_key": None,
               "expected_result_authority": "Defect author statement; not approved acceptance criteria"}
        if parsed["extraction_status"] == "explicit_actions_and_expected":
            procedure_steps = _action_rows(parsed["actions"], parsed["expected"])
            payload = {key: defect.get(key, "") for key in ("project", "assigned_ecu", "software_version", "lead_model")}
            payload.update({key: re.sub(r"[ \t]+", " ", parsed[key]).strip().casefold()
                            for key in ("preconditions", "expected")})
            payload["actions"] = [step["action"].strip().casefold() for step in procedure_steps]
            signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            key = "CNS-REG-" + signature
            feature_ids = set(defect.get("in_scope_feature_ids", []))
            for test_id in linked_tests:
                feature_ids.update(source_cases[test_id]["feature_ids"])
            families = {family for test_id in linked_tests + suggested_tests
                        if test_id in source_cases for family in source_cases[test_id]["family_candidates"]}
            if key not in regressions:
                lines = []
                if parsed["preconditions"]:
                    lines.append("- [PreCon] " + parsed["preconditions"])
                for step in procedure_steps:
                    lines.append("- " + step["action"])
                    if step["expected"]:
                        lines.append("- ? " + step["expected"])
                script = "\n".join(lines)
                regressions[key] = {
                    "case_key": key, "name": "[CN Speech][Regression Draft] " + defect["name"],
                    "source_kind": "defect_regression", "source_test_id": None,
                    "source_defect_ids": [], "feature_ids": [], "suggested_test_ids": [],
                    "families": [], "preconditions": parsed["preconditions"],
                    "source_version_stamp": None, "source_last_modified": defect.get("last_modified"),
                    "procedure_steps": procedure_steps,
                    "octane_script": script, "approval_status": "draft_needs_domain_review",
                    "automation_ready": False, "disposition": "review_regression_candidate",
                    "blockers": ["Confirm Epic/Feature applicability and failure-mode coverage",
                                 "Approve defect-stated expected result", "Review procedure and initial state",
                                 "Implement Action and Verify; approve timeouts/data; validate device"],
                    "octane_id_for_create": None, "owner_id_for_create": None, "servicepack_id_for_create": None,
                }
            regression = regressions[key]
            regression["source_defect_ids"].append(identifier)
            regression["feature_ids"] = sorted(set(regression["feature_ids"]).union(feature_ids))
            regression["suggested_test_ids"] = sorted(set(regression["suggested_test_ids"]).union(linked_tests, suggested_tests))
            regression["families"] = sorted(set(regression["families"]).union(families))
            row["candidate_case_key"] = key
            row["disposition"] = "review_extension_of_existing" if linked_tests else "review_new_or_variant_candidate"
        defect_rows.append(row)
    for regression in regressions.values():
        regression["source_defect_ids"].sort()
        regression["description_html"] = _description(
            regression["case_key"], regression["name"], regression["source_defect_ids"], None
        )
        steps.extend(_design_steps(regression["case_key"], regression["procedure_steps"], regression["families"]))
        cases.append(regression)
    derived = [{"case_key": case["case_key"], "defect_id": identifier,
                "relation_kind": "derived_regression_candidate",
                "evidence_id": "defect:" + identifier + ":actions_and_expected",
                "failure_mode_coverage_confirmed": False}
               for case in regressions.values() for identifier in case["source_defect_ids"]]
    return {"schema_version": 1, "epic_id": catalog["epic_id"],
            "document_type": "reviewable_test_design_not_executable_automation",
            "cases": cases, "steps": steps, "defects": defect_rows,
            "traceability": relations + proposals + derived, "automation_ready_count": 0,
            "writes_to_octane": False, "device_execution_validated": False}


def read_local_defect_source(db_path: Path, catalog: dict[str, Any]) -> dict[str, Any]:
    test_ids = [case["octane_test_id"] for case in catalog["cases"]]
    if not test_ids:
        raise ValueError("An empty baseline cannot define the defect retrieval scope")
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    relationships = []
    diagnostics = []
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"octane_defects", "octane_manual_runs"}.issubset(tables):
            raise ValueError("The source database lacks defect or manual-run evidence")
        placeholders = ",".join("?" for identifier in test_ids)
        query = (
            "SELECT mr_id,test_id,defect_id,execution_sw_version,test_version,fetched_at "
            "FROM octane_manual_runs WHERE test_id IN (" + placeholders + ") "
            "AND defect_id IS NOT NULL AND defect_id<>''"
        )
        for row in connection.execute(query, test_ids):
            relationships.append({"test_id": str(row["test_id"]), "defect_id": str(row["defect_id"]),
                                  "relation_kind": "manual_run", "evidence_id": str(row["mr_id"]),
                                  "source_fetched_at": row["fetched_at"],
                                  "execution_sw_version": row["execution_sw_version"],
                                  "executed_test_version": row["test_version"]})
        if "octane_testcases" in tables:
            query = ("SELECT test_id,scope_team,scope_release,defect_ids_json,fetched_at "
                     "FROM octane_testcases WHERE test_id IN (" + placeholders + ")")
            for row in connection.execute(query, test_ids):
                try:
                    identifiers = json.loads(row["defect_ids_json"] or "[]")
                    if not isinstance(identifiers, list):
                        raise ValueError("Defect references are not a list")
                except (TypeError, ValueError):
                    diagnostics.append({"test_id": row["test_id"], "error": "Invalid cached defect references"})
                    continue
                for identifier in identifiers:
                    identifier = identifier.get("id") if isinstance(identifier, dict) else identifier
                    if identifier is None:
                        continue
                    relationships.append({
                        "test_id": str(row["test_id"]), "defect_id": str(identifier),
                        "relation_kind": "testcase_cache",
                        "evidence_id": "|".join(str(row[key] or "") for key in ("test_id", "scope_team", "scope_release")),
                        "source_fetched_at": row["fetched_at"],
                    })
        if "octane_testcase_relations" in tables:
            query = (
                "SELECT test_id,related_id,relation_type,fetched_at FROM octane_testcase_relations "
                "WHERE test_id IN (" + placeholders + ") AND (lower(relation_type) LIKE '%defect%' "
                "OR related_subtype='defect')"
            )
            for row in connection.execute(query, test_ids):
                relationships.append({"test_id": str(row["test_id"]), "defect_id": str(row["related_id"]),
                                      "relation_kind": "testcase_relation", "evidence_id": str(row["relation_type"]),
                                      "source_fetched_at": row["fetched_at"]})
        unique = {(row["test_id"], row["defect_id"], row["relation_kind"], row["evidence_id"]): row
                  for row in relationships}
        relationships = sorted(unique.values(), key=lambda row: tuple(str(row[key]) for key in
                               ("test_id", "defect_id", "relation_kind", "evidence_id")))
        explicit_ids = {row["defect_id"] for row in relationships}
        fields = (
            "defect_id,name,description,project,phase,severity,assigned_ecu,software_version,"
            "lead_model,last_modified,fetched_at,aida_english,product_areas"
        )
        topic_query = (
            "SELECT " + fields + " FROM octane_defects WHERE project='IDCEVO' AND ("
            "lower(coalesce(name,'') || ' ' || coalesce(aida_english,'') || ' ' || coalesce(product_areas,'')) "
            "LIKE '%cn speech%' OR lower(coalesce(aida_english,'') || ' ' || coalesce(product_areas,'')) "
            "LIKE '%speech%china%') ORDER BY defect_id"
        )
        topic = {str(row["defect_id"]): dict(row) for row in connection.execute(topic_query)}
        defects = dict(topic)
        ordered_ids = sorted(explicit_ids)
        for offset in range(0, len(ordered_ids), 500):
            batch = ordered_ids[offset:offset + 500]
            query = "SELECT " + fields + " FROM octane_defects WHERE defect_id IN (" + ",".join("?" for identifier in batch) + ")"
            defects.update({str(row["defect_id"]): dict(row) for row in connection.execute(query, batch)})
        missing_ids = sorted(explicit_ids.difference(defects))
        for identifier in missing_ids:
            defects[identifier] = {"defect_id": identifier, "name": "", "description": "",
                                   "source_status": "Linked defect absent from local database"}
        for identifier, defect in defects.items():
            defect["scope_reasons"] = (["explicit_test_relation"] if identifier in explicit_ids else [])
            if identifier in topic:
                defect["scope_reasons"].append("idcevo_cn_speech_topic_candidate")
        assert connection.total_changes == 0
    finally:
        connection.close()
    return {
        "read_only": True, "source_db": str(db_path.resolve()),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Explicit links to the frozen tests plus local IDCEVO CN Speech topic candidates; not all Epic defects",
        "topic_filter": "IDCEVO and CN Speech in title/AIDA/product areas, or Speech-China in AIDA/product areas",
        "upstream_sync_performed": False, "defects": [defects[key] for key in sorted(defects)],
        "relationships": relationships, "explicit_defect_count": len(explicit_ids),
        "topic_candidate_count": len(topic), "missing_linked_defect_ids": missing_ids,
        "diagnostics": diagnostics,
        "fetched_at_values": sorted({str(row["fetched_at"]) for row in defects.values() if row.get("fetched_at")}),
    }


def suggest_test_matches(catalog: dict[str, Any], defects: list[dict[str, Any]], *, threshold: float = 0.18) -> list[dict[str, Any]]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    cases = catalog["cases"]
    documents = [case["title"] + " " + " ".join(
        step["action"] + " " + step["expected"] for step in case["parsed"]["steps"]
    ) for case in cases]
    queries = []
    for defect in defects:
        parsed = parse_defect_evidence(defect)
        queries.append((defect.get("name") or "") + " " + parsed["actions"] + " " + parsed["expected"])
    if not documents or not queries:
        return []
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), max_features=40000, sublinear_tf=True)
    matrix = vectorizer.fit_transform(documents)
    similarities = cosine_similarity(vectorizer.transform(queries), matrix)
    matches = []
    for defect, scores in zip(defects, similarities):
        for index in scores.argsort()[::-1][:3]:
            score = float(scores[index])
            if score >= threshold:
                matches.append({"defect_id": str(defect["defect_id"]),
                                "test_id": cases[int(index)]["octane_test_id"],
                                "similarity_score": round(score, 6),
                                "match_method": "char_2_4_tfidf_cosine_top3",
                                "scope_confirmed": False})
    return matches


def write_design_workbook(library: dict[str, Any], path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    book = Workbook()
    book.remove(book.active)
    long_text: dict[str, str] = {}

    def cell_value(value: Any, reference: str) -> Any:
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub(lambda match: f"\\u{ord(match.group()):04x}", value)
            if len(value.encode("utf-16-le")) // 2 > 32000:
                long_text[reference] = value
                return "Full text in Long Text / canonical JSON: " + reference
        return value

    def add_sheet(name: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
        sheet = book.create_sheet(name)
        sheet.append(headers)
        for row_number, row in enumerate(rows, start=2):
            identifier = str(row.get("case_key") or row.get("defect_id") or row.get("reference") or row_number)
            if "step_number" in row:
                identifier += ":step:" + str(row["step_number"])
            for column, header in enumerate(headers, start=1):
                value = cell_value(row.get(header), identifier + ":" + header)
                cell = sheet.cell(row_number, column, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            sheet.row_dimensions[row_number].height = 66
        for cell in sheet[1]:
            cell.font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
            cell.fill = PatternFill("solid", fgColor="245A62")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[1].height = 32
        for index, header in enumerate(headers, start=1):
            width = 64 if any(word in header for word in ("action", "expected", "description", "script", "blockers", "method", "evidence")) else 25
            if header in {"name", "preconditions", "observed", "mixed_actions_observed", "text"}:
                width = 70
            if name == "Readme" and header == "value":
                width = 110
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        if "approval_status" in headers and rows:
            column = get_column_letter(headers.index("approval_status") + 1)
            validation = DataValidation(type="list", formula1='"draft_needs_domain_review,reviewed,blocked"')
            sheet.add_data_validation(validation)
            validation.add(f"{column}2:{column}{len(rows) + 1}")

    baseline = sum(case["source_kind"] == "existing_testcase" for case in library["cases"])
    readme = [
        ("Purpose", "Reviewable Octane-content-compatible test design; not executable or import-certified automation"),
        ("Baseline cases", baseline), ("Regression candidates", len(library["cases"]) - baseline),
        ("Defects retained", len(library["defects"])), ("Automation ready", 0),
        ("Evidence strength", "manual_run/testcase_cache/testcase_relation are stored relations; text_similarity_candidate is only a suggestion; neither proves failure-mode coverage"),
        ("Expected results", "Existing source expected results and defect author statements need applicability/requirement approval; observed failures are not expected results"),
        ("Octane mapping", "name + description_html + octane_script -> /tests metadata + /tests/{id}/script; owner/servicepack/Feature IDs must be resolved before approved writes"),
        ("Import status", "Excel import template compatibility not verified; no Octane writes performed"),
        ("Review workflow", "Draft -> domain review -> capability/data binding -> script implementation -> device validation. Editing this workbook does not automatically update JSON or Octane."),
        ("Scope", library.get("source_scope", "Frozen baseline and explicitly supplied defect evidence")),
        ("Freshness", "Defects are from the existing local database; upstream synchronization was not performed. Per-defect fetched_at is retained."),
        ("Long text", "Content over the Excel cell limit is split in Long Text; complete source is always retained in JSON. XML control characters are visibly escaped."),
    ]
    add_sheet("Readme", ["item", "value"], [{"item": key, "value": value} for key, value in readme])
    add_sheet("Cases", ["case_key", "name", "source_kind", "source_test_id", "source_defect_ids",
                        "feature_ids", "suggested_test_ids", "families", "approval_status", "automation_ready",
                        "disposition", "preconditions", "blockers", "source_version_stamp", "source_last_modified",
                        "source_octane_phase", "source_test_substatus"], library["cases"])
    add_sheet("Steps", ["case_key", "step_number", "action", "expected_result", "review_status"], library["steps"])
    add_sheet("Automation", ["case_key", "step_number", "action_capability_candidate", "verify_method_candidate",
                             "verdict_rule", "timeout_seconds", "timeout_basis", "evidence_required",
                             "action_binding", "verify_binding", "review_status"], library["steps"])
    add_sheet("Traceability", ["case_key", "test_id", "defect_id", "relation_kind", "evidence_id", "similarity_score",
                               "match_method", "source_fetched_at", "execution_sw_version", "executed_test_version",
                               "failure_mode_coverage_confirmed"], library["traceability"])
    add_sheet("Defect Review", ["defect_id", "name", "scope_reasons", "project", "phase", "severity",
                                "assigned_ecu", "software_version", "lead_model", "preconditions", "actions",
                                "expected", "observed", "recovery", "mixed_actions_observed", "combined_setup_actions", "extraction_status",
                                "expected_result_authority", "explicit_test_ids", "suggested_test_ids",
                                "disposition", "candidate_case_key", "last_modified", "fetched_at", "source_status"], library["defects"])
    add_sheet("Octane Drafts", ["case_key", "source_test_id", "name", "description_html", "octane_script",
                                "feature_ids", "owner_id_for_create", "servicepack_id_for_create", "octane_id_for_create",
                                "approval_status", "automation_ready"], library["cases"])
    evidence = [{"case_key": case["case_key"], "source_kind": case["source_kind"],
                 "source_ids": [case["source_test_id"]], "related_defect_ids": case["source_defect_ids"],
                 "version_stamp": case["source_version_stamp"], "text": case["octane_script"]}
                for case in library["cases"] if case["source_kind"] == "existing_testcase"]
    evidence.extend({"defect_id": defect["defect_id"], "source_kind": "defect_description_html",
                     "source_ids": [defect["defect_id"]], "text": defect.get("description", "")}
                    for defect in library["defects"])
    add_sheet("Source Evidence", ["case_key", "defect_id", "source_kind", "source_ids", "related_defect_ids", "version_stamp", "text"], evidence)
    chunks = [{"reference": reference, "chunk_number": index // 15000 + 1, "text": value[index:index + 15000]}
              for reference, value in long_text.items() for index in range(0, len(value), 15000)]
    if chunks:
        add_sheet("Long Text", ["reference", "chunk_number", "text"], chunks)
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    book.close()


def write_compact_workbook(library: dict[str, Any], path: Path) -> None:
    from math import ceil

    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    headers = [
        "\u7528\u4f8b\u7f16\u53f7", "\u6765\u6e90\u7c7b\u578b", "\u6d4b\u8bd5\u540d\u79f0",
        "\u524d\u7f6e\u6761\u4ef6", "\u64cd\u4f5c\u6b65\u9aa4 Action", "\u9884\u671f\u7ed3\u679c",
        "\u9a8c\u8bc1\u65b9\u5f0f Verify\uff08\u5efa\u8bae\uff09", "\u6062\u590d\u8981\u6c42",
        "TA\u8bc4\u5ba1\u4e0e\u7f3a\u53e3", "\u6765\u6e90\u8ffd\u6eaf",
    ]
    widths = [29, 16, 48, 42, 68, 62, 48, 34, 42, 28]
    oracle_by_family = {family[0]: family[3] for family in FAMILIES}
    by_case: dict[str, list[dict[str, Any]]] = {}
    for step in library["steps"]:
        by_case.setdefault(step["case_key"], []).append(step)
    defects = {row["defect_id"]: row for row in library["defects"]}
    book = Workbook()
    sheet = book.active
    sheet.title = "TA\u4e2d\u95f4\u6001"
    sheet.append(headers)
    for index, header in enumerate(headers, start=1):
        cell = sheet.cell(1, index)
        cell.font = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245A62")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[get_column_letter(index)].width = widths[index - 1]
    sheet.row_dimensions[1].height = 36
    sheet.cell(1, 2).comment = Comment(
        "New regression candidates are listed first; original Octane cases follow. "
        "Filter this column to distinguish them. This is a layout-only review export.", "Vizion Lab"
    )
    sheet.cell(1, 7).comment = Comment(
        "Verification methods are proposals, not implemented bindings or approved acceptance criteria.", "Vizion Lab"
    )
    sheet.cell(1, 9).comment = Comment(
        "TA feasibility is not assessed by this export. No new approval, automation readiness or device validation is implied.", "Vizion Lab"
    )
    ordered = sorted(library["cases"], key=lambda case: case["source_kind"] != "defect_regression")
    for row_number, case in enumerate(ordered, start=2):
        steps = case.get("procedure_steps") or [
            {"action": step["action"], "expected": step["expected_result"]}
            for step in by_case.get(case["case_key"], [])
        ]
        actions = "\n".join(f'{index}. {step["action"]}' for index, step in enumerate(steps, start=1))
        expected = "\n".join(f'{index}. {step["expected"]}' for index, step in enumerate(steps, start=1)
                             if step.get("expected", "").strip())
        methods = case.get("verification_methods") or list(dict.fromkeys(
            oracle_by_family[family] for family in case["families"] if family in oracle_by_family))
        if not methods:
            methods = list(dict.fromkeys(step["verify_method_candidate"] for step in by_case.get(case["case_key"], [])))
        new_case = case["source_kind"] == "defect_regression"
        source_type = "\u65b0\u589e\u56de\u5f52\u5019\u9009" if new_case else "\u539f Octane \u7528\u4f8b"
        references = []
        if case["source_test_id"]:
            references.append("TC " + case["source_test_id"])
        if case["source_defect_ids"]:
            references.append("Defect " + ", ".join(case["source_defect_ids"]))
        recoveries = list(dict.fromkeys(defects[identifier].get("recovery", "").strip()
                                       for identifier in case["source_defect_ids"]
                                       if identifier in defects and defects[identifier].get("recovery", "").strip()))
        restoration = ("Source suggestion (review required):\n" + "\n".join(recoveries)) if new_case and recoveries else (
            "\u5f85\u8865\u5145\uff1a\u6062\u590d\u5230\u672c\u7528\u4f8b\u521d\u59cb\u72b6\u6001"
        )
        restoration = case.get("restoration_requirement") or restoration
        status = "\u5f85\u8bc4\u5ba1\uff0c\u672a\u7b5b\u5b9a TA \u9002\u7528\u6027\n\u5f85\u786e\u8ba4\uff1a\u8303\u56f4/\u9884\u671f\u3001\u63a5\u53e3\u3001\u6570\u636e\u3001\u65f6\u9650\u3001\u6062\u590d"
        status = case.get("ta_review_summary") or status
        if case.get("source_test_substatus") == "Outdated":
            status = "Outdated\uff1a\u5148\u786e\u8ba4\u539f\u7528\u4f8b\u9002\u7528\u6027\n" + status
        title = re.sub(r"^(?:\[[^\]\n]*\]\s*)+", "", case["name"]).strip()
        values = [case["case_key"], source_type, title,
                  case["preconditions"] or "\u6e90\u6750\u6599\u672a\u5355\u72ec\u5b9a\u4e49\uff0c\u5f85\u8865\u5145",
                  actions, expected or "\u6e90\u6750\u6599\u672a\u63d0\u4f9b\u660e\u786e\u68c0\u67e5\u70b9\uff0c\u5f85\u8865\u5145",
                  "\n".join(methods), restoration, status, "\n".join(references)]
        visual_lines = 1
        for column, value in enumerate(values, start=1):
            value = ILLEGAL_CHARACTERS_RE.sub(lambda match: f"\\u{ord(match.group()):04x}", str(value or ""))
            if len(value.encode("utf-16-le")) // 2 > 32767:
                raise ValueError(f'Excel cell limit exceeded for {case["case_key"]}; retain the full content in canonical JSON')
            cell = sheet.cell(row_number, column, value)
            cell.data_type = "s"
            cell.font = Font(name="Microsoft YaHei", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            visual_lines = max(visual_lines, sum(max(1, ceil(len(line) / max(10, widths[column - 1] - 3)))
                                                  for line in value.splitlines()))
        sheet.row_dimensions[row_number].height = min(300, max(66, visual_lines * 14 + 8))
        sheet.cell(row_number, 2).fill = PatternFill("solid", fgColor="E3F2E8" if new_case else "ECEFF1")
        sheet.cell(row_number, 9).fill = PatternFill("solid", fgColor="FFF4D6")
        sheet.cell(row_number, 3).comment = Comment("Original title: " + case.get("original_title", case["name"]) + "\nObjective: " + case.get("objective", ""), "Vizion Lab")
        provenance = {"case_key": case["case_key"], "feature_ids": case["feature_ids"],
                      "suggested_test_ids_not_confirmed": case["suggested_test_ids"],
                      "source_version_stamp": case["source_version_stamp"],
                      "source_last_modified": case["source_last_modified"],
                      "full_blockers": case["blockers"], "canonical_source": "test_design_library.json",
                      "design_review": case.get("design_review")}
        sheet.cell(row_number, 10).comment = Comment(json.dumps(provenance, ensure_ascii=False, indent=2), "Vizion Lab")
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 80
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:1"
    book.properties.title = "CN Speech TA Intermediate - One Case Per Row"
    book.properties.description = "One review tab. Candidates only; TA suitability, implementation and device execution remain unvalidated."
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    book.close()


def generate_design_library(catalog_path: Path, db_path: Path, output_directory: Path) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if str(catalog.get("epic_id")) != "1685240":
        raise ValueError("This design builder is specific to the frozen CN Speech Epic")
    source = read_local_defect_source(db_path, catalog)
    matches = suggest_test_matches(catalog, source["defects"])
    library = build_design_library(catalog, source["defects"], source["relationships"], matches)
    library["source_scope"] = source["scope"]
    library["source_catalog_sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    snapshot_path = catalog_path.with_name("evidence_snapshot.json")
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        metadata = {test["id"]: test["metadata"] for test in snapshot["tests"]}
        for case in library["cases"]:
            row = metadata.get(case["source_test_id"], {})
            case["source_octane_phase"] = (row.get("phase") or {}).get("name")
            case["source_test_substatus"] = (row.get("substatus_udf") or {}).get("name")
            if case["source_test_substatus"] == "Outdated":
                case["blockers"].append("Source testcase is marked Outdated; review applicability")
    output_directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("test_design_library.json", library), ("defect_evidence_snapshot.json", source)):
        (output_directory / name).write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    workbook_path = output_directory / "CN_Speech_Automation_Test_Design.xlsx"
    write_design_workbook(library, workbook_path)
    summary = {
        "baseline_cases": len(catalog["cases"]),
        "regression_candidates": len(library["cases"]) - len(catalog["cases"]),
        "total_design_cases": len(library["cases"]), "step_rows": len(library["steps"]),
        "defects_retained": len(library["defects"]), "explicit_linked_defects": source["explicit_defect_count"],
        "defects_with_available_body": sum(bool(row.get("description", "").strip()) for row in library["defects"]),
        "local_topic_candidates": source["topic_candidate_count"],
        "defects_with_actions_and_expected": sum(row["extraction_status"] == "explicit_actions_and_expected" for row in library["defects"]),
        "insufficient_evidence_defects": sum(row["extraction_status"] == "insufficient_evidence" for row in library["defects"]),
        "defects_pending_design": sum(row["disposition"] == "insufficient_evidence" for row in library["defects"]),
        "mixed_procedure_defects": sum(row["extraction_status"] == "mixed_procedure_requires_review" for row in library["defects"]),
        "explicit_relation_rows": len(source["relationships"]), "similarity_candidate_rows": len(matches),
        "outdated_source_cases": sum(case.get("source_test_substatus") == "Outdated" for case in library["cases"]),
        "missing_linked_defect_ids": source["missing_linked_defect_ids"],
        "source_diagnostics": source["diagnostics"], "source_fetched_at_values": source["fetched_at_values"],
        "automation_ready_count": 0, "octane_writes": 0, "device_operations": 0,
        "semantic_review": "Evidence parsing and similarity suggestions, not certified per-case LLM/domain review",
        "excel_import_compatibility_verified": False,
        "files_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (workbook_path, output_directory / "test_design_library.json",
                                      output_directory / "defect_evidence_snapshot.json")},
    }
    (output_directory / "design_manifest.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline Octane-compatible CN Speech Excel design library")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--db-path", type=Path, default=Path("database/source/qgate_raw.db"))
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--review-source", type=Path)
    parser.add_argument("--output-file", type=Path)
    arguments = parser.parse_args()
    if arguments.review_source:
        if arguments.output_file is None or arguments.catalog or arguments.output_directory:
            parser.error("--review-source requires --output-file and cannot be combined with source generation")
        library = json.loads(arguments.review_source.read_text(encoding="utf-8"))
        write_compact_workbook(library, arguments.output_file)
        print(json.dumps({"file": str(arguments.output_file), "tabs": 1, "rows": len(library["cases"]),
                          "regression_candidates": sum(case["source_kind"] == "defect_regression" for case in library["cases"]),
                          "source_json_unchanged": True,
                          "sha256": hashlib.sha256(arguments.output_file.read_bytes()).hexdigest()}, indent=2))
        return 0
    if arguments.catalog is None or arguments.output_directory is None:
        parser.error("--catalog and --output-directory are required for source generation")
    print(json.dumps(generate_design_library(arguments.catalog, arguments.db_path, arguments.output_directory),
                     ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())