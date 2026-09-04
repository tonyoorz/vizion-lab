"""JSON-lines bridge server for AIChat test-case creation.

Mirrors the protocol of ``scripts/duplicate_search_bridge.py``: read JSON lines from stdin,
write JSON + newline to stdout. The Node runtime (``testcaseBridgeRuntime.cjs``) spawns this
as a persistent child process and sends payloads like ``{"action": "prepare", "defect_id": "2804379"}``.

Actions:
  - ``warmup``        — build the 16K test-case embedding index (one-time, ~3 min first run)
  - ``search_defects``— semantic search over the defect index (reuses duplicate_search engine).
                        Returns ``{success, query, candidates[], dataset_size}``. Read-only.
  - ``search_defects_by_filter`` — exact filter by project/name keyword, excluding defects that
                        already have a linked test case. Returns ALL matches (paginated), not a
                        similarity top-K. ``{success, keyword, total, offset, limit, candidates[]}``.
    - ``discover_defect_patterns`` — freeze selected unlinked defects, then cluster only
                                                context-compatible defects before any LLM generation. Read-only.
  - ``prepare``       — read defect from local SQLite + RAG retrieve similar test cases. Returns
                        ``{success, defect_info, similar_cases, few_shot_text}``. No LLM call.
  - ``verify``        — run ``verify_test_case()`` on generated content. Returns verification result.
  - ``commit``        — create test_manual in Octane + write steps. Returns ``{success, test_id, octane_url}``.
  - ``export_csv``   — write generated drafts to a CSV file under ``exports/testcases/`` (no Octane
                        write). Safe offline-review alternative to ``commit``.
                        Returns ``{success, csv_path, row_count, errors}``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.analytics.testcase_pattern_discovery import (
    DEFAULT_SIMILARITY_THRESHOLD,
    PATTERN_DISCOVERY_POLICY_VERSION,
    audit_pattern_coverage,
    canonical_defect_id,
    discover_defect_patterns,
    encode_defect_evidence,
)


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _read_defect(defect_id: str, *, allow_live_fetch: bool = True) -> Dict[str, Any]:
    """Read a defect by id, falling back to a live Octane fetch if not in local SQLite.

    The local SQLite (``qgate_raw.db``) only contains defects for teams/years that were
    previously synced.  When a user references a defect outside that scope (e.g. a
    defect belonging to a team not yet synced), we transparently fetch it from Octane
    in real time so the test-case generation flow is not blocked by data-sync gaps.
    """
    normalized = str(defect_id or "").strip()
    if not normalized:
        raise ValueError("defect_id is required")
    # Accept human-facing "D2804379" form — strip the D prefix to match the
    # bare numeric id stored in octane_defects.defect_id (Octane raw id).
    if normalized[0] in ("D", "d") and normalized[1:].isdigit():
        normalized = normalized[1:]

    # --- Step 1: try local SQLite (fast path) ---
    from backend.analytics.config import get_full_picture_source_db_path

    conn = sqlite3.connect(str(get_full_picture_source_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT defect_id, name, severity, software_version, assigned_ecu, "
            "lead_model, project, phase, description FROM octane_defects WHERE defect_id = ?",
            (normalized,),
        ).fetchone()
    finally:
        conn.close()

    if row is not None:
        return {
            "defect_id": str(row["defect_id"] or ""),
            "name": str(row["name"] or ""),
            "severity": str(row["severity"] or ""),
            "software_version": str(row["software_version"] or ""),
            "assigned_ecu": str(row["assigned_ecu"] or ""),
            "lead_model": str(row["lead_model"] or ""),
            "project": str(row["project"] or ""),
            "phase": str(row["phase"] or ""),
            "description": str(row["description"] or ""),
        }

    if not allow_live_fetch:
        raise LookupError(f"defect {normalized} is not available in the local SQLite snapshot")

    # --- Step 2: fallback — fetch live from Octane API ---
    return _fetch_defect_from_octane(normalized)


def _fetch_defect_from_octane(defect_id: str) -> Dict[str, Any]:
    """Fetch a work item from Octane in real time and map it to the same dict shape as _read_defect.

    Tries the defects collection first (defects have severity / software_version / assigned_ecu
    fields). If that fails (e.g. the id is a feature/story/epic rather than a defect), falls
    back to fetch_work_item which works for any work_item subtype. Feature/story items lack
    defect-specific fields, so those are left empty.
    """
    from backend.analytics.ingest.client import build_default_octane_client
    from backend.analytics.ingest.source_store import _coalesce, _first_named_value
    from backend.analytics.processor import infer_defect_project, _project_from_software_version

    client = build_default_octane_client()

    # --- Step 1: try as a defect (has full defect metadata) ---
    try:
        row = client.fetch_defect_by_id(defect_id)
    except (ValueError, Exception):
        row = None

    # --- Step 2: fallback — fetch as a generic work_item (feature/story/epic) ---
    if row is None:
        row = client.fetch_work_item(defect_id)

    product_areas = _first_named_value(row.get("product_areas"))
    software_version = _coalesce(
        str(row.get("software_version") or ""),
        str(row.get("software_version_udf") or ""),
    )
    # project is a derived dimension in the local DB (set by processor.py), not a
    # direct Octane field.  Use the same inference chain so the live-fetched defect
    # gets the same project label it would have if it had been synced.
    project = _project_from_software_version(software_version) or infer_defect_project(
        {"top_aida": product_areas, "product_areas": product_areas}
    )

    return {
        "defect_id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "severity": _first_named_value(row.get("severity")),
        "software_version": software_version,
        "assigned_ecu": _coalesce(
            _first_named_value(row.get("assigned_ecu_udf")),
            str(row.get("assigned_ecu") or ""),
        ),
        "lead_model": _first_named_value(row.get("lead_model_udf")),
        "project": project,
        "phase": _first_named_value(row.get("phase")),
        "description": str(row.get("description") or ""),
    }


def _do_warmup() -> Dict[str, Any]:
    from backend.analytics.test_case_index import get_or_build_test_case_index

    idx = get_or_build_test_case_index()
    return {"success": True, "dataset_size": idx._row_count, "use_embeddings": idx._use_embeddings}


def _do_prepare(payload: Dict[str, Any]) -> Dict[str, Any]:
    from backend.analytics.test_case_index import retrieve_similar_test_cases, format_few_shot_examples

    defect_id = str(payload.get("defect_id") or "").strip()
    if not defect_id:
        return {"success": False, "error": "defect_id is required"}

    defect = _read_defect(defect_id)
    query = f"{defect['name']} {defect.get('project', '')} {defect.get('assigned_ecu', '')}"
    similar = retrieve_similar_test_cases(query, top_k=5)
    few_shot = format_few_shot_examples(similar, max_cases=3)

    return {
        "success": True,
        "defect_info": defect,
        "similar_cases": [
            {"testId": c.test_id, "name": c.name, "score": round(c.score, 3)}
            for c in similar
        ],
        "few_shot_text": few_shot,
    }


def _do_verify(payload: Dict[str, Any]) -> Dict[str, Any]:
    from backend.analytics.test_case_verifier import verify_test_case

    defect_info = payload.get("defect_info") or {}
    description_html = str(payload.get("description_html") or "")
    steps_text = str(payload.get("steps_text") or "")

    result = verify_test_case(
        defect=defect_info,
        description_html=description_html,
        steps_text=steps_text,
    )
    return {
        "success": True,
        "verification": {
            "passed": result.passed,
            "criteria": [
                {"name": c.name, "passed": c.passed, "evidence": c.evidence}
                for c in result.criteria
            ],
            "feedback": result.feedback,
        },
    }


def _do_verify_reusable_testcase_pattern(payload: Dict[str, Any]) -> Dict[str, Any]:
    from backend.analytics.test_case_verifier import verify_reusable_pattern_test_case

    defect_info = payload.get("defect_info") or {}
    member_defect_ids = payload.get("member_defect_ids") or []
    parameter_matrix = payload.get("parameter_matrix") or {}
    if not isinstance(member_defect_ids, list):
        return {"success": False, "error": "member_defect_ids must be an array"}
    if not isinstance(parameter_matrix, dict):
        return {"success": False, "error": "parameter_matrix must be an object"}

    result = verify_reusable_pattern_test_case(
        defect=defect_info,
        pattern_id=str(payload.get("pattern_id") or ""),
        member_defect_ids=[str(defect_id or "") for defect_id in member_defect_ids],
        parameter_matrix=parameter_matrix,
        description_html=str(payload.get("description_html") or ""),
        steps_text=str(payload.get("steps_text") or ""),
    )
    return {
        "success": True,
        "verification": {
            "passed": result.passed,
            "criteria": [
                {"name": criterion.name, "passed": criterion.passed, "evidence": criterion.evidence}
                for criterion in result.criteria
            ],
            "feedback": result.feedback,
        },
    }


def _do_commit(payload: Dict[str, Any]) -> Dict[str, Any]:
    from backend.analytics.ingest.client import build_default_octane_client

    test_case_data = payload.get("test_case_data") or {}
    feature_id = str(payload.get("feature_id") or "").strip()
    description_html = str(test_case_data.get("description_html") or test_case_data.get("descriptionHtml") or "")
    steps_text = str(test_case_data.get("steps_text") or test_case_data.get("stepsText") or "")
    name = str(test_case_data.get("name") or f"Generated test case from defect {test_case_data.get('defectId', '')}")

    # Resolve owner: read back from a created entity's author (no direct current-user endpoint).
    # For now, use the authenticated cookie identity — create_test_case defaults owner to the
    # authenticated user if omitted. But test_manual requires owner explicitly, so we resolve
    # it by fetching an existing test created by the current user.
    # Simpler: the caller (Node) can pass owner_workspace_user_id in the payload.
    owner_id = str(test_case_data.get("owner_workspace_user_id") or payload.get("owner_workspace_user_id") or "")
    if not owner_id:
        return {"success": False, "error": "owner_workspace_user_id is required for commit"}

    servicepack_id = str(test_case_data.get("servicepack_node_id") or "d1598r3mjrp37by4yjvy1860k")

    covered_ids = ()
    if feature_id:
        covered_ids = (feature_id,)

    client = build_default_octane_client()
    created = client.create_test_case(
        name=name,
        description_html=description_html,
        owner_workspace_user_id=owner_id,
        servicepack_node_id=servicepack_id,
        covered_work_item_ids=covered_ids,
    )
    test_id = str(created.get("id") or "")

    if steps_text and test_id:
        client.write_test_steps(test_id=test_id, steps_text=steps_text)

    octane_url = f"https://octane-prod.bmwgroup.net/ui/entity-navigation?p=1002/2001&entityType=test&id={test_id}"
    return {"success": True, "test_id": test_id, "octane_url": octane_url}


class _HtmlToPlainTextParser(HTMLParser):
    _BLOCK_TAGS = {"br", "div", "li", "p", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    _HTML_TAGS = _BLOCK_TAGS | {
        "a",
        "b",
        "body",
        "code",
        "em",
        "html",
        "i",
        "ol",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in self._HTML_TAGS:
            self._parts.append(self.get_starttag_text())
        elif normalized_tag == "li":
            self._append_line_break()
            self._parts.append("- ")
        elif normalized_tag in self._BLOCK_TAGS:
            self._append_line_break()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in self._HTML_TAGS:
            self._parts.append(f"</{tag}>")
        elif normalized_tag in self._BLOCK_TAGS:
            self._append_line_break()

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self._parts).splitlines():
            normalized = re.sub(r"\s+", " ", line).strip()
            if normalized:
                lines.append(normalized)
        return "\n".join(lines)

    def _append_line_break(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")


def _html_to_plain_text(value: Any) -> str:
    parser = _HtmlToPlainTextParser()
    parser.feed(str(value or ""))
    parser.close()
    return parser.text()


def _draft_value(draft: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = draft.get(key)
        if value is not None and value != "":
            return value
    return ""


def _test_priority(draft: Dict[str, Any]) -> str:
    explicit_priority = str(_draft_value(draft, "testPriority", "test_priority") or "").strip()
    if explicit_priority:
        return explicit_priority

    severity = str(_draft_value(draft, "defectSeverity", "defect_severity") or "").strip().casefold()
    priority_by_severity = {
        "critical": "P0",
        "very high": "P0",
        "high": "P1",
        "medium": "P2",
        "low": "P3",
    }
    return priority_by_severity.get(severity, "P2")


def _split_workflow_steps(value: Any) -> tuple[str, str, str]:
    preconditions: list[str] = []
    actions: list[str] = []
    expected_results: list[str] = []

    normalized_steps = str(value or "").replace("\\r\\n", "\n").replace("\\n", "\n")
    for raw_line in normalized_steps.splitlines():
        line = _html_to_plain_text(raw_line).strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()

        if line.casefold().startswith("[precon]"):
            preconditions.append(line[len("[PreCon]"):].strip())
        elif line.startswith("?"):
            expected_results.append(line[1:].strip())
        else:
            actions.append(line)

    def numbered(items: list[str]) -> str:
        return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))

    return "\n".join(preconditions), numbered(actions), numbered(expected_results)


def _display_defect_id(value: Any) -> str:
    normalized = canonical_defect_id(value)
    return f"D{normalized}" if normalized else ""


def _load_pattern_export_defects(defect_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Read all source fields needed by the review workbook in bounded SQLite batches."""
    from backend.analytics.config import get_full_picture_source_db_path

    normalized_ids = sorted({
        normalized for defect_id in defect_ids
        if (normalized := canonical_defect_id(defect_id))
    })
    if not normalized_ids:
        return {}

    defects: dict[str, dict[str, str]] = {}
    connection = sqlite3.connect(str(get_full_picture_source_db_path()))
    connection.row_factory = sqlite3.Row
    try:
        for offset in range(0, len(normalized_ids), 500):
            batch = normalized_ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT defect_id, name, severity, software_version, assigned_ecu, lead_model, description "
                f"FROM octane_defects WHERE defect_id IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                defect_id = canonical_defect_id(row["defect_id"])
                if defect_id:
                    defects[defect_id] = {
                        "name": str(row["name"] or ""),
                        "severity": str(row["severity"] or ""),
                        "software_version": str(row["software_version"] or ""),
                        "assigned_ecu": str(row["assigned_ecu"] or ""),
                        "lead_model": str(row["lead_model"] or ""),
                        "description": _html_to_plain_text(row["description"]),
                    }
    finally:
        connection.close()
    return defects


