# Business Project Classification

## Purpose

`tproject` is the local, final business-project classification for a defect. Full Picture exposes this value through its existing `project` field and uses it for the project filter. The legacy `octane_defects.project` value remains an input and compatibility field.

## Rule Version

`2026-09-03-v1`, implemented in `backend.analytics.processor`.

## Decision Order

1. Use a technical consensus when at least two independent sources agree: explicit feature area, assigned ECU, and software version.
2. Use an explicit feature-area classification when one is available.
3. Use an approved Top AIDA business mapping.
4. Use a non-empty, non-Unknown legacy `project` value.
5. Use a single technical or heuristic fallback.
6. Store `Unknown` when no rule can classify the defect.

A technical consensus can correct any legacy project value. For example, a legacy `IDCEVO` defect with `HU-MGU_02_A` and an `IDC23` software version is classified as final `IDC`. An explicit feature-area rule is a reviewed direct business classification and may also correct a legacy value. A single assigned-ECU or software-version signal does not override a known legacy project. A software version with identifiers for multiple projects is ambiguous and does not provide a decisive project signal.

## Persistence

- `run_processor_pipeline()` recalculates `tproject` for every defect.
- `backfill-projects --apply` materializes the same final classification for all defects.
- Re-ingestion preserves local `tproject`, including `Unknown`; payload data does not overwrite it.
- A legacy `project` value is only filled when it is blank or Unknown. Strong corrections are written to `tproject`, not back to the historical input.

## Operations

Create a read-only audit:

```powershell
python -m backend.analytics_cli audit-business-projects `
  --db-path database/source/qgate_raw.db `
  --output-path database/hot/business_project_classification_audit.json
```

Apply the final classification:

```powershell
python -m backend.analytics_cli backfill-projects `
  --db-path database/source/qgate_raw.db `
  --apply
```

Rebuild the Full Picture snapshot after applying changes:

```powershell
python -m backend.analytics_cli refresh-full-picture-outcomes `
  --db-path database/source/qgate_raw.db `
  --force
```

## Audit Output

The audit contains project distributions, final-project sources, the number of pending `tproject` updates, strong correction candidates, and unresolved defects. `correction_candidates` have at least two consistent technical evidence sources and differ from the legacy project. `unresolved_records` remain final `Unknown` and require business review.

Before applying a production run, retain a `defect_id`, `project`, and `tproject` snapshot. The 2026-09-03 application snapshot is stored under `database/hot/business_project_tproject_before_apply_20260903_093114.json`.
