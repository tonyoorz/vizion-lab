# Windows And macOS Local Deployment

This workflow recreates Vizion's local application services on another authorized computer. It does not publish data, transfer SSO sessions, or clone the source user's operating system or editor profile.

Use the code revision containing `scripts/deploy.mjs`. Downloading an older `ccgithub/main` will not provide these commands. The source working tree may contain unpublished changes: code must be synchronized separately from private state. The transfer manifest records its source revision and whether tracked changes were present, but does not contain application code.

## 1. Prerequisites

- Native Node 24.x, Git, Python 3.11-3.13 (prefer 3.13), ffmpeg, and company network/VPN access.
- Company-approved certificate trust, package proxy access, and authorization for Octane and the other business systems.
- Space for the transfer, restored data, package downloads, and any existing-data backup. The source preview on 2026-10-23 selected approximately 58.3 GB across 562 files with all options. This changes as local data grows.
- A private transfer channel or approved encrypted storage. The exported directory is **not encrypted**. Never use a public repository or personal cloud drive. FAT32 cannot hold the large SQLite files.

Windows x64 is verified here. macOS path/command selection is covered by automated tests, but real macOS installation is not verified from this Windows machine. Apple Silicon is the recommended Mac target. Intel Mac, custom CUDA configurations, and unavailable native package wheels need separate qualification; do not treat installation failure as permission to silently drop ASR or search features.

Never copy `.venv` or `node_modules` between computers or architectures.

## 2. Check Out The Three Components

Use an authorized parent directory, then clone these company repositories alongside one another:

```text
git clone https://cc-github.bmwgroup.net/tonyxie/Fullpicture.git vizion-lab
git clone https://cc-github.bmwgroup.net/arnekittlaus/BmwLogin.git BmwLogin
git clone https://cc-github.bmwgroup.net/tonyxie/beaconproxy.git GLM-proxy
```

Vizion contains the dashboard, Node API, analytics, and embedded duplicate search. BmwLogin supplies the `bmw-tools` package, SSO and Session Keeper adapters. GLMProxy supplies the separately running model endpoint on port 8001. Merely copying Vizion does not install the latter two components.

## 3. Install Vizion

The deployment entrypoint uses only Node built-ins, so it works before `node_modules` has been installed. It preserves an existing native virtual environment and never overwrites `.env.local`.

### Windows PowerShell

Install Node 24, Python 3.13, Git and ffmpeg through your approved software channel. Open a new terminal after changing PATH. From `vizion-lab`:

```powershell
npm run deploy:setup -- --keeper-source ../BmwLogin --asr --dry-run
npm run deploy:setup -- --keeper-source ../BmwLogin --asr
```

If `python` resolves to a different runtime, pass `--python C:/path/to/Python313/python.exe`. The installer automatically uses the local `%USERPROFILE%/.copilot/corp-pip-proxy.ps1` helper when present. `--pip-helper C:/path/to/helper.ps1` explicitly selects another trusted local helper. The helper itself and proxy credentials are never exported. Chromium downloads may also require approved proxy settings in the local environment.

### macOS Terminal

Install native Git, Node 24, Python 3.13 and ffmpeg. With an already approved Homebrew installation, one possible setup is:

```bash
brew install node@24 python@3.13 ffmpeg
export PATH="$(brew --prefix node@24)/bin:$PATH"
cd vizion-lab
npm run deploy:setup -- --python "$(brew --prefix python@3.13)/bin/python3.13" --keeper-source ../BmwLogin --asr --dry-run
npm run deploy:setup -- --python "$(brew --prefix python@3.13)/bin/python3.13" --keeper-source ../BmwLogin --asr
```

If the company network blocks a package or browser download, configure its approved local proxy/CA mechanism and rerun. Do not disable TLS verification or commit proxy credentials.

Setup runs lockfile-based `npm ci`, creates the native virtual environment if needed, installs Python dependencies, installs `BmwLogin[all,session-keeper]`, installs optional local ASR dependencies when `--asr` is supplied, and installs Python Playwright Chromium. It creates a blank local configuration from `.env.example` only when `.env.local` does not exist. It does not launch SSO login, obtain credentials, download model weights, or start GLMProxy.

## 4. Export Private State On The Source Computer

Pause ingestion, report builders, and model downloads first. SQLite uses the online backup API, including committed WAL contents. Each SQLite file is individually consistent; there is no cross-database transaction. Quiesce writers when a matching source/hot/report snapshot is required. DuckDB, Parquet, reports and model files must not change during copying.

