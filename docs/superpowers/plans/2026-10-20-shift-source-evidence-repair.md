# Shift Source Evidence Repair Implementation Plan

> Execute inline with focused regression checks. Do not create branches or commits.

**Goal:** Restore complete, current local evidence for the 1,805-ticket analysis scope and prevent incomplete evidence from being published as current Shift/Tolerate status.

**Architecture:** Repair the canonical ingestion writer, refresh the exact approved ticket IDs rather than expanding team scope, and validate local rows against independently fetched comments/history. Reanalyse versioned evidence and generate a separate candidate artifact set before publication. Preserve the existing release and a database backup.

**Tech Stack:** Python, SQLite, requests, openpyxl, existing company LLM gateway, static HTML catalog.

## Tasks

- [x] Fix comment cache/canonical-row consistency and preserve comments when a defect-only response omits them. Tests: `python -m pytest backend/tests/test_octane_ingest_pipeline.py -q`.
- [x] Add an exact-ID refresh path with complete ID coverage, fresh phase/description/comments/history, resumable evidence files, and a source audit. Back up the source database before applying successful batches.
- [x] Validate the approved 1,805-ticket scope: 1,794 accessible tickets fully refreshed; 11 unreturned IDs explicitly retained as historical-only. Canonical/cache/raw comments and history counts agree. Ticket 2531406 has 84 local comments.
- [x] Reanalyse current repair progress separately from historical Shift/Tolerate and current delivery plans. All 1,794 accessible tickets have matching `shift-current-evidence-v2` model outputs. Reject stale/invalid citations; downgrade uncited and lifecycle/delivery-conflicting status claims with warnings.
- [x] Generate a new FullLLM source workbook and synchronized V12 Excel/JSON/HTML candidate with current-state fields, preserving dates and clustered navigation. Candidate contains 1,805 tickets and 449,197 raw evidence rows; originals remain unchanged.
- [x] Add executable source completeness gates to the analysis build and document them in the repository Phase 09 skills. Verify original-ticket conclusions and candidate counts before reporting completion. Candidate only; official release remains unchanged.

## Acceptance Checks

- Successful comment refresh writes identical comment content to refresh state, canonical comments, and raw JSON.
- Omitting comments from an incoming defect response cannot delete existing comments.
- Failed or incomplete remote fetches never become successful empty evidence states.
- Unavailable IDs must belong to an explicit immutable exception allowlist; partial collection omissions cannot silently expand it.
- Local normalized history values, timestamps, actor/action and ordering match the fetched snapshot, not merely the row count.
- Every requested ticket has either verified current metadata/comments/history provenance or an explicit historical-only unavailable status. Never claim unavailable IDs have current evidence; report per-ticket freshness, not merely a database-wide maximum.
- Current repair/testing/verification/closure is not equated with historical shift mentions; model analysis cites dated local evidence and preserves contradictory or retracted statements.
- Actual output workbook cells are validated as values and date types; `None` is not counted as populated text.
- Output triple uses one in-memory snapshot. Prior releases are not overwritten until the candidate passes validation and locked files are closed.

## Verified Result

- Canonical source has 214,236 tickets. This repair touched the approved 1,805-ticket scope, not a full-database reload.
- 1,794 accessible tickets have verified current metadata, 28,637 comments and 416,969 normalized history rows. The 11 explicitly listed online-unreturned tickets remain historical-only.
- Scope empty-comment rows fell from 414 to 8. All 8 remaining rows are unavailable historical tickets, with no recoverable nonempty cache. Accessible current tickets have no empty comment lists. Ticket 2531406 has 84 local comments.
- All 1,794 current tickets have matching `shift-current-evidence-v2` summaries. Their historical process support, current postponement, repair progress and delivery progress remain separate; unsupported or conflicting claims are review-required.
- Candidate FullLLM and V12 triple are under `exports/idcevo_phase09/V4/source_repaired_20261020/candidate_v12`. Actual files contain 1,805 source/scenario/mapping tickets, 1,805 native UTC Excel creation dates, 449,197 distinct evidence records across 449,268 chunked evidence rows, 86 functions, 294 clustered plan leaves and 1,262 nodes.
- Actual file values and hashes are recorded in `exports/idcevo_phase09/V4/source_repaired_20261020/candidate_validation_report.json`. Source provenance, unavailable policy and matching-summary gates passed again during the final build.
- Focused regressions: 51 Python tests and 31 Node tests passed. Skill frontmatter identifiers/descriptions, local links, editor diagnostics and repair-related diff whitespace checks passed.
- Browser checks covered high-volume and zero-data functions, current-state fields, collapsed evidence, no ticket-branch control, and desktop/mobile overflow. No page runtime errors were observed. Node-selection/disclosure state was verified with DOM events; native pointer automation remained unavailable because of integrated-browser stability waits and an occupied external Chrome MCP profile. It is not claimed as a passing check.
- Official pre-repair artifacts were not overwritten. All four candidate artifacts were generated through the native writers, not hand-edited.

## Confirmed Omission Causes

- Comment refresh state could diverge from canonical comments/raw JSON; metadata-only upserts could erase previously stored comments. Both defects were reproduced and fixed.
- Default nightly finder/team coverage did not include the reported ticket's finder; a recent database-wide timestamp hid stale per-ticket evidence.
- The closure-audit skill named the wrong default raw-source database and lacked exact-scope evidence completeness gates. The relevant three skills and shared release contract now require those gates.
- Shared chat compaction could truncate structured input to its final 6,000 characters, losing earlier description/comment evidence. The current-state lane now uses intact bounded transport and a versioned source-bound fingerprint.
- Keyword co-occurrence and historical Shift/Tolerate mentions were overinterpreted as current pending status. They are now historical candidates only; current conclusions use dated evidence, citation validation and conservative conflict gates.
- The original September ingestion invocation was not recovered. There is no verified basis to assert that it used a skip-comments option.
