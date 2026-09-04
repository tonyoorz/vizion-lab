import json
import sqlite3

import backend.analytics_cli as analytics_cli
from backend.analytics import read_models
import pytest
from backend.analytics.china_scope import (
    CHINA_SCOPE_RULE_VERSION,
    build_china_scope_sql_expr,
    is_china_specific,
)
from backend.analytics_cli import main
from backend.analytics.dashboard_snapshot import build_full_picture_snapshot_version
from backend.analytics.read_models import (
    FullPictureDashboardQuery,
    _get_ticket_china_scope,
    _load_defect_rows,
)
from backend.analytics.processor import backfill_china_scope_fields
from backend.analytics.schema import ensure_schema
from backend.analytics.testing_coverage_hot import (
    _build_testing_coverage_snapshot_version,
    refresh_materialized_testing_coverage,
)
from backend.analytics.testing_coverage_models import (
    TestingCoverageDataNotReadyError as CoverageDataNotReadyError,
    _resolve_active_testing_snapshot_version,
    build_testing_coverage_filters,
)


def test_exact_canonical_solution_cluster_is_china_specific() -> None:
    assert is_china_specific(
        solution_cluster="China Product",
        defect_category="",
    )


def test_sql_expression_uses_category_when_solution_cluster_is_blank() -> None:
    expression = build_china_scope_sql_expr(
        solution_cluster_expr="solution_cluster",
        defect_category_expr="defect_category",
        china_label="China Specific",
        global_label="Global",
    )

    with sqlite3.connect(":memory:") as conn:
        row = conn.execute(
            f"""
            SELECT {expression} AS feature_region
            FROM (SELECT ? AS solution_cluster, ? AS defect_category)
            """,
            ("", "CN Speech"),
        ).fetchone()

    assert row == ("China Specific",)


def test_full_picture_uses_the_canonical_solution_cluster_rule() -> None:
    assert _get_ticket_china_scope(
        solution_cluster="China Product",
        defect_category="",
    ) == "China"


def test_full_picture_recovers_solution_cluster_udf_from_raw_json(tmp_path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                status_phase TEXT,
                problem_finder_team TEXT,
                year TEXT,
                assigned_ecu TEXT,
                top_aida TEXT,
                aida_english TEXT,
                aida_businesskey TEXT,
                phase TEXT,
                solution_cluster TEXT,
                lead_model TEXT,
                raw_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, name, status_phase, problem_finder_team, year,
                assigned_ecu, top_aida, aida_english, aida_businesskey, phase,
                solution_cluster, lead_model, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "D-SOLUTION-CLUSTER-UDF",
                "Solution cluster in raw JSON",
                "03-In Analysis",
                "DTSV_China",
                "2026",
                "ECU-A",
                "Speech",
                "",
                "",
                "03-In Analysis",
                "",
                "NA5",
                json.dumps({"solution_cluster_udf": {"name": "Speech CN"}}),
            ),
        )

    rows = _load_defect_rows(
        FullPictureDashboardQuery(years=("2026",)),
        defect_db_path=db_path,
    )

    assert rows[0]["solution_cluster"] == "Speech CN"


def test_full_picture_local_rows_recover_problem_category_udf_from_raw_json(tmp_path) -> None:
    db_path = tmp_path / "octane_data.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                name TEXT,
                project TEXT,
                market TEXT,
                pu TEXT,
                fv TEXT,
                team TEXT,
                lead_model TEXT,
                raw_json TEXT,
                fetched_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, name, project, market, pu, fv, team, lead_model,
                raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "D-LOCAL-CATEGORY-UDF",
                "Category in raw JSON",
                "IDCEVO",
                "DE",
                "PU1",
                "Speech",
                "DTSV_China",
                "NA5",
                json.dumps(
                    {
                        "year": "2026",
                        "problem_category_udf": {"name": "CN Payment"},
                    }
                ),
                "2026-09-03T00:00:00Z",
            ),
        )

    rows = _load_defect_rows(
        FullPictureDashboardQuery(years=("2026",)),
        defect_db_path=db_path,
    )

    assert rows[0]["defect_category"] == "CN Payment"
    assert _get_ticket_china_scope(
        solution_cluster=rows[0]["solution_cluster"],
        defect_category=rows[0]["defect_category"],
    ) == "China"


