#!/usr/bin/env python3
"""Export Octane ingest field + derived analysis field inventory to Excel (3 sheets)."""
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

OUTPUT = Path(__file__).resolve().parents[1] / "exports" / "idcevo_phase09" / "Vizion_Field_Inventory.xlsx"

THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill("solid", fgColor="2F5496")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
WRAP = Alignment(wrap_text=True, vertical="top")


def style_header(ws, ncols, row=1):
    for c in range(1, ncols + 1):
        cl = ws.cell(row=row, column=c)
        cl.font = HDR_FONT
        cl.fill = HDR_FILL
        cl.alignment = WRAP
        cl.border = BORDER


def autosize(ws, widths):
    for i, wd in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = wd


def add_filter(ws, ncols, freeze="A2"):
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(ncols)}{ws.max_row}"
    ws.freeze_panes = freeze


def build():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ════════════════════════════════════════════════════════════════════════
    # Sheet 1: Octane 原生字段（Ingest 拉取）
    # ════════════════════════════════════════════════════════════════════════
    s1 = wb.create_sheet("01_Octane原生字段")
    s1.append(["实体", "Octane 原生字段", "说明"])
    octane_fields = [
        # Defect
        ("Defect", "id", "缺陷 ID"),
        ("Defect", "name", "缺陷标题"),
        ("Defect", "description", "描述/现象"),
        ("Defect", "creation_time", "创建时间"),
        ("Defect", "last_modified", "最后修改时间"),
        ("Defect", "team{name}", "归属团队"),
        ("Defect", "problem_finder_team_udf{name}", "问题发现团队"),
        ("Defect", "product_areas{name}", "产品领域/AIDA"),
        ("Defect", "requirements{name}", "关联需求"),
        ("Defect", "assigned_ecu_udf{name}", "分配 ECU"),
        ("Defect", "software_version_udf", "软件版本"),
        ("Defect", "solution_cluster_udf{name}", "解决方案集群"),
        ("Defect", "problem_category_udf{name}", "问题类别"),
        ("Defect", "reporting_class_udf{name}", "报告类别 (QGate 分类)"),
        ("Defect", "problem_severity_udf{name}", "问题严重度"),
        ("Defect", "vin_udf", "VIN 码"),
        ("Defect", "first_use_sop_of_function_udf", "功能首次 SOP"),
        ("Defect", "function_responsible1_udf{full_name,name}", "功能负责人"),
        ("Defect", "ecu_to_modul_udf{name}", "ECU 到模块映射"),
        ("Defect", "lead_model_udf{name}", "主导车型"),
        ("Defect", "phase{name,id}", "当前阶段"),
        ("Defect", "severity{name,id}", "严重度"),
        ("Defect", "owner{full_name,name}", "负责人"),
        ("Defect", "author{full_name,name}", "创建人"),
        # Manual Run
        ("Manual Run", "id", "执行 ID"),
        ("Manual Run", "defect{id,name}", "关联缺陷"),
        ("Manual Run", "test{id,name}", "关联测试用例"),
        ("Manual Run", "test_name", "测试名称"),
        ("Manual Run", "status{name,id}", "执行状态"),
        ("Manual Run", "creation_time", "创建时间"),
        ("Manual Run", "last_modified", "最后修改时间"),
        ("Manual Run", "started", "执行开始时间"),
        ("Manual Run", "finished_udf", "执行完成时间"),
        ("Manual Run", "release{name}", "Release"),
        ("Manual Run", "run_team_000_udf{name}", "执行团队"),
        ("Manual Run", "run_by{full_name,name}", "执行人"),
        ("Manual Run", "author{full_name,name}", "创建人"),
        ("Manual Run", "product_areas{name}", "产品领域"),
        ("Manual Run", "set_udf{name}", "测试集"),
        ("Manual Run", "target_ecu_conf_udf", "目标 ECU 配置"),
        ("Manual Run", "exec_model_series_udf{name}", "执行车型系列"),
        ("Manual Run", "covered_content{id,name,subtype,parent,path}", "覆盖内容 (含 parent/path)"),
        ("Manual Run", "linked_defects", "关联缺陷列表"),
        # History
        ("History", "timestamp", "事件时间"),
        ("History", "action", "操作类型"),
        ("History", "user{full_name,name}", "操作人"),
        ("History", "change_set{field_name,old_value,new_value,...}", "变更详情"),
        # Work Item Relation
        ("Work Item", "id", "工作项 ID"),
        ("Work Item", "name", "工作项名称"),
        ("Work Item", "subtype", "子类型 (defect/feature/story)"),
        ("Work Item", "release{name}", "Release"),
        ("Work Item", "parent{id,name,subtype}", "父工作项"),
        ("Work Item", "run_covered_content_relation{id}", "关联执行"),
        ("Work Item", "path", "路径"),
        # Comment
        ("Comment", "id", "评论 ID"),
        ("Comment", "text", "评论内容"),
        ("Comment", "author{full_name,name}", "评论人"),
        ("Comment", "creation_time", "创建时间"),
        ("Comment", "last_modified", "修改时间"),
        ("Comment", "owner_work_item{id,subtype}", "所属工作项"),
    ]
    for row in octane_fields:
        s1.append(list(row))
    style_header(s1, 3)
    autosize(s1, [14, 42, 28])
    add_filter(s1, 3)

    # ════════════════════════════════════════════════════════════════════════
    # Sheet 2: SQLite 存储层扩展列（Ingest 从 raw_json 展开）
    # ════════════════════════════════════════════════════════════════════════
    s2 = wb.create_sheet("02_Ingest展开列")
    s2.append(["表", "列名", "来源/说明"])
    ingest_cols = [
        # Defect base + additional
        ("octane_defects", "defect_id", "主键 (Octane id)"),
        ("octane_defects", "name", "Octane name"),
        ("octane_defects", "project", "Octane 原始 project (保留维度)"),
        ("octane_defects", "market", "保留维度 (processor 补全)"),
        ("octane_defects", "pu", "保留维度 (processor 补全)"),
        ("octane_defects", "fv", "保留维度 (processor 补全)"),
        ("octane_defects", "fvp", "保留维度 (processor 补全)"),
        ("octane_defects", "team", "Octane team"),
        ("octane_defects", "lead_model", "Octane lead_model_udf"),
        ("octane_defects", "raw_json", "完整 Octane API 响应 JSON"),
        ("octane_defects", "fetched_at", "抓取时间戳"),
        ("octane_defects", "description", "raw_json 展开"),
        ("octane_defects", "comments", "raw_json 展开 (评论 JSON)"),
        ("octane_defects", "requirement", "raw_json 展开"),
        ("octane_defects", "requirements_json", "raw_json 展开"),
        ("octane_defects", "creation_time", "raw_json 展开"),
        ("octane_defects", "last_modified", "raw_json 展开"),
        ("octane_defects", "program", "raw_json 展开"),
        ("octane_defects", "author", "raw_json 展开"),
        ("octane_defects", "user_tags", "raw_json 展开"),
        ("octane_defects", "phase", "raw_json 展开"),
        ("octane_defects", "severity", "raw_json 展开"),
        ("octane_defects", "problem_severity", "raw_json 展开"),
        ("octane_defects", "owner", "raw_json 展开"),
        ("octane_defects", "detected_by", "raw_json 展开"),
        ("octane_defects", "detected_in_release", "raw_json 展开"),
        ("octane_defects", "vin", "raw_json 展开"),
        ("octane_defects", "ecu_no_of_changes", "raw_json 展开"),
        ("octane_defects", "parent_id", "raw_json 展开"),
        ("octane_defects", "parent_child_type", "raw_json 展开"),
        ("octane_defects", "relation_to", "raw_json 展开"),
        ("octane_defects", "test_week", "raw_json 展开"),
        ("octane_defects", "risk_score", "raw_json 展开 (Octane 原始)"),
        ("octane_defects", "tqr", "raw_json 展开"),
        ("octane_defects", "blocking_reason", "raw_json 展开"),
        ("octane_defects", "error_occurrence", "raw_json 展开"),
        ("octane_defects", "solution_responsible", "raw_json 展开"),
        ("octane_defects", "reporting_class", "raw_json 展开"),
        ("octane_defects", "involved_i_step", "raw_json 展开"),
        ("octane_defects", "first_use_sop_of_function", "raw_json 展开"),
        ("octane_defects", "tolerated_count", "raw_json 展开"),
        ("octane_defects", "reprel_changes", "raw_json 展开"),
        ("octane_defects", "tproject", "processor 推断 (最终业务项目)"),
        ("octane_defects", "function2modul", "raw_json 展开"),
        ("octane_defects", "model_series", "raw_json 展开"),
        ("octane_defects", "defect_category", "raw_json 展开"),
        ("octane_defects", "status_phase", "raw_json 展开"),
        ("octane_defects", "problem_finder_team", "raw_json 展开"),
        ("octane_defects", "year", "ingest 按创建时间标注"),
        ("octane_defects", "assigned_ecu", "raw_json 展开"),
        ("octane_defects", "top_aida", "raw_json 展开"),
        ("octane_defects", "aida_english", "raw_json 展开"),
        ("octane_defects", "aida_businesskey", "raw_json 展开"),
        ("octane_defects", "solution_cluster", "raw_json 展开"),
        ("octane_defects", "product_areas", "raw_json 展开"),
        ("octane_defects", "software_version", "raw_json 展开"),
        ("octane_defects", "function_responsible", "raw_json 展开"),
        ("octane_defects", "ecu_to_modul", "raw_json 展开"),
        # History
        ("octane_defect_history_events", "defect_id", "关联缺陷 ID"),
        ("octane_defect_history_events", "event_timestamp", "事件时间"),
        ("octane_defect_history_events", "field_name", "变更字段"),
        ("octane_defect_history_events", "old_value", "旧值"),
        ("octane_defect_history_events", "new_value", "新值"),
        ("octane_defect_history_events", "fetched_at", "抓取时间"),
        ("octane_defect_history_events", "action", "raw_json 展开"),
        ("octane_defect_history_events", "user_name", "raw_json 展开"),
        ("octane_defect_history_events", "team", "raw_json 展开"),
        ("octane_defect_history_events", "entry_index", "解析序号"),
        ("octane_defect_history_events", "change_index", "变更序号"),
        ("octane_defect_history_events", "old_value_text", "raw_json 展开"),
        ("octane_defect_history_events", "new_value_text", "raw_json 展开"),
        # Manual Run base + extra
        ("octane_manual_runs", "mr_id", "主键"),
        ("octane_manual_runs", "defect_id", "关联缺陷"),
        ("octane_manual_runs", "test_id", "关联测试"),
        ("octane_manual_runs", "test_name", "测试名称"),
        ("octane_manual_runs", "status", "执行状态"),
        ("octane_manual_runs", "year", "ingest 标注"),
        ("octane_manual_runs", "test_week", "processor 补全 (ISO 周)"),
        ("octane_manual_runs", "pu", "processor 补全"),
        ("octane_manual_runs", "top_aida", "processor 补全"),
        ("octane_manual_runs", "feature_region", "China/Global 派生"),
        ("octane_manual_runs", "tester", "processor 补全"),
        ("octane_manual_runs", "project", "processor 补全"),
        ("octane_manual_runs", "fv", "processor 补全"),
        ("octane_manual_runs", "fvp", "processor 补全"),
        ("octane_manual_runs", "team", "Octane run_team"),
        ("octane_manual_runs", "lead_model", "Octane exec_model_series"),
        ("octane_manual_runs", "raw_json", "完整 API 响应"),
        ("octane_manual_runs", "fetched_at", "抓取时间"),
        ("octane_manual_runs", "name", "raw_json 展开"),
        ("octane_manual_runs", "run_team", "raw_json 展开"),
        ("octane_manual_runs", "program", "raw_json 展开"),
        ("octane_manual_runs", "author", "raw_json 展开"),
        ("octane_manual_runs", "run_by", "raw_json 展开"),
        ("octane_manual_runs", "native_status", "raw_json 展开"),
        ("octane_manual_runs", "is_completed", "raw_json 展开"),
        ("octane_manual_runs", "steps_num", "raw_json 展开"),
        ("octane_manual_runs", "version_stamp", "raw_json 展开"),
        ("octane_manual_runs", "release", "raw_json 展开"),
        ("octane_manual_runs", "exec_model_series", "raw_json 展开"),
        ("octane_manual_runs", "execution_sw_version", "raw_json 展开"),
        ("octane_manual_runs", "test_version", "raw_json 展开"),
        ("octane_manual_runs", "domain", "raw_json 展开"),
        ("octane_manual_runs", "test_phase", "raw_json 展开"),
        ("octane_manual_runs", "testing_tool_type", "raw_json 展开"),
        ("octane_manual_runs", "taxonomies", "raw_json 展开"),
        ("octane_manual_runs", "set_field", "raw_json 展开"),
        ("octane_manual_runs", "testplatformid", "raw_json 展开"),
        ("octane_manual_runs", "spec", "raw_json 展开"),
        ("octane_manual_runs", "creation_time", "raw_json 展开"),
        ("octane_manual_runs", "last_modified", "raw_json 展开"),
        ("octane_manual_runs", "started", "raw_json 展开"),
        ("octane_manual_runs", "finished", "raw_json 展开"),
        ("octane_manual_runs", "product_areas", "raw_json 展开"),
        ("octane_manual_runs", "target_ecu_conf", "raw_json 展开"),
    ]
    for row in ingest_cols:
        s2.append(list(row))
    style_header(s2, 3)
    autosize(s2, [32, 28, 30])
    add_filter(s2, 3)

    # ════════════════════════════════════════════════════════════════════════
    # Sheet 3: 数据清洗/建模后自建的派生分析字段
    # ════════════════════════════════════════════════════════════════════════
    s3 = wb.create_sheet("03_派生分析字段")
    s3.append(["分析层", "派生字段", "说明"])
    derived_fields = [
        # --- Processor 数据清洗层 ---
        ("Processor 清洗", "tproject", "最终业务项目 (IDCEVO/IDC/MGU/RSU/RSE/App/Unknown)，5 级优先级推断"),
        ("Processor 清洗", "classification_source", "项目分类来源：technical_consensus/explicit_feature/business_mapping/legacy_project/heuristic_fallback/unknown"),
        ("Processor 清洗", "evidence_sources", "推断证据来源列表 [feature_area, assigned_ecu, software_version]"),
        ("Processor 清洗", "market (补全)", "从 VIN 前缀映射市场区域"),
        ("Processor 清洗", "fv (补全)", "从 top_aida 映射 Feature Version"),
        ("Processor 清洗", "fvp (补全)", "从 fv 映射 FVP 负责人"),
        ("Processor 清洗", "pu (补全)", "从 first_use_sop_of_function 补全产品单元"),
        ("Processor 清洗", "test_week (补全)", "从 finished_udf 计算 ISO 测试周 (YYYY-CWnn)"),
        ("Processor 清洗", "tester (补全)", "从 run_by/author 解析测试执行人"),
        ("Processor 清洗", "top_aida (补全)", "从 product_areas 解析顶层 AIDA"),
        ("Processor 清洗", "china_scope", "China / Global，从 solution_cluster + defect_category 分类"),
        ("Processor 清洗", "feature_region", "China Specific / Global (SQL 表达式版)"),
        ("Processor 清洗", "group_name", "阶段分组：Q-Gate(02,07)/Integration(00,01,06,08,09)/CoC(03,04,05)/Other"),
        ("Processor 清洗", "month", "从 creation_time 提取 YYYY-MM"),
        ("Processor 清洗", "ticket_date", "COALESCE(last_modified, creation_time, fetched_at)"),
        ("Processor 清洗", "age_days", "创建时间到 ticket_date 的天数"),
        ("Processor 清洗", "is_resolved_forward", "从 history 重建：phase 08→06 转换"),
        ("Processor 清洗", "is_rejected_directly", "从 history 重建：phase 01→09 转换"),
        ("Processor 清洗", "classification", "从 reporting_class_udf 解析问题分类"),
        # --- Phase09 Closure Risk 建模层 ---
        ("Phase09 风险建模", "direct_close", "是否直接 01→09 关闭 (从 phase history 重建)"),
        ("Phase09 风险建模", "seen_phase09", "是否经历过 Phase 09"),
        ("Phase09 风险建模", "latest_phase09_at", "最近进入 Phase 09 的时间戳"),
        ("Phase09 风险建模", "reopened_after_phase09", "Phase 09 后是否重开 (时序回退)"),
        ("Phase09 风险建模", "entered_phase06_after_phase09", "09 后是否进入 06 (表示有修复)"),
        ("Phase09 风险建模", "phase_history_count", "phase 变更次数"),
        ("Phase09 风险建模", "audit_history_event_count", "审计历史事件总数"),
        ("Phase09 风险建模", "effective_reason", "有效关闭原因 (blocking_reason + history + comments 综合)"),
        ("Phase09 风险建模", "reason_source", "原因来源：Phase-history blocking/blocked/Comment evidence/No reason"),
        ("Phase09 风险建模", "blocking_reason_group", "关闭原因归一化分组 (7 类)"),
        ("Phase09 风险建模", "rejection_route", "驳回路由 (从 rejected_status_udf history 提取)"),
        ("Phase09 风险建模", "target_version_status", "目标版本状态：after/before Phase 09 / No target version"),
        ("Phase09 风险建模", "target_version_value", "最新目标版本值 (从 history change_set 提取)"),
        ("Phase09 风险建模", "target_version_at", "目标版本事件时间戳"),
        ("Phase09 风险建模", "planning_window_status", "规划窗口状态"),
        ("Phase09 风险建模", "planning_window_value", "规划窗口值"),
        ("Phase09 风险建模", "planning_window_at", "规划窗口事件时间戳"),
        ("Phase09 风险建模", "reprel_change_count", "Reprel 变更次数 (从 history 解析)"),
        ("Phase09 风险建模", "reprel_change_status", "Reprel 变更状态"),
        ("Phase09 风险建模", "commercial_solution_date_status", "商业方案日期状态"),
        ("Phase09 风险建模", "commercial_solution_date_value", "商业方案日期值"),
        ("Phase09 风险建模", "postponement_document_status", "延期文档状态：Substantive/Template only/Not recorded"),
        ("Phase09 风险建模", "postponement_document_at", "延期文档时间戳"),
        ("Phase09 风险建模", "postponement_document_excerpt", "延期文档摘录"),
        ("Phase09 风险建模", "has_substantive_assessment_document", "是否有实质性评估文档 (非模板)"),
        ("Phase09 风险建模", "postponement_decision_date_status", "延期决策日期状态"),
        ("Phase09 风险建模", "postponement_decision_date_value", "延期决策日期值"),
        ("Phase09 风险建模", "duplicate_parent_id", "重复缺陷父票 ID (从 relation_to 提取)"),
        ("Phase09 风险建模", "duplicate_parent_phase", "父票当前 phase"),
        ("Phase09 风险建模", "duplicate_parent_name", "父票标题"),
        ("Phase09 风险建模", "has_resolved_duplicate_parent", "父票是否已解决 (phase resolved)"),
        ("Phase09 风险建模", "related_resolution_status", "关联解决状态"),
        ("Phase09 风险建模", "related_resolution_ticket_id", "关联解决票 ID"),
        ("Phase09 风险建模", "related_resolution_phase06_at", "关联票达到 Phase 06 的时间"),
        ("Phase09 风险建模", "explicit_linked_resolution_after_phase09", "09 后是否有显式关联解决"),
        ("Phase09 风险建模", "repair_plan_status", "修复计划状态：Explicit post-closure/Historical/Phase 06 after 09/No evidence"),
        ("Phase09 风险建模", "has_follow_up_evidence", "是否有后续跟踪证据 (综合布尔)"),
        ("Phase09 风险建模", "follow_up_status", "跟踪状态：Resolution recorded/Target version/Commercial date/No evidence"),
        ("Phase09 风险建模", "has_remediation_or_resolution_evidence", "是否有修复或解决证据 (has_follow_up_evidence 别名)"),
        ("Phase09 风险建模", "is_governed_severe", "是否治理严重 (reporting_class=showstopper + problem_severity 04/05/06)"),
        ("Phase09 风险建模", "decision_closure", "是否决策关闭 (management decision/tolerated/not implemented)"),
        ("Phase09 风险建模", "decision_closure_without_follow_up", "决策关闭但无跟踪证据"),
        ("Phase09 风险建模", "decision_evidence_conflict", "决策与证据冲突 (正常原因+严重影响+无修复)"),
        ("Phase09 风险建模", "needs_closure_evidence_review", "需要关闭证据评审 (核心评审队列标记)"),
        ("Phase09 风险建模", "closure_disposition", "关闭处置：Acceptable/Follow-up recorded/Direct closure without rationale/Decision without delivery 等"),
        ("Phase09 风险建模", "risk_score", "风险分 (severity + 重开+3 + 无修复+3 + 决策无修复+2 + 无原因+1 等累加)"),
        ("Phase09 风险建模", "risk_tier", "风险层级：Priority production-exposure review/Decision-evidence conflict/Evidence incomplete/Repair plan/Normal closure"),
        ("Phase09 风险建模", "risk_flags", "风险标记列表 (所有触发的规则 flag)"),
        ("Phase09 风险建模", "production_exposure_status", "生产暴露状态：Cannot be ruled out - priority/closure gap/Not elevated/Recorded"),
        ("Phase09 风险建模", "risk_basis", "风险依据文本 (综合理由)"),
        ("Phase09 风险建模", "automated_analysis", "自动化证据分析文本"),
        ("Phase09 风险建模", "evidence_index", "证据索引 (description/comments/history 计数)"),
        ("Phase09 风险建模", "comment_count", "评论数"),
        ("Phase09 风险建模", "plan_evidence_excerpt", "修复计划证据摘录 (从 comments 提取)"),
        ("Phase09 风险建模", "decision_evidence_excerpt", "决策证据摘录"),
        ("Phase09 风险建模", "no_fix_evidence_excerpt", "无修复证据摘录"),
        ("Phase09 风险建模", "description_excerpt", "描述摘录 (420 字符截断)"),
        # --- Scenario Mining 建模层 ---
        ("Scenario 场景挖掘", "scenario_key", "场景聚类哈希键 (symptom_keyword + trigger_keyword + function_domain)"),
        ("Scenario 场景挖掘", "scenario_id", "场景编号 FS-NNNN"),
        ("Scenario 场景挖掘", "customer_symptom", "LLM 提取的客户感知症状"),
        ("Scenario 场景挖掘", "trigger_condition", "LLM 提取的触发条件"),
        ("Scenario 场景挖掘", "affected_function", "标准化功能域 (FUNCTION_DOMAIN_MAP 映射 15 类)"),
        ("Scenario 场景挖掘", "affected_function_raw", "LLM 原始提取的功能"),
        ("Scenario 场景挖掘", "observed_behaviour", "LLM 提取的观察行为"),
        ("Scenario 场景挖掘", "expected_behaviour", "LLM 提取的期望行为"),
        ("Scenario 场景挖掘", "recovery_action", "LLM 提取的恢复措施"),
        ("Scenario 场景挖掘", "closure_decision", "LLM 提取的关闭决策"),
        ("Scenario 场景挖掘", "evidence_confidence", "证据置信度 high/medium/low"),
        ("Scenario 场景挖掘", "is_field_scenario", "是否客户现场场景 (排除测试/工厂/工具类)"),
        ("Scenario 场景挖掘", "exclusion_reason", "排除原因：spec_confirmed/plant_only/test_tooling/not_customer_facing 等 8 类"),
        ("Scenario 场景挖掘", "symptom_keyword", "症状关键词标准化 token (display-blank/audio-none/system-unresponsive 等)"),
        ("Scenario 场景挖掘", "trigger_keyword", "触发关键词标准化 token (wake-up/after-reboot/connecting 等)"),
        ("Scenario 场景挖掘", "canonical_function_domain", "规范功能域 (Audio/Speech/Navigation/Connectivity 等 15 类)"),
        ("Scenario 场景挖掘", "family_id", "场景族编号 FAM-NN"),
        ("Scenario 场景挖掘", "family_name", "功能域名称"),
        ("Scenario 场景挖掘", "risk_index", "Field Escape 风险指数 (加权: severity×0.30 + prod_app×0.25 + exposure×0.20 + percept×0.10 + diag×0.10 + recov×0.05)"),
        ("Scenario 场景挖掘", "risk_band", "风险等级 Critical(≥4.0)/High(≥3.0)/Medium(≥2.0)/Low(<2.0)"),
        ("Scenario 场景挖掘", "customer_severity", "客户严重度 1-5 (5=安全/不可驾驶/充电)"),
        ("Scenario 场景挖掘", "production_applicability", "生产适用性 1-5 (基于生产暴露票占比)"),
        ("Scenario 场景挖掘", "exposure_frequency", "暴露频率 1-5 (基于场景票数占比)"),
        ("Scenario 场景挖掘", "perceptibility", "可感知性 1-5 (客户是否容易察觉)"),
        ("Scenario 场景挖掘", "diagnosability", "可诊断性 1-5 (DTC/日志/根因可用性)"),
        ("Scenario 场景挖掘", "recoverability", "可恢复性 1-5 (已知 workaround=高分)"),
        ("Scenario 场景挖掘", "ticket_count", "场景内票数"),
        ("Scenario 场景挖掘", "member_risk_scores", "场景内所有票的风险分列表"),
        ("Scenario 场景挖掘", "max_risk_score", "场景内最高风险分"),
        ("Scenario 场景挖掘", "avg_risk_score", "场景内平均风险分"),
        ("Scenario 场景挖掘", "prod_exposure_count", "生产暴露票数"),
        ("Scenario 场景挖掘", "top_sw_versions", "Top 5 软件版本分布"),
        ("Scenario 场景挖掘", "top_ecus", "Top 5 ECU 分布"),
        ("Scenario 场景挖掘", "diagnostic_entry", "诊断入口文本 (ECU + 根因线索)"),
        ("Scenario 场景挖掘", "escalation_criteria", "升级条件文本"),
        ("Scenario 场景挖掘", "evidence_gaps", "证据缺口文本"),
        ("Scenario 场景挖掘", "vehicle_versions", "涉及车型/版本 Top 3"),
        # --- Duplicate Normalization 建模层 ---
        ("重复缺陷归一化", "pair_id", "重复对 ID (SHA-256 哈希前 16 位)"),
        ("重复缺陷归一化", "input_fingerprint", "输入指纹 (检测 LLM review 结果是否过期)"),
        ("重复缺陷归一化", "candidate_channels", "重复候选通道：shared_verified_duplicate_parent / exact_normalized_title_and_context"),
        ("重复缺陷归一化", "hard_contradictions", "硬矛盾列表：final_project_conflict/assigned_ecu_conflict/function_context_conflict"),
        ("重复缺陷归一化", "eligible_for_llm_review", "是否符合 LLM 评审条件"),
        ("重复缺陷归一化", "rejection_reason", "拒绝原因：hard_context_contradiction/title_match_without_anchor/insufficient_evidence"),
        ("重复缺陷归一化", "representative_ticket_id", "代表票 ID (证据最丰富)"),
        ("重复缺陷归一化", "group_id", "确认重复组 ID (group-{representative_id})"),
        ("重复缺陷归一化", "normalization_status", "confirmed_duplicate_group / unmerged_singleton"),
        ("重复缺陷归一化", "member_count", "组内票数"),
        ("重复缺陷归一化", "merged_member_count", "合并的重复票数 (member_count - 1)"),
        ("重复缺陷归一化", "worst_risk_score", "组内最高风险分"),
        ("重复缺陷归一化", "worst_risk_tier", "组内最高风险层级"),
        ("重复缺陷归一化", "decision", "DUAL_CONFIRMED_SAME (双模型确认相同)"),
        ("重复缺陷归一化", "verdict", "LLM 判定：SAME_UNDERLYING_DEFECT / DISTINCT / INSUFFICIENT_EVIDENCE"),
        ("重复缺陷归一化", "confidence", "LLM 置信度 0-1"),
        ("重复缺陷归一化", "rationale", "LLM 判定理由文本"),
        # --- Dashboard 聚合指标 ---
        ("Dashboard 聚合", "defect_count", "缺陷计数"),
        ("Dashboard 聚合", "resolved_forward_count", "前向解决数 (08→06)"),
        ("Dashboard 聚合", "rejected_directly_count", "直接驳回数 (01→09)"),
        ("Dashboard 聚合", "resolved_forward_rate", "前向解决率 (%)"),
        ("Dashboard 聚合", "rejected_directly_rate", "直接驳回率 (%)"),
        ("Dashboard 聚合", "average_days", "平均存活天数"),
        ("Dashboard 聚合", "overdue_count", "超期数 (>30 天)"),
        ("Dashboard 聚合", "severe_overdue_count", "严重超期数 (>60 天)"),
        ("Dashboard 聚合", "repeat_rate", "重复缺陷率 (%)"),
        ("Dashboard 聚合", "delta", "环比差值 (current - previous)"),
        ("Dashboard 聚合", "growth_pct", "环比增长率 (%)"),
        ("Dashboard 聚合", "is_new", "是否新增 (previous=0 且 current>0)"),
        ("Dashboard 聚合", "business_module", "业务模块 (solution_cluster 或 defect_category)"),
        ("Dashboard 聚合", "outcome_flag", "结果标记：resolved_forward/rejected_directly/other"),
        ("Dashboard 聚合", "snapshot_version", "快照版本 (china_scope_rule_version + source_signature 哈希)"),
    ]
    for row in derived_fields:
        s3.append(list(row))
    style_header(s3, 3)
    autosize(s3, [18, 42, 55])
    add_filter(s3, 3)

    # Apply borders + wrap to all data cells
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.border = BORDER
                cell.alignment = WRAP

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT)
    print(f"Saved to: {OUTPUT}")
    print(f"Sheet 1 (Octane原生字段): {s1.max_row - 1} rows")
    print(f"Sheet 2 (Ingest展开列): {s2.max_row - 1} rows")
    print(f"Sheet 3 (派生分析字段): {s3.max_row - 1} rows")


if __name__ == "__main__":
    build()
