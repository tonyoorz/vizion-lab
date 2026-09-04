from __future__ import annotations

from typing import Any


CHINA_SCOPE_RULE_VERSION = "china-scope-v1"

CHINA_SOLUTION_CLUSTERS: frozenset[str] = frozenset(
    {
        "china product",
        "ipa cn",
        "speech cn",
        "navigation cn",
        "ent_and_con cn",
        "navigation twn",
        "etc jp",
    }
)

CHINA_DEFECT_CATEGORIES: frozenset[str] = frozenset(
    {
        "ent/connected_music_china",
        "application navigation china hk",
        "cn dkr",
        "cn backend_service",
        "cn etc",
        "cn for asia contact book",
        "cn for asiatextsupportlib",
        "cn for global festive app",
        "cn for asia speller",
        "cn for usb media service",
        "cn intelligent reminder",
        "cn ipa_visualization",
        "cn launcher",
        "cn ipa_intelligence",
        "cn llm",
        "cn media",
        "cn mls",
        "cn nav",
        "cn mpp",
        "cn social login via wechat",
        "cn store",
        "cn speech",
        "cn vod",
        "cn wechat",
        "cn_core_ui",
        "cn_experience_mode",
        "cn_huawei_hicar",
        "cn_karaoke",
        "cn_gaming",
        "cn_ui_lib",
        "cn_platform",
        "cn_subscription",
        "oap/weather/cn",
        "offboard eroute",
        "road map taiwan",
        "application navigation taiwan",
        "international keyboard",
        "apps_festivalmode",
        "cn_video_conference",
        "cn adas setting",
        "cn_demo_mode",
        "cn ad view",
        "cn video casting",
        "cn payment",
        "personalization: cn data privacy",
        "personalization: driver authorization",
        "byod cn",
        "ent-podcasts_china",
        "cn_ent ai",
        "ent/podcasts_china",
        "cn_iccoa_carlink",
    }
)


def normalize_china_scope_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def is_china_specific(*, solution_cluster: Any, defect_category: Any) -> bool:
    normalized_solution_cluster = normalize_china_scope_value(solution_cluster)
    if normalized_solution_cluster:
        return normalized_solution_cluster in CHINA_SOLUTION_CLUSTERS
    return normalize_china_scope_value(defect_category) in CHINA_DEFECT_CATEGORIES


def _sql_literal(value: str) -> str:
    return "'{}'".format(value.replace("'", "''"))


def _normalized_sql_expr(expression: str) -> str:
    return "LOWER(TRIM(COALESCE(CAST(({expression}) AS TEXT), '')))".format(
        expression=expression,
    )


def _sql_value_list(values: frozenset[str]) -> str:
    return ", ".join(_sql_literal(value) for value in sorted(values))


def build_china_scope_sql_expr(
    *,
    solution_cluster_expr: str,
    defect_category_expr: str,
    china_label: str,
    global_label: str,
) -> str:
    normalized_solution_cluster = _normalized_sql_expr(solution_cluster_expr)
    normalized_defect_category = _normalized_sql_expr(defect_category_expr)
    return """
        CASE
            WHEN {solution_cluster} IN ({solution_clusters})
                THEN {china_label}
            WHEN {solution_cluster} = ''
                AND {defect_category} IN ({defect_categories})
                THEN {china_label}
            ELSE {global_label}
        END
    """.format(
        solution_cluster=normalized_solution_cluster,
        solution_clusters=_sql_value_list(CHINA_SOLUTION_CLUSTERS),
        defect_category=normalized_defect_category,
        defect_categories=_sql_value_list(CHINA_DEFECT_CATEGORIES),
        china_label=_sql_literal(china_label),
        global_label=_sql_literal(global_label),
    )