def test_tap_hot_refresh_classifies_exact_solution_cluster_as_china_specific(tmp_path) -> None:
    source_db_path = tmp_path / "qgate_raw.db"
    hot_db_path = tmp_path / "vizion_serving.db"
    with sqlite3.connect(source_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                solution_cluster TEXT,
                defect_category TEXT,
                market TEXT,
                top_aida TEXT
            );
            CREATE TABLE octane_manual_runs (
                mr_id TEXT PRIMARY KEY,
                defect_id TEXT,
                test_id TEXT,
                status TEXT,
                year TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, solution_cluster, defect_category, market, top_aida
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("D-IPA-CN", "IPA CN", "", "DE", "Unrelated AIDA"),
        )
        conn.execute(
            """
            INSERT INTO octane_manual_runs(mr_id, defect_id, test_id, status, year)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("MR-IPA-CN", "D-IPA-CN", "T-IPA-CN", "Passed", "2026"),
        )

    refresh_materialized_testing_coverage(source_db_path, hot_db_path, force=True)

    with sqlite3.connect(hot_db_path) as conn:
        row = conn.execute(
            "SELECT feature_region FROM testing_coverage_runs WHERE mr_id = ?",
            ("MR-IPA-CN",),
        ).fetchone()

    assert row == ("China Specific",)


def test_schema_adds_china_scope_fields_to_existing_defects_table(tmp_path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE octane_defects (defect_id TEXT PRIMARY KEY)")

    ensure_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(octane_defects)").fetchall()
        }

    assert {"solution_cluster", "defect_category"}.issubset(columns)


def test_backfill_china_scope_fields_recovers_and_normalizes_raw_json_values(tmp_path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT PRIMARY KEY,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO octane_defects(defect_id, raw_json) VALUES (?, ?)",
            (
                "D-BACKFILL-1",
                json.dumps(
                    {
                        "solution_cluster_udf": {
                            "name": "solution cluster:China Product"
                        },
                        "problem_category_udf": {"name": "CN Payment"},
                    }
                ),
            ),
        )

    summary = backfill_china_scope_fields(db_path, apply=True)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT solution_cluster, defect_category
            FROM octane_defects
            WHERE defect_id = ?
            """,
            ("D-BACKFILL-1",),
        ).fetchone()

    assert summary["filled_solution_cluster_rows"] == 1
    assert summary["filled_defect_category_rows"] == 1
    assert row == ("China Product", "CN Payment")


def test_cli_backfill_china_scope_fields_uses_source_db_and_apply_flag(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "qgate_raw.db"
    captured: dict[str, object] = {}

    def fake_backfill(db_path, *, apply: bool) -> dict[str, int]:
        captured["db_path"] = db_path
        captured["apply"] = apply
        return {"updated_rows": 1}

    monkeypatch.setattr(analytics_cli, "get_full_picture_source_db_path", lambda: source_db_path)
    monkeypatch.setattr(
        analytics_cli,
        "backfill_china_scope_fields",
        fake_backfill,
        raising=False,
    )

    assert main(["backfill-china-scope-fields", "--apply"]) == 0
    assert captured == {"db_path": source_db_path, "apply": True}


def test_tap_source_reads_ignore_persisted_feature_region_when_defect_join_exists(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "analytics.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE octane_defects ADD COLUMN test_week TEXT")
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, name, project, market, pu, fv, fvp, team, lead_model,
                solution_cluster, defect_category, test_week, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "D-LEGACY-OVERRIDE",
                "Legacy feature region must not override",
                "IDCEVO",
                "CN",
                "PU1",
                "Speech",
                "Voice Experience",
                "DTSV_China",
                "NA5",
                "Integration",
                "CN Speech",
                "2026-CW36",
                "{}",
                "2026-09-03T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO octane_manual_runs(
                mr_id, defect_id, test_id, test_name, status, year, test_week,
                project, pu, top_aida, feature_region, fvp, fv, tester, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MR-LEGACY-OVERRIDE",
                "D-LEGACY-OVERRIDE",
                "T-LEGACY-OVERRIDE",
                "Legacy test",
                "Passed",
                "2026",
                "2026-CW36",
                "IDCEVO",
                "PU1",
                "A China-specific AIDA must not override",
                "China Specific",
                "Voice Experience",
                "Speech",
                "Tester A",
                "{}",
                "2026-09-03T00:00:00Z",
            ),
        )

    monkeypatch.setenv("VIZION_ANALYTICS_DB_PATH", str(db_path))

    filters = build_testing_coverage_filters({})

    assert filters["feature_regions"] == ["Global"]


