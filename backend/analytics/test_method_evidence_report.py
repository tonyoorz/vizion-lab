"""Evidence-based comparison of testcase execution and free-test discovery."""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape, unescape
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


_EXPLICIT_FREE_TEST_PATTERN = re.compile(
    r"\b(?:free\s*-?\s*test|freetest|exploratory(?:\s+test)?|ad\s*-?\s*hoc(?:\s+test)?|monkey\s+test)\b",
    re.IGNORECASE,
)
_REPRODUCTION_ATTEMPT_PATTERN = re.compile(
    r"how\s+often(?:\s+did)?(?:\s+the)?\s+tester\s+try\s+to\s+reproduce"
    r"(?:\s+the)?\s+defect",
    re.IGNORECASE,
)
_LIFECYCLE_PATTERN = re.compile(
    r"in\s+how\s+many\s+lifecycles(?:\s+did)?(?:\s+the)?\s+tester\s+try\s+to\s+reproduce"
    r"(?:\s+the)?\s+defect",
    re.IGNORECASE,
)
_OCCURRENCE_COUNT_PATTERN = re.compile(
    r"how\s+often\s+did\s+the\s+defect\s+occur(?:\s*\([^)]*\))?",
    re.IGNORECASE,
)
_STRUCTURED_FIELD_END_PATTERN = re.compile(
    r"\b(?:in\s+how\s+many\s+lifecycles|how\s+often\s+did\s+the\s+defect\s+occur|"
    r"easiest\s+way\s+of\s+recovery|test\s+case\s+reference|observed\s+behavio[u]?r|preconditions?\s+and\s+actions?)\b",
    re.IGNORECASE,
)
_STRUCTURED_FIELD_INSTRUCTION_PATTERN = re.compile(
    r"\[\s*please\s+fill\s+in\s+natural\s+numbers\s*\]|"
    r"\(?\s*whole\s+numbers\s+starting\s+from\s+1\s*\)?",
    re.IGNORECASE,
)
_MULTI_PLATFORM_PATTERN = re.compile(
    r"observed\s+behavio[u]?r\s+occurs\s+on\s+multiple\s+platforms?"
    r"\s*(?:\(?yes/no\)?\s*)?[:\-]?\s*(?:yes|y|true)",
    re.IGNORECASE,
)
_VIN_VALUE_PATTERN = re.compile(r"\bvin(?:-num)?\s*:\s*[A-HJ-NPR-Z0-9]{6,17}\b", re.IGNORECASE)
_V_NUM_VALUE_PATTERN = re.compile(r"\bv-num\s*:\s*[A-Z0-9][A-Z0-9._-]{3,}\b", re.IGNORECASE)
_DIAGNOSTIC_ARTIFACT_PATTERN = re.compile(
    r"\b(?:logs?\s+(?:were\s+)?(?:attached|collected|uploaded|provided|available)|trace\s+(?:was\s+)?(?:attached|collected|uploaded|provided)|adb\s+log)\b",
    re.IGNORECASE,
)
_FREE_TEST_PRECONDITION_PATTERN = re.compile(r"\bpreconditions?\s*[:\-]", re.IGNORECASE)
_FREE_TEST_ACTION_SECTION_PATTERN = re.compile(
    r"\b(?:steps?\s+to\s+(?:re-?)?produce(?:\s+the\s+(?:error|problem))?|"
    r"actions?(?:/steps)?\s+to\s+(?:re-?)?produce(?:\s+the\s+(?:error|problem))?|"
    r"actions?\s*:|procedure\s*:|test\s+steps?\s*:)\s*",
    re.IGNORECASE,
)
_FREE_TEST_ACTION_SECTION_END_PATTERN = re.compile(
    r"\b(?:expected\s+(?:result|behavio[u]?r)|observed\s+(?:result|behavio[u]?r)|"
    r"customer\s+impact|possible\s+impact|test\s+case\s+reference)\b",
    re.IGNORECASE,
)
_FREE_TEST_EXPECTED_PATTERN = re.compile(r"\bexpected\s+(?:result|behavio[u]?r)\s*[:\-]?", re.IGNORECASE)
_FREE_TEST_OBSERVED_PATTERN = re.compile(r"\bobserved\s+(?:result|behavio[u]?r)\s*[:\-]?", re.IGNORECASE)
_FREE_TEST_SCENARIO_PATTERNS = (
    (
        "State / lifecycle variation",
        re.compile(
            r"\b(?:reboot|restart|sleep|wake(?:up)?|boot|reset|ignition|power[ -]?(?:cycle|off|on)|ota|upgrade|update)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Boundary / negative-path variation",
        re.compile(r"\b(?:invalid|boundary|maximum|minimum|overflow|empty|null|special character|unexpected|out of range)\b", re.IGNORECASE),
    ),
    (
        "Compatibility / variant variation",
        re.compile(r"\b(?:compatib(?:ility|le)|variant|market|region|language|locale|android|ios|huawei|samsung|iphone|device)\b", re.IGNORECASE),
    ),
    (
        "Cross-system scenario",
        re.compile(r"\b(?:integration|end[ -]?to[ -]?end|e2e|handshake|third[ -]?party|bluetooth|carplay|android auto|communication)\b", re.IGNORECASE),
    ),
    (
        "Performance / reliability exploration",
        re.compile(r"\b(?:performance|slow|lag|crash|memory|timeout|stability|stress|load|freeze|hang)\b", re.IGNORECASE),
    ),
)
_TESTCASE_TITLE_SIGNAL_PATTERNS = (
    (
        "State or lifecycle transition",
        re.compile(r"\b(?:reboot|restart|sleep|wake(?:up)?|boot|reset|update|upgrade|switch|login|logout)\b", re.IGNORECASE),
    ),
    (
        "Cross-device or cross-service workflow",
        re.compile(r"\b(?:app|android|ios|device|phone|share|casting|remote|wallet|bluetooth|carplay|profile)\b", re.IGNORECASE),
    ),
    (
        "Configuration or setup",
        re.compile(r"\b(?:set(?:\s+up)?|setting|configuration|configure|management|timer)\b", re.IGNORECASE),
    ),
    (
        "UI/HMI interaction",
        re.compile(r"\b(?:button|screen|display|shortcut|widget|dialog|popup|menu|view)\b", re.IGNORECASE),
    ),
)
_EXECUTION_METHOD_SIGNAL_ORDER = ("Planned functional workflow", *(label for label, _pattern in _TESTCASE_TITLE_SIGNAL_PATTERNS))
_FREE_TEST_SCENARIO_SIGNAL_ORDER = (
    *(label for label, _pattern in _FREE_TEST_SCENARIO_PATTERNS),
)
_FREE_TEST_DISCOVERY_BASIS_ORDER = (
    "Explicit Free-test report reference",
    "No testcase link (Free test by policy)",
)


def _canonical_defect_id(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("D-"):
        return text[2:]
    if text.startswith("D") and text[1:].isdigit():
        return text[1:]
    return text


def _clean_text(value: object) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _parse_defect_ids(value: object) -> set[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {_canonical_defect_id(item) for item in parsed if _canonical_defect_id(item)}


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _structured_field_count(text: str, field_pattern: re.Pattern[str]) -> int | None:
    """Read the populated number between one template label and the next."""
    match = field_pattern.search(text)
    if match is None:
        return None
    field_text = text[match.end():]
    boundary_match = _STRUCTURED_FIELD_END_PATTERN.search(field_text)
    if boundary_match is not None:
        field_text = field_text[:boundary_match.start()]
    field_text = _STRUCTURED_FIELD_INSTRUCTION_PATTERN.sub(" ", field_text[:120])
    values = re.findall(r"\b\d+\b", field_text)
    return int(values[0]) if values else None


def _repeatability_evidence(
    reproduction_attempts: int | None,
    reported_occurrence_count: int | None,
) -> tuple[str, float | None]:
    if (
        reproduction_attempts is None
        or reported_occurrence_count is None
        or reproduction_attempts <= 0
        or reported_occurrence_count <= 0
        or reported_occurrence_count > reproduction_attempts
    ):
        return "Not documented", None
    occurrence_rate = reported_occurrence_count / reproduction_attempts
    if occurrence_rate == 1:
        return "Fully reproducible (100%)", occurrence_rate
    if occurrence_rate >= 0.5:
        return "High repeatability (50-99%)", occurrence_rate
    return "Intermittent (<50%)", occurrence_rate


def _has_environment_fingerprint(text: str) -> bool:
    return bool(_VIN_VALUE_PATTERN.search(text) or _V_NUM_VALUE_PATTERN.search(text))


def _split_values(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in re.split(r"\s*[|;]\s*", text) if part.strip()]


def _defect_report_text(defect: sqlite3.Row) -> str:
    return " ".join(_clean_text(defect[field_name]) for field_name in ("name", "description"))


def _defect_provenance_text(defect: sqlite3.Row) -> str:
    return _defect_report_text(defect) + " " + _clean_text(defect["comments"])


def _free_test_scenario_labels(ticket_title: str) -> list[str]:
    return [label for label, pattern in _FREE_TEST_SCENARIO_PATTERNS if pattern.search(ticket_title)]


def _text_with_lines(value: object) -> str:
    text = re.sub(r"<(?:br|/p|/li|/div|/ol|/ul)[^>]*>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\n[ \t]*\n+", "\n", text).strip()


def _free_test_narrative_evidence(description: object) -> dict[str, int | bool]:
    text = _text_with_lines(description)
    action_match = _FREE_TEST_ACTION_SECTION_PATTERN.search(text)
    action_section = ""
    if action_match is not None:
        action_section = text[action_match.end():]
        if end_match := _FREE_TEST_ACTION_SECTION_END_PATTERN.search(action_section):
            action_section = action_section[:end_match.start()]
    candidate_procedure_lines = 0
    for line in action_section.splitlines():
        normalized = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if normalized:
            candidate_procedure_lines += 1
    return {
        "hasPreconditions": bool(_FREE_TEST_PRECONDITION_PATTERN.search(text)),
        "hasActionProcedure": bool(action_section.strip()),
        "hasExpectedResult": bool(_FREE_TEST_EXPECTED_PATTERN.search(text)),
        "hasObservedResult": bool(_FREE_TEST_OBSERVED_PATTERN.search(text)),
        "candidateProcedureLines": candidate_procedure_lines,
    }


def _ordered_counts(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"label": label, "count": count}
        for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _ordered_signal_counts(counter: Counter[str], order: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = [{"label": label, "count": counter[label]} for label in order if counter[label]]
    known_labels = set(order)
    rows.extend(
        {"label": label, "count": count}
        for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))
        if label not in known_labels and count
    )
    return rows


def build_test_method_evidence_payload(
    *,
    db_path: str | Path,
    team: str | None = "DTSV_China",
) -> dict[str, Any]:
    """Build an auditable discovery-method payload from Octane source data.

    A direct manual-run defect link is the only evidence used for testcase
    execution. Explicit testcase links without a run are retained separately.
    Per the team policy, every ticket without testcase-link evidence is a free
    test; an explicit report label is retained as a stronger provenance detail.
    """
    resolved_db_path = Path(db_path)
    with sqlite3.connect(resolved_db_path) as connection:
        connection.row_factory = sqlite3.Row
        required_tables = ("octane_defects", "octane_manual_runs", "octane_testcases")
        missing_tables = [table_name for table_name in required_tables if not _table_exists(connection, table_name)]
        if missing_tables:
            raise ValueError(f"missing required tables: {', '.join(missing_tables)}")

        defect_columns = _table_columns(connection, "octane_defects")
        for column_name in ("defect_id", "name", "description", "comments", "team", "project"):
            if column_name not in defect_columns:
                raise ValueError(f"octane_defects missing required column: {column_name}")

        where_clause = ""
        parameters: tuple[str, ...] = ()
        if team and team.casefold() != "all":
            where_clause = " WHERE team = ?"
            parameters = (team,)
        optional_fields = ("software_version", "product_areas", "creation_time")
        optional_projection = ", ".join(
            f"{field_name} AS {field_name}" if field_name in defect_columns else f"'' AS {field_name}"
            for field_name in optional_fields
        )
        defect_rows = connection.execute(
            "SELECT defect_id, name, description, comments, team, project, "
            + optional_projection
            + " FROM octane_defects"
            + where_clause,
            parameters,
        ).fetchall()

        defects: dict[str, sqlite3.Row] = {}
        for row in defect_rows:
            defect_id = _canonical_defect_id(row["defect_id"])
            if defect_id:
                defects[defect_id] = row

        executed_testcase_defect_ids: set[str] = set()
        direct_execution_run_count = 0
        direct_execution_step_counts: list[int] = []
        testcase_names_by_defect: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
                """
            SELECT defect_id, test_name, steps_num FROM octane_manual_runs
                WHERE TRIM(COALESCE(defect_id, '')) <> ''
                  AND TRIM(COALESCE(test_id, '')) <> ''
                """
            ):
            defect_id = _canonical_defect_id(row["defect_id"])
            if defect_id not in defects:
                continue
            executed_testcase_defect_ids.add(defect_id)
            direct_execution_run_count += 1
            try:
                step_count = int(str(row["steps_num"] or "").strip())
            except (TypeError, ValueError):
                step_count = 0
            if step_count > 0:
                direct_execution_step_counts.append(step_count)
            testcase_name = str(row["test_name"] or "").strip()
            if testcase_name:
                testcase_names_by_defect[defect_id].add(testcase_name)

        linked_testcase_defect_ids: set[str] = set()
        for table_name in ("octane_testcases", "octane_traceability_testcases"):
            if not _table_exists(connection, table_name):
                continue
            if "defect_ids_json" not in _table_columns(connection, table_name):
                continue
            for row in connection.execute(f"SELECT defect_ids_json FROM {table_name}"):
                linked_testcase_defect_ids.update(_parse_defect_ids(row["defect_ids_json"]))
        linked_testcase_defect_ids.intersection_update(defects)

    testcase_linked_no_execution_ids = linked_testcase_defect_ids - executed_testcase_defect_ids
    no_testcase_evidence_ids = set(defects) - executed_testcase_defect_ids - testcase_linked_no_execution_ids
    free_test_defect_ids = no_testcase_evidence_ids
    explicit_free_test_ids = {
        defect_id
        for defect_id in free_test_defect_ids
        if _EXPLICIT_FREE_TEST_PATTERN.search(_defect_provenance_text(defects[defect_id]))
    }
    inferred_free_test_ids = free_test_defect_ids - explicit_free_test_ids

    execution_scope = Counter(
        str(defects[defect_id]["project"] or "Unknown").strip() or "Unknown"
        for defect_id in executed_testcase_defect_ids
    )
    testcase_title_counts = Counter(
        testcase_name
        for testcase_names in testcase_names_by_defect.values()
        for testcase_name in testcase_names
    )
    execution_method_signals = Counter({"Planned functional workflow": len(executed_testcase_defect_ids)})
    for defect_id in executed_testcase_defect_ids:
        testcase_title_text = " ".join(testcase_names_by_defect.get(defect_id, ()))
        for signal_label, pattern in _TESTCASE_TITLE_SIGNAL_PATTERNS:
            if pattern.search(testcase_title_text):
                execution_method_signals[signal_label] += 1
    free_test_scope = Counter(
        str(defects[defect_id]["project"] or "Unknown").strip() or "Unknown"
        for defect_id in free_test_defect_ids
    )

    effort_evidence = {
        "ticketsWithReproductionAttempts": 0,
        "documentedReproductionAttempts": 0,
        "ticketsWithLifecycleCoverage": 0,
        "documentedLifecycleRuns": 0,
        "ticketsWithMultiPlatformEvidence": 0,
        "ticketsWithEnvironmentEvidence": 0,
        "ticketsWithDiagnosticEvidence": 0,
        "ticketsWithOccurrenceCount": 0,
        "documentedOccurrences": 0,
    }
    repeatability_totals = {
        "ticketsWithComparableRepeatability": 0,
        "documentedAttempts": 0,
        "documentedOccurrences": 0,
        "fullyReproducibleTickets": 0,
        "highRepeatabilityTickets": 0,
        "intermittentTickets": 0,
    }
    pattern_effort_totals = {
        label: {
            "defectCount": 0,
            "ticketsWithReproductionAttempts": 0,
            "documentedReproductionAttempts": 0,
            "ticketsWithLifecycleCoverage": 0,
            "documentedLifecycleRuns": 0,
            "ticketsWithComparableRepeatability": 0,
            "fullyReproducibleTickets": 0,
            "highRepeatabilityTickets": 0,
            "intermittentTickets": 0,
            "comparableAttempts": 0,
            "comparableOccurrences": 0,
        }
        for label, _pattern in _FREE_TEST_SCENARIO_PATTERNS
    }
    free_test_discovery_basis = Counter({
        "Explicit Free-test report reference": len(explicit_free_test_ids),
        "No testcase link (Free test by policy)": len(inferred_free_test_ids),
    })
    free_test_scenario_signals: Counter[str] = Counter()
    free_test_narrative_totals = {
        "ticketsWithPreconditions": 0,
        "ticketsWithActionProcedure": 0,
        "ticketsWithExpectedResult": 0,
        "ticketsWithObservedResult": 0,
        "ticketsWithFullNarrative": 0,
        "ticketsWithCandidateProcedureLines": 0,
        "documentedCandidateProcedureLines": 0,
    }
    free_test_candidate_procedure_counts: list[int] = []
    free_test_tickets: list[dict[str, Any]] = []
    for defect_id in free_test_defect_ids:
        defect = defects[defect_id]
        report_text = _defect_report_text(defect)
        provenance_text = _defect_provenance_text(defect)
        attempts = _structured_field_count(report_text, _REPRODUCTION_ATTEMPT_PATTERN)
        lifecycle_runs = _structured_field_count(report_text, _LIFECYCLE_PATTERN)
        reported_occurrence_count = _structured_field_count(report_text, _OCCURRENCE_COUNT_PATTERN)
        repeatability_class, observed_occurrence_rate = _repeatability_evidence(
            attempts,
            reported_occurrence_count,
        )
        multi_platform_confirmed = bool(_MULTI_PLATFORM_PATTERN.search(report_text))
        has_environment_fingerprint = _has_environment_fingerprint(report_text)
        has_diagnostic_artifact_reference = bool(_DIAGNOSTIC_ARTIFACT_PATTERN.search(provenance_text))
        scenario_labels = _free_test_scenario_labels(_clean_text(defect["name"]))
        narrative_evidence = _free_test_narrative_evidence(defect["description"])
        free_test_scenario_signals.update(scenario_labels)
        for field_name in (
            "hasPreconditions",
            "hasActionProcedure",
            "hasExpectedResult",
            "hasObservedResult",
        ):
            if narrative_evidence[field_name]:
                free_test_narrative_totals[f"ticketsWith{field_name[3:]}"] += 1
        if all(
            narrative_evidence[field_name]
            for field_name in ("hasPreconditions", "hasActionProcedure", "hasExpectedResult", "hasObservedResult")
        ):
            free_test_narrative_totals["ticketsWithFullNarrative"] += 1
        candidate_procedure_lines = int(narrative_evidence["candidateProcedureLines"])
        if candidate_procedure_lines:
            free_test_narrative_totals["ticketsWithCandidateProcedureLines"] += 1
            free_test_narrative_totals["documentedCandidateProcedureLines"] += candidate_procedure_lines
            free_test_candidate_procedure_counts.append(candidate_procedure_lines)
        if attempts is not None:
            effort_evidence["ticketsWithReproductionAttempts"] += 1
            effort_evidence["documentedReproductionAttempts"] += attempts
        if lifecycle_runs is not None:
            effort_evidence["ticketsWithLifecycleCoverage"] += 1
            effort_evidence["documentedLifecycleRuns"] += lifecycle_runs
        if multi_platform_confirmed:
            effort_evidence["ticketsWithMultiPlatformEvidence"] += 1
        if has_environment_fingerprint:
            effort_evidence["ticketsWithEnvironmentEvidence"] += 1
        if has_diagnostic_artifact_reference:
            effort_evidence["ticketsWithDiagnosticEvidence"] += 1
        if reported_occurrence_count is not None:
            effort_evidence["ticketsWithOccurrenceCount"] += 1
            effort_evidence["documentedOccurrences"] += reported_occurrence_count
        if observed_occurrence_rate is not None:
            repeatability_totals["ticketsWithComparableRepeatability"] += 1
            repeatability_totals["documentedAttempts"] += attempts or 0
            repeatability_totals["documentedOccurrences"] += reported_occurrence_count or 0
            if repeatability_class == "Fully reproducible (100%)":
                repeatability_totals["fullyReproducibleTickets"] += 1
            elif repeatability_class == "High repeatability (50-99%)":
                repeatability_totals["highRepeatabilityTickets"] += 1
            else:
                repeatability_totals["intermittentTickets"] += 1
        for pattern_label in scenario_labels:
            pattern_totals = pattern_effort_totals[pattern_label]
            pattern_totals["defectCount"] += 1
            if attempts is not None:
                pattern_totals["ticketsWithReproductionAttempts"] += 1
                pattern_totals["documentedReproductionAttempts"] += attempts
            if lifecycle_runs is not None:
                pattern_totals["ticketsWithLifecycleCoverage"] += 1
                pattern_totals["documentedLifecycleRuns"] += lifecycle_runs
            if observed_occurrence_rate is not None:
                pattern_totals["ticketsWithComparableRepeatability"] += 1
                pattern_totals["comparableAttempts"] += attempts or 0
                pattern_totals["comparableOccurrences"] += reported_occurrence_count or 0
                if repeatability_class == "Fully reproducible (100%)":
                    pattern_totals["fullyReproducibleTickets"] += 1
                elif repeatability_class == "High repeatability (50-99%)":
                    pattern_totals["highRepeatabilityTickets"] += 1
                else:
                    pattern_totals["intermittentTickets"] += 1
        free_test_tickets.append(
            {
                "defectId": defect_id,
                "name": str(defect["name"] or "").strip(),
                "project": str(defect["project"] or "Unknown").strip() or "Unknown",
                "discoveryBasis": (
                    "Explicit Free-test report reference"
                    if defect_id in explicit_free_test_ids
                    else "No testcase link (Free test by policy)"
                ),
                "scenarioSignals": scenario_labels,
                "reproductionAttempts": attempts,
                "lifecycleRuns": lifecycle_runs,
                "reportedOccurrenceCount": reported_occurrence_count,
                "repeatabilityClass": repeatability_class,
                "multiPlatformConfirmed": multi_platform_confirmed,
                "hasEnvironmentFingerprint": has_environment_fingerprint,
                "hasDiagnosticArtifactReference": has_diagnostic_artifact_reference,
                "hasPreconditions": bool(narrative_evidence["hasPreconditions"]),
                "hasActionProcedure": bool(narrative_evidence["hasActionProcedure"]),
                "hasExpectedResult": bool(narrative_evidence["hasExpectedResult"]),
                "hasObservedResult": bool(narrative_evidence["hasObservedResult"]),
                "candidateProcedureLines": candidate_procedure_lines,
                "softwareVersion": str(defect["software_version"] or "").strip(),
                "productAreas": _split_values(defect["product_areas"]),
                "createdAt": str(defect["creation_time"] or "").strip(),
            }
        )
    free_test_tickets.sort(key=lambda ticket: (ticket["createdAt"], ticket["defectId"]), reverse=True)
    repeatability_summary = {
        **repeatability_totals,
        "observedOccurrenceRate": (
            round(
                repeatability_totals["documentedOccurrences"] / repeatability_totals["documentedAttempts"],
                3,
            )
            if repeatability_totals["documentedAttempts"]
            else None
        ),
    }
    pattern_analysis = [
        {
            "pattern": pattern_label,
            "defectCount": totals["defectCount"],
            "ticketsWithReproductionAttempts": totals["ticketsWithReproductionAttempts"],
            "documentedReproductionAttempts": totals["documentedReproductionAttempts"],
            "ticketsWithLifecycleCoverage": totals["ticketsWithLifecycleCoverage"],
            "documentedLifecycleRuns": totals["documentedLifecycleRuns"],
            "ticketsWithComparableRepeatability": totals["ticketsWithComparableRepeatability"],
            "fullyReproducibleTickets": totals["fullyReproducibleTickets"],
            "highRepeatabilityTickets": totals["highRepeatabilityTickets"],
            "intermittentTickets": totals["intermittentTickets"],
            "observedOccurrenceRate": (
                round(totals["comparableOccurrences"] / totals["comparableAttempts"], 3)
                if totals["comparableAttempts"]
                else None
            ),
        }
        for pattern_label, _pattern in _FREE_TEST_SCENARIO_PATTERNS
        if (totals := pattern_effort_totals[pattern_label])["defectCount"]
    ]
    free_test_narrative = {
        "ticketCount": len(free_test_defect_ids),
        **free_test_narrative_totals,
        "medianCandidateProcedureLines": (
            sorted(free_test_candidate_procedure_counts)[len(free_test_candidate_procedure_counts) // 2]
            if free_test_candidate_procedure_counts
            else 0
        ),
    }
    step_evidence_comparison = {
        "testcaseExecution": {
            "directDefectCount": len(executed_testcase_defect_ids),
            "linkedRunCount": direct_execution_run_count,
            "runsWithStepCount": len(direct_execution_step_counts),
            "documentedStepCount": sum(direct_execution_step_counts),
            "localScriptCount": 0,
            "scriptEvidenceStatus": "testcase_script_unavailable_locally",
        },
        "freeTestNarrative": free_test_narrative,
    }

    return {
        "scope": {"team": team or "ALL", "sourceDb": str(resolved_db_path)},
        "summary": {
            "totalDefects": len(defects),
            "executedTestcaseDefects": len(executed_testcase_defect_ids),
            "testcaseLinkedNoExecutionDefects": len(testcase_linked_no_execution_ids),
            "noTestcaseEvidenceDefects": len(no_testcase_evidence_ids),
            "freeTestDefects": len(free_test_defect_ids),
            "explicitFreeTestDefects": len(explicit_free_test_ids),
            "inferredFreeTestDefects": len(inferred_free_test_ids),
        },
        "execution": {
            "businessScope": _ordered_counts(execution_scope),
            "testcaseTitles": _ordered_counts(testcase_title_counts),
            "methodSignals": _ordered_signal_counts(execution_method_signals, _EXECUTION_METHOD_SIGNAL_ORDER),
        },
        "stepEvidenceComparison": step_evidence_comparison,
        "freeTest": {
            "businessScope": _ordered_counts(free_test_scope),
            "discoveryBasis": _ordered_signal_counts(free_test_discovery_basis, _FREE_TEST_DISCOVERY_BASIS_ORDER),
            "scenarioSignals": _ordered_signal_counts(free_test_scenario_signals, _FREE_TEST_SCENARIO_SIGNAL_ORDER),
            "effortEvidence": effort_evidence,
            "repeatabilitySummary": repeatability_summary,
            "patternAnalysis": pattern_analysis,
            "tickets": free_test_tickets,
        },
    }


def _count_rows(rows: list[dict[str, Any]], *, empty_message: str) -> str:
        if not rows:
                return f'<tr><td colspan="2" class="empty">{escape(empty_message)}</td></tr>'
        return "".join(
                f'<tr><td>{escape(str(row["label"]))}</td><td class="number">{int(row["count"]):,}</td></tr>'
                for row in rows
        )


def _metric_cards(summary: dict[str, int]) -> str:
        labels = (
                ("totalDefects", "Scoped defects", "All defects in the selected scope"),
                ("executedTestcaseDefects", "Executed testcase", "Direct run plus test ID evidence"),
                ("testcaseLinkedNoExecutionDefects", "Testcase link only", "Linked, but no execution record"),
        ("freeTestDefects", "Free test", "No testcase link under team policy"),
        ("explicitFreeTestDefects", "Explicit Free-test reference", "Report text names Free test or exploratory test"),
        ("inferredFreeTestDefects", "Free test by policy", "No testcase link; no explicit report label"),
        )
        return "".join(
                "<article class=\"metric\">"
                f"<span>{escape(label)}</span><strong>{int(summary[key]):,}</strong>"
                f"<small>{escape(detail)}</small></article>"
                for key, label, detail in labels
        )


def _effort_rows(effort_evidence: dict[str, int]) -> str:
        rows = (
                ("Documented reproduction attempts", effort_evidence["documentedReproductionAttempts"]),
                ("Tickets with a reproduction count", effort_evidence["ticketsWithReproductionAttempts"]),
        ("Reported defect occurrences", effort_evidence["documentedOccurrences"]),
        ("Tickets with an occurrence count", effort_evidence["ticketsWithOccurrenceCount"]),
                ("Documented lifecycle runs", effort_evidence["documentedLifecycleRuns"]),
                ("Tickets with lifecycle coverage", effort_evidence["ticketsWithLifecycleCoverage"]),
                ("Tickets with multi-platform confirmation", effort_evidence["ticketsWithMultiPlatformEvidence"]),
                ("Tickets with environment fingerprint", effort_evidence["ticketsWithEnvironmentEvidence"]),
                ("Tickets with diagnostic/log artifact reference (report or comment)", effort_evidence["ticketsWithDiagnosticEvidence"]),
        )
        return "".join(
                f"<tr><td>{escape(label)}</td><td class=\"number\">{int(value):,}</td></tr>" for label, value in rows
        )


def _repeatability_rows(summary: dict[str, int | float | None]) -> str:
    rows = (
        ("Tickets with comparable attempts and occurrences", summary["ticketsWithComparableRepeatability"]),
        ("Comparable reproduction attempts", summary["documentedAttempts"]),
        ("Comparable observed occurrences", summary["documentedOccurrences"]),
        ("Observed occurrence per attempt", _percent(summary["observedOccurrenceRate"])),
        ("Fully reproducible (100%)", summary["fullyReproducibleTickets"]),
        ("High repeatability (50-99%)", summary["highRepeatabilityTickets"]),
        ("Intermittent (<50%)", summary["intermittentTickets"]),
    )
    return "".join(
        f"<tr><td>{escape(str(label))}</td><td class=\"number\">{escape(_display_metric(value))}</td></tr>"
        for label, value in rows
    )


def _display_metric(value: object) -> str:
    return f"{int(value):,}" if isinstance(value, int) else str(value if value is not None else "Not available")


def _percent(value: object) -> str:
    return f"{float(value) * 100:.1f}%" if isinstance(value, (float, int)) else "Not available"


def _pattern_effort_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="11" class="empty">No title-based scenario patterns in this scope.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{escape(str(row['pattern']))}</td>"
        f"<td class=\"number\">{int(row['defectCount']):,}</td>"
        f"<td class=\"number\">{int(row['ticketsWithReproductionAttempts']):,}</td>"
        f"<td class=\"number\">{int(row['documentedReproductionAttempts']):,}</td>"
        f"<td class=\"number\">{int(row['ticketsWithLifecycleCoverage']):,}</td>"
        f"<td class=\"number\">{int(row['documentedLifecycleRuns']):,}</td>"
        f"<td class=\"number\">{int(row['ticketsWithComparableRepeatability']):,}</td>"
        f"<td class=\"number\">{int(row['fullyReproducibleTickets']):,}</td>"
        f"<td class=\"number\">{int(row['highRepeatabilityTickets']):,}</td>"
        f"<td class=\"number\">{int(row['intermittentTickets']):,}</td>"
        f"<td class=\"number\">{escape(_percent(row['observedOccurrenceRate']))}</td>"
        "</tr>"
        for row in rows
    )


def _high_level_insight(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    effort = payload["freeTest"]["effortEvidence"]
    repeatability = payload["freeTest"]["repeatabilitySummary"]
    documented_rate = _percent(repeatability["observedOccurrenceRate"])
    return f"""
        <section class="insight-band" aria-labelledby="high-level-insight-title">
            <div class="insight-heading">
                <div class="eyebrow">Decision view</div>
                <h2 id="high-level-insight-title">High-level Insight</h2>
            </div>
            <div class="insight-grid">
                <div class="insight-item">
                    <h3>Additional work beyond testcase execution</h3>
                    <p>Free test covers <strong>{int(summary['freeTestDefects']):,}</strong> no-link defects and records the work needed to turn unexpected failures into reproducible evidence: <strong>{int(effort['documentedReproductionAttempts']):,}</strong> reproduction attempts, <strong>{int(effort['documentedLifecycleRuns']):,}</strong> lifecycle runs, <strong>{int(effort['ticketsWithMultiPlatformEvidence']):,}</strong> multi-platform confirmations, and <strong>{int(effort['ticketsWithEnvironmentEvidence']):,}</strong> environment fingerprints.</p>
                </div>
                <div class="insight-item">
                    <h3>Evidence basis</h3>
                    <p>Testcase execution is evidenced by a direct manual-run and test ID. Free-test work is evidenced by the no-link policy plus values recorded in defect reports: reproduction attempts, lifecycle coverage, occurrence count, platform confirmation, VIN/V-Num, and strict diagnostic artifact references.</p>
                </div>
                <div class="insight-item">
                    <h3>Why the work is necessary</h3>
                    <p>Formal testcase execution shows planned functional scope. Free test adds scenario exploration and stability confirmation when the defect is not linked to a testcase. Among <strong>{int(repeatability['ticketsWithComparableRepeatability']):,}</strong> tickets with comparable counts, the documented occurrence per attempt is <strong>{escape(documented_rate)}</strong>; <strong>{int(repeatability['fullyReproducibleTickets']):,}</strong> are fully reproducible and <strong>{int(repeatability['intermittentTickets']):,}</strong> remain intermittent.</p>
                </div>
            </div>
            <p class="insight-boundary">This is defect discovery evidence, not an overall test efficiency rate: the source does not contain the total executed or passed Free-test population and work-hour denominator.</p>
        </section>
    """


def _step_evidence_comparison_html(payload: dict[str, Any]) -> str:
    comparison = payload["stepEvidenceComparison"]
    testcase = comparison["testcaseExecution"]
    free_test = comparison["freeTestNarrative"]
    return f"""
        <section class="step-comparison" aria-labelledby="step-evidence-comparison-title">
            <div class="comparison-heading">
                <div class="eyebrow">Method evidence</div>
                <h2 id="step-evidence-comparison-title">Step Evidence Comparison</h2>
            </div>
            <p class="comparison-intro">This compares the evidence actually available in the local snapshot. It does not treat testcase titles as procedural steps.</p>
            <div class="comparison-grid">
                <div class="comparison-column">
                    <h3>Testcase execution discoveries</h3>
                    <dl>
                        <div><dt>Direct defect discoveries</dt><dd>{int(testcase['directDefectCount']):,}</dd></div>
                        <div><dt>Linked execution runs</dt><dd>{int(testcase['linkedRunCount']):,}</dd></div>
                        <div><dt>Runs with step-count metadata</dt><dd>{int(testcase['runsWithStepCount']):,}</dd></div>
                        <div><dt>Documented step count</dt><dd>{int(testcase['documentedStepCount']):,}</dd></div>
                    </dl>
                    <p class="comparison-note"><strong>Testcase script evidence gap.</strong> The local source snapshot has <strong>{int(testcase['localScriptCount']):,}</strong> testcase scripts. Actual testcase scripts live in Octane at <code>/tests/&lt;id&gt;/script</code> and require an authenticated read to support a true step-by-step comparison.</p>
                </div>
                <div class="comparison-column free-evidence">
                    <h3>Free-test discovery narratives</h3>
                    <dl>
                        <div><dt>Free-test tickets</dt><dd>{int(free_test['ticketCount']):,}</dd></div>
                        <div><dt>Preconditions recorded</dt><dd>{int(free_test['ticketsWithPreconditions']):,}</dd></div>
                        <div><dt>Action / procedure recorded</dt><dd>{int(free_test['ticketsWithActionProcedure']):,}</dd></div>
                        <div><dt>Expected result recorded</dt><dd>{int(free_test['ticketsWithExpectedResult']):,}</dd></div>
                        <div><dt>Observed result recorded</dt><dd>{int(free_test['ticketsWithObservedResult']):,}</dd></div>
                        <div><dt>Full P/A/E/O narrative</dt><dd>{int(free_test['ticketsWithFullNarrative']):,}</dd></div>
                        <div><dt>Candidate procedure lines</dt><dd>{int(free_test['documentedCandidateProcedureLines']):,}</dd></div>
                        <div><dt>Median candidate procedure lines</dt><dd>{int(free_test['medianCandidateProcedureLines']):,}</dd></div>
                    </dl>
                    <p class="comparison-note">Free-test narratives preserve the actual discovery path: setup, action, expected outcome, and observed failure. Candidate procedure lines are extracted conservatively from explicit action/procedure sections and remain linked to the source ticket for review.</p>
                </div>
            </div>
            <aside class="method-interpretation"><strong>Method interpretation.</strong> Free-test evidence is strongest for checking the difference between expected and observed behavior: <strong>{int(free_test['ticketsWithExpectedResult']):,}</strong> tickets record an expected result and <strong>{int(free_test['ticketsWithObservedResult']):,}</strong> record an observed result. It also records an explicit action or procedure in <strong>{int(free_test['ticketsWithActionProcedure']):,}</strong> tickets. The documentation improvement opportunity is precondition completeness: only <strong>{int(free_test['ticketsWithPreconditions']):,}</strong> tickets record explicit preconditions and <strong>{int(free_test['ticketsWithFullNarrative']):,}</strong> contain a full P/A/E/O narrative. Use those complete narratives as the highest-quality candidates for testcase conversion; do not claim a step-by-step testcase comparison until Octane scripts are refreshed.</aside>
        </section>
    """


def _build_html(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        execution_scope_rows = _count_rows(
                payload["execution"]["businessScope"],
                empty_message="No direct testcase-execution evidence in this scope.",
        )
        execution_method_rows = _count_rows(
            payload["execution"]["methodSignals"],
            empty_message="No testcase title signals in this scope.",
        )
        testcase_title_rows = _count_rows(
            payload["execution"]["testcaseTitles"][:20],
            empty_message="No testcase titles linked to executed defects in this scope.",
        )
        free_test_scope_rows = _count_rows(
                payload["freeTest"]["businessScope"],
                empty_message="No Free-test defects in this scope.",
        )
        free_test_basis_rows = _count_rows(
            payload["freeTest"]["discoveryBasis"],
            empty_message="No Free-test discovery basis in this scope.",
        )
        free_test_scenario_rows = _count_rows(
            payload["freeTest"]["scenarioSignals"],
            empty_message="No Free-test scenario signals in this scope.",
        )
        repeatability_rows = _repeatability_rows(payload["freeTest"]["repeatabilitySummary"])
        pattern_effort_rows = _pattern_effort_rows(payload["freeTest"]["patternAnalysis"])
        step_evidence_comparison_html = _step_evidence_comparison_html(payload)
        free_test_tickets_json = json.dumps(payload["freeTest"]["tickets"], ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        html = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Test Discovery Evidence</title>
    <style>
        :root { --ink:#17352f; --muted:#587069; --paper:#f8f5ec; --surface:#fffdf8; --line:#d8d2c5; --teal:#007d78; --orange:#bf6820; --gold:#d9a420; --red:#a44035; }
        * { box-sizing:border-box; }
        body { margin:0; background:linear-gradient(135deg,#f8f5ec 0%,#e9f0e8 55%,#f8f5ec 100%); color:var(--ink); font:15px/1.5 Georgia, "Times New Roman", serif; }
        .shell { max-width:1240px; margin:0 auto; padding:28px 24px 56px; }
        .masthead { border-bottom:4px solid var(--ink); padding:0 0 22px; display:grid; gap:10px; }
        .eyebrow { color:var(--teal); font:700 12px/1.2 "Trebuchet MS", sans-serif; letter-spacing:0; text-transform:uppercase; }
        h1,h2 { margin:0; font-family:"Trebuchet MS", sans-serif; letter-spacing:0; }
        h1 { font-size:34px; line-height:1.1; max-width:760px; }
        h2 { font-size:21px; line-height:1.2; }
        .source { color:var(--muted); overflow-wrap:anywhere; }
        .summary { margin:26px 0; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); border:1px solid var(--line); background:var(--surface); }
        .metric { min-height:140px; padding:18px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); display:grid; align-content:space-between; gap:8px; }
        .metric:nth-child(3n) { border-right:0; }
        .metric:nth-last-child(-n+3) { border-bottom:0; }
        .metric span { color:var(--muted); font:700 13px/1.25 "Trebuchet MS", sans-serif; }
        .metric strong { color:var(--teal); font:700 34px/1 "Trebuchet MS", sans-serif; }
        .metric small { color:var(--muted); }
        .notice { margin:26px 0; padding:14px 16px; border-left:5px solid var(--orange); background:#fff7e7; color:#5c3a18; }
        .insight-band { margin:26px 0; padding:22px 0; border-top:4px solid var(--teal); border-bottom:1px solid var(--line); }
        .insight-heading { display:flex; align-items:baseline; justify-content:space-between; gap:16px; margin-bottom:18px; }
        .insight-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:22px; }
        .insight-item { min-width:0; padding-right:18px; border-right:1px solid var(--line); }
        .insight-item:last-child { padding-right:0; border-right:0; }
        .insight-item h3 { margin:0 0 8px; color:var(--teal); font:700 15px/1.25 "Trebuchet MS", sans-serif; letter-spacing:0; }
        .insight-item p,.insight-boundary { margin:0; color:var(--muted); }
        .insight-item strong { color:var(--ink); font-variant-numeric:tabular-nums; }
        .insight-boundary { margin-top:16px; padding-top:12px; border-top:1px solid var(--line); font-size:13px; }
        .step-comparison { margin:26px 0; padding:22px 0; border-top:4px solid var(--orange); border-bottom:1px solid var(--line); }
        .comparison-heading { display:flex; align-items:baseline; justify-content:space-between; gap:16px; margin-bottom:8px; }
        .comparison-intro,.comparison-note { color:var(--muted); margin:0; }
        .comparison-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:24px; margin-top:18px; }
        .comparison-column { min-width:0; padding-right:24px; border-right:1px solid var(--line); }
        .comparison-column:last-child { padding-right:0; border-right:0; }
        .comparison-column h3 { margin:0 0 10px; color:var(--teal); font:700 16px/1.25 "Trebuchet MS", sans-serif; letter-spacing:0; }
        .free-evidence h3 { color:var(--orange); }
        .comparison-column dl { margin:0; }
        .comparison-column dl div { display:flex; align-items:baseline; justify-content:space-between; gap:12px; padding:5px 0; border-bottom:1px solid #ebe5d9; }
        .comparison-column dt { color:var(--muted); }
        .comparison-column dd { margin:0; color:var(--ink); font-weight:700; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .comparison-note { margin-top:14px; font-size:13px; }
        .comparison-note code { color:var(--ink); font-family:Consolas, "Courier New", monospace; }
        .method-interpretation { margin-top:18px; padding:14px 16px; border-left:5px solid var(--teal); background:#edf5f0; color:var(--muted); }
        .method-interpretation strong { color:var(--ink); }
        .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:26px; margin-top:26px; }
        section { background:var(--surface); border-top:4px solid var(--teal); padding:20px; min-width:0; }
        section.free { border-top-color:var(--orange); }
        section h2 + p { color:var(--muted); margin:8px 0 18px; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        th,td { padding:9px 6px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; overflow-wrap:anywhere; }
        th { color:var(--muted); font:700 12px/1.2 "Trebuchet MS", sans-serif; text-transform:uppercase; }
        .number { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .empty { color:var(--muted); text-align:center; padding:20px; }
        section.wide { grid-column:1 / -1; }
        .method { margin-top:20px; padding-top:14px; border-top:1px solid var(--line); }
        .method:first-child { margin-top:0; padding-top:0; border-top:0; }
        .method ul { margin:10px 0 0; padding-left:20px; }
        .method li { margin:7px 0; }
        .deep-dive { border-top-color:var(--orange); }
        .deep-dive-controls { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:18px 0; }
        .deep-dive-controls label { color:var(--muted); display:grid; gap:5px; font:700 12px/1.2 "Trebuchet MS", sans-serif; text-transform:uppercase; }
        .deep-dive-controls input,.deep-dive-controls select { width:100%; min-height:36px; border:1px solid var(--line); border-radius:4px; background:#fffdf8; color:var(--ink); font:14px/1.2 Georgia, "Times New Roman", serif; padding:7px 8px; }
        .ticket-status { color:var(--muted); font-size:13px; margin:14px 0 8px; }
        .table-wrap { overflow-x:auto; border:1px solid var(--line); }
        .ticket-table { min-width:1160px; }
        .pattern-table { min-width:1260px; }
        .ticket-table th { position:sticky; top:0; background:#f4f0e5; }
        .load-more { margin-top:14px; min-height:36px; border:1px solid var(--teal); border-radius:4px; background:var(--teal); color:#fff; cursor:pointer; font:700 13px/1 "Trebuchet MS", sans-serif; padding:0 12px; }
        .load-more:hover { background:#006862; }
        footer { margin-top:26px; color:var(--muted); font-size:13px; }
        @media (max-width:760px) { .shell { padding:20px 14px 40px; } h1 { font-size:28px; } .summary,.grid,.deep-dive-controls,.insight-grid,.comparison-grid { grid-template-columns:1fr; } .metric,.metric:nth-child(3n) { border-right:0; } .metric:nth-last-child(-n+3) { border-bottom:1px solid var(--line); } .metric:last-child { border-bottom:0; } .insight-heading,.comparison-heading { display:grid; gap:6px; } .insight-item,.comparison-column { padding:0 0 16px; border:0; border-bottom:1px solid var(--line); } .insight-item:last-child,.comparison-column:last-child { padding-bottom:0; border-bottom:0; } }
    </style>
</head>
<body>
    <main class="shell">
        <header class="masthead">
            <div class="eyebrow">Evidence-led quality work</div>
            <h1>Test Discovery Evidence</h1>
            <div class="source">Scope: __TEAM__ | Source: __SOURCE__</div>
        </header>
        __HIGH_LEVEL_INSIGHT__
        <div class="summary">__METRIC_CARDS__</div>
        <aside class="notice"><strong>Team policy.</strong> A direct run linked to a test ID is proof of testcase execution. A testcase relation without a run remains separate. Every ticket without either form of testcase link is counted as Free test; an explicit Free-test label is retained as a provenance detail.</aside>
        __STEP_EVIDENCE_COMPARISON__
        <div class="grid">
            <section>
                <h2>Planned Testcase Execution</h2>
                <p>Business scope inferred from the Project field of defects directly linked to an executed testcase.</p>
                <table><thead><tr><th>Business scope</th><th class="number">Defects</th></tr></thead><tbody>__EXECUTION_SCOPE_ROWS__</tbody></table>
            </section>
            <section>
                <h2>Testcase Method Signals</h2>
                <p>Overlapping signals inferred from executed testcase titles; they describe planned test intent, not a step-by-step audit.</p>
                <table><thead><tr><th>Title signal</th><th class="number">Defects</th></tr></thead><tbody>__EXECUTION_METHOD_ROWS__</tbody></table>
            </section>
            <section>
                <h2>Directly Linked Testcase Titles</h2>
                <p>Most frequent testcase titles connected to defects through an actual manual-run record.</p>
                <table><thead><tr><th>Testcase title</th><th class="number">Defects</th></tr></thead><tbody>__TESTCASE_TITLE_ROWS__</tbody></table>
            </section>
            <section class="free">
                <h2>Free-test Business Scope</h2>
                <p>All defects with no testcase link are included under the agreed Free-test policy.</p>
                <table><thead><tr><th>Business scope</th><th class="number">Defects</th></tr></thead><tbody>__FREE_SCOPE_ROWS__</tbody></table>
            </section>
            <section class="free">
                <h2>Free-test Discovery Basis</h2>
                <p>Separates explicit report references from tickets included by the no-link policy.</p>
                <table><thead><tr><th>Discovery basis</th><th class="number">Defects</th></tr></thead><tbody>__FREE_BASIS_ROWS__</tbody></table>
            </section>
            <section class="free">
                <h2>Free-test Scenario Signals</h2>
                <p>Overlapping signals inferred only from ticket titles; they describe scenario coverage, not a definitive test-step audit.</p>
                <table><thead><tr><th>Scenario signal</th><th class="number">Tickets</th></tr></thead><tbody>__FREE_SCENARIO_ROWS__</tbody></table>
            </section>
            <section class="free">
                <h2>Free-test Test-method and Effort Evidence</h2>
                <p>Recorded test activity across all Free-test tickets. Counts are evidence volume, not labour-hour estimates.</p>
                <table><thead><tr><th>Recorded evidence</th><th class="number">Count</th></tr></thead><tbody>__EFFORT_ROWS__</tbody></table>
            </section>
            <section class="free">
                <h2>Repeatability Evidence</h2>
                <p>Only tickets with internally comparable attempt and occurrence counts contribute to this view.</p>
                <table><thead><tr><th>Recorded evidence</th><th class="number">Count</th></tr></thead><tbody>__REPEATABILITY_ROWS__</tbody></table>
            </section>
            <section class="wide free">
                <h2>Scenario Pattern x Effort</h2>
                <p>Title-based scenario patterns are overlapping. This table shows recorded effort behind discovered defects, not a pass-rate or a causal measure of total test efficiency.</p>
                <div class="table-wrap"><table class="pattern-table"><thead><tr><th>Scenario pattern</th><th class="number">Defects found</th><th class="number">Tickets with attempts</th><th class="number">Attempts</th><th class="number">Tickets with lifecycles</th><th class="number">Lifecycle runs</th><th class="number">Comparable tickets</th><th class="number">Full</th><th class="number">High</th><th class="number">Intermittent</th><th class="number">Occurrence / attempt</th></tr></thead><tbody>__PATTERN_EFFORT_ROWS__</tbody></table></div>
            </section>
            <section class="wide deep-dive">
                <h2>Free-test Ticket Deep Dive</h2>
                <p>Filter individual Free-test tickets by discovery basis, business scope, software version, product area, scenario signal, or repeatability. P/A/E/O indicates recorded preconditions, action/procedure, expected result, and observed result.</p>
                <div class="deep-dive-controls">
                    <label>Search ticket or title<input id="free-ticket-search" type="search" placeholder="ID, title, project, product area" /></label>
                    <label>Discovery basis<select id="free-ticket-basis"><option value="">All</option></select></label>
                    <label>Business scope<select id="free-ticket-project"><option value="">All</option></select></label>
                    <label>Software version<select id="free-ticket-version"><option value="">All</option></select></label>
                    <label>Product area<select id="free-ticket-area"><option value="">All</option></select></label>
                    <label>Scenario signal<select id="free-ticket-scenario"><option value="">All</option></select></label>
                    <label>Repeatability<select id="free-ticket-repeatability"><option value="">All</option></select></label>
                </div>
                <div id="free-ticket-status" class="ticket-status"></div>
                <div class="table-wrap"><table class="ticket-table"><thead><tr><th>Ticket</th><th>Project</th><th>Basis</th><th>Scenario signals</th><th>Title</th><th>P/A/E/O</th><th>Candidate procedure lines</th><th>Attempts</th><th>Occurrences</th><th>Repeatability</th><th>Lifecycles</th><th>Multi-platform</th><th>Environment</th><th>Diagnostic/log artifact (report or comment)</th><th>Software version</th><th>Product areas</th><th>Created</th></tr></thead><tbody id="free-ticket-rows"></tbody></table></div>
                <button id="free-ticket-load-more" class="load-more" type="button">Load more</button>
            </section>
            <section class="wide">
                <h2>What the Data Supports</h2>
                <div class="method"><strong>Testcase execution</strong><ul><li>Shows planned functional coverage through concrete testcase titles and direct run links.</li><li>Shows which projects were reached by formal testcase execution.</li></ul></div>
                <div class="method"><strong>Free-test work</strong><ul><li>Includes every ticket without testcase-link evidence under the team policy, while preserving explicit Free-test wording as provenance.</li><li>Supports ticket-level review of repeated reproduction, lifecycle variation, platform confirmation, environment fingerprints, and diagnostic/log terms.</li></ul></div>
            </section>
        </div>
        <footer>This report is generated from local Octane source data. The adjacent JSON file is the machine-readable audit payload for the same scope.</footer>
    </main>
        <script>
            const FREE_TEST_TICKETS = __FREE_TEST_TICKETS_JSON__;
            const PAGE_SIZE = 50;
            let visibleTicketCount = PAGE_SIZE;
            const byId = (id) => document.getElementById(id);
            const valuesFor = (extract) => [...new Set(FREE_TEST_TICKETS.flatMap(extract).filter(Boolean))].sort((left, right) => left.localeCompare(right));
            const populateSelect = (id, values) => {
                const select = byId(id);
                values.forEach((value) => {
                    const option = document.createElement('option');
                    option.value = value;
                    option.textContent = value;
                    select.append(option);
                });
            };
            populateSelect('free-ticket-basis', valuesFor((ticket) => [ticket.discoveryBasis]));
            populateSelect('free-ticket-project', valuesFor((ticket) => [ticket.project]));
            populateSelect('free-ticket-version', valuesFor((ticket) => [ticket.softwareVersion]));
            populateSelect('free-ticket-area', valuesFor((ticket) => ticket.productAreas));
            populateSelect('free-ticket-scenario', valuesFor((ticket) => ticket.scenarioSignals));
            populateSelect('free-ticket-repeatability', valuesFor((ticket) => [ticket.repeatabilityClass]));
            const selectedValue = (id) => byId(id).value;
            const matchingTickets = () => {
                const search = byId('free-ticket-search').value.trim().toLocaleLowerCase();
                const basis = selectedValue('free-ticket-basis');
                const project = selectedValue('free-ticket-project');
                const version = selectedValue('free-ticket-version');
                const area = selectedValue('free-ticket-area');
                const scenario = selectedValue('free-ticket-scenario');
                const repeatability = selectedValue('free-ticket-repeatability');
                return FREE_TEST_TICKETS.filter((ticket) => {
                    const searchText = [ticket.defectId, ticket.name, ticket.project, ticket.discoveryBasis, ticket.softwareVersion, ticket.repeatabilityClass, ...ticket.productAreas, ...ticket.scenarioSignals].join(' ').toLocaleLowerCase();
                    return (!search || searchText.includes(search))
                        && (!basis || ticket.discoveryBasis === basis)
                        && (!project || ticket.project === project)
                        && (!version || ticket.softwareVersion === version)
                        && (!area || ticket.productAreas.includes(area))
                        && (!scenario || ticket.scenarioSignals.includes(scenario))
                        && (!repeatability || ticket.repeatabilityClass === repeatability);
                });
            };
            const cell = (row, value) => {
                const element = document.createElement('td');
                element.textContent = value === true ? 'Yes' : value === false ? 'No' : String(value || '-');
                row.append(element);
            };
            const renderTickets = () => {
                const matching = matchingTickets();
                const tableBody = byId('free-ticket-rows');
                tableBody.replaceChildren();
                matching.slice(0, visibleTicketCount).forEach((ticket) => {
                    const row = document.createElement('tr');
                    cell(row, ticket.defectId);
                    cell(row, ticket.project);
                    cell(row, ticket.discoveryBasis);
                      cell(row, ticket.scenarioSignals.join('; '));
                    cell(row, ticket.name);
                    cell(row, [ticket.hasPreconditions ? 'P' : '', ticket.hasActionProcedure ? 'A' : '', ticket.hasExpectedResult ? 'E' : '', ticket.hasObservedResult ? 'O' : ''].filter(Boolean).join('/') || '-');
                    cell(row, ticket.candidateProcedureLines);
                    cell(row, ticket.reproductionAttempts);
                    cell(row, ticket.reportedOccurrenceCount);
                    cell(row, ticket.repeatabilityClass);
                    cell(row, ticket.lifecycleRuns);
                    cell(row, ticket.multiPlatformConfirmed);
                    cell(row, ticket.hasEnvironmentFingerprint);
                      cell(row, ticket.hasDiagnosticArtifactReference);
                    cell(row, ticket.softwareVersion);
                    cell(row, ticket.productAreas.join('; '));
                    cell(row, ticket.createdAt);
                    tableBody.append(row);
                });
                byId('free-ticket-status').textContent = `Showing ${Math.min(visibleTicketCount, matching.length)} of ${matching.length} matching tickets (${FREE_TEST_TICKETS.length} total Free-test tickets).`;
                const loadMore = byId('free-ticket-load-more');
                const remaining = Math.max(matching.length - visibleTicketCount, 0);
                loadMore.hidden = remaining === 0;
                loadMore.textContent = `Load ${Math.min(PAGE_SIZE, remaining)} more`;
            };
            ['free-ticket-search', 'free-ticket-basis', 'free-ticket-project', 'free-ticket-version', 'free-ticket-area', 'free-ticket-scenario', 'free-ticket-repeatability'].forEach((id) => {
                byId(id).addEventListener(id === 'free-ticket-search' ? 'input' : 'change', () => { visibleTicketCount = PAGE_SIZE; renderTickets(); });
            });
            byId('free-ticket-load-more').addEventListener('click', () => { visibleTicketCount += PAGE_SIZE; renderTickets(); });
            renderTickets();
        </script>
</body>
</html>"""
        return (
                html.replace("__TEAM__", escape(str(payload["scope"]["team"])))
                .replace("__SOURCE__", escape(str(payload["scope"]["sourceDb"])))
                .replace("__HIGH_LEVEL_INSIGHT__", _high_level_insight(payload))
                .replace("__STEP_EVIDENCE_COMPARISON__", step_evidence_comparison_html)
                .replace("__METRIC_CARDS__", _metric_cards(summary))
                .replace("__EXECUTION_SCOPE_ROWS__", execution_scope_rows)
                .replace("__EXECUTION_METHOD_ROWS__", execution_method_rows)
                .replace("__TESTCASE_TITLE_ROWS__", testcase_title_rows)
                .replace("__FREE_SCOPE_ROWS__", free_test_scope_rows)
                .replace("__FREE_BASIS_ROWS__", free_test_basis_rows)
                .replace("__FREE_SCENARIO_ROWS__", free_test_scenario_rows)
                .replace("__EFFORT_ROWS__", _effort_rows(payload["freeTest"]["effortEvidence"]))
                .replace("__REPEATABILITY_ROWS__", repeatability_rows)
                .replace("__PATTERN_EFFORT_ROWS__", pattern_effort_rows)
                .replace("__FREE_TEST_TICKETS_JSON__", free_test_tickets_json)
        )


def generate_test_method_evidence_report(
        *,
        db_path: str | Path,
        output_path: str | Path,
        team: str | None = "DTSV_China",
) -> tuple[Path, Path]:
        """Write a static HTML report and its JSON audit payload."""
        payload = build_test_method_evidence_payload(db_path=db_path, team=team)
        resolved_output_path = Path(output_path)
        resolved_json_path = resolved_output_path.with_suffix(".json")
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        resolved_output_path.write_text(_build_html(payload), encoding="utf-8")
        return resolved_output_path, resolved_json_path