def _pattern_export_topic(pattern: Mapping[str, Any]) -> str:
    anchor_terms = [
        str(term).strip()
        for term in pattern.get("commonAnchorTerms") or []
        if str(term).strip()
    ]
    if anchor_terms:
        return " / ".join(anchor_terms[:3])

    context = pattern.get("context") or {}
    context_label = str(
        context.get("assignedEcu") or context.get("project") or ""
    ).strip()
    return f"待人工归类 ({context_label})" if context_label else "待人工归类"


def _pattern_review_recommendation(draft: Mapping[str, Any]) -> str:
    if str(draft.get("error") or "").strip():
        return "重新生成草稿"

    verification = draft.get("verification") or {}
    if bool(verification.get("passed")):
        return "通过自动校验"

    feedback = str(verification.get("feedback") or "").casefold()
    if "step_reproducibility" in feedback or "checkpoint_quantification" in feedback:
        return "补齐可复现步骤和可量化预期"
    return "人工复核生成内容"


def _linked_defect_preview(defect_ids: Sequence[str], limit: int = 6) -> str:
    display_ids = [_display_defect_id(defect_id) for defect_id in defect_ids]
    display_ids = [defect_id for defect_id in display_ids if defect_id]
    preview = "\n".join(display_ids[:limit])
    if len(display_ids) > limit:
        preview = f"{preview}\n... {len(display_ids) - limit} more; see mapping"
    return preview


