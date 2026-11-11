# DataHub Boundary And Legacy Post-Processing

## Verified Scope Of `C:\Users\q446328\Desktop\datahub`

DataHub is a separate Next.js tool-catalog application. Its local SQLite file
`data\datahub.db` contains a `tools` table seeded from `src\data\tools.ts`.
It does not contain IDCEVO Tickets, Octane raw tables, LLM caches, V12 artifacts,
or a Ticket-ingestion API.

The application entry points (`src\lib\db\index.ts`, `src\lib\tools.ts`, and
`GET /api/tools`) manage the local tool catalog only. The verified external API
integration is GitHub statistics, not Octane or an LLM provider.

## Phase 06 Contrast

The governed Phase 06 resolution-evidence pipeline is implemented in the
Vizion Lab repository, not DataHub. Its source payload, LLM summary runner,
RCA Candidate Library, and symptom-to-resolution evidence-tree builder are
documented in [Phase 06 resolution-evidence lane](./phase06-resolution-evidence-lane.md).
Do not infer that a Phase 06 status reference in a DataHub Phase 09 script is a
DataHub-owned Phase 06 analysis pipeline.

## Legacy Phase 09 Scripts

The following scripts are useful for legacy derived reports only. They consume
existing Vizion Lab Excel exports and do not create the governed V12/V1 lineage.

| Script | Role | Write behavior |
| --- | --- | --- |
| `scripts\idcevo_phase09_audit.py` | Rule-based closure quality/risk audit | Writes separate audit workbook. |
| `scripts\idcevo_phase09_audit_lite.py` | Projects audit columns into a compact workbook | Writes separate lite workbook. |
| `scripts\idcevo_phase09_dedup.py` | Exact and Jaccard C+D dedup reports | Writes separate dedup workbooks. |
| `scripts\idcevo_phase09_final_dedup.py` | Closure classification + dedup clustering | Creates a backup, then overwrites its configured FinalProject workbook. |
| `scripts\idcevo_phase09_rca_analysis.py` | Regex extraction of RCA fields from existing evidence | Overwrites its configured FinalProject workbook. |
| `scripts\idcevo_phase09_aftersales_decision_tree.py` | Rule-based after-sales work queue/report | Writes a separate decision-tree workbook. |

## Hard-Coded Legacy Inputs

The scripts use absolute paths under:

```text
C:\Users\q446328\Desktop\vizion-lab\exports\idcevo_phase09\
```

Most point to `20260907` legacy FullLLM/FinalProject workbooks. They do not
reference the governed V12/V1 files and should never be run as a replacement for
the current `phase09-ticket-to-aftersales-decision-tree` pipeline.

## Safe Use Rules

1. Treat DataHub output as a separately named derived report.
2. Never run `final_dedup.py` or `rca_analysis.py` against a governed source
   workbook without first taking and validating an immutable backup.
3. Never hand-copy DataHub RCA, audit, or recovery text into V12/V1 as an
   approved root cause, branch, or repair rule.
4. If a legacy report is needed, record its exact input workbook, output
   workbook, script version, and whether it mutated anything.
