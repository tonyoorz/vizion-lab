# CN Speech Capability-Grounded Regeneration Plan

**Goal:** Regenerate the frozen CN Speech testcase/defect population into a materially improved, one-tab TA intermediate specification, using verified framework capabilities, per-case design reasoning and explicit evidence gates.

**Architecture:** Keep the 2026-10-28 snapshots and review files immutable. Build a static, source-referenced AIGENT capability inventory without importing device code. Use the existing configured corporate model route for per-case design generation, then validate source coverage, quoted evidence, capability references and pending execution/approval status before producing a 2026-11-02 workbook and JSON. Do not fetch credentials, synchronize Octane, execute hardware or publish production changes.

**Tech Stack:** Existing Python environment, frozen JSON, AST/source inspection, configured model gateway, openpyxl and pytest.

## Task 1: Ground the Generation Contract

- [x] Add failing tests in `backend/tests/test_octane_epic_automation.py` for nonexistent capabilities, missing source steps, unsupported evidence quotes and forbidden readiness claims.
- [x] Implement the validation/prompt slice in `backend/analytics/cn_speech_ta_regeneration.py` and run the focused checks.
- [x] Inspect relevant AIGENT skills, configuration and source functions; retain exact paths/signatures/hashes and distinguish complete operations from observation-only helpers.

## Task 2: Regenerate Per-Case Designs

- [x] Reuse the repository's configured corporate LLM route, with bounded concurrency, evidence/model/prompt/capability-matched caching and explicit failure records.
- [x] For every retained case, preserve original content and derive objective, reviewed-or-pending initial state, action/verification mapping, wait conditions, restoration, reference cases and justified TA feasibility.
- [x] Do not fabricate selectors, requirements, numeric thresholds, audio assets, device evidence, domain approvals or implementation readiness. Route unresolved observation paths to a separately authorized recorder stage.

## Task 3: Deliver and Verify

- [x] Produce a new single-tab, one-case-per-row workbook and canonical regenerated JSON under `exports/octane_speech_epic_1685240/20261102/`.
- [x] Preserve every source ID and classify unprocessed/invalid outputs explicitly rather than silently dropping them.
- [x] Validate workbook counts, source hashes, action coverage, capability IDs, full-content fields, literal-text safety and default pending approvals.
- [x] Synchronize the inactive workflow skill and output guide with the actual implemented stages and remaining hardware gates.

Focused command: `.venv/Scripts/python.exe -m pytest backend/tests/test_octane_epic_automation.py -q`.

## Historical GLM Flash Acceleration Attempt

The user explicitly replaced Qwen with `glm-5.3-flash`. The configured gateway listed this exact ID and returned a matching successful JSON probe. Do not fall back to another model or reuse its cached designs as GLM outputs.

Implemented: one reference-index fit per library, one persistent Node NDJSON process, request-ID routing for out-of-order results, reusable HTTP connections, a request to disable GLM thinking, a 32-worker default bounded to 64, rate-limit-aware retries, per-case checkpoints, and elapsed-time/model provenance. Existing source-step, quote, capability and numeric-threshold validation remains active. Actual Beacon probes still returned reasoning tokens despite disabled-thinking variants; the backend behavior must not be reported as successfully disabled. Large live design requests timed out before content, so no successful full regeneration or speed multiplier is claimed yet.

Verification so far: 57 focused Python checks and two GLM transport checks pass. Exact source text and step indexes survive prompt compaction. Non-retryable no-design timeouts now fail fast rather than repeating the entire timeout; rate-limit/transient gateway retries and content-validation repair remain bounded.

## Historical GLM Gateway Blocker

The three-case live GLM pilot produced request timeouts and was stopped; no successful full regeneration is claimed. Four small parameter probes showed that `thinking.type=disabled`, `enable_thinking=false`, `chat_template_kwargs.enable_thinking=false` and `reasoning_effort=none` still produced reasoning tokens through the current Beacon route. The local proxy forwards those request parameters without filtering them.

A compressed one-case probe returned after 175.409 seconds, but its response was truncated (`finish_reason=length`) under the requested 8192-token design limit. Result: zero validated designs. Evidence: `exports/octane_speech_epic_1685240/20261102/glm_5_3_flash_compact_probe/regeneration_manifest.json` and the per-case review checkpoint. This is a backend inference/output-budget blocker, not proof that the capability or source gates are too strict.

The GLM-5.3 Flash default, persistent process, one-time reference retrieval, 32-worker bounded profile and checkpointing are implemented. The full 369-case GLM run is not started until a representative complete design succeeds. No fallback to Qwen, lower quality gate, speed multiplier, completed workbook or device result is claimed.

## Completed DeepSeek V4 Flash Release

Following the user's approval, default regeneration now uses `deepseek-v4-flash` with explicit thinking disabled. Both Flash and Pro supported the switch in controlled live probes; Flash also passed the real testcase design probe. The user authorized the full 369-case run at 32 workers.

Output: `exports/octane_speech_epic_1685240/20261102/deepseek_v4_flash/`. All 369 designs pass unchanged source/capability/numeric gates: 368 require adapters/data and one explicitly retains insufficient acceptance evidence. No cases were dropped, no source text overwritten, and no device/approval readiness claimed.

First pass: 602.830 seconds, 198 validated. Second pass with exact field/value feedback: 263.102 seconds, 366 validated, 199 cache hits. Targeted repairs resolved the last reference mismatches and retained missing seek acceptance as insufficient evidence. Final assembly revalidated 369 cache hits; its elapsed time is not fresh-generation throughput.

Final acceptance verified one visible workbook tab, 369 unique case rows, correct model-generated Verify/restoration/assessment fields, retained Outdated warnings, full source CSV, matching source hashes, no formulas, no nonzero reported reasoning tokens, and zero Octane/device operations. 58 focused Python checks and five model-transport checks passed.

## Task 4: Independent Historical Business Consistency Review

- [x] Add an independent Pro reviewer comparing original testcase/linked defect evidence, generated design and capability limits across six business checks.
- [x] Require complete fixed-schema reports, exact original/design quotes, explicit evidence gaps, reviewer-specific caches and bounded content corrections. Preserve controlled failures without model fallback.
- [x] Produce `business_audit_pro/CN_Speech_Business_Audit.xlsx` with one visible tab and 369 unique case rows; retain unchanged original Action/expected/Verify/restoration fields and full source CSV.
- [x] Keep all findings unconfirmed and all approval/device/readiness flags false. Document known repeated-gap overflags; never equate a valid citation with a correct interpretation or automatically apply recommendations.
- [x] Resolve independent code-review findings: bind exports to the actual reviewed design projection, preserve supported issues alongside missing defect bodies, and return controlled failures for mismatched-model responses.
- [x] Verify all 369 reports and source/output hashes, no formulas, complete CSV and unchanged source designs. Synchronize the inactive skill and both output guides.

Result: 369 structurally/citation-validated reports, zero failed reports, 727 unconfirmed candidate findings. The reviewer marked all cases `needs_revision`; this is not a confirmed defect rate or a claim that every recommendation is correct. Domain/device approvals remain zero.

The initial 32-worker attempt encountered HTTP 429 and a terminal interruption; completed report caches survived. Lower-concurrency passes repaired only invalid reports. The final 369-cache-hit revalidation/packaging took 0.926 seconds, not full inference time. The prior 277.815/97.057/50.892-second passes reused 235/360/366 reports respectively.

75 focused Python checks pass after the review fixes; five Node transport checks remain a separate gate. Next is domain adjudication against current requirements, not automatic script generation or hardware execution.
