# CN Speech AgenticAutomation Adaptation Plan

**Goal:** Reuse the 267 frozen Octane cases and 102 defect-derived candidates while replacing German AIGENT assumptions with source-verified AgenticAutomation capabilities and native review-gated YAML drafts.

**Architecture:** Add an explicit framework profile to the existing inventory/prompt/regeneration path; retain the legacy profile for reproducibility. Inspect AgenticAutomation with AST and structured YAML only, never import or run device code. Regenerate into a separate dated release, then export native suite/skill drafts that cannot silently become approved tests.

**Tech Stack:** Existing Python environment, AST, PyYAML, openpyxl, pytest, and the existing DeepSeek request bridge.

## Task 1: Framework Profile

- [x] Add focused tests in `backend/tests/test_octane_epic_automation.py` for profile-specific inventories and prompts, wrong-framework references, input-WAV ASR limitations and disabled-LLM false-pass risk.
- [x] Add the AgenticAutomation inventory/profile in `backend/analytics/cn_speech_ta_regeneration.py`, using exact source symbols/signatures/hashes and real native suite examples.
- [x] Run `.venv/Scripts/python.exe -m pytest backend/tests/test_octane_epic_automation.py -q`; require every existing and new check to pass.

## Task 2: Native YAML Drafts

- [x] Implement native review-gated suite/skill export in `backend/analytics/cn_speech_agentic_automation.py`, reusing generated source-grounded designs and preserving unresolved step bindings.
- [x] Map source stimuli to `utterance`/`steps` and independent results to `validation`/`checkpoints`; keep unsupported operations explicit rather than inventing selectors, diagnostic values or acceptance thresholds.
- [x] Add focused tests for full source retention, real action/validation names, pending approvals, and rejection of input-WAV or disabled-LLM pseudo-verification.

## Task 3: Regenerate and Verify

- [x] Run a representative pilot, then regenerate the 369-case frozen population using the authorized DeepSeek V4 Flash model with thinking disabled and bounded concurrency. Reuse model/evidence/profile-matched caches; do not resynchronize Octane or relabel AIGENT outputs.
- [x] Output one visible workbook tab, one case per row, complete CSV/JSON, profile inventory and YAML drafts under `exports/octane_speech_epic_1685240/20261103/agentic_automation/`.
- [x] Reassess prior capability-dependent audit claims against the new inventory; retain original audit opinions separately and do not convert model disagreement into domain approval.
- [x] Verify all case IDs/full scripts, exact citations, known capability IDs, workbook/CSV counts, native YAML schema, pending approvals and unchanged frozen source hashes.
- [x] Synchronize the inactive workflow skill and output guide, then run the focused Python and model-transport checks.

## Boundaries

No changes to AgenticAutomation itself, no service/device/ADB/rack operations, no Octane/Jira writes, no package installation, no publication and no commits. Real TTS/ASR/OCR/EDIABAS source availability does not establish device compatibility or business acceptance. Generated-WAV ASR is an input check, not vehicle feedback. A disabled LLM validation path must not be counted as a verified business PASS. All old releases remain intact.

## Verified Release

All 369 designs pass source/native gates; 368 require case-specific adapters/data/validation and one mixed-source candidate requires manual curation. The old 727 unconfirmed opinions were reassessed: 243 addressed in the proposed design, 111 retained candidates and 373 needing domain review. None is domain-approved or device-validated.

The first full pass took 752.394 seconds and validated 327; subsequent lower-concurrency passes repaired only failures. Matching failed drafts preserve prior error/call context, with three correction attempts per invocation. Final assembly retained 368 cache hits plus one new case; its time is not full inference throughput.

Independent code review led to fixes for declared-gap propagation, inactive partial oracles, source/display binding, numbered-command coverage and source/output restrictions. Artifact verification checked all 369 original scripts and defect evidence, 15 source hashes, 727 indexes, one visible workbook tab, complete CSV, no formulas and zero cases selected by the isolated native filter. Final gates passed: 100 focused Python checks and five model-transport checks, with output guide/skill links and actual disk contents verified.
