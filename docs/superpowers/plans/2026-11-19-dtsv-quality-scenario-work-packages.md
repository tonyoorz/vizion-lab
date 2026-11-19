# DTSV Quality and Scenario Work Packages Implementation Plan

> **For agentic workers:** Execute inline with review checkpoints. Use checkbox steps to track verification. No commits, branches, upstream synchronization or Confluence writes are authorized by this plan.

**Goal:** Publish a source-preserving revision of the DTSV work-breakdown navigator with independent quality, scenario and activity-stage dimensions and auditable FreeTest counts.

**Architecture:** Keep the existing work-package matrix and its stable identifiers. Add a quality-led FreeTest package catalog based on the supplied historical ISO/IEC 25010:2011 scope, map the speech checkpoints to quality and scenario dimensions, and classify frozen ticket evidence as reviewable candidates. Count each historical ticket once in the primary quality/scenario matrix and keep overlapping tags separate. Future session/activity entries supply execution and time denominators; tickets do not.

**Tech Stack:** Python, openpyxl, pytest, existing frozen JSON evidence and existing workbook navigation.

---

## Task 1: Prove Classification and Counting Boundaries

**Files:**

- Test: `backend/tests/test_free_test_effort_breakdown.py`
- Create: `backend/analytics/free_test_quality_scope.py`

- [x] Add `test_quality_scope_deduplicates_candidates_and_does_not_confuse_triggers_with_quality` using overlapping pattern membership, a delayed speech response, rapid tapping with an incorrect result, and a restored permission with failed recovery.
- [x] Run the focused check and confirm that the new API is missing:

```powershell
& .\.venv\Scripts\python.exe -m pytest backend/tests/test_free_test_effort_breakdown.py -k quality_scope -q --tb=short
```

- [x] Implement `classify_free_test_evidence(data)` with stable ticket identifiers, one primary quality, one primary scenario, separate overlapping tags, and evidence fragments. Never infer measured executions or hours.
- [x] Rerun the same check; confirm unique primary totals and negative classification fixtures.

## Task 2: Preserve the Navigator and Add the Quality View

**Files:**

- Modify: `backend/analytics/free_test_quality_scope.py`
- Test: `backend/tests/test_free_test_effort_breakdown.py`

- [x] Inspect actual frozen workbook/evidence structure and the relevant Confluence scope only. Record source versions and evidence dates without refreshing Octane.
- [x] Add a workbook regression test covering preserved source values, immutable inputs, quality taxonomy, checkpoint mapping, unique historical totals, editable activity fields, validated lists and internal navigation.
- [x] Implement `build_quality_scope_workbook(source_path, evidence_path, output_path)` and a bounded CLI. Refuse to overwrite any input or existing output.
- [x] Generate a new dated navigator under `exports/test_method_evidence/`, preserving the original three sheets and their stable package identifiers.

## Task 3: Verify and Deliver

- [x] Run the full touched test module: 22 passed.
- [x] Reopen the generated workbook and reconcile candidate IDs, primary quality totals, scenario totals and cross-tab totals against the frozen evidence.
- [x] Check source hashes, formula references, list validation and internal hyperlink targets. Keep empty activity/time fields empty and distinguish no observed evidence from zero testing.
- [x] Include a concise classification/counting guide in the workbook and a same-folder summary describing evidence limits and the original source paths.
- [x] Prepare the revised workbook link, the recommended classification model and the historical-ticket versus future-session counting boundary for delivery. No Confluence write was performed.

## Verification Evidence

- Native Excel synthetic checks passed for invalid pasted classifications, missing function metadata, invalid outcomes, unique-session credit, blank/negative time, separate waiting time, no-finding execution, blocked outcomes, duplicate activities and conflicting session targets.
- Native Excel publication filters reconciled ALL=2218, EXPLICIT=987, INFERRED=1231 and COMPLEX=150. Recalculation caches were saved; only new catalog rows were autofitted.
- Final reopened artifact audit passed: original three sheet values and 55 package IDs preserved, source/evidence SHA256 unchanged, 2,218 unique IDs exactly matched, and zero cached Excel formula errors.
- Seven quality packages, 72 screenshot-transcribed/grouped Speech checkpoint templates, 12 total worksheets. Quality-rule proposals cover 779 records; 1,439 retain UNCLASSIFIED rather than inventing objectives.
- Confluence page reads: main strategy v89 (still marked Drating), CN SPEECH Check Point v11 and Speech Checklist v5. The old XMind attachment response decoded to HTML, not XMind; it was rejected as evidence. Supplied CN screenshots control the CN quality scope; the DE-61 interoperability difference remains documented.
- Publication: `exports/test_method_evidence/work_package_quality_scope_20261119/DTSV_Work_Breakdown_Navigator_Quality_Scenario.xlsx`.
