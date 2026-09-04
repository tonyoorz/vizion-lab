#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IDC-EVO Phase 09 (Concluded without action) Ticket Analysis
=============================================================
Analyzes all IDC-EVO project tickets with phase=09 ("Concluded without action"),
excluding CoC development teams. Classifies rejected tickets into:
  - "Acceptable reject" (has valid technical blocking_reason)
  - "Unresolved closure" (no blocking_reason or Management decision — no solution,
    no fix version, no deferred date — just closed)

For unresolved closures, performs deep analysis:
  - Functional area grouping (solution_cluster / fv / defect_category)
  - Risk level assessment (severity + reporting_class)
  - Customer escape risk analysis
  - Per-ticket detailed AI analysis written into Excel

Output: Excel file with multiple sheets, ready for re-feeding to AI tools.

Usage:
  .venv/Scripts/python.exe scripts/analyze_idc_evo_rejected_tickets.py
"""

import sqlite3
import json
import re
import os
from datetime import datetime
from collections import Counter, defaultdict

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ============================================================================
# CONFIG
# ============================================================================

DB_PATH = r"C:\Users\q446328\Desktop\vizion-lab\database\source\qgate_raw.db"
OUTPUT_DIR = r"C:\Users\q446328\Desktop\vizion-lab\scripts"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "IDC_EVO_Phase09_Rejected_Ticket_Analysis.xlsx")

PROJECT = "IDCEVO"
PHASE_PREFIX = "09"

# CoC teams to exclude (user: "不用分析coc那些开发团队的票")
# Match any team containing "CoC" (case-insensitive)
COC_EXCLUDE_PATTERN = re.compile(r"coc", re.IGNORECASE)

# ============================================================================
# BLOCKING REASON CLASSIFICATION
# ============================================================================

# "Acceptable reject" — the ticket was rejected/closed with a valid technical
# justification. The defect either doesn't need fixing or has a documented
# reason that is technically sound.
ACCEPTABLE_BLOCKING_REASONS = {
    "Not reproducible",              # Can't reproduce → can't fix
    "Expected behaviour",            # Working as designed
    "User Error",                    # Tester mistake
    "Invalid Testcase",              # Test case itself is wrong
    "Child (Duplicate)",            # Duplicate of another ticket
    "Insufficient Defect Quality",   # Defect report quality too low to act
    "Measuring Tool Issue",          # Test tooling problem, not product
    "CR - conceptional issue",       # Conceptional issue handled via CR process
    "Function not implemented/testable yet",  # Feature not yet available
    "Additional Information necessary",        # Need more info (but may reopen)
    "Further traces necessary",                 # Need more traces (but may reopen)
}

# "Unresolved closure" — ticket was closed without a technical resolution.
# No fix version, no solution, no deferred date — just administratively closed.
UNRESOLVED_BLOCKING_REASONS = {
    "Management decision",           # Management decided to close without fix
    "Tolerated",                     # Known issue, tolerated but not fixed
    "PQM Transfer",                  # Transferred to PQM (quality management)
    "Defect handling in supplier system",  # Handed off to supplier, unclear resolution
}

# Empty blocking_reason — these are the most concerning: closed without ANY reason
# classified as unresolved


# ============================================================================
# RISK SCORING
# ============================================================================

SEVERITY_RANK = {
    "Critical": 5,
    "Very High": 4,
    "High": 3,
    "Medium": 2,
    "Low": 1,
    "": 0,
}

# reporting_class risk weights — higher = more dangerous if left unresolved
REPORTING_CLASS_RISK = {
    "Showstopper_Confirmed": 10,
    "Showstopper_Candidate": 8,
    "Top_Issue_Confirmed": 8,
    "Homologation L-labelled": 7,
    "Preventing Maturity Grade Customer Function": 7,
    "Preventing Maturity Grade ExtInt": 6,
    "Preventing Maturity Grade ConDrive": 6,
    "Preventing Maturity Grade System Function": 6,
    "Preventing Maturity Grade EnBN": 6,
    "Preventing Maturity Grade Powertrain": 6,
    "Preventing General Testing": 5,
    "Preventing Maturity Grade SI": 5,
    "Obstructing Maturity Grade ExtInt": 4,
    "Obstructing Maturity Grade ConDrive": 4,
    "Obstructing Maturity Grade System Function": 4,
    "No_Showstopper_Candidate": 3,
    "No_Showstopper_Confirmed": 3,
    "No Top Issue ExtInt": 2,
    "Escalation ConDrive": 5,
    "Escalation General Testing": 4,
    "Buildability SOP": 6,
    "Buildability affected": 5,
    "Not Homologation relevant": 1,
}


def compute_risk_score(severity, reporting_class):
    """Compute risk score 1-10 for an unresolved ticket."""
    sev_score = SEVERITY_RANK.get(severity or "", 0) * 1.5
    rc_score = REPORTING_CLASS_RISK.get(reporting_class or "", 0)

    base = sev_score + rc_score

    # Normalize to 1-10 scale
    # Max possible: severity 5*1.5=7.5 + reporting_class 10 = 17.5
    if base >= 14:
        return 10, "Critical"
    elif base >= 10:
        return 9, "Very High"
    elif base >= 7:
        return 7, "High"
    elif base >= 5:
        return 5, "Medium"
    elif base >= 3:
        return 3, "Low"
    else:
        return 2, "Low"


# ============================================================================
# DESCRIPTION PARSING (extract structured info from HTML description)
# ============================================================================

def strip_html(text):
    """Strip HTML tags and decode common entities."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?div[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?strong[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?u[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?body[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?html[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_description_field(description, field_patterns):
    """Extract a field value from the description text by label patterns."""
    if not description:
        return ""
    text = strip_html(description)
    for pattern in field_patterns:
        match = re.search(
            pattern + r"\s*[:：]?\s*(.+?)(?=\n\n|\n[a-zA-Z].*?[:：]|\Z)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group(1).strip()
    return ""


def parse_description_fields(description):
    """Parse structured fields from the defect description HTML."""
    fields = {}

    fields["observed_behaviour"] = extract_description_field(
        description,
        [r"observed result/behaviour", r"observed behaviour", r"observed result"],
    )
    fields["expected_behaviour"] = extract_description_field(
        description,
        [r"expected result/behaviour", r"expected behaviour", r"expected result"],
    )
    fields["possible_impact"] = extract_description_field(
        description, [r"possible impact", r"impact"]
    )
    fields["preconditions"] = extract_description_field(
        description, [r"preconditions and actions", r"preconditions", r"precondition"]
    )
    fields["vin_in_desc"] = ""
    if description:
        vin_match = re.search(
            r"(?:VIN|Vin|vin)\s*[:：]?\s*([A-Z0-9]{10,20})", description
        )
        if vin_match:
            fields["vin_in_desc"] = vin_match.group(1)
        else:
            # Try to find VIN pattern in stripped text
            text = strip_html(description)
            vin_match = re.search(r"\b([A-Z0-9]{17})\b", text)
            if vin_match:
                fields["vin_in_desc"] = vin_match.group(1)

    return fields


# ============================================================================
# COMMENT PARSING
# ============================================================================

def parse_comments(comments_json):
    """Parse comments JSON and extract human-readable comment texts."""
    if not comments_json or comments_json.strip() == "[]":
        return []

    try:
        comments = json.loads(comments_json)
    except (json.JSONDecodeError, TypeError):
        return []

    parsed = []
    for c in comments:
        text = c.get("text", "")
        author = c.get("author", c.get("author.full_name", ""))
        creation = c.get("creation_time", "")
        text = strip_html(text)
        if text:
            parsed.append(
                {"author": author, "time": creation, "text": text}
            )
    return parsed


def extract_close_reason_from_comments(comments_parsed):
    """Try to extract the closure reason from human comments."""
    if not comments_parsed:
        return ""

    # Look at last few comments for closure reason
    for c in comments_parsed[-5:]:
        text_lower = c["text"].lower()
        if any(
            kw in text_lower
            for kw in [
                "close",
                "closed",
                "reject",
                "管理",
                "关闭",
                "拒绝",
                "management decision",
                "md ",
                "not repro",
                "not fix",
                "won't fix",
                "wontfix",
                "tolerate",
                "defer",
                "shift",
            ]
        ):
            return c["text"][:300]

    return ""


# ============================================================================
# PER-TICKET AI ANALYSIS
# ============================================================================

def analyze_ticket(record, desc_fields, comments_parsed):
    """
    Perform detailed analysis for a single unresolved ticket.
    Returns a structured analysis string explaining what happened and why the
    risk level was assigned.
    """
    parts = []

    defect_id = record.get("defect_id", "")
    name = record.get("name", "")
    severity = record.get("severity", "")
    blocking_reason = record.get("blocking_reason", "") or "(empty)"
    reporting_class = record.get("reporting_class", "") or "(empty)"
    solution_cluster = record.get("solution_cluster", "") or "(empty)"
    fv = record.get("fv", "") or "(empty)"
    defect_category = record.get("defect_category", "") or "(empty)"
    pu = record.get("pu", "") or "(empty)"
    detected_in_release = record.get("detected_in_release", "") or "(empty)"
    software_version = record.get("software_version", "") or "(empty)"
    assigned_ecu = record.get("assigned_ecu", "") or "(empty)"
    lead_model = record.get("lead_model", "") or "(empty)"
    team = record.get("team", "")
    detected_by = record.get("detected_by", "")

    risk_score, risk_level = compute_risk_score(severity, reporting_class)

    # --- Section 1: Ticket Summary ---
    parts.append(f"【Ticket {defect_id} 深度分析】")
    parts.append(f"")
    parts.append(f"1. 问题描述:")
    parts.append(f"   标题: {name}")
    parts.append(f"   功能模块: {solution_cluster}")
    parts.append(f"   功能版本(FV): {fv}")
    parts.append(f"   缺陷类别: {defect_category}")
    parts.append(f"   严重程度: {severity}")
    parts.append(f"   报告等级(Reporting Class): {reporting_class}")
    parts.append(f"   ECU: {assigned_ecu}")
    parts.append(f"   车型: {lead_model}")
    parts.append(f"   PU/I-Step: {pu}")
    parts.append(f"   检测版本: {detected_in_release}")
    parts.append(f"   软件版本: {software_version}")
    parts.append(f"   发现团队: {team}")
    parts.append(f"   发现者: {detected_by}")
    parts.append(f"")

    # --- Section 2: Closure Analysis ---
    parts.append(f"2. 关闭处置分析:")
    parts.append(f"   关闭阶段: 09-Concluded without action (无动作关闭)")
    parts.append(f"   Blocking Reason: {blocking_reason}")

    if blocking_reason == "(empty)":
        parts.append(f"   ⚠ 该ticket被关闭但未填写任何 blocking_reason！")
        parts.append(f"   ⚠ 这意味着没有任何技术理由记录为何关闭此缺陷。")
        parts.append(f"   ⚠ 这是最高风险的情况——问题可能被忽略而非真正解决。")
    elif blocking_reason == "Management decision":
        parts.append(f"   ⚠ 管理层决定关闭，通常没有技术修复方案。")
        parts.append(f"   ⚠ 问题可能因成本/进度原因被接受，而非技术解决。")
    elif blocking_reason == "Tolerated":
        parts.append(f"   ⚠ 该问题被容忍(Tolerated)，已知的缺陷被接受但未修复。")
        parts.append(f"   ⚠ 客户可能会感知到此问题。")
    elif blocking_reason == "PQM Transfer":
        parts.append(f"   ⚠ 转移至PQM(产品质量管理)，后续处理不透明。")
    elif blocking_reason == "Defect handling in supplier system":
        parts.append(f"   ⚠ 移交供应商系统处理，修复状态不透明。")

    # Check for fix version / deferred date
    fv_val = record.get("fv", "") or ""
    tqr = record.get("tqr", "") or ""
    has_fix_version = bool(fv_val and fv_val.strip())
    has_tqr = bool(tqr and tqr.strip() and tqr.strip() != "{}")

    parts.append(f"   修复版本(FV): {'有' if has_fix_version else '❌ 无'} ({fv_val or '空'})")
    parts.append(f"   测试限定结果(TQR): {'有' if has_tqr else '❌ 无'}")
    parts.append(f"   推迟日期/Shift PU: ❌ 无 (无推迟字段)")

    # Close reason from comments
    close_reason = extract_close_reason_from_comments(comments_parsed)
    if close_reason:
        parts.append(f"   关闭评论摘要: {close_reason[:200]}")
    else:
        parts.append(f"   关闭评论摘要: (无明确关闭说明)")
    parts.append(f"")

    # --- Section 3: Risk Assessment ---
    parts.append(f"3. 风险评估:")
    parts.append(f"   风险评分: {risk_score}/10")
    parts.append(f"   风险等级: {risk_level}")

    risk_reasons = []
    if severity in ("Critical", "Very High", "High"):
        risk_reasons.append(f"严重程度为 {severity}，属于高严重度缺陷")
    if reporting_class == "Showstopper_Confirmed":
        risk_reasons.append("已确认为 Showstopper——影响系统关键功能")
    elif reporting_class == "Showstopper_Candidate":
        risk_reasons.append("Showstopper 候选——可能影响系统关键功能")
    elif reporting_class and "Preventing" in reporting_class:
        risk_reasons.append(f"报告等级为 '{reporting_class}'——阻碍成熟度等级达成")
    elif reporting_class == "Homologation L-labelled":
        risk_reasons.append("标注为 Homologation L-labelled——涉及法规认证相关")
    if blocking_reason == "(empty)":
        risk_reasons.append("无 blocking_reason——关闭理由完全不透明")
    if not has_fix_version:
        risk_reasons.append("无修复版本——问题未被计划修复")
    if not has_tqr:
        risk_reasons.append("无测试限定结果——没有验证记录")

    if risk_reasons:
        parts.append(f"   风险因素:")
        for r in risk_reasons:
            parts.append(f"   • {r}")
    else:
        parts.append(f"   风险因素: 严重度和报告等级较低，风险可控")
    parts.append(f"")

    # --- Section 4: Customer Escape Risk ---
    parts.append(f"4. 客户逃逸风险分析:")

    escape_risk = "Low"
    escape_reasons = []

    # Check description for customer-facing keywords
    observed = desc_fields.get("observed_behaviour", "").lower()
    impact = desc_fields.get("possible_impact", "").lower()
    name_lower = name.lower()

    customer_keywords = [
        "customer",
        "kunde",
        "客户",
        "用户",
        "complain",
        "投诉",
        "safety",
        "安全",
        "crash",
        "崩溃",
        "black screen",
        "黑屏",
        "yellow screen",
        "黄屏",
        "no navigation",
        "导航不可用",
        "no sound",
        "无声",
        "no audio",
        "mute",
        "静音",
        "freeze",
        "卡死",
        "hang",
        "挂起",
        "reboot",
        "重启",
        "reset",
        "复位",
        "can't start",
        "无法启动",
        "fail to",
        "失败",
    ]

    matched_keywords = []
    combined_text = f"{name_lower} {observed} {impact}"
    for kw in customer_keywords:
        if kw in combined_text:
            matched_keywords.append(kw)

    if matched_keywords:
        escape_reasons.append(
            f"描述中包含客户影响关键词: {', '.join(matched_keywords[:5])}"
        )

    if severity in ("Critical", "Very High"):
        escape_risk = "Very High"
        escape_reasons.append(f"严重程度为 {severity}——极端影响")
    elif severity == "High":
        escape_risk = "High"
        escape_reasons.append("严重程度为 High——高影响")
    elif severity == "Medium" and matched_keywords:
        escape_risk = "High"

    if reporting_class == "Showstopper_Confirmed":
        escape_risk = "Very High"
        escape_reasons.append("Showstopper 已确认——系统关键功能受影响")
    elif reporting_class == "Showstopper_Candidate":
        if escape_risk in ("Low", "Medium"):
            escape_risk = "High"
        escape_reasons.append("Showstopper 候选——潜在关键功能影响")
    elif "Homologation" in (reporting_class or ""):
        if escape_risk == "Low":
            escape_risk = "Medium"
        escape_reasons.append("涉及法规认证——可能影响车型上市")

    # Check if detected in production release
    if detected_in_release and "R-2" in detected_in_release:
        escape_reasons.append(f"在生产版本 {detected_in_release} 中检测到")
        if escape_risk == "Low":
            escape_risk = "Medium"

    if blocking_reason == "(empty)":
        escape_reasons.append("无关闭原因——问题可能未被充分分析")

    if escape_reasons:
        parts.append(f"   客户逃逸风险: {escape_risk}")
        for r in escape_reasons:
            parts.append(f"   • {r}")
    else:
        parts.append(f"   客户逃逸风险: {escape_risk}")
        parts.append(f"   • 严重程度和报告等级较低，客户感知风险有限")
    parts.append(f"")

    # --- Section 5: Description Summary ---
    parts.append(f"5. 缺陷描述摘要:")
    observed = desc_fields.get("observed_behaviour", "")
    expected = desc_fields.get("expected_behaviour", "")
    impact = desc_fields.get("possible_impact", "")

    if observed:
        parts.append(f"   观察到的行为: {observed[:300]}")
    else:
        parts.append(f"   观察到的行为: (描述中未提取到)")
    if expected:
        parts.append(f"   预期行为: {expected[:200]}")
    if impact:
        parts.append(f"   可能影响: {impact[:200]}")

    parts.append(f"")
    parts.append(f"---")

    return "\n".join(parts), risk_score, risk_level, escape_risk


# ============================================================================
# FUNCTIONAL AREA RISK AGGREGATION
# ============================================================================

def aggregate_functional_risk(tickets):
    """Aggregate risk by functional area (solution_cluster)."""
    area_stats = defaultdict(
        lambda: {
            "total": 0,
            "by_severity": Counter(),
            "by_reporting_class": Counter(),
            "by_blocking_reason": Counter(),
            "high_risk_count": 0,
            "very_high_risk_count": 0,
            "critical_risk_count": 0,
            "customer_escape_high": 0,
            "ticket_ids": [],
        }
    )

    for t in tickets:
        area = t.get("solution_cluster", "") or "(unassigned)"
        stats = area_stats[area]
        stats["total"] += 1
        stats["by_severity"][t.get("severity", "")] += 1
        stats["by_reporting_class"][t.get("reporting_class", "")] += 1
        br = t.get("blocking_reason", "") or "(empty)"
        stats["by_blocking_reason"][br] += 1

        risk_level = t.get("_risk_level", "")
        if risk_level == "Critical":
            stats["critical_risk_count"] += 1
        elif risk_level == "Very High":
            stats["very_high_risk_count"] += 1
        elif risk_level == "High":
            stats["high_risk_count"] += 1

        escape = t.get("_escape_risk", "")
        if escape in ("High", "Very High"):
            stats["customer_escape_high"] += 1

        stats["ticket_ids"].append(t["defect_id"])

    return area_stats


def assess_functional_area_risk(area, stats):
    """Assess overall risk for a functional area and return narrative.

    Uses proportional metrics (ratios) alongside absolute counts to ensure
    differentiation between large and small functional areas.
    """
    total = stats["total"]
    high_count = stats["high_risk_count"]
    very_high_count = stats["very_high_risk_count"]
    critical_count = stats["critical_risk_count"]
    escape_high = stats["customer_escape_high"]

    high_sev = stats["by_severity"].get("High", 0) + stats["by_severity"].get(
        "Very High", 0
    ) + stats["by_severity"].get("Critical", 0)

    showstoppers = sum(
        v
        for k, v in stats["by_reporting_class"].items()
        if k and ("Showstopper" in k or "Top_Issue" in k)
    )

    homologation = sum(
        v
        for k, v in stats["by_reporting_class"].items()
        if k and "Homologation" in k
    )

    # Ratios (proportional to total) — more discriminative than raw counts
    showstopper_ratio = showstoppers / total if total > 0 else 0
    escape_ratio = escape_high / total if total > 0 else 0
    high_sev_ratio = high_sev / total if total > 0 else 0

    # Risk score calculation (0-10 scale, more discriminative thresholds)
    risk_score = 0
    risk_factors = []

    # Critical individual tickets — absolute count matters here
    if critical_count > 0:
        risk_score += 3
        risk_factors.append(f"有 {critical_count} 个 Critical 风险等级的 ticket")
    if very_high_count > 10 or (very_high_count > 0 and very_high_count / total > 0.1):
        risk_score += 2
        risk_factors.append(f"有 {very_high_count} 个 Very High 风险等级的 ticket")
    elif very_high_count > 0:
        risk_score += 1
        risk_factors.append(f"有 {very_high_count} 个 Very High 风险等级的 ticket")
    if high_count > 10:
        risk_score += 1
        risk_factors.append(f"有 {high_count} 个 High 风险等级的 ticket")

    # Showstopper ratio — proportional, not just absolute
    if showstopper_ratio > 0.15:
        risk_score += 2
        risk_factors.append(
            f"有 {showstoppers} 个 Showstopper/Top Issue ticket (占比 {showstopper_ratio*100:.1f}%)"
        )
    elif showstopper_ratio > 0.05 or showstoppers > 10:
        risk_score += 1
        risk_factors.append(
            f"有 {showstoppers} 个 Showstopper/Top Issue ticket (占比 {showstopper_ratio*100:.1f}%)"
        )

    # Homologation ratio
    if homologation > 0:
        hom_ratio = homologation / total
        if hom_ratio > 0.1:
            risk_score += 2
        else:
            risk_score += 1
        risk_factors.append(
            f"有 {homologation} 个法规认证(Homologation)相关 ticket (占比 {hom_ratio*100:.1f}%)"
        )

    # Customer escape risk
    if escape_ratio > 0.4:
        risk_score += 2
        risk_factors.append(
            f"有 {escape_high} 个客户逃逸高风险 ticket (占比 {escape_ratio*100:.1f}%)"
        )
    elif escape_ratio > 0.2 or escape_high > 20:
        risk_score += 1
        risk_factors.append(
            f"有 {escape_high} 个客户逃逸高风险 ticket (占比 {escape_ratio*100:.1f}%)"
        )

    # High severity ratio
    if high_sev_ratio > 0.3:
        risk_score += 1
        risk_factors.append(
            f"有 {high_sev} 个 High+ 严重度的 ticket (占比 {high_sev_ratio*100:.1f}%)"
        )

    # Scale cap at 10
    risk_score = min(risk_score, 10)

    if risk_score >= 8:
        area_risk = "Critical"
    elif risk_score >= 6:
        area_risk = "Very High"
    elif risk_score >= 4:
        area_risk = "High"
    elif risk_score >= 2:
        area_risk = "Medium"
    else:
        area_risk = "Low"

    return area_risk, risk_score, risk_factors


# ============================================================================
# MAIN
# ============================================================================

def main():
    print(f"[{datetime.now()}] Starting IDC-EVO Phase 09 ticket analysis...")
    print(f"  DB: {DB_PATH}")
    print(f"  Project: {PROJECT}")
    print(f"  Phase: {PHASE_PREFIX}*")
    print(f"  Excluding teams matching: CoC (case-insensitive)")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query all IDC-EVO phase 09 tickets, excluding CoC teams
    print(f"\n[{datetime.now()}] Querying tickets from database...")
    query = """
        SELECT defect_id, name, description, creation_time, last_modified,
               team, problem_finder_team, program, author,
               aida_businesskey, aida_english, top_aida,
               product_areas, user_tags, phase, severity, problem_severity,
               status_phase, owner, detected_by, detected_in_release,
               software_version, vin, assigned_ecu, lead_model,
               ecu_no_of_changes, parent_id, parent_child_type, relation_to,
               test_week, risk_score, fv, fvp, tqr, blocking_reason,
               error_occurrence, solution_responsible, solution_cluster,
               reporting_class, function_responsible, involved_i_step,
               first_use_sop_of_function, tolerated_count, reprel_changes,
               project, tproject, market, pu, ecu_to_modul, function2modul,
               model_series, defect_category, requirement, comments
        FROM octane_defects
        WHERE project = ? AND phase LIKE ?
          AND team NOT LIKE '%CoC%'
          AND team NOT LIKE '%coc%'
    """
    cursor.execute(query, (PROJECT, PHASE_PREFIX + "%"))
    rows = cursor.fetchall()
    print(f"  Total tickets found: {len(rows)}")

    # Convert to list of dicts
    all_tickets = [dict(r) for r in rows]

    # Classify tickets
    print(f"\n[{datetime.now()}] Classifying tickets...")

    acceptable_rejects = []
    unresolved_closures = []
    other_closures = []

    for t in all_tickets:
        br = t.get("blocking_reason", "") or ""

        if br in ACCEPTABLE_BLOCKING_REASONS:
            t["_classification"] = "Acceptable Reject"
            acceptable_rejects.append(t)
        elif br in UNRESOLVED_BLOCKING_REASONS or br == "":
            t["_classification"] = "Unresolved Closure"
            unresolved_closures.append(t)
        else:
            t["_classification"] = "Other Closure"
            other_closures.append(t)

    print(f"  Acceptable Rejects: {len(acceptable_rejects)}")
    print(f"  Unresolved Closures: {len(unresolved_closures)}")
    print(f"  Other Closures: {len(other_closures)}")

    # Deep analysis for unresolved closures
    print(f"\n[{datetime.now()}] Performing deep analysis on {len(unresolved_closures)} unresolved tickets...")

    for i, t in enumerate(unresolved_closures):
        if (i + 1) % 5000 == 0:
            print(f"  Processing {i+1}/{len(unresolved_closures)}...")

        desc_fields = parse_description_fields(t.get("description", ""))
        comments_parsed = parse_comments(t.get("comments", ""))

        analysis_text, risk_score, risk_level, escape_risk = analyze_ticket(
            t, desc_fields, comments_parsed
        )

        t["_analysis_text"] = analysis_text
        t["_risk_score"] = risk_score
        t["_risk_level"] = risk_level
        t["_escape_risk"] = escape_risk
        t["_observed_behaviour"] = desc_fields.get("observed_behaviour", "")
        t["_expected_behaviour"] = desc_fields.get("expected_behaviour", "")
        t["_possible_impact"] = desc_fields.get("possible_impact", "")
        t["_close_reason_comment"] = extract_close_reason_from_comments(
            comments_parsed
        )

    # Aggregate by functional area
    print(f"\n[{datetime.now()}] Aggregating risk by functional area...")
    area_stats = aggregate_functional_risk(unresolved_closures)

    # Build functional area risk summary
    area_results = []
    for area, stats in sorted(
        area_stats.items(), key=lambda x: x[1]["total"], reverse=True
    ):
        area_risk, risk_score, risk_factors = assess_functional_area_risk(
            area, stats
        )
        area_results.append(
            {
                "functional_area": area,
                "total_tickets": stats["total"],
                "high_risk_tickets": stats["high_risk_count"],
                "very_high_risk_tickets": stats["very_high_risk_count"],
                "critical_risk_tickets": stats["critical_risk_count"],
                "customer_escape_high": stats["customer_escape_high"],
                "showstopper_count": sum(
                    v
                    for k, v in stats["by_reporting_class"].items()
                    if k and ("Showstopper" in k or "Top_Issue" in k)
                ),
                "homologation_count": sum(
                    v
                    for k, v in stats["by_reporting_class"].items()
                    if k and "Homologation" in k
                ),
                "area_risk_level": area_risk,
                "area_risk_score": risk_score,
                "risk_factors": "; ".join(risk_factors),
                "severity_breakdown": "; ".join(
                    f"{k}:{v}" for k, v in stats["by_severity"].most_common()
                ),
                "blocking_reason_breakdown": "; ".join(
                    f"{k}:{v}" for k, v in stats["by_blocking_reason"].most_common(5)
                ),
                "ticket_ids": ",".join(stats["ticket_ids"][:50]),
            }
        )

    # Generate Excel
    print(f"\n[{datetime.now()}] Generating Excel report...")
    generate_excel(
        all_tickets,
        acceptable_rejects,
        unresolved_closures,
        other_closures,
        area_results,
    )

    conn.close()
    print(f"\n[{datetime.now()}] Analysis complete!")
    print(f"  Output: {OUTPUT_FILE}")


# ============================================================================
# EXCEL GENERATION
# ============================================================================

def generate_excel(
    all_tickets, acceptable_rejects, unresolved_closures, other_closures, area_results
):
    """Generate multi-sheet Excel report."""

    wb = openpyxl.Workbook()

    # Styles
    header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True
    )
    cell_alignment = Alignment(vertical="top", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Risk level colors
    risk_fills = {
        "Critical": PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid"),
        "Very High": PatternFill(start_color="FF6347", end_color="FF6347", fill_type="solid"),
        "High": PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid"),
        "Medium": PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid"),
        "Low": PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid"),
    }

    # === Sheet 1: Executive Summary ===
    ws1 = wb.active
    ws1.title = "执行摘要"

    ws1["A1"] = "IDC-EVO Phase 09 (无动作关闭) Ticket 深度分析报告"
    ws1["A1"].font = Font(name="Arial", bold=True, size=14)
    ws1.merge_cells("A1:F1")

    ws1["A3"] = "生成时间"
    ws1["B3"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws1["A4"] = "项目"
    ws1["B4"] = "IDC-EVO"
    ws1["A5"] = "阶段"
    ws1["B5"] = "09-Concluded without action (无动作关闭)"
    ws1["A6"] = "排除团队"
    ws1["B6"] = "所有 CoC 团队 (CoC_EI_IuK, DIPS_CoC_BON_Apps, IuK_CoC_*, etc.)"
    ws1["A7"] = "数据源"
    ws1["B7"] = "vizion-lab/database/source/qgate_raw.db (SQLite)"

    # Summary stats
    ws1["A9"] = "统计摘要"
    ws1["A9"].font = Font(name="Arial", bold=True, size=12)
    ws1.merge_cells("A9:F9")

    summary_data = [
        ("总 Ticket 数 (IDC-EVO, Phase 09, 排除CoC)", len(all_tickets)),
        ("正向可接受 Reject (有合理 blocking_reason)", len(acceptable_rejects)),
        ("❌ 未解决直接关闭 (无 blocking_reason 或 Management decision)", len(unresolved_closures)),
        ("其他关闭", len(other_closures)),
        ("", ""),
        ("未解决 Ticket - Critical 风险", sum(1 for t in unresolved_closures if t.get("_risk_level") == "Critical")),
        ("未解决 Ticket - Very High 风险", sum(1 for t in unresolved_closures if t.get("_risk_level") == "Very High")),
        ("未解决 Ticket - High 风险", sum(1 for t in unresolved_closures if t.get("_risk_level") == "High")),
        ("未解决 Ticket - Medium 风险", sum(1 for t in unresolved_closures if t.get("_risk_level") == "Medium")),
        ("未解决 Ticket - Low 风险", sum(1 for t in unresolved_closures if t.get("_risk_level") == "Low")),
        ("", ""),
        ("客户逃逸风险 Very High", sum(1 for t in unresolved_closures if t.get("_escape_risk") == "Very High")),
        ("客户逃逸风险 High", sum(1 for t in unresolved_closures if t.get("_escape_risk") == "High")),
        ("", ""),
        ("功能区域总数", len(area_results)),
        ("Critical 风险功能区域", sum(1 for a in area_results if a["area_risk_level"] == "Critical")),
        ("Very High 风险功能区域", sum(1 for a in area_results if a["area_risk_level"] == "Very High")),
        ("High 风险功能区域", sum(1 for a in area_results if a["area_risk_level"] == "High")),
    ]

    row = 10
    for label, value in summary_data:
        ws1.cell(row=row, column=1, value=label).font = Font(name="Arial", bold=bool(label))
        ws1.cell(row=row, column=2, value=value)
        row += 1

    # Key findings
    row += 1
    ws1.cell(row=row, column=1, value="关键发现").font = Font(name="Arial", bold=True, size=12)
    row += 1

    findings = [
        f"1. IDC-EVO 项目 Phase 09 共有 {len(all_tickets)} 条被拒/无动作关闭的 ticket（排除CoC团队）。",
        f"2. 其中 {len(unresolved_closures)} 条 ({len(unresolved_closures)/len(all_tickets)*100:.1f}%) 是未解决直接关闭——无 blocking_reason 或 Management decision 关闭。",
        f"3. 这些未解决的 ticket 中，blocking_reason 为空的有 {sum(1 for t in unresolved_closures if not t.get('blocking_reason',''))} 条，Management decision 有 {sum(1 for t in unresolved_closures if t.get('blocking_reason','')=='Management decision')} 条。",
        f"4. 未解决 ticket 中有 {sum(1 for t in unresolved_closures if t.get('_risk_level') in ('Critical','Very High','High'))} 条被评估为 High 及以上风险等级。",
        f"5. 有 {sum(1 for t in unresolved_closures if t.get('_escape_risk') in ('High','Very High'))} 条存在高客户逃逸风险——这些问题可能被客户感知。",
        f"6. 在 {len(area_results)} 个功能区域中，{sum(1 for a in area_results if a['area_risk_level'] in ('Critical','Very High','High'))} 个区域被评估为 High 及以上风险。",
        "7. 最大风险在于: 无 blocking_reason 直接关闭的 ticket 占比极高，说明大量缺陷的关闭理由不透明，可能存在未充分分析的技术问题。",
        "8. 建议: 优先审查 Critical/Very High 风险的 ticket，特别是 Showstopper 和 Homologation 相关的缺陷。",
    ]
    for finding in findings:
        cell = ws1.cell(row=row, column=1, value=finding)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws1.row_dimensions[row].height = 30
        row += 1

    # Column widths
    ws1.column_dimensions["A"].width = 50
    ws1.column_dimensions["B"].width = 20
    ws1.column_dimensions["C"].width = 20
    ws1.column_dimensions["D"].width = 20
    ws1.column_dimensions["E"].width = 20
    ws1.column_dimensions["F"].width = 20

    # === Sheet 2: Functional Area Risk Summary ===
    ws2 = wb.create_sheet("功能区域风险汇总")

    headers2 = [
        "功能区域 (Solution Cluster)",
        "Ticket 总数",
        "Critical 风险 Ticket",
        "Very High 风险 Ticket",
        "High 风险 Ticket",
        "客户逃逸高风险 Ticket",
        "Showstopper/Top Issue 数",
        "Homologation 相关数",
        "区域风险等级",
        "区域风险评分",
        "风险因素",
        "严重度分布",
        "Blocking Reason 分布",
        "Ticket ID 列表 (前50)",
    ]

    for col, h in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_idx, a in enumerate(area_results, 2):
        values = [
            a["functional_area"],
            a["total_tickets"],
            a["critical_risk_tickets"],
            a["very_high_risk_tickets"],
            a["high_risk_tickets"],
            a["customer_escape_high"],
            a["showstopper_count"],
            a["homologation_count"],
            a["area_risk_level"],
            a["area_risk_score"],
            a["risk_factors"],
            a["severity_breakdown"],
            a["blocking_reason_breakdown"],
            a["ticket_ids"],
        ]
        for col, v in enumerate(values, 1):
            cell = ws2.cell(row=row_idx, column=col, value=v)
            cell.alignment = cell_alignment
            cell.border = thin_border

        # Color risk level cell
        risk_cell = ws2.cell(row=row_idx, column=9)
        if a["area_risk_level"] in risk_fills:
            risk_cell.fill = risk_fills[a["area_risk_level"]]
            if a["area_risk_level"] in ("Critical", "Very High"):
                risk_cell.font = Font(bold=True, color="FFFFFF")

    # Column widths
    col_widths2 = [30, 12, 14, 16, 14, 16, 18, 16, 14, 12, 50, 30, 30, 60]
    for col, w in enumerate(col_widths2, 1):
        ws2.column_dimensions[get_column_letter(col)].width = w

    ws2.freeze_panes = "A2"

    # === Sheet 3: Unresolved Tickets Detail (with analysis) ===
    ws3 = wb.create_sheet("未解决Ticket详情")

    headers3 = [
        "Defect ID",
        "标题",
        "分类",
        "Blocking Reason",
        "严重程度",
        "Reporting Class",
        "功能模块 (Solution Cluster)",
        "功能版本 (FV)",
        "缺陷类别",
        "ECU",
        "车型",
        "PU/I-Step",
        "检测版本",
        "软件版本",
        "发现团队",
        "发现者",
        "风险评分",
        "风险等级",
        "客户逃逸风险",
        "修复版本 (FV) 有无",
        "TQR 有无",
        "关闭评论摘要",
        "观察到的行为",
        "预期行为",
        "可能影响",
        "Ticket URL",
        "AI 详细分析",
    ]

    for col, h in enumerate(headers3, 1):
        cell = ws3.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # Sort by risk score descending
    sorted_tickets = sorted(
        unresolved_closures, key=lambda t: t.get("_risk_score", 0), reverse=True
    )

    for row_idx, t in enumerate(sorted_tickets, 2):
        fv_val = t.get("fv", "") or ""
        tqr_val = t.get("tqr", "") or ""
        has_fv = "有" if fv_val and fv_val.strip() else "❌ 无"
        has_tqr = "有" if tqr_val and tqr_val.strip() and tqr_val.strip() != "{}" else "❌ 无"

        ticket_url = f"https://almurl.bmw.com/ui/?p=1001/2001&t=defect&id={t.get('defect_id', '')}"

        values = [
            t.get("defect_id", ""),
            t.get("name", ""),
            t.get("_classification", ""),
            t.get("blocking_reason", "") or "(empty)",
            t.get("severity", ""),
            t.get("reporting_class", "") or "(empty)",
            t.get("solution_cluster", "") or "(empty)",
            fv_val or "(empty)",
            t.get("defect_category", "") or "(empty)",
            t.get("assigned_ecu", "") or "(empty)",
            t.get("lead_model", "") or "(empty)",
            t.get("pu", "") or "(empty)",
            t.get("detected_in_release", "") or "(empty)",
            t.get("software_version", "") or "(empty)",
            t.get("team", ""),
            t.get("detected_by", "") or "(empty)",
            t.get("_risk_score", ""),
            t.get("_risk_level", ""),
            t.get("_escape_risk", ""),
            has_fv,
            has_tqr,
            t.get("_close_reason_comment", ""),
            t.get("_observed_behaviour", ""),
            t.get("_expected_behaviour", ""),
            t.get("_possible_impact", ""),
            ticket_url,
            t.get("_analysis_text", ""),
        ]
        for col, v in enumerate(values, 1):
            cell = ws3.cell(row=row_idx, column=col, value=v)
            cell.alignment = cell_alignment
            cell.border = thin_border

        # Color risk level cell
        risk_cell = ws3.cell(row=row_idx, column=18)
        rl = t.get("_risk_level", "")
        if rl in risk_fills:
            risk_cell.fill = risk_fills[rl]
            if rl in ("Critical", "Very High"):
                risk_cell.font = Font(bold=True, color="FFFFFF")

        # Color escape risk cell
        esc_cell = ws3.cell(row=row_idx, column=19)
        er = t.get("_escape_risk", "")
        if er in risk_fills:
            esc_cell.fill = risk_fills[er]
            if er in ("Critical", "Very High"):
                esc_cell.font = Font(bold=True, color="FFFFFF")

    # Column widths
    col_widths3 = [
        12, 40, 16, 20, 10, 25, 25, 20, 18, 12, 10, 10, 12, 25,
        18, 12, 8, 10, 12, 10, 10, 30, 40, 30, 25, 35, 80,
    ]
    for col, w in enumerate(col_widths3, 1):
        ws3.column_dimensions[get_column_letter(col)].width = w

    ws3.freeze_panes = "C2"

    # === Sheet 4: Acceptable Rejects ===
    ws4 = wb.create_sheet("可接受Reject")
    headers4 = [
        "Defect ID",
        "标题",
        "Blocking Reason",
        "严重程度",
        "Reporting Class",
        "功能模块",
        "功能版本 (FV)",
        "缺陷类别",
        "ECU",
        "车型",
        "PU/I-Step",
        "发现团队",
        "创建时间",
        "Ticket URL",
    ]
    for col, h in enumerate(headers4, 1):
        cell = ws4.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_idx, t in enumerate(acceptable_rejects, 2):
        ticket_url = f"https://almurl.bmw.com/ui/?p=1001/2001&t=defect&id={t.get('defect_id', '')}"
        values = [
            t.get("defect_id", ""),
            t.get("name", ""),
            t.get("blocking_reason", "") or "(empty)",
            t.get("severity", ""),
            t.get("reporting_class", "") or "(empty)",
            t.get("solution_cluster", "") or "(empty)",
            t.get("fv", "") or "(empty)",
            t.get("defect_category", "") or "(empty)",
            t.get("assigned_ecu", "") or "(empty)",
            t.get("lead_model", "") or "(empty)",
            t.get("pu", "") or "(empty)",
            t.get("team", ""),
            t.get("creation_time", ""),
            ticket_url,
        ]
        for col, v in enumerate(values, 1):
            cell = ws4.cell(row=row_idx, column=col, value=v)
            cell.alignment = cell_alignment
            cell.border = thin_border

    col_widths4 = [12, 40, 20, 10, 25, 25, 20, 18, 12, 10, 10, 18, 20, 35]
    for col, w in enumerate(col_widths4, 1):
        ws4.column_dimensions[get_column_letter(col)].width = w

    ws4.freeze_panes = "C2"

    # === Sheet 5: Blocking Reason Distribution ===
    ws5 = wb.create_sheet("关闭原因分布")
    headers5 = ["Blocking Reason", "分类", "Ticket 数", "占比 (%)"]
    for col, h in enumerate(headers5, 1):
        cell = ws5.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    total = len(all_tickets)
    br_counter = Counter()
    for t in all_tickets:
        br = t.get("blocking_reason", "") or "(empty)"
        br_counter[br] += 1

    row_idx = 2
    for br, count in br_counter.most_common():
        if br in ACCEPTABLE_BLOCKING_REASONS:
            classification = "正向可接受"
        elif br in UNRESOLVED_BLOCKING_REASONS or br == "(empty)":
            classification = "❌ 未解决"
        else:
            classification = "其他"
        values = [br, classification, count, f"{count/total*100:.1f}%"]
        for col, v in enumerate(values, 1):
            cell = ws5.cell(row=row_idx, column=col, value=v)
            cell.alignment = cell_alignment
            cell.border = thin_border
        if classification == "❌ 未解决":
            ws5.cell(row=row_idx, column=2).fill = risk_fills.get("High", risk_fills["Low"])
        row_idx += 1

    col_widths5 = [30, 18, 12, 10]
    for col, w in enumerate(col_widths5, 1):
        ws5.column_dimensions[get_column_letter(col)].width = w

    ws5.freeze_panes = "A2"

    # === Sheet 6: High Risk Tickets (filtered) ===
    ws6 = wb.create_sheet("高风险Ticket聚焦")
    high_risk_tickets = [
        t for t in sorted_tickets if t.get("_risk_score", 0) >= 7
    ]

    headers6 = headers3.copy()
    for col, h in enumerate(headers6, 1):
        cell = ws6.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = PatternFill(
            start_color="C00000", end_color="C00000", fill_type="solid"
        )
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_idx, t in enumerate(high_risk_tickets, 2):
        fv_val = t.get("fv", "") or ""
        tqr_val = t.get("tqr", "") or ""
        has_fv = "有" if fv_val and fv_val.strip() else "❌ 无"
        has_tqr = (
            "有"
            if tqr_val and tqr_val.strip() and tqr_val.strip() != "{}"
            else "❌ 无"
        )
        ticket_url = f"https://almurl.bmw.com/ui/?p=1001/2001&t=defect&id={t.get('defect_id', '')}"

        values = [
            t.get("defect_id", ""),
            t.get("name", ""),
            t.get("_classification", ""),
            t.get("blocking_reason", "") or "(empty)",
            t.get("severity", ""),
            t.get("reporting_class", "") or "(empty)",
            t.get("solution_cluster", "") or "(empty)",
            fv_val or "(empty)",
            t.get("defect_category", "") or "(empty)",
            t.get("assigned_ecu", "") or "(empty)",
            t.get("lead_model", "") or "(empty)",
            t.get("pu", "") or "(empty)",
            t.get("detected_in_release", "") or "(empty)",
            t.get("software_version", "") or "(empty)",
            t.get("team", ""),
            t.get("detected_by", "") or "(empty)",
            t.get("_risk_score", ""),
            t.get("_risk_level", ""),
            t.get("_escape_risk", ""),
            has_fv,
            has_tqr,
            t.get("_close_reason_comment", ""),
            t.get("_observed_behaviour", ""),
            t.get("_expected_behaviour", ""),
            t.get("_possible_impact", ""),
            ticket_url,
            t.get("_analysis_text", ""),
        ]
        for col, v in enumerate(values, 1):
            cell = ws6.cell(row=row_idx, column=col, value=v)
            cell.alignment = cell_alignment
            cell.border = thin_border

        risk_cell = ws6.cell(row=row_idx, column=18)
        rl = t.get("_risk_level", "")
        if rl in risk_fills:
            risk_cell.fill = risk_fills[rl]
            if rl in ("Critical", "Very High"):
                risk_cell.font = Font(bold=True, color="FFFFFF")

        esc_cell = ws6.cell(row=row_idx, column=19)
        er = t.get("_escape_risk", "")
        if er in risk_fills:
            esc_cell.fill = risk_fills[er]
            if er in ("Critical", "Very High"):
                esc_cell.font = Font(bold=True, color="FFFFFF")

    for col, w in enumerate(col_widths3, 1):
        ws6.column_dimensions[get_column_letter(col)].width = w

    ws6.freeze_panes = "C2"

    # Save
    wb.save(OUTPUT_FILE)
    print(f"  Excel saved: {OUTPUT_FILE}")
    print(f"  Sheets: {wb.sheetnames}")


if __name__ == "__main__":
    main()
