from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from backend.analytics.phase09_idcevo_issue_normalization import (
    PAIR_INPUT_POLICY_VERSION,
    PAIR_REVIEWER_POLICY_VERSION,
    TARGET_CLOSURE_DISPOSITIONS,
    build_confirmed_duplicate_groups,
)
from backend.analytics.phase09_idcevo_report import build_phase09_idcevo_report_payload


SUMMARY_SHEET = "00_总览"
REPRESENTATIVES_SHEET = "01_代表票"
MEMBERS_SHEET = "02_成员映射"
CONFIRMED_SHEET = "03_确认依据"
QUARANTINED_SHEET = "04_隔离候选"
FUNCTION_RISK_SHEET = "05_功能风险对比"
EVIDENCE_SHEET = "06_原始代表证据"
METHODOLOGY_SHEET = "07_方法论"
NORMALIZATION_SHEET_NAMES = (
    SUMMARY_SHEET,
    REPRESENTATIVES_SHEET,
    MEMBERS_SHEET,
    CONFIRMED_SHEET,
    QUARANTINED_SHEET,
    FUNCTION_RISK_SHEET,
    EVIDENCE_SHEET,
    METHODOLOGY_SHEET,
)

NAVY_FILL = PatternFill("solid", fgColor="18365D")
BLUE_FILL = PatternFill("solid", fgColor="D9EAF7")
GREEN_FILL = PatternFill("solid", fgColor="D9EAD3")
AMBER_FILL = PatternFill("solid", fgColor="FCE4D6")
RED_FILL = PatternFill("solid", fgColor="F4CCCC")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
BODY_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _text(value: object, fallback: str = "") -> str:
    value_text = str(value or "").strip()
    return value_text or fallback


def _cell_value(value: object) -> object:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    value_text = str(value)
    if value_text.startswith(("=", "+", "-", "@")):
        value_text = "'" + value_text
    if len(value_text) <= 32_767:
        return value_text
    suffix = "\n[Truncated: source text exceeds Excel's 32,767-character cell limit.]"
    return value_text[: 32_767 - len(suffix)] + suffix