def test_tap_legacy_rows_without_defects_default_to_global(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_analytics.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE octane_manual_runs (
                year TEXT,
                test_week TEXT,
                project TEXT,
                pu TEXT,
                top_aida TEXT,
                feature_region TEXT,
                fvp TEXT,
                fv TEXT,
                status TEXT,
                test_id TEXT,
                test_name TEXT,
                tester TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO octane_manual_runs(
                year, test_week, project, pu, top_aida, feature_region, fvp, fv,
                status, test_id, test_name, tester
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026",
                "2026-CW36",
                "IDCEVO",
                "PU1",
                "Legacy AIDA",
                "China Specific",
                "Voice Experience",
                "Speech",
                "Passed",
                "T-LEGACY-ONLY",
                "Legacy-only test",
                "Tester A",
            ),
        )

    monkeypatch.setenv("VIZION_ANALYTICS_DB_PATH", str(db_path))

    filters = build_testing_coverage_filters({})

    assert filters["feature_regions"] == ["Global"]


def test_tap_snapshot_version_includes_china_scope_rule_version() -> None:
    snapshot_version = _build_testing_coverage_snapshot_version("source-signature")

    assert snapshot_version.startswith(
        f"testing-coverage-{CHINA_SCOPE_RULE_VERSION}-"
    )


def test_tap_active_snapshot_rejects_pre_rule_version() -> None:
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            """
            CREATE TABLE testing_coverage_runs (snapshot_version TEXT);
            CREATE TABLE testing_coverage_snapshot_state (
                snapshot_version TEXT,
                row_count INTEGER,
                refresh_status TEXT
            );
            CREATE TABLE testing_coverage_snapshot_pointer (
                pointer_name TEXT,
                snapshot_version TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO testing_coverage_snapshot_state(
                snapshot_version, row_count, refresh_status
            ) VALUES (?, ?, ?)
            """,
            ("testing-coverage-legacy", 1, "ready"),
        )
        conn.execute(
            """
            INSERT INTO testing_coverage_snapshot_pointer(pointer_name, snapshot_version)
            VALUES (?, ?)
            """,
            ("active", "testing-coverage-legacy"),
        )

        assert _resolve_active_testing_snapshot_version(conn) == ""


def test_tap_configured_database_rejects_unversioned_hot_table(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hot.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE testing_coverage_runs (
                snapshot_version TEXT,
                year TEXT,
                test_week TEXT,
                project TEXT,
                pu TEXT,
                top_aida TEXT,
                feature_region TEXT,
                fvp TEXT,
                fv TEXT,
                status TEXT,
                test_id TEXT,
                test_name TEXT,
                tester TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO testing_coverage_runs(
                snapshot_version, year, test_week, project, pu, top_aida,
                feature_region, fvp, fv, status, test_id, test_name, tester
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "testing-coverage-legacy",
                "2026",
                "2026-CW36",
                "IDCEVO",
                "PU1",
                "Legacy AIDA",
                "China Specific",
                "Voice Experience",
                "Speech",
                "Passed",
                "T-LEGACY-HOT",
                "Legacy hot test",
                "Tester A",
            ),
        )

    monkeypatch.setenv("VIZION_ANALYTICS_DB_PATH", str(db_path))

    with pytest.raises(CoverageDataNotReadyError):
        build_testing_coverage_filters({})


def test_tap_unmatched_defect_join_preserves_manual_dimensions_and_is_global(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "analytics.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO octane_manual_runs(
                mr_id, defect_id, test_id, test_name, status, year, test_week,
                project, pu, top_aida, feature_region, fvp, fv, tester, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MR-UNMATCHED-DEFECT",
                "D-NOT-IN-SOURCE",
                "T-UNMATCHED-DEFECT",
                "Manual-only dimensions",
                "Passed",
                "2026",
                "2026-CW36",
                "IDCEVO",
                "PU1",
                "AIDA-1",
                "China Specific",
                "Voice Experience",
                "Speech",
                "Tester A",
                "{}",
                "2026-09-03T00:00:00Z",
            ),
        )

    monkeypatch.setenv("VIZION_ANALYTICS_DB_PATH", str(db_path))

    filters = build_testing_coverage_filters({})

    assert filters["feature_regions"] == ["Global"]
    assert filters["projects"] == ["IDCEVO"]
    assert filters["test_weeks"] == ["2026-CW36"]


def test_full_picture_snapshot_version_includes_china_scope_rule_version(tmp_path) -> None:
    source_db_path = tmp_path / "qgate_raw.db"
    source_db_path.touch()

    snapshot_version = build_full_picture_snapshot_version(source_db_path)

    assert snapshot_version.startswith(f"snapshot-{CHINA_SCOPE_RULE_VERSION}-")


