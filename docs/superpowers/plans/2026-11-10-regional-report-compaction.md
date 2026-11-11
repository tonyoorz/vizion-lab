# Regional Report Compaction Implementation Plan

**Goal:** Reduce HK/TW/CN HTML, Excel and CSV redundancy without losing current analysis, records, filters or audit evidence.

**Architecture:** Keep the canonical frozen JSON as the refresh/audit source. Publish a UI-specific HTML projection and compact spreadsheet tables from the same frozen facts. Do not query or modify upstream systems or source databases.

**Tech Stack:** Existing Python generator, pytest, openpyxl, Node/JSDOM.

## Task 1: HTML Data Boundary

- [x] Add a failing test showing that unused root audit collections are not embedded.
- [x] Add a pure HTML payload projection retaining all frontend dependencies.
- [x] Run focused payload and chart/filter DOM regressions immediately.
- [x] Remove repeated row-level prose only when represented once in shared metadata; retain all business records and displayed values.

## Task 2: Spreadsheet Exports

- [x] Inventory repeated/empty spreadsheet tables and constant columns.
- [x] Add regression coverage for compact output, complete primary MR/testcase/defect records and separate strict-platform attribution.
- [x] Consolidate duplicate exports, move repeated explanations into shared Readme/reference data, and retain necessary audit columns.
- [x] Remove only generator-owned obsolete files, preserving native testcase scripts, snapshots and unrelated user files.
- [x] Re-run export and formula-safety tests.

## Task 3: Publication and Verification

- [x] Regenerate all three regions locally from frozen JSON; preserve HK native-body documentation and dedicated CSV.
- [x] Verify case/MR/defect business fingerprints, platform counts, observation timestamps and Excel/CSV/JSON parity.
- [x] Verify actual generated HTML charts, table views, filters and paging with bounded DOM execution.
- [x] Update the report skill to reflect the newly authorized compaction policy and output contract.
- [x] Report measured size/file-count reductions and any unverified visual boundary.

## Verified Results

| Region | HTML MiB Before / After | Excel MiB Before / After | All CSV MiB Before / After |
| --- | --- | --- | --- |
| HK | 24.084 / 3.965 | 1.961 / 0.932 | 15.774 / 10.654 |
| TW | 16.922 / 3.322 | 1.454 / 0.791 | 5.658 / 2.217 |
| CN | 499.570 / 78.297 | 41.776 / 20.031 | 158.943 / 57.111 |

- Each report now has 23 worksheets instead of 43, and 16 core CSVs instead of 35. HK retains five additional native/historical CSVs.
- Complete audit JSON remains available, minified without removing frozen facts: HK 22.966 MiB, TW 16.107 MiB, CN 476.786 MiB.
- All original testcase-ID, MR business-fact and defect business-fact fingerprints passed. Every primary CSV field was verified as retained or represented identically by a shared Data Dictionary constant.
- HK native-body CSV retains 375 rows / 371 scripts; native-body and source-recovery documentation sections survive rerenders.
- 120 regression tests passed, including full-versus-projected views/filters and a deterministic check that platform filtering reads year/VIN controls once per selection rather than per MR/defect.
- Actual embedded-payload boot, MR search, page-two order, year filter and platform-comparison checks passed. Real-browser screenshot/visual acceptance was not performed.

## Constraints

- No commits, branches, source backfill, Octane/Fleem calls, uploads or inference of missing platforms.
- The user has now authorized HTML/artifact minimization, superseding the earlier pause for this workflow.
- Dependent schema and rendering changes are executed locally, not delegated.
