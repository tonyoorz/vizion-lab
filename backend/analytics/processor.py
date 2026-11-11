from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from backend.analytics.asset_loader import load_mapping_assets
from backend.analytics.duplicate_search_state import advance_duplicate_search_source_revision


BUSINESS_PROJECT_RULE_VERSION = "2026-09-03-v1"
from backend.analytics.schema import ensure_schema


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_project_name(value: object) -> str:
    text = _normalize_text(value)
    if not text:
        return ""

    normalized = text.upper().replace(" ", "")
    if normalized in {"UNKNOWN", "UNCLASSIFIED"}:
        return "Unknown"
    if normalized in {"IUK", "EEMAIN", "EE_MAIN"}:
        return ""
    if "IDCEVO" in normalized:
        return "IDCEVO"
    if normalized in {"IDC", "IDC23"} or "IDC23" in normalized:
        return "IDC"
    if normalized in {"MGU", "MGU22"} or "BMWMGU" in normalized:
        return "MGU"
    if normalized.startswith("APP"):
        return "App"
    if "RSU" in normalized:
        return "RSU"
    if "RSE" in normalized:
        return "RSE"
    return text


def _software_version_tokens(value: object) -> set[str]:
    text = _normalize_text(value).upper()
    if not text:
        return set()

    base_tokens = {token for token in re.split(r"[^A-Z0-9]+", text) if token}
    tokens = set(base_tokens)
    for token in list(base_tokens):
        if token.startswith("BMW") and len(token) > 3:
            tokens.add(token[3:])
    return tokens


def _software_version_project_candidates(value: object) -> set[str]:
    tokens = _software_version_tokens(value)
    if not tokens:
        return set()

    projects: set[str] = set()
    if "IDCEVO" in tokens or "CDE" in tokens:
        projects.add("IDCEVO")
    if "IDC23" in tokens:
        projects.add("IDC")
    if "MGU22" in tokens:
        projects.add("MGU")
    if "RSE26" in tokens:
        projects.add("RSE")
    if tokens.intersection({"MOBILE", "ANDROID", "IOS", "HARMONYOS"}):
        projects.add("App")
    return projects


def _project_from_software_version(value: object) -> str:
    projects = _software_version_project_candidates(value)
    if len(projects) == 1:
        return next(iter(projects))
    return ""


def _project_from_assigned_ecu(value: object) -> str:
    assigned_ecu = _normalize_text(value).upper()
    if assigned_ecu == "IPN-15":
        return "IDCEVO"
    if assigned_ecu == "UCAP-10":
        return "MGU"

    for project, signals in (
        ("IDCEVO", ("IDCEVO", "ENTRYEVO", "NBTEVO", "CDE-01", "ICON-25", "BMTH-01", "IPN-10", "IPN-10_DE", "SD-AMAP")),
        ("IDC", ("IDC23", "HU-MGU_02_A")),
        ("MGU", ("MGU", "HU-MGU_02_L", "HU-MGU_01", "SP_NAVINFO", "BMT")),
        ("RSU", ("RSU",)),
        ("RSE", ("RSE",)),
        ("App", ("APP", "MOBILE", "MY BMW", "ANDROID", "IOS", "HARMONYOS", "BYOD")),
    ):
        if any(signal in assigned_ecu for signal in signals):
            return project
    return ""


def _project_from_feature_area(row: dict[str, object]) -> str:
    top_aida = _normalize_text(row.get("top_aida"))
    product_areas = _normalize_text(row.get("product_areas"))
    solution_cluster = _normalize_text(row.get("solution_cluster"))
    feature_area = top_aida or product_areas

    if feature_area == "Use Rear Seat Entertainment [01.04.01.09.02]":
        return "RSE"
    if "[sys_rsu]" in feature_area.lower() or "remote software update" in feature_area.lower():
        return "RSU"
    if feature_area in {
        "Provide Navigation 2.0 [01.04.03.01.03.06]",
        "Use Speech operation [01.04.02.01.01.05]",
    }:
        return "IDCEVO"
    if feature_area in {
        "My Modes [01.04.02.01.04.03]",
        "Destination Input [01.04.03.01.03.02.05]",
        "Map [01.04.03.01.03.02.01]",
        "Provide headunit launcher [01.04.02.03.01.01.09]",
    }:
        return "IDC"
    if solution_cluster == "Navigation Asia":
        return "MGU"
    return ""