def _excel_cell_text(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= 32_767:
        return value

    suffix = "\n[Truncated: source text exceeds Excel's 32,767-character cell limit.]"
    return f"{value[:32_767 - len(suffix)]}{suffix}"


def _write_pattern_drafts_workbook(
    workbook_path: Path,
    source_run_id: str,
    drafts_by_pattern_id: Mapping[str, Mapping[str, Any]],
    patterns_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    """Build the review workbook used by the RSU testcase-library workflow."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    all_member_ids = [
        str(member.get("defectId") or "")
        for pattern_id in drafts_by_pattern_id
        for member in patterns_by_id[pattern_id].get("members") or []
        if isinstance(member, dict)
    ]
    source_defects = _load_pattern_export_defects(all_member_ids)

    cases: list[dict[str, Any]] = []
    mapping_rows: list[list[Any]] = []
    raw_rows: list[list[Any]] = []
    topics: dict[str, dict[str, Any]] = {}
    review_rows: list[list[Any]] = []
    project_name = "RSU"

    for case_number, (pattern_id, draft) in enumerate(drafts_by_pattern_id.items(), start=1):
        pattern = patterns_by_id[pattern_id]
        representative_id = canonical_defect_id((pattern.get("representative") or {}).get("defectId"))
        members = [
            member for member in pattern.get("members") or []
            if isinstance(member, dict) and canonical_defect_id(member.get("defectId"))
        ]
        member_ids = [canonical_defect_id(member.get("defectId")) for member in members]
        member_ids = [defect_id for defect_id in member_ids if defect_id]
        representative = source_defects.get(representative_id, {})
        representative_snapshot = pattern.get("representative") or {}
        candidate_project = str(
            representative_snapshot.get("project") or representative.get("project") or ""
        ).strip()
        if candidate_project:
            project_name = candidate_project.upper()

        topic = _pattern_export_topic(pattern)
        if topic not in topics:
            topics[topic] = {
                "id": f"{len(topics) + 1:02d}",
                "case_count": 0,
                "defect_count": 0,
                "review_count": 0,
            }
        topic_summary = topics[topic]
        topic_summary["case_count"] += 1
        topic_summary["defect_count"] += len(member_ids)

        test_name = str(draft.get("name") or "")
        test_overview = _html_to_plain_text(draft.get("descriptionHtml"))
        preconditions, execution_steps, expected_results = _split_workflow_steps(draft.get("stepsText"))
        verification = draft.get("verification") or {}
        verification_passed = bool(verification.get("passed"))
        verification_feedback = str(verification.get("feedback") or "")
        generation_error = str(draft.get("error") or "")
        verification_status = "通过" if verification_passed and not generation_error else "需评审"
        priority = _test_priority({
            "testPriority": draft.get("testPriority"),
            "defect_severity": representative.get("severity", ""),
        })
        case_id = f"TC-{project_name}-{case_number:03d}"
        models = sorted({
            str(source_defects.get(defect_id, {}).get("lead_model") or member.get("leadModel") or "").strip()
            for defect_id, member in zip(member_ids, members)
            if str(source_defects.get(defect_id, {}).get("lead_model") or member.get("leadModel") or "").strip()
        })
        ecus = sorted({
            str(source_defects.get(defect_id, {}).get("assigned_ecu") or member.get("assignedEcu") or "").strip()
            for defect_id, member in zip(member_ids, members)
            if str(source_defects.get(defect_id, {}).get("assigned_ecu") or member.get("assignedEcu") or "").strip()
        })
        case = {
            "topic_id": topic_summary["id"],
            "topic": topic,
            "case_id": case_id,
            "test_name": test_name,
            "priority": priority,
            "test_overview": test_overview,
            "preconditions": preconditions,
            "execution_steps": execution_steps,
            "expected_results": expected_results,
            "verification_status": verification_status,
            "verification_feedback": verification_feedback or generation_error,
            "member_ids": member_ids,
            "models": " / ".join(models),
            "ecus": " / ".join(ecus),
            "pattern_id": pattern_id,
            "verification_passed": verification_passed,
        }
        cases.append(case)

        if verification_status != "通过":
            topic_summary["review_count"] += 1
            review_rows.append([
                _pattern_review_recommendation(draft),
                case["topic_id"], case["topic"], case_id, test_name, priority,
                test_overview, preconditions, execution_steps, expected_results,
                verification_status, case["verification_feedback"], len(member_ids),
                _linked_defect_preview(member_ids), case["models"], case["ecus"], pattern_id,
            ])

        for defect_id, member in zip(member_ids, members):
            source_defect = source_defects.get(defect_id, {})
            defect_name = str(source_defect.get("name") or member.get("name") or "")
            defect_severity = str(source_defect.get("severity") or "")
            software_version = str(source_defect.get("software_version") or member.get("softwareVersion") or "")
            assigned_ecu = str(source_defect.get("assigned_ecu") or member.get("assignedEcu") or "")
            lead_model = str(source_defect.get("lead_model") or member.get("leadModel") or "")
            is_primary = defect_id == representative_id
            similarity = member.get("similarityToRepresentative")
            mapping_rows.append([
                case_id, topic, test_name, _display_defect_id(defect_id), defect_name,
                defect_severity, software_version, assigned_ecu, lead_model,
                "是" if is_primary else "否", None if is_primary else similarity,
            ])
            raw_rows.append([
                _display_defect_id(defect_id), defect_name, defect_severity, software_version,
                assigned_ecu, lead_model, pattern_id, is_primary, None if is_primary else similarity,
                test_name, priority, test_overview, preconditions, execution_steps,
                expected_results, verification_passed, case["verification_feedback"],
                str(source_defect.get("description") or ""),
            ])

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "00_总览"
    topic_sheet = workbook.create_sheet("01_主题概览")
    case_sheet = workbook.create_sheet("02_标准Testcase库")
    mapping_sheet = workbook.create_sheet("03_Defect映射")
    review_sheet = workbook.create_sheet("04_评审队列")
    raw_sheet = workbook.create_sheet("05_RSU原始数据")

    header_fill = PatternFill("solid", fgColor="17365D")
    header_font = Font(color="FFFFFF", bold=True)
    title_font = Font(color="FFFFFF", bold=True, size=16)
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    body_alignment = Alignment(vertical="top", wrap_text=True)

    def populate_table(sheet: Any, headers: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[float]) -> None:
        sheet.append(list(headers))
        for row in rows:
            sheet.append([_excel_cell_text(value) for value in row])
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = body_alignment
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(sheet.max_row, 1)}"

    for column, width in {
        "A": 22, "B": 16, "C": 22, "D": 16, "E": 22, "F": 16, "G": 24, "H": 18,
    }.items():
        summary_sheet.column_dimensions[column].width = width
    summary_sheet.merge_cells("A1:H2")
    summary_sheet.merge_cells("A3:H3")
    summary_sheet.merge_cells("B10:H10")
    summary_sheet.merge_cells("B11:H11")
    summary_sheet["A1"] = f"{project_name} Testcase 资产库"
    summary_sheet["A3"] = "范围：参数化缺陷模式生成的 testcase 草稿、映射关系与原始 defect 数据。"
    summary_sheet["A5"] = "原始记录"
    summary_sheet["B5"] = len(raw_rows)
    summary_sheet["C5"] = "保留映射 Defect"
    summary_sheet["D5"] = len(raw_rows)
    summary_sheet["E5"] = "去除非目标 Defect"
    summary_sheet["F5"] = 0
    summary_sheet["G5"] = "Canonical Case"
    summary_sheet["H5"] = len(cases)
    summary_sheet["A7"] = "主题数"
    summary_sheet["B7"] = len(topics)
    summary_sheet["C7"] = "需评审 Case"
    summary_sheet["D7"] = len(review_rows)
    summary_sheet["E7"] = "平均压缩率"
    summary_sheet["F7"] = f"{len(raw_rows) / len(cases):.1f}x" if cases else "0.0x"
    summary_sheet["G7"] = "生成时间"
    summary_sheet["H7"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary_sheet["A10"] = "来源运行 ID"
    summary_sheet["B10"] = source_run_id
    summary_sheet["A11"] = "说明"
    summary_sheet["B11"] = "error_description 为本地 defect description 的可读文本版本；超出 Excel 单元格上限时会标记截断。"
    for row in summary_sheet.iter_rows(min_row=1, max_row=11, min_col=1, max_col=8):
        for cell in row:
            cell.alignment = body_alignment
    for coordinate in ("A1", "A3", "A5", "C5", "E5", "G5", "A7", "C7", "E7", "G7", "A10", "A11"):
        summary_sheet[coordinate].fill = header_fill
        summary_sheet[coordinate].font = header_font
    summary_sheet["A1"].font = title_font
    summary_sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")

    topic_rows = [
        [
            summary["id"], topic, summary["case_count"], summary["defect_count"],
            summary["review_count"],
            f"{summary['defect_count'] / summary['case_count']:.1f}x" if summary["case_count"] else "0.0x",
        ]
        for topic, summary in topics.items()
    ]
    populate_table(
        topic_sheet,
        ["主题编号", "测试主题", "标准Case数", "关联Defect数", "需评审Case数", "压缩率"],
        topic_rows,
        [10, 32, 14, 14, 15, 12],
    )
    populate_table(
        case_sheet,
        [
            "主题编号", "测试主题", "标准Case ID", "Testcase名称", "Priority", "测试目的 / Overview",
            "前置条件", "执行步骤", "预期结果", "验 证状态", "验证反馈", "Linked Defect数",
            "Linked Defect预览", "覆盖车型", "覆盖ECU", "原始Group ID",
        ],
        [
            [
                case["topic_id"], case["topic"], case["case_id"], case["test_name"], case["priority"],
                case["test_overview"], case["preconditions"], case["execution_steps"], case["expected_results"],
                case["verification_status"], case["verification_feedback"], len(case["member_ids"]),
                _linked_defect_preview(case["member_ids"]), case["models"], case["ecus"], case["pattern_id"],
            ]
            for case in cases
        ],
        [10, 30, 17, 42, 10, 44, 42, 48, 48, 12, 38, 13, 23, 22, 24, 22],
    )
    populate_table(
        mapping_sheet,
        [
            "标准Case ID", "测试主题", "Testcase名称", "Defect ID", "Defect Name", "Severity",
            "Software Version", "ECU", "Lead Model", "是否Primary", "相似度",
        ],
        mapping_rows,
        [17, 30, 42, 13, 52, 12, 25, 20, 14, 12, 13],
    )
    populate_table(
        review_sheet,
        [
            "评审建议", "主题编号", "测试主题", "标准Case ID", "Testcase名称", "Priority",
            "测试目的 / Overview", "前置条件", "执行步骤", "预期结果", "验证状态", "验证反馈",
            "Linked Defect数", "Linked Defect预览", "覆盖车型", "覆盖ECU", "原始Group ID",
        ],
        review_rows,
        [38, 10, 30, 17, 42, 10, 44, 42, 48, 48, 12, 38, 13, 23, 22, 24, 22],
    )
    populate_table(
        raw_sheet,
        [
            "Defect ID", "defect_name", "defect_severity", "software_version", "assigned_ecu", "lead_model",
            "group_id", "is_primary", "similarity_to_primary", "test_name", "test_priority", "test_overview",
            "preconditions", "execution_steps", "expected_results", "verification_passed", "verification_feedback",
            "error_description",
        ],
        raw_rows,
        [13, 52, 28, 12, 25, 20, 22, 10, 13, 42, 10, 42, 40, 48, 48, 14, 40, 52],
    )
    workbook.save(workbook_path)


def _do_export_csv(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Write generated test-case drafts to a CSV file (no Octane write).

    Accepts a list of generated drafts (same shape as ``prepare-batch`` results)
    and writes them to a CSV under ``<repo>/exports/testcases/``. This is the
    safe alternative to ``commit``: review the CSV offline before any Octane
    entity is created.

    CSV columns start with source defect information and then contain the
    generated test name, priority, overview, preconditions, execution steps,
    and expected results. HTML is intentionally not written to the CSV.

    Returns ``{success, csv_path, row_count, errors}``.
    """
    import csv as csv_module

    drafts = payload.get("drafts")
    if not isinstance(drafts, list) or not drafts:
        return {"success": False, "error": "drafts[] is required and must be non-empty"}

    keyword = str(payload.get("keyword") or "testcases").strip()
    # Sanitize keyword for use in filename — keep alnum/dash/underscore only.
    safe_keyword = "".join(c if c.isalnum() or c in "-_" else "_" for c in keyword) or "testcases"

    # Clustering: when cluster=true, group similar drafts by step/name similarity
    # and write them grouped (primary first, then similar). Adds group_id /
    # is_primary / similarity_to_primary columns to the CSV.
    do_cluster = bool(payload.get("cluster"))
    threshold = float(payload.get("threshold") or 0.85)
    cluster_summary = None

    # Default output dir: <repo>/exports/testcases/ (created if missing).
    output_dir = str(payload.get("output_dir") or "").strip()
    if not output_dir:
        output_dir = str(REPO_ROOT / "exports" / "testcases")
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Build a unique filename using the keyword + a sequence counter to avoid
    # collisions. The caller can also pass an explicit filename.
    filename = str(payload.get("filename") or "").strip()
    if not filename:
        seq = 1
        while True:
            candidate = out_path / f"{safe_keyword}_testcases_{seq}.csv"
            if not candidate.exists():
                filename = candidate.name
                break
            seq += 1
    if not filename.endswith(".csv"):
        filename += ".csv"
    csv_path = out_path / filename

    columns = [
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

    # Build the ordered row list. When clustering, group drafts so the CSV
    # reads primary → similar (with similarity score). Otherwise flat list.
    if do_cluster:
        groups = _cluster_drafts(drafts, threshold=threshold)
        multi_groups = sum(1 for g in groups if len(g["similar"]) > 0)
        cluster_summary = {
            "total_drafts": len(drafts),
            "total_groups": len(groups),
            "similar_clusters": multi_groups,
            "reduction": len(drafts) - len(groups),
        }
        ordered_rows = []
        for group in groups:
            primary = group["primary"]
            ordered_rows.append((group["group_id"], True, "", primary))
            for sim_draft in group["similar"]:
                sim_score = sim_draft.get("similarity_to_primary", "")
                ordered_rows.append((group["group_id"], False, sim_score, sim_draft))
    else:
        ordered_rows = [
            (
                _draft_value(d, "group_id") or 0,
                _draft_value(d, "is_primary"),
                _draft_value(d, "similarity_to_primary"),
                d,
            )
            for d in drafts
            if isinstance(d, dict)
        ]

    row_count = 0
    error_count = 0
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv_module.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for group_id, is_primary, sim_to_primary, draft in ordered_rows:
            if not isinstance(draft, dict):
                continue
            verification = draft.get("verification") or {}
            similar = _draft_value(draft, "similarCases", "similar_cases")
            if isinstance(similar, list):
                similar_str = "; ".join(
                    f"{c.get('testId','')}({c.get('score','')})" for c in similar if isinstance(c, dict)
                )
            else:
                similar_str = str(similar or "")
            description_html = _draft_value(draft, "descriptionHtml", "description_html")
            steps_text = _draft_value(draft, "stepsText", "steps_text")
            preconditions, execution_steps, expected_results = _split_workflow_steps(steps_text)
            defect_id = str(_draft_value(draft, "defectId", "defect_id") or "")
            defect_name = str(_draft_value(draft, "defectName", "defect_name") or "").strip()
            if not defect_name:
                defect_name = f"Source title unavailable (D{defect_id})" if defect_id else "Source title unavailable"
            verification_passed = (
                verification.get("passed", "")
                if isinstance(verification, dict) and "passed" in verification
                else _draft_value(draft, "verification_passed")
            )
            verification_feedback = (
                verification.get("feedback", "")
                if isinstance(verification, dict) and "feedback" in verification
                else _draft_value(draft, "verification_feedback")
            )
            if draft.get("error"):
                error_count += 1
            writer.writerow(
                {
                    "group_id": group_id,
                    "is_primary": is_primary,
                    "similarity_to_primary": sim_to_primary,
                    "defect_id": defect_id,
                    "defect_name": defect_name,
                    "defect_severity": _draft_value(draft, "defectSeverity", "defect_severity"),
                    "software_version": _draft_value(draft, "defectSoftwareVersion", "software_version"),
                    "assigned_ecu": _draft_value(draft, "defectAssignedEcu", "assigned_ecu"),
                    "lead_model": _draft_value(draft, "defectLeadModel", "lead_model"),
                    "project": _draft_value(draft, "project"),
                    "phase": _draft_value(draft, "phase"),
                    "test_name": _draft_value(draft, "name", "test_name"),
                    "test_priority": _test_priority(draft),
                    "test_overview": _html_to_plain_text(description_html),
                    "preconditions": preconditions,
                    "execution_steps": execution_steps,
                    "expected_results": expected_results,
                    "verification_passed": verification_passed,
                    "verification_feedback": verification_feedback,
                    "similar_cases": similar_str,
                    "generated_at": _draft_value(draft, "generatedAt", "generated_at"),
                    "error": _draft_value(draft, "error"),
                }
            )
            row_count += 1

    result = {
        "success": True,
        "csv_path": str(csv_path),
        "row_count": row_count,
        "errors": error_count,
    }
    if cluster_summary:
        result["cluster_summary"] = cluster_summary
    return result


def _cluster_drafts(drafts: list, threshold: float = 0.85) -> list:
    """Cluster generated testcase drafts by step/name similarity.

    Uses sentence-transformer embeddings (the same singleton model that powers
    defect duplicate search) to encode each draft's stepsText + name, computes
    pairwise cosine similarity, and greedily groups drafts whose similarity to
    the group's primary exceeds ``threshold``.

    Returns a list of groups, each:
        ``{group_id, primary_index, member_indices, primary, similar: [{..., similarity_to_primary}]}``
    sorted by group size descending (largest clusters first).
    """
    if not drafts:
        return []

    from backend.duplicate_issue_finder import _get_st_model

    model = _get_st_model()
    if model is None:
        # No embedding model available — fall back to one draft per group (no clustering).
        return [
            {
                "group_id": i + 1,
                "primary_index": i,
                "member_indices": [i],
                "primary": drafts[i],
                "similar": [],
            }
            for i in range(len(drafts))
        ]

    texts = [
        f"{d.get('stepsText', '')} {d.get('name', '')}".strip() or d.get("defectId", str(i))
        for i, d in enumerate(drafts)
    ]
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    import numpy as np

    sim_matrix = (embeddings @ embeddings.T).astype(float)

    assigned = [False] * len(drafts)
    groups: list = []
    for i in range(len(drafts)):
        if assigned[i]:
            continue
        member_indices = [i]
        assigned[i] = True
        for j in range(i + 1, len(drafts)):
            if assigned[j]:
                continue
            if sim_matrix[i][j] >= threshold:
                member_indices.append(j)
                assigned[j] = True
        groups.append({"primary_index": i, "member_indices": member_indices})

    groups.sort(key=lambda g: len(g["member_indices"]), reverse=True)

    result = []
    for seq, group in enumerate(groups):
        pi = group["primary_index"]
        similar = []
        for idx in group["member_indices"]:
            if idx == pi:
                continue
            draft_copy = dict(drafts[idx])
            draft_copy["similarity_to_primary"] = round(float(sim_matrix[pi][idx]), 3)
            similar.append(draft_copy)
        result.append(
            {
                "group_id": seq + 1,
                "primary_index": pi,
                "member_indices": group["member_indices"],
                "primary": drafts[pi],
                "similar": similar,
            }
        )
    return result


def _do_cluster_testcases(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Cluster generated testcase drafts by similarity and return groups.

    Returns ``{success, total_drafts, total_groups, groups[], summary}`` where
    each group has a ``primary`` testcase and a ``similar`` list (each with
    ``similarity_to_primary`` score). This lets the user review only the primary
    testcases and quickly decide whether to merge the similar ones.
    """
    drafts = payload.get("drafts")
    if not isinstance(drafts, list) or not drafts:
        return {"success": False, "error": "drafts[] is required and must be non-empty"}

    threshold = float(payload.get("threshold") or 0.85)
    groups = _cluster_drafts(drafts, threshold=threshold)

    multi_groups = sum(1 for g in groups if len(g["similar"]) > 0)
    return {
        "success": True,
        "total_drafts": len(drafts),
        "total_groups": len(groups),
        "groups": groups,
        "summary": {
            "unique_testcases": len(groups),
            "similar_clusters": multi_groups,
            "total_drafts": len(drafts),
            "reduction": len(drafts) - len(groups),
        },
    }


def _do_search_defects(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Semantic search over the defect index (reuses duplicate_search's engine).

    Returns a lightweight candidate list (defectId / name / project / phase /
    similarity / snippet) for the batch test-case flow. Unlike the full
    duplicate search, this does NOT write feedback or return reranker timings —
    it's a read-only retrieval for picking which defects to turn into tests.
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}

    top_k = int(payload.get("top_k") or 10)
    top_k = max(1, min(30, top_k))

    # Delegate to duplicate_search_bridge._search — it already handles prepared
    # index manifest, cache, reranker, and calibration. We just strip the result
    # down to the fields the test-case batch flow needs.
    from duplicate_search_bridge import _search as dup_search

    raw = dup_search({"query": query, "top_k": top_k}, REPO_ROOT)
    if not raw.get("success"):
        return raw

    candidates = []
    for item in (raw.get("result") or {}).get("candidates") or []:
        candidates.append(
            {
                "defectId": str(item.get("ticketId") or ""),
                "name": str(item.get("name") or ""),
                "project": item.get("project"),
                "pu": item.get("pu"),
                "statusPhase": item.get("statusPhase"),
                "similarity": float(item.get("similarity") or 0.0),
                "confidenceLabel": str(item.get("confidenceLabel") or ""),
                "snippet": str(item.get("snippet") or "")[:240],
            }
        )

    return {
        "success": True,
        "query": query,
        "candidates": candidates,
        "dataset_size": (raw.get("result") or {}).get("dataset_size"),
    }


_LINKED_DEFECT_CACHE: Set[str] | None = None


def _load_linked_defect_ids() -> Set[str]:
    """Build the set of defect ids that already have a linked test case.

    A defect is "linked" if it appears in any test case's ``defect_ids_json``
    field. Two tables are scanned and unioned:

    - ``octane_testcases`` (primary test-case table, 16K+ rows)
    - ``octane_traceability_testcases`` (traceability view, 36K+ rows)

    The union catches defects that appear in only one of the two. The result is
    cached for the process lifetime (the SQLite data only changes on re-ingest).
    """
    global _LINKED_DEFECT_CACHE
    if _LINKED_DEFECT_CACHE is not None:
        return _LINKED_DEFECT_CACHE

    from backend.analytics.config import get_full_picture_source_db_path

    linked: Set[str] = set()
    db_path = str(get_full_picture_source_db_path())
    if not db_path:
        _LINKED_DEFECT_CACHE = linked
        return linked

    conn = sqlite3.connect(db_path)
    try:
        for table in ("octane_testcases", "octane_traceability_testcases"):
            try:
                cursor = conn.execute(
                    f"SELECT defect_ids_json FROM {table} "
                    f"WHERE defect_ids_json IS NOT NULL AND defect_ids_json != ''"
                )
            except sqlite3.OperationalError:
                continue  # table missing — skip silently
            for (raw,) in cursor.fetchall():
                try:
                    ids = json.loads(raw or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(ids, list):
                    linked.update(
                        normalized
                        for defect_id in ids
                        if (normalized := canonical_defect_id(defect_id))
                    )
    finally:
        conn.close()

    _LINKED_DEFECT_CACHE = linked
    return linked


_DISCOVERY_DEFECT_FIELDS = (
    "defect_id",
    "name",
    "description",
    "comments",
    "project",
    "assigned_ecu",
    "lead_model",
    "software_version",
    "detected_in_release",
    "phase",
    "status_phase",
    "severity",
    "last_modified",
    "creation_time",
)
_PATTERN_RUN_ID_PATTERN = re.compile(r"pattern-discovery-[0-9a-f]{16}")


def _load_pattern_discovery_rows(keyword: str, project_filter: str) -> list[sqlite3.Row]:
    from backend.analytics.config import get_full_picture_source_db_path

    with sqlite3.connect(str(get_full_picture_source_db_path())) as conn:
        conn.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(octane_defects)")}
        select_fields = [
            field if field in columns else f"'' AS {field}"
            for field in _DISCOVERY_DEFECT_FIELDS
        ]
        return conn.execute(
            f"SELECT {', '.join(select_fields)} FROM octane_defects "
            "WHERE (project = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE) "
            "ORDER BY defect_id",
            (project_filter or keyword, f"%{keyword}%"),
        ).fetchall()


def _parse_similarity_threshold(payload: Dict[str, Any]) -> float:
    raw = payload.get("similarity_threshold")
    if raw in {None, ""}:
        return DEFAULT_SIMILARITY_THRESHOLD
    try:
        threshold = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("similarity_threshold must be a number") from exc
    if not 0 < threshold <= 1:
        raise ValueError("similarity_threshold must be between 0 and 1")
    return threshold


def _pattern_source_fingerprint(rows: list[sqlite3.Row]) -> str:
    source = [
        {
            "defectId": canonical_defect_id(row["defect_id"]),
            "name": str(row["name"] or ""),
            "description": str(row["description"] or ""),
            "comments": str(row["comments"] or ""),
            "project": str(row["project"] or ""),
            "assignedEcu": str(row["assigned_ecu"] or ""),
            "leadModel": str(row["lead_model"] or ""),
            "softwareVersion": str(row["software_version"] or ""),
            "detectedInRelease": str(row["detected_in_release"] or ""),
            "lastModified": str(row["last_modified"] or ""),
        }
        for row in rows
    ]
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pattern_snapshot_path(payload: Dict[str, Any], run_id: str) -> Path:
    output_dir = str(payload.get("snapshot_output_dir") or "").strip()
    directory = Path(output_dir) if output_dir else REPO_ROOT / "exports" / "testcases" / "pattern-discovery"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_id}.json"


def _load_pattern_snapshot(payload: Dict[str, Any]) -> tuple[dict[str, Any], Path]:
    run_id = str(payload.get("run_id") or "").strip()
    if not _PATTERN_RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must be a valid pattern discovery run ID")
    snapshot_path = _pattern_snapshot_path(payload, run_id)
    if not snapshot_path.is_file():
        raise ValueError("pattern discovery snapshot was not found")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError("pattern discovery snapshot is invalid")
    source_snapshot = snapshot.get("sourceSnapshot") or {}
    if source_snapshot.get("runId") != run_id or not isinstance(snapshot.get("patterns"), list):
        raise ValueError("pattern discovery snapshot is invalid")
    return snapshot, snapshot_path


def _load_direct_testcases_by_defect() -> dict[str, list[dict[str, str]]]:
    from backend.analytics.config import get_full_picture_source_db_path

    direct_links: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    with sqlite3.connect(str(get_full_picture_source_db_path())) as conn:
        conn.row_factory = sqlite3.Row
        for table in ("octane_testcases", "octane_traceability_testcases"):
            columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
            if "defect_ids_json" not in columns:
                continue
            select_fields = [
                field if field in columns else f"'' AS {field}"
                for field in ("test_id", "test_name", "scope_team", "scope_release", "test_subtype")
            ]
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(select_fields)}, defect_ids_json FROM {table} "
                    "WHERE defect_ids_json IS NOT NULL AND defect_ids_json != ''"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                try:
                    defect_ids = json.loads(row["defect_ids_json"] or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(defect_ids, list):
                    continue
                testcase = {
                    "testId": str(row["test_id"] or ""),
                    "name": str(row["test_name"] or ""),
                    "team": str(row["scope_team"] or ""),
                    "release": str(row["scope_release"] or ""),
                    "subtype": str(row["test_subtype"] or ""),
                    "source": table,
                }
                for defect_id in defect_ids:
                    normalized_id = canonical_defect_id(defect_id)
                    link_key = (normalized_id, testcase["testId"])
                    if not normalized_id or link_key in seen:
                        continue
                    seen.add(link_key)
                    direct_links.setdefault(normalized_id, []).append(testcase)
    return direct_links


def _retrieve_pattern_testcase_candidates(representative: Dict[str, Any]) -> list[dict[str, Any]]:
    from backend.analytics.test_case_index import retrieve_similar_test_cases

    query = " ".join(
        str(representative.get(field_name) or "")
        for field_name in ("name", "project", "assignedEcu", "leadModel")
    ).strip()
    return [
        {
            "testId": testcase.test_id,
            "name": testcase.name,
            "score": testcase.score,
            "team": testcase.team,
            "release": testcase.release,
            "subtype": testcase.subtype,
        }
        for testcase in retrieve_similar_test_cases(query, top_k=3)
    ]


def _pattern_coverage_audit_path(snapshot_path: Path, pattern_ids: set[str]) -> Path:
    selection = "|".join(sorted(pattern_ids)).encode("utf-8")
    selection_hash = hashlib.sha256(selection).hexdigest()[:12]
    return snapshot_path.with_name(f"{snapshot_path.stem}-coverage-audit-{selection_hash}.json")


def _find_matching_coverage_audit(
    snapshot_path: Path,
    source_run_id: str,
    selected_pattern_ids: set[str],
) -> dict[str, Any] | None:
    exact_path = _pattern_coverage_audit_path(snapshot_path, selected_pattern_ids)
    candidates = [exact_path, *sorted(snapshot_path.parent.glob(f"{snapshot_path.stem}-coverage-audit-*.json"))]
    seen_paths: set[Path] = set()
    audited_pattern_ids: set[str] = set()
    matched_audits: list[dict[str, Any]] = []
    for candidate_path in candidates:
        if candidate_path in seen_paths or not candidate_path.is_file():
            continue
        seen_paths.add(candidate_path)
        try:
            audit = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(audit, dict) or (audit.get("sourceSnapshot") or {}).get("runId") != source_run_id:
            continue
        audited_pattern_ids.update(
            str(item.get("patternId") or "")
            for item in audit.get("audits") or []
            if isinstance(item, dict)
        )
        matched_audits.append(audit)
    if selected_pattern_ids <= audited_pattern_ids:
        return {"audits": matched_audits}
    return None


def _do_audit_defect_pattern_coverage(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Audit exact and semantic testcase candidates for persisted patterns only."""
    requested_pattern_ids = payload.get("pattern_ids")
    if requested_pattern_ids is not None and not isinstance(requested_pattern_ids, list):
        return {"success": False, "error": "pattern_ids must be an array when provided"}
    selected_pattern_ids = {str(pattern_id or "").strip() for pattern_id in requested_pattern_ids or [] if str(pattern_id or "").strip()}
    if requested_pattern_ids is not None and not selected_pattern_ids:
        return {"success": False, "error": "pattern_ids must contain at least one pattern ID"}

    try:
        snapshot, snapshot_path = _load_pattern_snapshot(payload)
        patterns = snapshot["patterns"]
        available_pattern_ids = {str(pattern.get("patternId") or "") for pattern in patterns}
        unknown_pattern_ids = selected_pattern_ids - available_pattern_ids
        if unknown_pattern_ids:
            raise ValueError("pattern_ids include IDs not present in the discovery snapshot")
        selected_patterns = [
            pattern
            for pattern in patterns
            if not selected_pattern_ids or str(pattern.get("patternId") or "") in selected_pattern_ids
        ]
        audit = audit_pattern_coverage(
            selected_patterns,
            direct_testcases_by_defect=_load_direct_testcases_by_defect(),
            retrieve_similar=_retrieve_pattern_testcase_candidates,
        )
        audit_path = _pattern_coverage_audit_path(snapshot_path, selected_pattern_ids or available_pattern_ids)
        result = {
            "sourceSnapshot": snapshot["sourceSnapshot"],
            "audits": audit["audits"],
            "summary": audit["summary"],
        }
        audit_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, **result, "auditPath": str(audit_path)}
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return {"success": False, "error": str(exc)}


def _prepare_pattern_few_shot(defect: Dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    from backend.analytics.test_case_index import format_few_shot_examples, retrieve_similar_test_cases

    query = " ".join(
        str(defect.get(field_name) or "")
        for field_name in ("name", "project", "assigned_ecu", "lead_model")
    ).strip()
    similar = retrieve_similar_test_cases(query, top_k=5)
    return (
        [
            {"testId": testcase.test_id, "name": testcase.name, "score": testcase.score}
            for testcase in similar
        ],
        format_few_shot_examples(similar, max_cases=3),
    )


def _prepare_one_pattern(pattern: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare a single pattern's defect + few-shot context (worker for ThreadPoolExecutor).

    Thread-safe: ``_read_defect`` opens its own SQLite connection per call, and
    ``_prepare_pattern_few_shot`` reads the shared test-case index (read-only after
    the initial build, which has already happened in the discover/warmup phase).
    """
    pattern_id = str(pattern.get("patternId") or "")
    representative = dict(pattern.get("representative") or {})
    representative_id = canonical_defect_id(representative.get("defectId"))
    try:
        defect = _read_defect(representative_id, allow_live_fetch=False)
        similar_cases, few_shot_text = _prepare_pattern_few_shot(defect)
        error = None
    except (LookupError, RuntimeError, sqlite3.Error, ValueError) as exc:
        defect = {}
        similar_cases = []
        few_shot_text = ""
        error = str(exc)
    return {
        "patternId": pattern_id,
        "requiresManualReview": bool(pattern.get("requiresManualReview")),
        "representative": representative,
        "memberDefectIds": [
            canonical_defect_id(member.get("defectId"))
            for member in pattern.get("members") or []
            if isinstance(member, dict) and canonical_defect_id(member.get("defectId"))
        ],
        "memberCount": len(pattern.get("members") or []),
        "commonAnchorTerms": list(pattern.get("commonAnchorTerms") or []),
        "parameterMatrix": dict(pattern.get("parameterMatrix") or {}),
        "evidencePreview": str(pattern.get("evidencePreview") or ""),
        "defectInfo": defect,
        "similarCases": similar_cases,
        "fewShotText": few_shot_text,
        "error": error,
    }


_PATTERN_PREPARE_WORKERS = 6


def _do_prepare_reusable_testcase_patterns(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare local evidence and RAG context for explicitly selected patterns only."""
    requested_pattern_ids = payload.get("pattern_ids")
    if not isinstance(requested_pattern_ids, list):
        return {"success": False, "error": "pattern_ids must be a non-empty array"}
    selected_pattern_ids = {str(pattern_id or "").strip() for pattern_id in requested_pattern_ids if str(pattern_id or "").strip()}
    if not selected_pattern_ids:
        return {"success": False, "error": "pattern_ids must contain at least one pattern ID"}

    try:
        snapshot, snapshot_path = _load_pattern_snapshot(payload)
        patterns = snapshot["patterns"]
        available_pattern_ids = {str(pattern.get("patternId") or "") for pattern in patterns}
        if selected_pattern_ids - available_pattern_ids:
            raise ValueError("pattern_ids include IDs not present in the discovery snapshot")
        audit = _find_matching_coverage_audit(
            snapshot_path,
            str(snapshot["sourceSnapshot"].get("runId") or ""),
            selected_pattern_ids,
        )
        if audit is None:
            raise ValueError("matching coverage audit is required before pattern draft preparation")

        selected_patterns = [
            pattern for pattern in patterns
            if str(pattern.get("patternId") or "") in selected_pattern_ids
        ]
        # Parallel prepare: each pattern does a SQLite read + embedding index query
        # (both I/O-bound, read-only). Thread pool avoids serial blocking.
        with ThreadPoolExecutor(max_workers=min(_PATTERN_PREPARE_WORKERS, len(selected_patterns))) as pool:
            contexts = list(pool.map(_prepare_one_pattern, selected_patterns))
        succeeded = sum(1 for context in contexts if not context["error"])
        return {
            "success": True,
            "sourceSnapshot": snapshot["sourceSnapshot"],
            "contexts": contexts,
            "summary": {"total": len(contexts), "succeeded": succeeded, "failed": len(contexts) - succeeded},
        }
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return {"success": False, "error": str(exc)}


def _do_export_reusable_pattern_drafts(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Write review-only CSV artifacts for drafts generated from a frozen pattern snapshot."""
    import csv

    drafts = payload.get("drafts")
    if not isinstance(drafts, list) or not drafts:
        return {"success": False, "error": "drafts must be a non-empty array"}

    try:
        snapshot, snapshot_path = _load_pattern_snapshot(payload)
        patterns_by_id = {
            str(pattern.get("patternId") or ""): pattern
            for pattern in snapshot["patterns"]
            if isinstance(pattern, dict) and str(pattern.get("patternId") or "")
        }
        drafts_by_pattern_id: dict[str, dict[str, Any]] = {}
        for draft in drafts:
            if not isinstance(draft, dict):
                raise ValueError("drafts must contain objects")
            pattern_id = str(draft.get("patternId") or "").strip()
            if not pattern_id or pattern_id not in patterns_by_id:
                raise ValueError("draft contains a pattern ID not present in the discovery snapshot")
            if pattern_id in drafts_by_pattern_id:
                raise ValueError("drafts contain duplicate pattern IDs")
            drafts_by_pattern_id[pattern_id] = draft

        pattern_ids = set(drafts_by_pattern_id)
        selection_hash = hashlib.sha256("|".join(sorted(pattern_ids)).encode("utf-8")).hexdigest()[:12]
        pattern_drafts_path = snapshot_path.with_name(f"{snapshot_path.stem}-pattern-drafts-{selection_hash}.csv")
        defect_mapping_path = snapshot_path.with_name(f"{snapshot_path.stem}-defect-mapping-{selection_hash}.csv")
        testcase_library_path = snapshot_path.with_name(f"{snapshot_path.stem}-testcase-library-{selection_hash}.xlsx")
        draft_columns = [
            "source_run_id", "pattern_id", "representative_defect_id", "member_count", "member_defect_ids",
            "common_anchor_terms", "software_versions", "detected_in_releases", "test_name", "test_overview",
            "steps_text", "verification_passed", "verification_feedback", "similar_cases", "generated_at", "error",
        ]
        mapping_columns = [
            "source_run_id", "pattern_id", "representative_defect_id", "defect_id", "similarity_to_representative",
            "requires_manual_review", "review_status", "draft_generation_status", "test_name", "error",
        ]
        source_run_id = str(snapshot["sourceSnapshot"].get("runId") or "")

        with pattern_drafts_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=draft_columns)
            writer.writeheader()
            for pattern_id, draft in drafts_by_pattern_id.items():
                pattern = patterns_by_id[pattern_id]
                parameter_matrix = pattern.get("parameterMatrix") or {}
                verification = draft.get("verification") or {}
                similar_cases = draft.get("similarCases") or []
                writer.writerow(
                    {
                        "source_run_id": source_run_id,
                        "pattern_id": pattern_id,
                        "representative_defect_id": str((pattern.get("representative") or {}).get("defectId") or ""),
                        "member_count": len(pattern.get("members") or []),
                        "member_defect_ids": ",".join(
                            canonical_defect_id(member.get("defectId"))
                            for member in pattern.get("members") or []
                            if isinstance(member, dict) and canonical_defect_id(member.get("defectId"))
                        ),
                        "common_anchor_terms": ",".join(str(term) for term in pattern.get("commonAnchorTerms") or []),
                        "software_versions": ",".join(str(value) for value in parameter_matrix.get("softwareVersions") or []),
                        "detected_in_releases": ",".join(str(value) for value in parameter_matrix.get("detectedInReleases") or []),
                        "test_name": str(draft.get("name") or ""),
                        "test_overview": _html_to_plain_text(str(draft.get("descriptionHtml") or "")),
                        "steps_text": str(draft.get("stepsText") or ""),
                        "verification_passed": bool(verification.get("passed")),
                        "verification_feedback": str(verification.get("feedback") or ""),
                        "similar_cases": "; ".join(
                            f"T{case.get('testId', '')}({case.get('score', '')})"
                            for case in similar_cases
                            if isinstance(case, dict)
                        ),
                        "generated_at": str(draft.get("generatedAt") or ""),
                        "error": str(draft.get("error") or ""),
                    }
                )

        with defect_mapping_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=mapping_columns)
            writer.writeheader()
            for pattern_id, draft in drafts_by_pattern_id.items():
                pattern = patterns_by_id[pattern_id]
                representative_id = str((pattern.get("representative") or {}).get("defectId") or "")
                for member in pattern.get("members") or []:
                    if not isinstance(member, dict):
                        continue
                    writer.writerow(
                        {
                            "source_run_id": source_run_id,
                            "pattern_id": pattern_id,
                            "representative_defect_id": representative_id,
                            "defect_id": canonical_defect_id(member.get("defectId")),
                            "similarity_to_representative": member.get("similarityToRepresentative", ""),
                            "requires_manual_review": bool(pattern.get("requiresManualReview")),
                            "review_status": "pending",
                            "draft_generation_status": "failed" if draft.get("error") else "draft",
                            "test_name": str(draft.get("name") or ""),
                            "error": str(draft.get("error") or ""),
                        }
                    )

        _write_pattern_drafts_workbook(
            testcase_library_path,
            source_run_id,
            drafts_by_pattern_id,
            patterns_by_id,
        )

        return {
            "success": True,
            "patternDraftsCsvPath": str(pattern_drafts_path),
            "defectMappingCsvPath": str(defect_mapping_path),
            "testcaseLibraryXlsxPath": str(testcase_library_path),
            "patternCount": len(drafts_by_pattern_id),
            "mappingRowCount": sum(len(patterns_by_id[pattern_id].get("members") or []) for pattern_id in pattern_ids),
        }
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return {"success": False, "error": str(exc)}


def _do_discover_defect_patterns(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze and cluster selected, unlinked defects without generating testcases."""
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        return {"success": False, "error": "keyword is required"}

    project_filter = str(payload.get("project") or "").strip()
    requested_values = payload.get("defect_ids")
    if requested_values is not None and not isinstance(requested_values, list):
        return {"success": False, "error": "defect_ids must be an array when provided"}

    requested_ids = {
        normalized
        for defect_id in requested_values or []
        if (normalized := canonical_defect_id(defect_id))
    }
    if requested_values is not None and not requested_ids:
        return {"success": False, "error": "defect_ids must contain at least one valid defect ID"}

    try:
        similarity_threshold = _parse_similarity_threshold(payload)
        matching_rows = _load_pattern_discovery_rows(keyword, project_filter)
        linked_ids = _load_linked_defect_ids()
        unlinked_rows = [
            row for row in matching_rows
            if canonical_defect_id(row["defect_id"]) not in linked_ids
        ]
        selected_rows = [
            row for row in unlinked_rows
            if not requested_ids or canonical_defect_id(row["defect_id"]) in requested_ids
        ]
        source_fingerprint = _pattern_source_fingerprint(selected_rows)
        run_seed = json.dumps(
            {
                "sourceFingerprint": source_fingerprint,
                "keyword": keyword,
                "project": project_filter,
                "similarityThreshold": similarity_threshold,
                "policyVersion": PATTERN_DISCOVERY_POLICY_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        run_id = f"pattern-discovery-{hashlib.sha256(run_seed).hexdigest()[:16]}"
        snapshot_path = _pattern_snapshot_path(payload, run_id)

        if snapshot_path.exists():
            cached = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if cached.get("sourceSnapshot", {}).get("sourceFingerprint") == source_fingerprint:
                return {"success": True, **cached}

        discovery = discover_defect_patterns(
            [dict(row) for row in selected_rows],
            encode=encode_defect_evidence,
            similarity_threshold=similarity_threshold,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return {"success": False, "error": str(exc)}

    source_snapshot = {
        "runId": run_id,
        "sourceFingerprint": source_fingerprint,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "policyVersion": PATTERN_DISCOVERY_POLICY_VERSION,
        "keyword": keyword,
        "project": project_filter or None,
        "matchedTotal": len(matching_rows),
        "linkedExcluded": len(matching_rows) - len(unlinked_rows),
        "selectedCount": len(selected_rows),
        "snapshotPath": str(snapshot_path),
    }
    snapshot = {
        "sourceSnapshot": source_snapshot,
        "similarityThreshold": discovery["similarityThreshold"],
        "patterns": discovery["patterns"],
        "needsEvidence": discovery["needs_evidence"],
        "summary": discovery["summary"],
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, **snapshot}


def _do_search_defects_by_filter(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Filter defects by project/name keyword, excluding defects already linked
    to a test case.

    Unlike ``_do_search_defects`` (semantic similarity top-K), this is an exact
    filter: it returns **all** matching defects that lack a linked test case.
    Supports pagination (offset/limit) so the frontend can page through large
    result sets (e.g. ~1,661 RSU defects without test cases).

    Matching rule: ``project = <keyword> OR name LIKE '%<keyword>%'`` (case-
    insensitive). The keyword is matched as-is; callers pass a single token like
    "RSU".

    Returns ``{success, keyword, total, offset, limit, candidates[], summary}``
    where each candidate is ``{defectId, name, project, phase, statusPhase,
    hasLinkedTest: false}``.
    """
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        return {"success": False, "error": "keyword is required"}

    project_filter = str(payload.get("project") or "").strip()
    limit = max(1, min(int(payload.get("limit") or 50), 200))
    offset = max(0, int(payload.get("offset") or 0))

    from backend.analytics.config import get_full_picture_source_db_path

    linked = _load_linked_defect_ids()
    like_pattern = f"%{keyword}%"
    # If an explicit project is given it takes priority over the keyword for the
    # project column; the keyword still applies to name. When no project is
    # given, the keyword matches both project and name.
    project_param = project_filter or keyword

    conn = sqlite3.connect(str(get_full_picture_source_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        # Total count (before pagination) of matching defects WITHOUT a linked test.
        # We fetch all matching ids and filter the linked set in Python — the
        # linked set (2,635 ids) is too large for a stable ``NOT IN`` parameter
        # list and the full match set (1,674) is small enough to iterate.
        matching_rows = conn.execute(
            "SELECT defect_id, name, project, phase, status_phase "
            "FROM octane_defects "
            "WHERE (project = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE) "
            "ORDER BY defect_id",
            (project_param, like_pattern),
        ).fetchall()
    finally:
        conn.close()

    unlinked_rows = [
        row for row in matching_rows
        if canonical_defect_id(row["defect_id"]) not in linked
    ]
    total = len(unlinked_rows)

    # Lightweight mode: return only the defect IDs (no metadata). Used by the
    # frontend's "select all" button to grab all 1,600+ IDs without paging.
    if payload.get("ids_only"):
        return {
            "success": True,
            "keyword": keyword,
            "project": project_filter or None,
            "total": total,
            "defect_ids": [str(r["defect_id"] or "") for r in unlinked_rows],
            "summary": {
                "matched_total": len(matching_rows),
                "linked_excluded": len(matching_rows) - total,
                "without_linked_testcase": total,
            },
        }

    page = unlinked_rows[offset : offset + limit]

    candidates = [
        {
            "defectId": str(row["defect_id"] or ""),
            "name": str(row["name"] or ""),
            "project": row["project"],
            "phase": row["phase"],
            "statusPhase": row["status_phase"],
            "hasLinkedTest": False,
        }
        for row in page
    ]

    return {
        "success": True,
        "keyword": keyword,
        "project": project_filter or None,
        "total": total,
        "offset": offset,
        "limit": limit,
        "candidates": candidates,
        "summary": {
            "matched_total": len(matching_rows),
            "linked_excluded": len(matching_rows) - total,
            "without_linked_testcase": total,
        },
    }


def _handle_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = str(payload.get("action") or "").strip()

    if action == "warmup":
        return _do_warmup()
    if action == "search_defects":
        return _do_search_defects(payload)
    if action == "search_defects_by_filter":
        return _do_search_defects_by_filter(payload)
    if action == "discover_defect_patterns":
        return _do_discover_defect_patterns(payload)
    if action == "audit_defect_pattern_coverage":
        return _do_audit_defect_pattern_coverage(payload)
    if action == "prepare_reusable_testcase_patterns":
        return _do_prepare_reusable_testcase_patterns(payload)
    if action == "export_reusable_pattern_drafts":
        return _do_export_reusable_pattern_drafts(payload)
    if action == "prepare":
        return _do_prepare(payload)
    if action == "verify":
        return _do_verify(payload)
    if action == "verify_reusable_testcase_pattern":
        return _do_verify_reusable_testcase_pattern(payload)
    if action == "commit":
        return _do_commit(payload)
    if action == "export_csv":
        return _do_export_csv(payload)
    if action == "cluster_testcases":
        return _do_cluster_testcases(payload)
    return {"success": False, "error": f"unknown action: {action}"}


def run_server(
    input_stream: Any = sys.stdin,
    output_stream: Any = sys.stdout,
) -> None:
    for raw_line in input_stream:
        line = str(raw_line or "").strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except Exception:
            payload = {}

        try:
            result = _handle_payload(payload)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}

        output_stream.write(json.dumps(result, ensure_ascii=False))
        output_stream.write("\n")
        output_stream.flush()


def main() -> None:
    _configure_utf8_stdio()
    if "--server" in sys.argv[1:]:
        run_server()
        return

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    try:
        result = _handle_payload(payload)
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