def test_full_picture_rejects_active_snapshot_from_pre_rule_version(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "qgate_raw.db"
    source_db_path.touch()
    monkeypatch.setattr(read_models, "_resolve_defect_db_path", lambda: source_db_path)

    snapshot_metadata = {
        "active_snapshot_version": "snapshot-legacy",
        "source_db_path": str(source_db_path.resolve()),
        "source_db_mtime": read_models._format_snapshot_source_mtime(source_db_path),
    }

    assert not read_models._active_snapshot_matches_current_source(snapshot_metadata)


def test_full_picture_live_fallback_snapshot_changes_with_rule_version(
    tmp_path,
    monkeypatch,
) -> None:
    source_db_path = tmp_path / "qgate_raw.db"
    source_db_path.touch()
    monkeypatch.setattr(read_models, "_resolve_defect_db_path", lambda: source_db_path)
    monkeypatch.setattr(
        read_models,
        "_read_outcome_refresh_signature",
        lambda _hot_db_path: "outcome-signature",
    )

    current_snapshot_version = read_models._build_fallback_snapshot_version()
    monkeypatch.setattr(
        read_models,
        "CHINA_SCOPE_RULE_VERSION",
        "china-scope-v-next",
        raising=False,
    )

    next_snapshot_version = read_models._build_fallback_snapshot_version()

    assert next_snapshot_version != current_snapshot_version


@pytest.mark.parametrize(
    "solution_cluster",
    (
        "China Product",
        "IPA CN",
        "Speech CN",
        "Navigation CN",
        "ENT_and_CON CN",
        "Navigation TWN",
        "ETC JP",
    ),
)
def test_every_approved_solution_cluster_is_china_specific(solution_cluster: str) -> None:
    assert is_china_specific(
        solution_cluster=solution_cluster,
        defect_category="Unrelated category",
    )


@pytest.mark.parametrize(
    "defect_category",
    (
        "ENT/CONNECTED_MUSIC_CHINA",
        "Application Navigation China HK",
        "CN DKR",
        "CN Backend_Service",
        "CN ETC",
        "CN for Asia Contact book",
        "CN for AsiaTextSupportLib",
        "CN For Global Festive App",
        "CN for Asia Speller",
        "CN for USB media service",
        "CN Intelligent Reminder",
        "CN IPA_Visualization",
        "CN Launcher",
        "CN IPA_Intelligence",
        "CN LLM",
        "CN Media",
        "CN MLS",
        "CN Nav",
        "CN MPP",
        "CN Social login Via Wechat",
        "CN Store",
        "CN Speech",
        "CN VoD",
        "CN Wechat",
        "CN_Core_UI",
        "CN_Experience_Mode",
        "CN_HUAWEI_HiCar",
        "CN_Karaoke",
        "CN_Gaming",
        "CN_UI_Lib",
        "CN_Platform",
        "CN_Subscription",
        "oap/weather/cn",
        "Offboard eRoute",
        "Road Map Taiwan",
        "Application Navigation Taiwan",
        "International Keyboard",
        "Apps_FestivalMode",
        "CN_Video_conference",
        "CN ADAS Setting",
        "CN_Demo_Mode",
        "CN AD View",
        "CN Video Casting",
        "CN Payment",
        "Personalization: CN Data Privacy",
        "Personalization: Driver Authorization",
        "BYOD CN",
        "ENT-PODCASTS_CHINA",
        "CN_ENT AI",
        "ENT/PODCASTS_CHINA",
        "CN_ICCOA_Carlink",
    ),
)
def test_every_approved_defect_category_is_china_specific_when_cluster_is_blank(
    defect_category: str,
) -> None:
    assert is_china_specific(
        solution_cluster="",
        defect_category=defect_category,
    )


@pytest.mark.parametrize(
    ("solution_cluster", "defect_category"),
    (
        ("Integration", "CN Speech"),
        ("China Product Extended", "CN Payment"),
        ("solution cluster:China Product", "CN Speech"),
    ),
)
def test_nonmatching_nonblank_cluster_blocks_category_fallback(
    solution_cluster: str,
    defect_category: str,
) -> None:
    assert not is_china_specific(
        solution_cluster=solution_cluster,
        defect_category=defect_category,
    )


@pytest.mark.parametrize(
    ("solution_cluster", "defect_category", "expected"),
    (
        (" IPA CN ", "", "China Specific"),
        ("", " CN Payment ", "China Specific"),
        (None, "CN_ENT AI", "China Specific"),
        ("Integration", "CN Payment", "Global"),
        ("China Product Extended", "", "Global"),
    ),
)
def test_sql_expression_matches_strict_python_rule(
    solution_cluster: str | None,
    defect_category: str,
    expected: str,
) -> None:
    expression = build_china_scope_sql_expr(
        solution_cluster_expr="solution_cluster",
        defect_category_expr="defect_category",
        china_label="China Specific",
        global_label="Global",
    )

    with sqlite3.connect(":memory:") as conn:
        row = conn.execute(
            f"""
            SELECT {expression} AS feature_region
            FROM (SELECT ? AS solution_cluster, ? AS defect_category)
            """,
            (solution_cluster, defect_category),
        ).fetchone()

    assert row == (expected,)


def test_backfill_china_scope_fields_dry_run_preserves_conflicts_and_invalid_json(
    tmp_path,
) -> None:
    db_path = tmp_path / "qgate_raw.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO octane_defects(
                defect_id, name, project, market, pu, fv, fvp, team, lead_model,
                solution_cluster, defect_category, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "D-FILL",
                    "Fill from raw JSON",
                    "IDCEVO",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    json.dumps(
                        {
                            "solution_cluster_udf": {
                                "name": "solution cluster:China Product"
                            },
                            "problem_category_udf": {"name": "CN Payment"},
                        }
                    ),
                    "2026-09-03T00:00:00Z",
                ),
                (
                    "D-CONFLICT",
                    "Keep source values",
                    "IDCEVO",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "Speech CN",
                    "Existing Category",
                    json.dumps(
                        {
                            "solution_cluster_udf": {"name": "IPA CN"},
                            "problem_category_udf": {"name": "CN Speech"},
                        }
                    ),
                    "2026-09-03T00:00:00Z",
                ),
                (
                    "D-INVALID",
                    "Malformed raw JSON",
                    "IDCEVO",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "{",
                    "2026-09-03T00:00:00Z",
                ),
            ),
        )

    dry_run_summary = backfill_china_scope_fields(db_path, apply=False)

    with sqlite3.connect(db_path) as conn:
        dry_run_row = conn.execute(
            """
            SELECT solution_cluster, defect_category
            FROM octane_defects
            WHERE defect_id = ?
            """,
            ("D-FILL",),
        ).fetchone()

    assert dry_run_summary == {
        "candidate_rows": 3,
        "updated_rows": 0,
        "filled_solution_cluster_rows": 1,
        "filled_defect_category_rows": 1,
        "normalized_solution_cluster_rows": 1,
        "conflict_rows": 1,
        "invalid_json_rows": 1,
    }
    assert dry_run_row == ("", "")

    apply_summary = backfill_china_scope_fields(db_path, apply=True)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT defect_id, solution_cluster, defect_category
            FROM octane_defects
            ORDER BY defect_id
            """
        ).fetchall()

    assert apply_summary == {
        **dry_run_summary,
        "updated_rows": 1,
    }
    assert rows == [
        ("D-CONFLICT", "Speech CN", "Existing Category"),
        ("D-FILL", "China Product", "CN Payment"),
        ("D-INVALID", "", ""),
    ]


def test_backfill_preserves_nonlegacy_nonblank_values_byte_for_byte(tmp_path) -> None:
    db_path = tmp_path / "qgate_raw.db"
    ensure_schema(db_path)
    original_solution_cluster = " Integration "
    original_defect_category = " Existing Category "
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO octane_defects(
                defect_id, name, project, market, pu, fv, fvp, team, lead_model,
                solution_cluster, defect_category, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "D-PRESERVE",
                "Preserve existing values",
                "IDCEVO",
                "",
                "",
                "",
                "",
                "",
                "",
                original_solution_cluster,
                original_defect_category,
                json.dumps(
                    {
                        "solution_cluster_udf": {"name": "IPA CN"},
                        "problem_category_udf": {"name": "CN Payment"},
                    }
                ),
                "2026-09-03T00:00:00Z",
            ),
        )

    summary = backfill_china_scope_fields(db_path, apply=True)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT solution_cluster, defect_category
            FROM octane_defects
            WHERE defect_id = ?
            """,
            ("D-PRESERVE",),
        ).fetchone()

    assert summary["updated_rows"] == 0
    assert summary["conflict_rows"] == 1
    assert row == (original_solution_cluster, original_defect_category)