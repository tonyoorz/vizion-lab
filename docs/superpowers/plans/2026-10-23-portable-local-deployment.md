# Portable Local Deployment Implementation Plan

> **For agentic workers:** Execute this plan inline with the executing-plans workflow. Preserve the existing dirty worktree. Do not create branches, commit, push, transfer secrets, or initiate SSO login.

**Goal:** Make a fresh Windows or macOS checkout installable and diagnosable, with an explicit private-state transfer workflow.

**Architecture:** Reuse the existing dev launcher and local environment loader. A Node deployment command handles installation, redacted diagnostics, and allowlisted migration manifests. SQLite snapshots use the standard-library backup API rather than copying active database files. External SSO/model services remain explicit prerequisites, never silently replaced.

**Tech Stack:** Node 24, Python virtual environments, SQLite, Vitest, existing requirements.

---

### Task 1: Native Python Selection

**Files:** `scripts/devHelpers.mjs`, `scripts/dev.mjs`, `src/test/server/devHelpers.test.ts`.

- [x] Add platform cases for `.venv/Scripts/python.exe` and `.venv/bin/python`, explicit overrides, and stale paths.
- [x] Run `npm test -- src/test/server/devHelpers.test.ts` and observe missing resolver failures.
- [x] Export `resolvePythonCommand({ repoRoot, platform, env, existsSync })` and use it in the launcher.
- [x] Rerun the focused helper and startup tests. Standalone npm Python tools also use the native wrapper; new frontend processes bind loopback.

### Task 2: Setup and Redacted Diagnostics

**Files:** `scripts/deploy.mjs`, `scripts/deployment.mjs`, `src/test/server/deployment.test.ts`, `requirements-asr.txt`, `package.json`.

- [x] Test native command planning, missing private prerequisites, redaction, and safe argument validation before implementation.
- [x] Provide `setup`, `doctor`, and explicit optional ASR/keeper installation flags. Never overwrite local credentials.
- [x] Require Node 24, install JavaScript dependencies using the lockfile, create a native Python environment, and install Chromium.
- [x] Report database/model availability, external model endpoints, Python modules, certificate paths, ffmpeg, service health, and expected keeper tool names without emitting credentials.
- [x] Run focused tests and a real read-only doctor check. The source GLMProxy is not listening; other checked prerequisites pass.

### Task 3: Private State Transfer

**Files:** The deployment modules and their existing focused test file.

- [x] Test allowlisted export paths, secret/session exclusion, path traversal rejection, native import paths, overwrite refusal, and integrity validation.
- [x] Export an ignored directory with a versioned manifest. Data/model export is explicit; secrets and browser sessions are excluded.
- [x] Snapshot SQLite through Node 24's SQLite backup API; hash exported artifacts and verify them before importing.
- [x] Never package virtual environments, dependencies, arbitrary user directories, browser profiles, or raw environment files.
- [x] Verify round trips using synthetic fixtures only; do not duplicate the production source database. Full dry-run: 562 selected files, 58,322,963,307 estimated bytes.

### Task 4: Operator Guide and Final Verification

**Files:** `docs/portable-deployment.md`, `README.md`, `.gitignore`, `.env.example`.

- [x] Document Windows and macOS commands, separately installed BmwLogin/GLMProxy, secure credential setup, native model paths, data transfer, and feature acceptance checks.
- [x] Run helper/startup/deployment tests (87 passed), touched-file diagnostics and ESLint (clean), and the production build (passed with existing chunk-size warnings).
- [x] State explicitly that macOS execution and the destination computer are not verified from this Windows machine.
- [x] Review only the task diff; do not publish or alter unrelated changes. Read-only review findings are covered by seven passing regression tests: WAL/backup/volume capacity, inventory preservation, dangling sidecars and staged-byte validation.

## Delivery State

- `.deployment/source-baseline/manifest.json` contains redacted source inventory and zero private data files; Git exclusion was verified.
- Production database/model/report export was not executed. The all-options preview estimated approximately 58.3 GB; use an approved private destination when initiating the actual transfer.
- Source GLMProxy was not running during the real service check; doctor correctly exited nonzero rather than claiming full readiness.
- No commit, push, branch creation, SSO login, credential transfer, package installation or schedule registration was performed.