def _json(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _text(value)


def _set_widths(sheet: Any, widths: Sequence[float]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_table(
    sheet: Any,
    *,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    table_name: str,
) -> None:
    for column_index, header in enumerate(headers, start=1):
        cell = sheet.cell(1, column_index, header)
        cell.fill = NAVY_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    for row_index, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, column_index, _cell_value(value))
            cell.alignment = BODY_ALIGNMENT
    _set_widths(sheet, widths)
    sheet.freeze_panes = "A2"
    last_column = get_column_letter(len(headers))
    last_row = max(1, len(rows) + 1)
    sheet.auto_filter.ref = f"A1:{last_column}{last_row}"
    if rows:
        table = Table(displayName=table_name, ref=f"A1:{last_column}{last_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)


def _function_name(ticket: Mapping[str, object]) -> str:
    for field_name in (
        "solution_cluster",
        "defect_category",
        "product_areas",
        "function2modul",
        "assigned_ecu",
    ):
        value = _text(ticket.get(field_name))
        if value and value.casefold() not in {"not recorded", "unknown", "(blank)"}:
            return value
    return "Not recorded"


def _risk_score(ticket: Mapping[str, object]) -> float:
    try:
        return float(ticket.get("risk_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _review_summary(results: object) -> str:
    if not isinstance(results, list):
        return "No valid reviewer result"
    return " | ".join(
        "{}: {} ({:.2f})".format(
            _text(result.get("model"), "Not recorded"),
            _text(result.get("verdict"), "Not recorded"),
            float(result.get("confidence") or 0),
        )
        for result in results
        if isinstance(result, Mapping)
    ) or "No valid reviewer result"


def _review_citations(results: object) -> str:
    if not isinstance(results, list):
        return ""
    return " | ".join(
        "{}: +[{}] -[{}]".format(
            _text(result.get("model"), "Not recorded"),
            ", ".join(str(value) for value in result.get("positive_evidence_ids", [])),
            ", ".join(str(value) for value in result.get("contradiction_evidence_ids", [])),
        )
        for result in results
        if isinstance(result, Mapping)
    )


def _review_status_summary(statuses: object) -> str:
    if not isinstance(statuses, list):
        return "Not recorded"
    return " | ".join(
        "{}: {}{}".format(
            _text(status.get("model"), "Not recorded"),
            _text(status.get("status"), "Not recorded"),
            " ({})".format(_text(status.get("error_code")))
            if _text(status.get("error_code"))
            else "",
        )
        for status in statuses
        if isinstance(status, Mapping)
    ) or "Not recorded"


def _review_rationales(results: object) -> str:
    if not isinstance(results, list):
        return ""
    return " | ".join(
        "{}: {}".format(
            _text(result.get("model"), "Not recorded"), _text(result.get("rationale"))
        )
        for result in results
        if isinstance(result, Mapping) and _text(result.get("rationale"))
    )


def _summary_rows(
    tickets: Sequence[Mapping[str, object]],
    grouping: Mapping[str, object],
    run_manifest: Mapping[str, object],
) -> list[list[object]]:
    groups = grouping.get("groups", [])
    confirmed = grouping.get("confirmed_pairs", [])
    quarantined = grouping.get("quarantined_pairs", [])
    group_rows = groups if isinstance(groups, list) else []
    confirmed_pairs = confirmed if isinstance(confirmed, list) else []
    quarantined_pairs = quarantined if isinstance(quarantined, list) else []
    all_pairs = [*confirmed_pairs, *quarantined_pairs]
    dual_reviewed_pairs = sum(
        1
        for pair in all_pairs
        if isinstance(pair, Mapping)
        and isinstance(pair.get("reviewer_statuses"), list)
        and len(pair["reviewer_statuses"]) == 2
        and all(
            isinstance(status, Mapping) and _text(status.get("status")) == "completed"
            for status in pair["reviewer_statuses"]
        )
    )
    valid_nonconfirmation_pairs = sum(
        1
        for pair in quarantined_pairs
        if isinstance(pair, Mapping)
        and _text(pair.get("reason")) == "reviewer_non_confirmation"
    )
    return [
        ["Run ID", _text(run_manifest.get("run_id"), "Not recorded")],
        ["Input SHA-256", _text(run_manifest.get("input_sha256"), "Not recorded")],
        ["Pair input policy", PAIR_INPUT_POLICY_VERSION],
        ["Reviewer policy", PAIR_REVIEWER_POLICY_VERSION],
        ["Target ticket population", len(tickets)],
        ["Normalized representatives", len(group_rows)],
        ["Confirmed duplicate groups", sum(1 for group in group_rows if group.get("member_count", 0) > 1)],
        ["Confirmed collapsed members", sum(int(group.get("merged_member_count") or 0) for group in group_rows)],
        ["Pairs with two valid reviews", dual_reviewed_pairs],
        ["Dual-confirmed representative pairs", len(confirmed_pairs)],
        ["Valid non-confirmation pairs", valid_nonconfirmation_pairs],
        ["Incomplete review pairs", len(all_pairs) - dual_reviewed_pairs],
        ["Quarantined representative pairs", len(quarantined_pairs)],
        ["Scope", "Only Follow-up/remediation recorded and direct closure without auditable rationale/follow-up."],
    ]


def _representative_rows(groups: Sequence[Mapping[str, object]]) -> list[list[object]]:
    return [
        [
            _text(group.get("representative_ticket_id")),
            _text(group.get("representative_title")),
            _text(group.get("function_context")),
            _text(group.get("assigned_ecu")),
            _text(group.get("normalization_status")),
            group.get("member_count"),
            group.get("merged_member_count"),
            group.get("worst_risk_score"),
            _text(group.get("worst_risk_tier")),
            _json(group.get("formal_parent_ids", [])),
            _json(group.get("closure_disposition_counts", {})),
            group.get("has_any_follow_up_evidence"),
            _json(group.get("follow_up_evidence", [])),
            _json(group.get("risk_flags", [])),
        ]
        for group in groups
    ]


def _member_rows(member_map: Sequence[Mapping[str, object]]) -> list[list[object]]:
    return [
        [
            _text(member.get("group_id")),
            _text(member.get("ticket_id")),
            _text(member.get("representative_ticket_id")),
            member.get("is_representative"),
            member.get("is_confirmed_member"),
            _text(member.get("closure_disposition")),
            member.get("risk_score"),
            _text(member.get("risk_tier")),
            member.get("has_follow_up_evidence"),
            _text(member.get("follow_up_status")),
        ]
        for member in member_map
    ]


def _pair_rows(pairs: Sequence[Mapping[str, object]], confirmed: bool) -> list[list[object]]:
    return [
        [
            _text(pair.get("pair_id")),
            _text(pair.get("representative_ticket_id")),
            _text(pair.get("member_ticket_id")),
            _text(pair.get("formal_parent_id")),
            _text(pair.get("policy_version")),
            _text(pair.get("input_fingerprint")),
            _json(pair.get("required_evidence_ids", [])),
            _review_summary(pair.get("reviewer_results")),
            _review_status_summary(pair.get("reviewer_statuses")),
            _review_citations(pair.get("reviewer_results")),
            _review_rationales(pair.get("reviewer_results")),
            _text(pair.get("decision" if confirmed else "reason")),
            _text(pair.get("review_policy_version"), PAIR_REVIEWER_POLICY_VERSION),
        ]
        for pair in pairs
    ]


def _function_risk_rows(
    tickets: Sequence[Mapping[str, object]], groups: Sequence[Mapping[str, object]]
) -> list[list[object]]:
    original: dict[str, Counter[str]] = defaultdict(Counter)
    normalized: dict[str, Counter[str]] = defaultdict(Counter)
    function_labels: dict[str, str] = {}
    for ticket in tickets:
        function_name = _function_name(ticket)
        function_key = function_name.casefold()
        function_labels.setdefault(function_key, function_name)
        counter = original[function_key]
        counter["tickets"] += 1
        counter["direct"] += int(
            _text(ticket.get("closure_disposition"))
            == "Direct closure without auditable rationale or follow-up"
        )
        counter["follow_up"] += int(bool(ticket.get("has_follow_up_evidence")))
        counter["max_risk"] = max(counter["max_risk"], _risk_score(ticket))
    for group in groups:
        function_name = _text(group.get("function_context"), "Not recorded")
        function_key = function_name.casefold()
        function_labels.setdefault(function_key, function_name)
        counter = normalized[function_key]
        counter["representatives"] += 1
        counter["collapsed"] += int(group.get("merged_member_count") or 0)
        dispositions = group.get("closure_disposition_counts")
        if isinstance(dispositions, Mapping):
            counter["direct"] += int(
                dispositions.get("Direct closure without auditable rationale or follow-up", 0)
                > 0
            )
        counter["max_risk"] = max(counter["max_risk"], float(group.get("worst_risk_score") or 0))
    rows: list[list[object]] = []
    for function_key in sorted(set(original) | set(normalized)):
        original_counter = original[function_key]
        normalized_counter = normalized[function_key]
        rows.append(
            [
                function_labels[function_key],
                original_counter["tickets"],
                normalized_counter["representatives"],
                normalized_counter["collapsed"],
                original_counter["direct"],
                normalized_counter["direct"],
                original_counter["follow_up"],
                original_counter["max_risk"],
                normalized_counter["max_risk"],
            ]
        )
    return rows


def _representative_evidence_rows(
    tickets_by_id: Mapping[str, Mapping[str, object]],
    groups: Sequence[Mapping[str, object]],
    reviewed_representative_ticket_ids: set[str],
) -> list[list[object]]:
    rows: list[list[object]] = []
    for group in groups:
        ticket_id = _text(group.get("representative_ticket_id"))
        if ticket_id not in reviewed_representative_ticket_ids:
            continue
        ticket = tickets_by_id.get(ticket_id)
        if ticket is None:
            continue
        for evidence_type, field_name in (
            ("Ticket description", "description_full"),
            ("Ticket description", "description"),
            ("Ticket comments", "comments"),
            ("Comment records", "comment_records"),
            ("Closure history", "audit_history_events"),
        ):
            value = ticket.get(field_name)
            if not value or (field_name == "description" and ticket.get("description_full")):
                continue
            rows.append([ticket_id, evidence_type, field_name, _json(value)])
    return rows


def _write_methodology(sheet: Any) -> None:
    _write_table(
        sheet,
        headers=("判定项目", "规则与边界"),
        rows=(
            ("范围", "仅 final tproject=IDCEVO 的当前 Phase 09，且仅保留两种指定关闭处置。"),
            ("候选门槛", "需要正式 Child (Duplicate) 父票关系、相同 ECU、相同功能上下文；标题或语义相似性不构成合并依据。"),
            ("模型审核", "每个成员仅与证据更丰富的代表票比较；deepseek-v4-pro 与 qwen3.7-max 独立审核。"),
            ("确认规则", "只有两位模型均以至少 0.95 信心返回 SAME_UNDERLYING_DEFECT、无矛盾证据并引用全部硬证据时才合并。"),
            ("禁止传递合并", "成员之间的相似性或同一父票不会产生传递性合并；每个成员必须独立通过代表边审核。"),
            ("风险汇总", "组级别取成员中最高风险，并保留全部成员的关闭处置、follow-up 状态和可记录计划字段。"),
            ("证据边界", "06_原始代表证据仅保存代表票证据；成员证据通过成员映射和 pair input fingerprint 回溯。"),
            ("隔离", "模型分歧、非 SAME、缺失或无效结果均保留在 04_隔离候选，绝不自动合并。"),
        ),
        widths=(30, 140),
        table_name="NormalizationMethodology",
    )


def write_phase09_idcevo_issue_normalization_excel(
    *,
    tickets: Sequence[Mapping[str, object]],
    grouping: Mapping[str, object],
    run_manifest: Mapping[str, object],
    output_path: str | Path,
) -> Path:
    ticket_records = [dict(ticket) for ticket in tickets]
    tickets_by_id = {
        _text(ticket.get("ticket_id")): ticket
        for ticket in ticket_records
        if _text(ticket.get("ticket_id"))
    }
    groups = [dict(group) for group in grouping.get("groups", []) if isinstance(group, Mapping)]
    member_map = [dict(member) for member in grouping.get("member_map", []) if isinstance(member, Mapping)]
    confirmed_pairs = [dict(pair) for pair in grouping.get("confirmed_pairs", []) if isinstance(pair, Mapping)]
    quarantined_pairs = [dict(pair) for pair in grouping.get("quarantined_pairs", []) if isinstance(pair, Mapping)]
    reviewed_representative_ticket_ids = {
        _text(pair.get("representative_ticket_id"))
        for pair in [*confirmed_pairs, *quarantined_pairs]
        if _text(pair.get("representative_ticket_id"))
    }

    workbook = Workbook()
    sheets = [workbook.active] + [workbook.create_sheet(name) for name in NORMALIZATION_SHEET_NAMES[1:]]
    for sheet, name in zip(sheets, NORMALIZATION_SHEET_NAMES):
        sheet.title = name
        sheet.sheet_view.showGridLines = False

    _write_table(
        workbook[SUMMARY_SHEET],
        headers=("项目", "值"),
        rows=_summary_rows(ticket_records, grouping, run_manifest),
        widths=(38, 140),
        table_name="NormalizationOverview",
    )
    _write_table(
        workbook[REPRESENTATIVES_SHEET],
        headers=("Representative Ticket ID", "标题", "功能", "ECU", "规范化状态", "成员数", "合并成员数", "最高风险分", "最高风险层级", "Formal parent", "关闭处置分布", "任一跟进证据", "成员跟进证据", "风险标记"),
        rows=_representative_rows(groups),
        widths=(18, 52, 30, 22, 28, 12, 14, 14, 34, 24, 58, 16, 96, 60),
        table_name="NormalizationRepresentatives",
    )
    _write_table(
        workbook[MEMBERS_SHEET],
        headers=("Group ID", "Ticket ID", "Representative Ticket ID", "代表票", "双确认成员", "关闭处置", "风险分", "风险层级", "有跟进证据", "跟进状态"),
        rows=_member_rows(member_map),
        widths=(24, 18, 22, 12, 14, 50, 12, 34, 16, 52),
        table_name="NormalizationMembers",
    )
    pair_headers = ("Pair ID", "Representative", "Member", "Formal parent", "Policy", "Input fingerprint", "Required evidence IDs", "Independent reviewer results", "Reviewer record status", "Evidence citations", "Rationales", "Decision / quarantine reason", "Reviewer policy")
    _write_table(
        workbook[CONFIRMED_SHEET],
        headers=pair_headers,
        rows=_pair_rows(confirmed_pairs, confirmed=True),
        widths=(24, 18, 18, 20, 28, 68, 66, 78, 54, 100, 100, 30, 34),
        table_name="ConfirmedDuplicatePairs",
    )
    _write_table(
        workbook[QUARANTINED_SHEET],
        headers=pair_headers,
        rows=_pair_rows(quarantined_pairs, confirmed=False),
        widths=(24, 18, 18, 20, 28, 68, 66, 78, 54, 100, 100, 30, 34),
        table_name="QuarantinedDuplicatePairs",
    )
    _write_table(
        workbook[FUNCTION_RISK_SHEET],
        headers=("功能", "原始 Ticket 数", "归一化代表票数", "确认压缩成员数", "原始无证据直关数", "归一化含直关代表组数", "原始有跟进证据数", "原始最高风险分", "归一化最高风险分"),
        rows=_function_risk_rows(ticket_records, groups),
        widths=(42, 16, 20, 18, 22, 26, 20, 18, 20),
        table_name="NormalizedFunctionRisk",
    )
    _write_table(
        workbook[EVIDENCE_SHEET],
        headers=("Representative Ticket ID", "证据类型", "来源字段", "原始证据"),
        rows=_representative_evidence_rows(
            tickets_by_id, groups, reviewed_representative_ticket_ids
        ),
        widths=(22, 22, 28, 130),
        table_name="RepresentativeOriginalEvidence",
    )
    _write_methodology(workbook[METHODOLOGY_SHEET])

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    workbook.close()
    return resolved_output_path


def _read_jsonl(path: str | Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}") from error
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record at {path}:{line_number} must be an object")
        records.append(record)
    return records


def generate_phase09_idcevo_issue_normalization_excel(
    *,
    pair_inputs_path: str | Path,
    review_cache_path: str | Path,
    run_id: str,
    output_root: str | Path = "exports/idcevo_phase09/normalization_runs",
    source_snapshot_path: str | Path | None = None,
    db_path: str | Path | None = None,
    include_china_specific: bool = True,
) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, digits, underscores, and hyphens")
    source_snapshot_bytes = b""
    if source_snapshot_path is not None:
        source_snapshot_bytes = Path(source_snapshot_path).read_bytes()
        try:
            snapshot = json.loads(source_snapshot_bytes)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid source snapshot JSON: {source_snapshot_path}") from error
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("tickets"), list):
            raise ValueError("Source snapshot must contain a tickets array")
        payload = snapshot
    else:
        payload = build_phase09_idcevo_report_payload(
            db_path=db_path,
            include_china_specific=include_china_specific,
            include_full_evidence=True,
        )
    tickets = [
        ticket
        for ticket in payload.get("tickets", [])
        if isinstance(ticket, Mapping)
        and ticket.get("closure_disposition") in TARGET_CLOSURE_DISPOSITIONS
    ]
    pair_inputs = _read_jsonl(pair_inputs_path)
    review_records = _read_jsonl(review_cache_path)
    grouping = build_confirmed_duplicate_groups(tickets, pair_inputs, review_records)
    pair_input_bytes = Path(pair_inputs_path).read_bytes()
    return write_phase09_idcevo_issue_normalization_excel(
        tickets=tickets,
        grouping=grouping,
        run_manifest={
            "run_id": run_id,
            "input_sha256": sha256(pair_input_bytes).hexdigest(),
            "source_snapshot_sha256": (
                sha256(source_snapshot_bytes).hexdigest()
                if source_snapshot_bytes
                else ""
            ),
            "source_snapshot_path": str(source_snapshot_path or ""),
            "source": payload.get("source", {}),
            "review_cache_path": str(review_cache_path),
        },
        output_path=Path(output_root) / run_id / "IDCEVO_Phase09_Duplicate_Normalization.xlsx",
    )