from __future__ import annotations

from backend.analytics.china_scope import build_china_scope_sql_expr


def build_feature_region_sql_expr(
    *,
    solution_cluster_expr: str,
    defect_category_expr: str,
) -> str:
    return build_china_scope_sql_expr(
        solution_cluster_expr=solution_cluster_expr,
        defect_category_expr=defect_category_expr,
        china_label="China Specific",
        global_label="Global",
    )


def build_reference_project_sql_expr(
    *,
    name_expr: str,
    target_ecu_conf_expr: str,
    top_aida_expr: str,
    fallback_project_expr: str,
) -> str:
    return """
        CASE
            WHEN LOWER(TRIM({name_expr})) LIKE '%dtsv_china-rsu%'
                OR LOWER(TRIM({name_expr})) LIKE '%rsu%'
                OR LOWER(TRIM({top_aida_expr})) LIKE '%[sys_rsu]%'
                THEN 'RSU'
            WHEN LOWER(TRIM({name_expr})) LIKE '%rse%'
                OR LOWER(TRIM({top_aida_expr})) LIKE '%rear seat entertainment%'
                THEN 'RSE'
            WHEN LOWER(TRIM({target_ecu_conf_expr})) LIKE '%my bmw%'
                OR LOWER(TRIM({name_expr})) LIKE '%ios%'
                OR LOWER(TRIM({name_expr})) LIKE '%android%'
                OR LOWER(TRIM({name_expr})) LIKE '%harmonyos%'
                THEN 'App'
            WHEN LOWER(TRIM({target_ecu_conf_expr})) LIKE '%idcevo%'
                OR LOWER(TRIM({name_expr})) LIKE '%idcevo%'
                THEN 'IDCEVO'
            WHEN LOWER(TRIM({target_ecu_conf_expr})) LIKE '%hu-mgu_02_a%'
                OR LOWER(TRIM({name_expr})) LIKE '%hu-mgu_02_a%'
                OR LOWER(TRIM({name_expr})) LIKE '%idc23%'
                THEN 'IDC'
            WHEN LOWER(TRIM({target_ecu_conf_expr})) LIKE '%hu-mgu_02_l%'
                OR LOWER(TRIM({target_ecu_conf_expr})) LIKE '%hu-mgu_01%'
                OR LOWER(TRIM({name_expr})) LIKE '%mgu22%'
                OR LOWER(TRIM({name_expr})) LIKE '%mgu21%'
                OR LOWER(TRIM({name_expr})) LIKE '%mgu18%'
                OR LOWER(TRIM({name_expr})) LIKE '%hu-mgu_02_l%'
                OR LOWER(TRIM({name_expr})) LIKE '%hu-mgu_01%'
                THEN 'MGU'
            WHEN TRIM({fallback_project_expr}) <> ''
                THEN TRIM({fallback_project_expr})
            ELSE 'Unknown'
        END
    """.format(
        name_expr=name_expr,
        target_ecu_conf_expr=target_ecu_conf_expr,
        top_aida_expr=top_aida_expr,
        fallback_project_expr=fallback_project_expr,
    )


def build_tpmdashboard_project_sql_expr(
    *,
    name_expr: str,
    target_ecu_conf_expr: str,
    top_aida_expr: str,
    fallback_project_expr: str,
) -> str:
    return build_reference_project_sql_expr(
        name_expr=name_expr,
        target_ecu_conf_expr=target_ecu_conf_expr,
        top_aida_expr=top_aida_expr,
        fallback_project_expr=fallback_project_expr,
    )


def build_iso_test_week_sql_expr(*, finished_expr: str, fallback_test_week_expr: str) -> str:
    thursday_expr = "date({finished_expr}, '-3 days', 'weekday 4')".format(
        finished_expr=finished_expr,
    )
    return """
        CASE
            WHEN TRIM({finished_expr}) <> ''
                THEN (
                    strftime('%Y', {thursday_expr})
                    || '-CW'
                    || printf(
                        '%02d',
                        CAST(((CAST(strftime('%j', {thursday_expr}) AS INTEGER) - 1) / 7) + 1 AS INTEGER)
                    )
                )
            WHEN TRIM({fallback_test_week_expr}) <> ''
                THEN TRIM({fallback_test_week_expr})
            ELSE ''
        END
    """.format(
        finished_expr=finished_expr,
        thursday_expr=thursday_expr,
        fallback_test_week_expr=fallback_test_week_expr,
    )