def _technical_project_evidence(row: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return (
        ("feature_area", _project_from_feature_area(row)),
        ("assigned_ecu", _project_from_assigned_ecu(row.get("assigned_ecu"))),
        ("software_version", _project_from_software_version(row.get("software_version"))),
    )


def _technical_project_consensus(row: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    sources_by_project: dict[str, list[str]] = {}
    for source, project in _technical_project_evidence(row):
        if not project:
            continue
        sources_by_project.setdefault(project, []).append(source)

    for project, sources in sources_by_project.items():
        if len(sources) >= 2:
            return project, tuple(sources)
    return "", ()


def infer_defect_project(row: dict[str, object]) -> str:
    existing_project = _normalize_project_name(row.get("project"))
    if existing_project and existing_project != "Unknown":
        return existing_project

    feature_project = _project_from_feature_area(row)
    if feature_project:
        return feature_project

    ecu_to_modul = _normalize_text(row.get("ecu_to_modul")).upper()
    function_responsible = _normalize_text(row.get("function_responsible")).upper()
    software_version = _normalize_text(row.get("software_version"))
    lead_model = _normalize_text(row.get("lead_model")).upper()
    assigned_ecu_project = _project_from_assigned_ecu(row.get("assigned_ecu"))
    software_version_project = _project_from_software_version(software_version)

    if assigned_ecu_project:
        return assigned_ecu_project
    if software_version_project:
        return software_version_project

    if lead_model == "G78" and ecu_to_modul in {"CC", "KH"}:
        return "MGU"
    if lead_model == "G70" and ecu_to_modul == "FH":
        return "IDCEVO"
    if lead_model == "G68" and ecu_to_modul in {"CC", "FH"}:
        return "MGU"

    if lead_model == "G70" and function_responsible in {"RAINER FUNKE", "MARIJKE BRINKMANN"}:
        return "IDCEVO"

    if lead_model in {"U11", "U12"}:
        return "IDC"
    if lead_model in {"G50", "G58", "NA5", "NA6", "NA8"}:
        return "IDCEVO"
    if lead_model in {"G18", "G28"}:
        return "MGU"

    return "Unknown"


def _classify_final_defect_project(
    row: dict[str, object],
    *,
    mapped_project: object = "",
) -> dict[str, object]:
    technical_consensus_project, evidence_sources = _technical_project_consensus(row)
    if technical_consensus_project:
        return {
            "project": technical_consensus_project,
            "classification_source": "technical_consensus",
            "evidence_sources": list(evidence_sources),
        }

    feature_project = _project_from_feature_area(row)
    if feature_project:
        return {
            "project": feature_project,
            "classification_source": "explicit_feature",
            "evidence_sources": ["feature_area"],
        }

    normalized_mapped_project = _normalize_project_name(mapped_project)
    if normalized_mapped_project and normalized_mapped_project != "Unknown":
        return {
            "project": normalized_mapped_project,
            "classification_source": "business_mapping",
            "evidence_sources": ["top_aida"],
        }

    existing_project = _normalize_project_name(row.get("project"))
    if existing_project and existing_project != "Unknown":
        return {
            "project": existing_project,
            "classification_source": "legacy_project",
            "evidence_sources": ["project"],
        }

    fallback_project = infer_defect_project({**row, "project": ""})
    if fallback_project == "Unknown":
        return {
            "project": "Unknown",
            "classification_source": "unknown",
            "evidence_sources": [],
        }

    for source, project in _technical_project_evidence(row):
        if project == fallback_project:
            return {
                "project": fallback_project,
                "classification_source": "technical_fallback",
                "evidence_sources": [source],
            }
    return {
        "project": fallback_project,
        "classification_source": "heuristic_fallback",
        "evidence_sources": [],
    }


def infer_final_defect_project(
    row: dict[str, object],
    *,
    mapped_project: object = "",
) -> str:
    return str(_classify_final_defect_project(row, mapped_project=mapped_project)["project"])


def build_business_project_audit_payload(
    db_path: Path | str,
    *,
    asset_root: Path | str | None = None,
) -> dict[str, object]:
    assets = load_mapping_assets(asset_root)
    aida_map = _aida_dimension_map(assets.aida_rows)
    resolved_db_path = Path(db_path).resolve()
    connection_uri = f"{resolved_db_path.as_uri()}?mode=ro"
    conn = sqlite3.connect(connection_uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn, "octane_defects")
        if "defect_id" not in columns:
            raise ValueError("octane_defects missing required column: defect_id")

        def optional_text_expr(column_name: str) -> str:
            if column_name not in columns:
                return f"'' AS {column_name}"
            return f"COALESCE({column_name}, '') AS {column_name}"

        rows = conn.execute(
            f"""
            SELECT
                defect_id,
                {optional_text_expr('project')},
                {optional_text_expr('tproject')},
                {optional_text_expr('top_aida')},
                {optional_text_expr('product_areas')},
                {optional_text_expr('solution_cluster')},
                {optional_text_expr('assigned_ecu')},
                {optional_text_expr('software_version')},
                {optional_text_expr('lead_model')},
                {optional_text_expr('ecu_to_modul')},
                {optional_text_expr('function_responsible')}
            FROM octane_defects
            ORDER BY defect_id
            """
        ).fetchall()
    finally:
        conn.close()

    current_project_counts: Counter[str] = Counter()
    current_tproject_counts: Counter[str] = Counter()
    final_tproject_counts: Counter[str] = Counter()
    classification_source_counts: Counter[str] = Counter()
    correction_candidates: list[dict[str, object]] = []
    unresolved_records: list[dict[str, object]] = []
    tproject_update_count = 0

    for row in rows:
        values = dict(row)
        current_project = _normalize_project_name(values.get("project")) or "Unknown"
        current_tproject = _normalize_project_name(values.get("tproject"))
        mapped = aida_map.get(_normalize_text(values.get("top_aida")), {})
        classification = _classify_final_defect_project(
            values,
            mapped_project=mapped.get("project"),
        )
        final_tproject = str(classification["project"])
        classification_source = str(classification["classification_source"])
        evidence_sources = list(classification["evidence_sources"])

        current_project_counts[current_project] += 1
        current_tproject_counts[current_tproject or "<blank>"] += 1
        final_tproject_counts[final_tproject] += 1
        classification_source_counts[classification_source] += 1
        if current_tproject != final_tproject:
            tproject_update_count += 1

        record = {
            "defect_id": str(values.get("defect_id") or ""),
            "project": current_project,
            "tproject": current_tproject,
            "final_tproject": final_tproject,
            "classification_source": classification_source,
            "evidence_sources": evidence_sources,
            "top_aida": _normalize_text(values.get("top_aida")),
            "assigned_ecu": _normalize_text(values.get("assigned_ecu")),
            "software_version": _normalize_text(values.get("software_version")),
        }
        if (
            classification_source == "technical_consensus"
            and not _is_missing_dimension(values.get("project"))
            and current_project != final_tproject
        ):
            correction_candidates.append(record)
        if final_tproject == "Unknown":
            unresolved_records.append(record)

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "db_path": str(resolved_db_path),
        "rule_version": BUSINESS_PROJECT_RULE_VERSION,
        "summary": {
            "total_rows": len(rows),
            "current_project_counts": dict(sorted(current_project_counts.items())),
            "current_tproject_counts": dict(sorted(current_tproject_counts.items())),
            "final_tproject_counts": dict(sorted(final_tproject_counts.items())),
            "classification_source_counts": dict(sorted(classification_source_counts.items())),
            "tproject_update_count": tproject_update_count,
            "correction_candidate_count": len(correction_candidates),
            "unresolved_count": len(unresolved_records),
        },
        "correction_candidates": correction_candidates,
        "unresolved_records": unresolved_records,
    }


def infer_manual_run_project(raw_payload: dict[str, object], *, top_aida: str) -> str:
    name = _normalize_text(raw_payload.get("name")).upper()
    if "DTSV_CHINA-RSU" in name or "RSU" in name:
        return "RSU"
    if "RSE" in name:
        return "RSE"
    if "ENTRYEVO" in name or "NBTEVO" in name:
        return "IDCEVO"

    inferred = infer_defect_project(
        {
            "project": _nested_text(raw_payload, "program", "name"),
            "top_aida": top_aida,
            "product_areas": top_aida,
            "assigned_ecu": _normalize_text(raw_payload.get("target_ecu_conf_udf")),
            "software_version": _normalize_text(raw_payload.get("target_ecu_conf_udf")),
            "lead_model": _nested_text(raw_payload, "exec_model_series_udf", "name"),
            "ecu_to_modul": "",
            "function_responsible": "",
            "solution_cluster": "",
        }
    )
    if inferred != "Unknown":
        return inferred

    if any(signal in name for signal in ("IOS", "ANDROID", "HARMONYOS")):
        return "App"
    if any(signal in name for signal in ("IDC23_MINI", "IDC23_BMW", "HU-MGU_02_A", "IDC23")):
        return "IDC"
    if re.search(r"(^|[^A-Z0-9])IDC([^A-Z0-9]|$)", name):
        return "IDC"
    if "IDCEVO" in name:
        return "IDCEVO"
    if any(signal in name for signal in ("MGU22", "HU-MGU_02_L", "HU-MGU_01", "MGU21", "MGU18")):
        return "MGU"
    return ""


def _aida_dimension_map(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        top_aida = _normalize_text(row.get("top_aida"))
        if not top_aida:
            continue
        mapping[top_aida] = {
            "project": _normalize_text(row.get("project")),
            "fv": _normalize_text(row.get("fv")),
            "fvp": _normalize_text(row.get("fvp")),
        }
    return mapping


def _vin_market_map(rows: list[dict[str, str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        vin_prefix = _normalize_text(row.get("vin_prefix") or row.get("VIN"))
        market = _normalize_text(row.get("market") or row.get("ISO Countrycode (INT)"))
        if vin_prefix and market:
            mapping[vin_prefix] = market
    return mapping


def _market_from_vin(value: object, mapping: dict[str, str]) -> str:
    vin_text = _normalize_text(value)
    if not vin_text or not mapping:
        return ""
    markets: list[str] = []
    for vin in (part.strip() for part in re.split(r"[,;\s]+", vin_text) if part.strip()):
        for vin_prefix, market in mapping.items():
            if vin.startswith(vin_prefix) and market not in markets:
                markets.append(market)
                break
    return ", ".join(markets)


def _fvp_from_fv(value: object) -> str:
    fv = _normalize_text(value)
    if not fv:
        return ""

    fvp_mapping = {
        "DIPS_TSP_Call_Services": "Tianhua",
        "DIPS_TSP_CD_Updates": "Tianhua",
        "DIPS_TSP_Remote_Services": "Tianhua",
        "eMob": "Tianhua",
        "DIPS_TSP_MobileApps": "Tianhua",
        "DIPS_TSP_Enabler": "Tianhua",
        "IuK_TSP_Navi": "Tony",
        "IuK_TSP_AZV": "Xu Miao",
        "IuK_TSP_Entertainment": "Xu Miao",
        "IuK_TSP_Audio": "Xu Miao",
        "IuK_TSP_Connectivity": "Xu Miao",
        "DIPS_TSP_Car_Apps_CN": "Huanran",
        "IuK_TSP_HMI": "Jerry",
        "DIPS_TSP_RSU": "Jerry",
        "IuK_TSP_Carfunctions": "Jerry",
        "IuK_TSP_Perso CN": "Jerry",
        "RSU": "Jerry",
        "Mybmw App": "Marin",
    }
    return fvp_mapping.get(fv, "")


def _is_missing_dimension(value: object) -> bool:
    text = str(value or "").strip()
    return not text or text.casefold() in {"unknown", "unclassified"}


def _dimension_change_kind(current_value: object, derived_value: str) -> str | None:
    current_text = str(current_value or "").strip()
    derived_text = str(derived_value or "").strip()
    derived_missing = _is_missing_dimension(derived_value)
    if _is_missing_dimension(current_value):
        return "fillable" if derived_text and not derived_missing else None
    if derived_text and not derived_missing and derived_text != current_text:
        return "conflict"
    return None


def _parse_json_object(value: object) -> dict[str, object]:
    text = _normalize_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested_text(value: object, *keys: str) -> str:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _normalize_text(current)


def _first_named_value(value: object) -> str:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            for item in data:
                name = _nested_text(item, "name")
                if name:
                    return name
        return _nested_text(value, "name")
    if isinstance(value, list):
        for item in value:
            name = _first_named_value(item)
            if name:
                return name
    return ""


def _iso_test_week(value: object) -> str:
    text = _normalize_text(value)
    if not text:
        return ""

    normalized = text.replace("Z", "+00:00")
    try:
        finished_at = datetime.fromisoformat(normalized)
    except ValueError:
        return ""

    iso_year, iso_week, _ = finished_at.isocalendar()
    return f"{iso_year}-CW{iso_week:02d}"


def run_processor_pipeline(
    db_path: Path | str,
    *,
    asset_root: Path | str | None = None,
    dry_run: bool = False,
    report_path: Path | str | None = None,
    manual_run_ids: Sequence[str] | None = None,
    defect_ids: Sequence[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    assets = load_mapping_assets(asset_root)
    aida_map = _aida_dimension_map(assets.aida_rows)
    vin_market_map = _vin_market_map(assets.vin_project_rows)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        defect_columns = _table_columns(conn, "octane_defects")
        _ensure_tproject_column(conn, defect_columns, apply=not dry_run)
        manual_run_columns = _table_columns(conn, "octane_manual_runs")
        normalized_manual_run_ids = (
            tuple(sorted({str(run_id).strip() for run_id in manual_run_ids if str(run_id).strip()}))
            if manual_run_ids is not None
            else None
        )
        normalized_defect_ids = (
            tuple(sorted({str(defect_id).strip() for defect_id in defect_ids if str(defect_id).strip()}))
            if defect_ids is not None
            else None
        )
        manual_parent_defect_ids: set[str] = set()
        if normalized_manual_run_ids and "defect_id" in manual_run_columns:
            for start_index in range(0, len(normalized_manual_run_ids), 900):
                batch_ids = normalized_manual_run_ids[start_index : start_index + 900]
                placeholders = ", ".join("?" for _ in batch_ids)
                manual_parent_defect_ids.update(
                    str(row[0]).strip()
                    for row in conn.execute(
                        f"SELECT DISTINCT defect_id FROM octane_manual_runs WHERE mr_id IN ({placeholders})",
                        batch_ids,
                    ).fetchall()
                    if str(row[0] or "").strip()
                )

        scoped_defect_ids: tuple[str, ...] | None
        if normalized_defect_ids is None and normalized_manual_run_ids is None:
            scoped_defect_ids = None
        else:
            scoped_defect_ids = tuple(sorted(set(normalized_defect_ids or ()) | manual_parent_defect_ids))
        defect_select_parts = [
            "defect_id",
            "project",
            "tproject" if "tproject" in defect_columns else "'' AS tproject",
            "market" if "market" in defect_columns else "'' AS market",
            "top_aida" if "top_aida" in defect_columns else "'' AS top_aida",
            "pu" if "pu" in defect_columns else "'' AS pu",
            "vin" if "vin" in defect_columns else "'' AS vin",
            "assigned_ecu" if "assigned_ecu" in defect_columns else "'' AS assigned_ecu",
            "software_version" if "software_version" in defect_columns else "'' AS software_version",
            "lead_model" if "lead_model" in defect_columns else "'' AS lead_model",
            "first_use_sop_of_function" if "first_use_sop_of_function" in defect_columns else "'' AS first_use_sop_of_function",
            "product_areas" if "product_areas" in defect_columns else "'' AS product_areas",
            "solution_cluster" if "solution_cluster" in defect_columns else "'' AS solution_cluster",
            "ecu_to_modul" if "ecu_to_modul" in defect_columns else "'' AS ecu_to_modul",
            "function_responsible" if "function_responsible" in defect_columns else "'' AS function_responsible",
            "fv",
            "fvp",
        ]
        defect_selection_started_at = time.perf_counter()
        if scoped_defect_ids is None:
            defect_rows = conn.execute(
                f"SELECT {', '.join(defect_select_parts)} FROM octane_defects ORDER BY defect_id"
            ).fetchall()
        elif not scoped_defect_ids:
            defect_rows = []
        else:
            defect_rows = []
            for start_index in range(0, len(scoped_defect_ids), 900):
                batch_ids = scoped_defect_ids[start_index : start_index + 900]
                placeholders = ", ".join("?" for _ in batch_ids)
                defect_rows.extend(
                    conn.execute(
                        f"""
                        SELECT {', '.join(defect_select_parts)}
                        FROM octane_defects
                        WHERE defect_id IN ({placeholders})
                        ORDER BY defect_id
                        """,
                        batch_ids,
                    ).fetchall()
                )
        defect_selection_duration_seconds = round(time.perf_counter() - defect_selection_started_at, 3)

        defect_processing_started_at = time.perf_counter()
        defect_updates: list[tuple[str, str, str, str]] = []
        duplicate_search_dimension_updates: set[str] = set()
        tproject_updates: list[tuple[str, str]] = []
        fillable_rows: list[dict[str, object]] = []
        conflict_rows: list[dict[str, object]] = []
        resolved_defect_dimensions: dict[str, dict[str, str]] = {}
        for row in defect_rows:
            top_aida = str(row["top_aida"] or "").strip()
            mapped = aida_map.get(top_aida, {})
            derived_project = str(mapped.get("project") or infer_defect_project(dict(row))).strip()
            tproject = infer_final_defect_project(dict(row), mapped_project=mapped.get("project"))
            derived_fv = str(mapped.get("fv") or "").strip()
            derived_fvp = str(mapped.get("fvp") or "").strip() or _fvp_from_fv(derived_fv)
            derived_pu = str(row["first_use_sop_of_function"] or "").strip()
            derived_market = _market_from_vin(row["vin"], vin_market_map)
            normalized_current_project = _normalize_project_name(row["project"])
            project = (
                normalized_current_project
                if not _is_missing_dimension(row["project"])
                else _normalize_project_name(derived_project)
            )
            fv = str(row["fv"] or "").strip() if not _is_missing_dimension(row["fv"]) else derived_fv
            fvp = str(row["fvp"] or "").strip() if not _is_missing_dimension(row["fvp"]) else derived_fvp
            pu = str(row["pu"] or "").strip() if not _is_missing_dimension(row["pu"]) else derived_pu
            market = str(row["market"] or "").strip() if not _is_missing_dimension(row["market"]) else derived_market

            row_fillable: dict[str, str] = {}
            row_conflicts: dict[str, dict[str, str]] = {}
            for field_name, current_value, derived_value in (
                ("project", row["project"], project),
                ("fv", row["fv"], derived_fv),
                ("fvp", row["fvp"], derived_fvp),
                ("pu", row["pu"], derived_pu),
                ("market", row["market"], derived_market),
            ):
                change_kind = _dimension_change_kind(current_value, derived_value)
                if change_kind == "fillable":
                    row_fillable[field_name] = str(derived_value or "")
                elif change_kind == "conflict":
                    row_conflicts[field_name] = {
                        "current": str(current_value or "").strip(),
                        "derived": str(derived_value or "").strip(),
                    }

            if row_fillable:
                fillable_rows.append({"entity": "defect", "defect_id": str(row["defect_id"]), "fields": row_fillable})
            if row_conflicts:
                conflict_rows.append({"entity": "defect", "defect_id": str(row["defect_id"]), "fields": row_conflicts})

            if "tproject" in defect_columns and tproject != str(row["tproject"] or "").strip():
                tproject_updates.append((tproject, str(row["defect_id"])))

            resolved_defect_dimensions[str(row["defect_id"])] = {
                "project": project,
                "fv": fv,
                "fvp": fvp,
                "top_aida": top_aida,
                "pu": pu,
            }

            if project != str(row["project"] or "").strip() or fv != str(row["fv"] or "").strip() or fvp != str(row["fvp"] or "").strip() or pu != str(row["pu"] or "").strip() or market != str(row["market"] or "").strip():
                defect_updates.append((project, market, fv, fvp, pu, str(row["defect_id"])))
                if project != str(row["project"] or "").strip() or pu != str(row["pu"] or "").strip():
                    duplicate_search_dimension_updates.add(str(row["defect_id"]))

        if defect_updates and not dry_run:
            conn.executemany(
                "UPDATE octane_defects SET project=?, market=?, fv=?, fvp=?, pu=? WHERE defect_id=?",
                defect_updates,
            )
        if tproject_updates and not dry_run:
            conn.executemany(
                "UPDATE octane_defects SET tproject=? WHERE defect_id=?",
                tproject_updates,
            )
        if duplicate_search_dimension_updates and not dry_run:
            advance_duplicate_search_source_revision(conn)
        defect_processing_duration_seconds = round(time.perf_counter() - defect_processing_started_at, 3)
        if progress is not None:
            progress(
                "Processor defects: "
                f"selected={len(defect_rows)} processed={len(defect_rows)} "
                f"updated={len(defect_updates)} tproject_updates={len(tproject_updates)} "
                f"select_duration_seconds={defect_selection_duration_seconds} "
                f"process_duration_seconds={defect_processing_duration_seconds}"
            )

        run_select_parts = [
            "mr_id",
            "defect_id",
            "project" if "project" in manual_run_columns else "'' AS project",
            "fv" if "fv" in manual_run_columns else "'' AS fv",
            "fvp" if "fvp" in manual_run_columns else "'' AS fvp",
            "test_week" if "test_week" in manual_run_columns else "'' AS test_week",
            "pu" if "pu" in manual_run_columns else "'' AS pu",
            "top_aida" if "top_aida" in manual_run_columns else "'' AS top_aida",
            "tester" if "tester" in manual_run_columns else "'' AS tester",
            "raw_json" if "raw_json" in manual_run_columns else "'{}' AS raw_json",
        ]
        manual_run_selection_started_at = time.perf_counter()
        if normalized_manual_run_ids is None:
            run_rows = conn.execute(
                f"SELECT {', '.join(run_select_parts)} FROM octane_manual_runs ORDER BY mr_id"
            ).fetchall()
        elif not normalized_manual_run_ids:
            run_rows = []
        else:
            run_rows = []
            for start_index in range(0, len(normalized_manual_run_ids), 900):
                batch_ids = normalized_manual_run_ids[start_index : start_index + 900]
                placeholders = ", ".join("?" for _ in batch_ids)
                run_rows.extend(
                    conn.execute(
                        f"""
                        SELECT {', '.join(run_select_parts)}
                        FROM octane_manual_runs
                        WHERE mr_id IN ({placeholders})
                        ORDER BY mr_id
                        """,
                        batch_ids,
                    ).fetchall()
                )
        manual_run_selection_duration_seconds = round(time.perf_counter() - manual_run_selection_started_at, 3)
        run_update_fields = [
            field_name
            for field_name in ("project", "fv", "fvp", "test_week", "pu", "top_aida", "tester")
            if field_name in manual_run_columns
        ]
        manual_run_processing_started_at = time.perf_counter()
        run_updates: list[tuple[object, ...]] = []
        for row in run_rows:
            parent = resolved_defect_dimensions.get(str(row["defect_id"] or "").strip(), {})

            raw_payload = _parse_json_object(row["raw_json"])
            raw_top_aida = _first_named_value(raw_payload.get("product_areas"))
            mapped_run = aida_map.get(raw_top_aida, {}) if raw_top_aida else {}

            derived_project = str(parent.get("project") or "").strip() or str(mapped_run.get("project") or "").strip() or infer_manual_run_project(raw_payload, top_aida=raw_top_aida)
            derived_fv = str(parent.get("fv") or "").strip() or str(mapped_run.get("fv") or "").strip()
            derived_fvp = str(parent.get("fvp") or "").strip() or str(mapped_run.get("fvp") or "").strip() or _fvp_from_fv(derived_fv)
            derived_test_week = _iso_test_week(raw_payload.get("finished_udf") or raw_payload.get("finished"))
            derived_pu = _nested_text(raw_payload, "set_udf", "name") or str(parent.get("pu") or "").strip()
            derived_top_aida = raw_top_aida or str(parent.get("top_aida") or "").strip()
            derived_tester = (
                _nested_text(raw_payload, "run_by", "full_name")
                or _nested_text(raw_payload, "run_by", "name")
                or _nested_text(raw_payload, "author", "full_name")
                or _nested_text(raw_payload, "author", "name")
            )

            normalized_current_project = _normalize_project_name(row["project"])
            project = (
                normalized_current_project
                if not _is_missing_dimension(row["project"])
                else _normalize_project_name(derived_project)
            )
            fv = str(row["fv"] or "").strip() if not _is_missing_dimension(row["fv"]) else derived_fv
            fvp = str(row["fvp"] or "").strip() if not _is_missing_dimension(row["fvp"]) else derived_fvp
            test_week = str(row["test_week"] or "").strip() if not _is_missing_dimension(row["test_week"]) else derived_test_week
            pu = str(row["pu"] or "").strip() if not _is_missing_dimension(row["pu"]) else derived_pu
            top_aida = str(row["top_aida"] or "").strip() if not _is_missing_dimension(row["top_aida"]) else derived_top_aida
            tester = str(row["tester"] or "").strip() if not _is_missing_dimension(row["tester"]) else derived_tester

            row_fillable: dict[str, str] = {}
            row_conflicts: dict[str, dict[str, str]] = {}
            for field_name, current_value, derived_value in (
                ("project", row["project"], project),
                ("fv", row["fv"], derived_fv),
                ("fvp", row["fvp"], derived_fvp),
                ("test_week", row["test_week"], derived_test_week),
                ("pu", row["pu"], derived_pu),
                ("top_aida", row["top_aida"], derived_top_aida),
                ("tester", row["tester"], derived_tester),
            ):
                if field_name not in manual_run_columns:
                    continue
                change_kind = _dimension_change_kind(current_value, derived_value)
                if change_kind == "fillable":
                    row_fillable[field_name] = str(derived_value or "")
                elif change_kind == "conflict":
                    row_conflicts[field_name] = {
                        "current": str(current_value or "").strip(),
                        "derived": str(derived_value or "").strip(),
                    }

            if row_fillable:
                fillable_rows.append({"entity": "manual_run", "mr_id": str(row["mr_id"]), "fields": row_fillable})
            if row_conflicts:
                conflict_rows.append({"entity": "manual_run", "mr_id": str(row["mr_id"]), "fields": row_conflicts})

            resolved_run_values = {
                "project": project,
                "fv": fv,
                "fvp": fvp,
                "test_week": test_week,
                "pu": pu,
                "top_aida": top_aida,
                "tester": tester,
            }
            if any(resolved_run_values[field_name] != str(row[field_name] or "").strip() for field_name in run_update_fields):
                run_updates.append(tuple(resolved_run_values[field_name] for field_name in run_update_fields) + (str(row["mr_id"]),))

        if run_updates and not dry_run:
            set_clause = ", ".join(f"{field_name}=?" for field_name in run_update_fields)
            conn.executemany(
                f"UPDATE octane_manual_runs SET {set_clause} WHERE mr_id=?",
                run_updates,
            )
        manual_run_processing_duration_seconds = round(time.perf_counter() - manual_run_processing_started_at, 3)
        if progress is not None:
            progress(
                "Processor manual runs: "
                f"selected={len(run_rows)} processed={len(run_rows)} updated={len(run_updates)} "
                f"select_duration_seconds={manual_run_selection_duration_seconds} "
                f"process_duration_seconds={manual_run_processing_duration_seconds}"
            )

        updated_defect_ids = sorted(
            {str(update[-1]) for update in defect_updates}
            | {str(defect_id) for _, defect_id in tproject_updates}
        )

        if dry_run:
            conn.rollback()
            if report_path is not None:
                resolved_report_path = Path(report_path)
                resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
                resolved_report_path.write_text(
                    json.dumps(
                        {
                            "fillable_rows": fillable_rows,
                            "conflict_rows": conflict_rows,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            return {
                "defect_updates": 0,
                "run_updates": 0,
                "tproject_updates": 0,
                "fillable_rows": len(fillable_rows),
                "conflict_rows": len(conflict_rows),
                "updated_defect_ids": [],
            }

        commit_started_at = time.perf_counter()
        conn.commit()
        if progress is not None:
            progress(
                "Processor SQLite commit: "
                f"duration_seconds={round(time.perf_counter() - commit_started_at, 3)}"
            )
        return {
            "defect_updates": len(defect_updates),
            "run_updates": len(run_updates),
            "tproject_updates": len(tproject_updates),
            "updated_defect_ids": updated_defect_ids,
        }
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1]).strip()
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _ensure_tproject_column(
    conn: sqlite3.Connection,
    columns: set[str],
    *,
    apply: bool,
) -> bool:
    if "tproject" in columns or not apply:
        return False
    conn.execute("ALTER TABLE octane_defects ADD COLUMN tproject TEXT")
    columns.add("tproject")
    return True


def _extract_raw_json_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return _first_named_value(value)
    return _normalize_text(value)


def _first_raw_json_text(raw_payload: dict[str, object], *field_names: str) -> str:
    for field_name in field_names:
        text = _extract_raw_json_text(raw_payload.get(field_name))
        if text:
            return text
    return ""


def _canonical_solution_cluster(value: object) -> tuple[str, bool]:
    text = _normalize_text(value)
    if text.casefold() == "solution cluster:china product":
        return "China Product", True
    return text, False


def _parse_backfill_raw_json(value: object) -> tuple[dict[str, object], bool]:
    text = _normalize_text(value)
    if not text:
        return {}, False
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, True
    if not isinstance(parsed, dict):
        return {}, True
    return parsed, False


def backfill_china_scope_fields(
    db_path: Path | str,
    *,
    apply: bool = False,
) -> dict[str, int]:
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn, "octane_defects")
        required_columns = {
            "defect_id",
            "raw_json",
            "solution_cluster",
            "defect_category",
        }
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise ValueError(
                "octane_defects missing required columns: " + ", ".join(missing_columns)
            )

        rows = conn.execute(
            """
            SELECT
                defect_id,
                solution_cluster,
                defect_category,
                raw_json
            FROM octane_defects
            ORDER BY defect_id
            """
        ).fetchall()

        updates: list[tuple[str, str, str]] = []
        filled_solution_cluster_rows = 0
        filled_defect_category_rows = 0
        normalized_solution_cluster_rows = 0
        invalid_json_rows = 0
        conflict_ids: set[str] = set()

        for row in rows:
            defect_id = str(row["defect_id"] or "").strip()
            raw_payload, is_invalid_json = _parse_backfill_raw_json(row["raw_json"])
            if is_invalid_json:
                invalid_json_rows += 1

            current_solution_cluster, current_solution_cluster_normalized = (
                _canonical_solution_cluster(row["solution_cluster"])
            )
            raw_solution_cluster, raw_solution_cluster_normalized = _canonical_solution_cluster(
                _first_raw_json_text(
                    raw_payload,
                    "solution_cluster",
                    "solutionCluster",
                    "solution_cluster_udf",
                )
            )
            current_defect_category = _normalize_text(row["defect_category"])
            raw_defect_category = _first_raw_json_text(
                raw_payload,
                "defect_category",
                "defectCategory",
                "problem_category_udf",
            )

            next_solution_cluster = current_solution_cluster
            next_defect_category = current_defect_category
            if current_solution_cluster:
                if current_solution_cluster_normalized:
                    normalized_solution_cluster_rows += 1
                if (
                    raw_solution_cluster
                    and current_solution_cluster.casefold() != raw_solution_cluster.casefold()
                ):
                    conflict_ids.add(defect_id)
            elif raw_solution_cluster:
                next_solution_cluster = raw_solution_cluster
                filled_solution_cluster_rows += 1
                if raw_solution_cluster_normalized:
                    normalized_solution_cluster_rows += 1

            if current_defect_category:
                if (
                    raw_defect_category
                    and current_defect_category.casefold() != raw_defect_category.casefold()
                ):
                    conflict_ids.add(defect_id)
            elif raw_defect_category:
                next_defect_category = raw_defect_category
                filled_defect_category_rows += 1

            if (
                next_solution_cluster != _normalize_text(row["solution_cluster"])
                or next_defect_category != _normalize_text(row["defect_category"])
            ):
                updates.append((next_solution_cluster, next_defect_category, defect_id))

        if apply and updates:
            conn.executemany(
                """
                UPDATE octane_defects
                SET solution_cluster = ?, defect_category = ?
                WHERE defect_id = ?
                """,
                updates,
            )
            conn.commit()

        return {
            "candidate_rows": len(rows),
            "updated_rows": len(updates) if apply else 0,
            "filled_solution_cluster_rows": filled_solution_cluster_rows,
            "filled_defect_category_rows": filled_defect_category_rows,
            "normalized_solution_cluster_rows": normalized_solution_cluster_rows,
            "conflict_rows": len(conflict_ids),
            "invalid_json_rows": invalid_json_rows,
        }
    finally:
        conn.close()


def backfill_defect_projects(
    db_path: Path | str,
    *,
    asset_root: Path | str | None = None,
    apply: bool = False,
    only_unknown: bool = True,
) -> dict[str, object]:
    assets = load_mapping_assets(asset_root)
    aida_map = _aida_dimension_map(assets.aida_rows)
    conn = sqlite3.connect(db_path)
    try:
        columns = _table_columns(conn, "octane_defects")
        schema_updated = _ensure_tproject_column(conn, columns, apply=apply)
        required_columns = {"defect_id", "project", "top_aida", "assigned_ecu", "software_version", "lead_model"}
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise ValueError(f"octane_defects missing required columns: {', '.join(missing_columns)}")

        where_clause = "WHERE 1=1"
        if only_unknown:
            where_clause += " AND UPPER(TRIM(COALESCE(project, ''))) IN ('', 'UNKNOWN')"

        product_areas_select = "product_areas" if "product_areas" in columns else "'' AS product_areas"
        solution_cluster_select = (
            "solution_cluster" if "solution_cluster" in columns else "'' AS solution_cluster"
        )
        ecu_to_modul_select = "ecu_to_modul" if "ecu_to_modul" in columns else "'' AS ecu_to_modul"
        function_responsible_select = (
            "function_responsible"
            if "function_responsible" in columns
            else "'' AS function_responsible"
        )
        tproject_select = "tproject" if "tproject" in columns else "'' AS tproject"

        rows = conn.execute(
            f"""
            SELECT
                defect_id,
                project,
                {tproject_select},
                top_aida,
                assigned_ecu,
                software_version,
                lead_model,
                {product_areas_select},
                {solution_cluster_select},
                {ecu_to_modul_select},
                {function_responsible_select}
            FROM octane_defects
            {where_clause}
            ORDER BY defect_id
            """
        ).fetchall()

        predicted_counts: dict[str, int] = {}
        project_updates: list[tuple[str, str]] = []
        tproject_updates: list[tuple[str, str]] = []
        updated_defect_ids: set[str] = set()
        for (
            defect_id,
            project,
            tproject,
            top_aida,
            assigned_ecu,
            software_version,
            lead_model,
            product_areas,
            solution_cluster,
            ecu_to_modul,
            function_responsible,
        ) in rows:
            mapped = aida_map.get(str(top_aida or "").strip(), {})
            predicted = infer_final_defect_project(
                {
                    "project": project,
                    "top_aida": top_aida,
                    "assigned_ecu": assigned_ecu,
                    "software_version": software_version,
                    "lead_model": lead_model,
                    "product_areas": product_areas,
                    "solution_cluster": solution_cluster,
                    "ecu_to_modul": ecu_to_modul,
                    "function_responsible": function_responsible,
                },
                mapped_project=mapped.get("project"),
            )
            predicted_counts[predicted] = predicted_counts.get(predicted, 0) + 1
            normalized_defect_id = str(defect_id)
            if predicted != "Unknown" and _is_missing_dimension(project):
                project_updates.append((predicted, normalized_defect_id))
                updated_defect_ids.add(normalized_defect_id)
            if "tproject" in columns and predicted != _normalize_project_name(tproject):
                tproject_updates.append((predicted, normalized_defect_id))
                updated_defect_ids.add(normalized_defect_id)

        if apply and project_updates:
            conn.executemany(
                """
                UPDATE octane_defects
                SET project=?
                WHERE defect_id=?
                """,
                project_updates,
            )
        if apply and tproject_updates:
            conn.executemany(
                """
                UPDATE octane_defects
                SET tproject=?
                WHERE defect_id=?
                """,
                tproject_updates,
            )
        if apply and (updated_defect_ids or schema_updated):
            conn.commit()

        return {
            "candidate_rows": len(rows),
            "updated_rows": len(updated_defect_ids) if apply else 0,
            "tproject_updates": len(tproject_updates) if apply else 0,
            "predicted_counts": dict(sorted(predicted_counts.items())),
        }
    finally:
        conn.close()


def sync_dimension_fields(
    db_path: Path | str,
    defect_dimension_rows: list[dict],
    run_dimension_rows: list[dict],
) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        defect_payload = [
            (
                row.get("project", ""),
                row.get("market", ""),
                row.get("pu", ""),
                row.get("fv", ""),
                row.get("fvp", ""),
                row.get("team", ""),
                row.get("lead_model", ""),
                row["id"],
            )
            for row in defect_dimension_rows
        ]
        run_payload = [
            (
                row.get("project", ""),
                row.get("fv", ""),
                row.get("fvp", ""),
                row.get("team", ""),
                row.get("lead_model", ""),
                row["id"],
            )
            for row in run_dimension_rows
        ]

        conn.executemany(
            """
            UPDATE octane_defects
            SET project=?, market=?, pu=?, fv=?, fvp=?, team=?, lead_model=?
            WHERE defect_id=?
            """,
            defect_payload,
        )
        defect_updates = conn.total_changes
        conn.executemany(
            """
            UPDATE octane_manual_runs
            SET project=?, fv=?, fvp=?, team=?, lead_model=?
            WHERE mr_id=?
            """,
            run_payload,
        )
        run_updates = conn.total_changes - defect_updates
        conn.commit()
        return {"defect_updates": defect_updates, "run_updates": run_updates}
    finally:
        conn.close()