From the source `vizion-lab` checkout, preview the complete selection:

```text
npm run deploy:export -- --output .deployment/transfer-20261023 --with-data --with-models --with-reports --dry-run
```

After reviewing the capacity and choosing an approved private destination, remove only `--dry-run`:

```text
npm run deploy:export -- --output .deployment/transfer-20261023 --with-data --with-models --with-reports
```

An existing output directory is refused. Change `--output` to an approved disk location if the repository volume is too small. Keep the complete directory, including its manifest and `repo`/`home` subdirectories.

| Option | Selected private state |
| --- | --- |
| None | Redacted inventory manifest only; no private data/model files |
| `--with-data` | SQLite source/hot/feedback databases and supported cold data below the allowlisted database directories |
| `--with-models` | Repository embedding model cache; user-level SenseVoiceSmall, RapidOCR and GLM-OCR caches when present |
| `--with-reports` | Supported generated report files below `exports` and `docs/qgate-reports/generated_runs` |

The manifest contains SHA-256 hashes, sizes, source platform/runtime versions, configured **variable names only**, and registered keeper tool names when the source keeper is running. Hashes detect corruption, not the identity of an untrusted sender. Only import a package from a trusted source using the approved channel.

Excluded: environment file contents, passwords, API keys, cookies, SSO session stores, browser profiles, logs, virtual environments, installed dependencies, application code, editor configuration and arbitrary user directories. Database WAL/SHM files are not transferred separately. Symlinks/junctions and unsafe manifest paths are refused. Large model caches are not Git artifacts.

Explicit dashboard source/hot overrides are normalized to destination repository defaults. A separately configured duplicate-search database is refused rather than silently substituted. Review any nondefault paths and the inventory's configured-key list before reusing source configuration.

## 5. Import On The Destination Computer

Complete setup first. Stop Vizion, background refresh jobs and other data/model writers. The importer checks the local API/analytics ports and will not stop processes for you.

Windows example:

```powershell
npm run deploy:import -- --input D:/VizionTransfer/transfer-20261023 --dry-run
npm run deploy:import -- --input D:/VizionTransfer/transfer-20261023
```

macOS example:

```bash
npm run deploy:import -- --input /Volumes/ApprovedTransfer/transfer-20261023 --dry-run
npm run deploy:import -- --input /Volumes/ApprovedTransfer/transfer-20261023
```

Use your actual approved path. Dry-run validates every manifest entry/hash and detects destination conflicts without writing. Import maps `repo` paths into the new checkout and `home` paths into the current user's home; it does not reuse Windows usernames or drive letters on a Mac.

Existing files are refused, including a tracked feedback DB or previously downloaded model files. Review those conflicts first. On a reviewed fresh installation, explicitly permit replacement:

```text
npm run deploy:import -- --input /path/to/transfer-20261023 --replace --dry-run
npm run deploy:import -- --input /path/to/transfer-20261023 --replace
```

Replacement saves previous files, including an existing adapter inventory, under the destination `.deployment/pre-import-*` directory and reports its path. It never merges SQLite databases. Database sidecars must be closed/checkpointed before replacement. Capacity checks include SQLite WAL estimates, destination-volume staging, repository-volume backups, and a 10% margin.

Import validates every source hash, stages every copied file on its destination volume, and verifies all staged bytes before backing up/replacing targets. Existing inventory also requires `--replace`. Restoring multiple files is still not one atomic transaction: retain the package and backups until acceptance is complete.

The imported inventory is stored under `.deployment/source-inventory.json`; subsequent doctor runs compare the source keeper adapter names against the destination. `.deployment` and private environment files are Git-ignored.

## 6. Configure Credentials And GLMProxy

Configure private files locally, never in chat or command-line arguments. Do not overwrite a previously configured file with an example.

For **GLMProxy**, initialize its own `.env` from that component's example on a new checkout, then configure the authorized `BEACON_API_KEY`, any legacy `BMW_ACCESS_CODE` needed by your models, a native `UPSTREAM_CA_BUNDLE` path, and a private `INCOMING_API_KEY`. Obtain the certificate from an approved channel, not by disabling certificate verification. Use the component's own environment and requirements; do not mix its package pins into Vizion's environment.

Windows: in the sibling GLM-proxy checkout, use its documented `tools/setup.ps1` and `tools/start_proxy.ps1`. Supply secret values through local private configuration, not command-line flags.

macOS: in the sibling GLM-proxy checkout, prepare and run the native Python service:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Initialize .env from that checkout's .env.example only on a new, unconfigured clone.
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

For **Vizion**, edit its preserved/generated `.env.local`:

- `GLM_PROXY_BASE_URL`: normally `http://127.0.0.1:8001/v1`.
- `GLM_PROXY_API_KEY`: match GLMProxy's private `INCOMING_API_KEY`.
- `UPSTREAM_CA_BUNDLE`/`NODE_EXTRA_CA_CERTS`: only native, existing certificate paths when required. Never carry a Windows path onto a Mac.
- Retain only authorized `VIZION_INTERNAL_*` scopes/policies needed to match the source. The private inventory lists configured keys, not their values; inspect the original privately.
- Remove stale interpreter/database/model overrides. Default imports use this checkout's database directories and the current user's ModelScope cache. Keep native local ASR on CPU initially; qualify GPU acceleration separately.

The default template is for a trusted single-user loopback deployment. New `npm run dev` frontend processes bind `127.0.0.1`; healthy existing processes retain their previous settings until restarted. Do not publish this internal-mode proxy through another interface. Shared/LAN deployments require the README's OIDC and network-boundary procedure, not a `--host 0.0.0.0` shortcut.

## 7. Start And Check

Keep GLMProxy running in its own terminal. From Vizion:

```text
npm run deploy:doctor
npm run dev
```

Open `http://127.0.0.1:8080`. In a separate terminal:

```text
npm run deploy:doctor -- --services
npm run deploy:doctor -- --services --json
```

Doctor exits nonzero when a required prerequisite is missing. Without `--services`, it does not claim the services were checked. `/models` success is a configuration/connectivity check, not proof of upstream inference or tool-call support. Registered tools still require the destination user's own SSO authorization.

| Service | Default port |
| --- | --- |
| Frontend | 8080 |
| Local Node API | 3004 |
| Analytics | 3003 |
| Session Keeper | 18090 |
| Separately started GLMProxy | 8001 |

Log in afresh through the application. Do not import cookies or session-store JSON from the old computer. First duplicate search may rebuild its native index and take several minutes; model-cache transfer does not copy every generated search index.

## 8. Functional Acceptance

Do not equate HTTP 200 or importable modules with complete feature parity. Record pass/fail on the destination:

1. Login and a read-only Octane request with the destination account.
2. Dashboard/coverage/drilldown results against the selected source snapshot and authorized scopes.
3. AI chat using each required model, including a real analytics tool call and returned evidence.
4. Duplicate search and feedback round trip after warmup.
5. Voice recording/transcription, image OCR and PDF text extraction using synthetic non-sensitive samples.
6. An Excel/HTML report export and the required static catalog links.
7. AppCockpit, Codebeamer and each required VPS adapter using authorized read-only checks. Tool registration alone does not verify login or entitlement.
8. Source refresh, if needed, only after confirming the intended scope and existing-data backup.

Cross-platform report/refresh examples use the native interpreter wrapper:

```text
npm run report:qgate-kpi
node scripts/runPython.mjs -m backend.analytics_cli refresh-full-picture-outcomes
```

## 9. What Still Needs Separate Migration

- Windows scheduled tasks are not copied. Re-register the repository's nightly task on Windows only after reviewing paths/account/scope. On macOS configure an approved `launchd` job to run the equivalent native wrapper command; do not copy a PowerShell scheduled task. Scheduling/upstream synchronization is never enabled by setup/import.
- Browser-local AI chat history is not in Git or this private package. It stays in the original browser unless separately migrated through an approved workflow. This does not remove chat capability.
- VS Code extensions, user-level Skills, MCP servers, shell helpers and memory are not application dependencies and are not exported here. Review and transfer approved configuration separately; change Windows executable paths to native Mac paths and re-enter credentials locally. Do not automatically upload company configuration/data through personal sync services.
- A fresh checkout does not include ignored generated analysis artifacts unless the corresponding report/data option selects them. Special artifact directories outside the allowlist require a separately reviewed transfer; this tool does not recursively copy the user's Desktop.

Keep the original installation and private backups until all required destination checks pass. macOS execution and the destination computer must be qualified there; automated Windows tests are not a substitute.