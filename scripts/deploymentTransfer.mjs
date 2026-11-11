import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

const HOME_MODELS = [
  ".cache/modelscope/iic/SenseVoiceSmall",
  ".cache/modelscope/RapidAI/RapidOCR",
  ".cache/modelscope/ZhipuAI/GLM-OCR",
];
const FORBIDDEN_NAMES = /^(?:\.env(?:\..*)?|\.git|\.venv|node_modules|__pycache__|logs?|session-keeper|cookie(?:\..*)?|login_info(?:\..*)?|credentials?(?:\..*)?|secrets?(?:\..*)?)$/i;
const DATA_EXTENSION = /\.(db|sqlite|duckdb|parquet|json)$/i;
const REPORT_EXTENSION = /\.(html?|jsonl?|csv|xlsx|md|txt|pdf|png|jpe?g|webp|css|js)$/i;

function allowedPath(root, relative) {
  if (typeof relative !== "string" || !relative || /[\\:\x00-\x1f]/.test(relative)) return false;
  const segments = relative.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === ".." || /[. ]$/.test(segment) || FORBIDDEN_NAMES.test(segment) || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(segment))) return false;
  if (root === "home") return HOME_MODELS.some((prefix) => relative.startsWith(`${prefix}/`));
  if (root !== "repo") return false;
  if (/^database\/(source|hot|cold)\//.test(relative)) return DATA_EXTENSION.test(relative);
  if (relative.startsWith("backend/database/")) return /\.(db|sqlite)$/i.test(relative);
  if (relative.startsWith("backend/cache/embedding_models/")) return !/\.(pyc|dll|exe|so)$/i.test(relative);
  if (relative.startsWith("exports/") || relative.startsWith("docs/qgate-reports/generated_runs/")) return REPORT_EXTENSION.test(relative);
  return false;
}

function assertNoLinks(root, target) {
  const relative = path.relative(root, target);
  if (relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) throw new Error("Unsafe path outside its declared root.");
  let current = root;
  for (const segment of ["", ...relative.split(path.sep).filter(Boolean)]) {
    current = path.join(current, segment);
    if (fs.lstatSync(current, { throwIfNoEntry: false })?.isSymbolicLink()) throw new Error("Symbolic links and directory junctions are not allowed in transfer paths.");
  }
}

function collectFiles({ sourceRoot, sourcePath, root, relative, entries }) {
  if (!fs.existsSync(sourcePath)) return;
  assertNoLinks(sourceRoot, sourcePath);
  const stat = fs.lstatSync(sourcePath);
  if (stat.isDirectory()) {
    for (const child of fs.readdirSync(sourcePath, { withFileTypes: true })) {
      if (FORBIDDEN_NAMES.test(child.name)) continue;
      collectFiles({ sourceRoot, sourcePath: path.join(sourcePath, child.name), root, relative: `${relative}/${child.name}`, entries });
    }
  } else if (stat.isFile() && allowedPath(root, relative)) {
    const kind = /\.(db|sqlite)$/i.test(relative) ? "sqlite" : "file";
    const wal = kind === "sqlite" ? fs.lstatSync(`${sourcePath}-wal`, { throwIfNoEntry: false }) : null;
    if (wal?.isSymbolicLink()) throw new Error("Source database WAL links are not allowed.");
    entries.set(`${root}/${relative}`.toLowerCase(), { root, path: relative, source: sourcePath, bytes: stat.size, estimatedBytes: stat.size + (wal?.size || 0), kind });
  }
}

export function planTransfer({ repoRoot, homeDir = os.homedir(), includeData = false, includeModels = false, includeReports = false, env = process.env }) {
  const entries = new Map();
  const collect = (sourceRoot, sourcePath, root, relative) => collectFiles({ sourceRoot, sourcePath, root, relative, entries });
  if (includeData) {
    const databaseRoot = path.resolve(env.VIZION_DATABASE_ROOT || path.join(repoRoot, "database"));
    collect(env.VIZION_DATABASE_ROOT ? databaseRoot : repoRoot, databaseRoot, "repo", "database");
    collect(repoRoot, path.join(repoRoot, "backend", "database"), "repo", "backend/database");
    for (const [key, relative] of [["VIZION_FULL_PICTURE_SOURCE_DB_PATH", "database/source/qgate_raw.db"], ["VIZION_FULL_PICTURE_HOT_DB_PATH", "database/hot/vizion_serving.db"]]) {
      if (env[key]) {
        const sourcePath = path.resolve(env[key]);
        collect(path.dirname(sourcePath), sourcePath, "repo", relative);
      }
    }
    if (env.DUPSEARCH_SQLITE_PATH && path.resolve(env.DUPSEARCH_SQLITE_PATH) !== path.resolve(env.VIZION_FULL_PICTURE_SOURCE_DB_PATH || path.join(databaseRoot, "source", "qgate_raw.db"))) {
      throw new Error("A separate DUPSEARCH_SQLITE_PATH requires a separate reviewed data migration; it cannot silently replace the dashboard source.");
    }
  }
  if (includeModels) {
    collect(repoRoot, path.join(repoRoot, "backend", "cache", "embedding_models"), "repo", "backend/cache/embedding_models");
    for (const relative of HOME_MODELS) collect(homeDir, path.join(homeDir, relative), "home", relative);
    if (env.DUPSEARCH_LOCAL_ASR_MODEL && env.DUPSEARCH_LOCAL_ASR_MODEL !== "iic/SenseVoiceSmall") {
      const sourcePath = path.resolve(env.DUPSEARCH_LOCAL_ASR_MODEL);
      if (!fs.existsSync(sourcePath)) throw new Error("Configured local ASR model path does not exist; correct it before exporting models.");
      collect(sourcePath, sourcePath, "home", HOME_MODELS[0]);
    }
  }
  if (includeReports) {
    collect(repoRoot, path.join(repoRoot, "exports"), "repo", "exports");
    collect(repoRoot, path.join(repoRoot, "docs", "qgate-reports", "generated_runs"), "repo", "docs/qgate-reports/generated_runs");
  }
  return [...entries.values()].sort((left, right) => `${left.root}/${left.path}`.localeCompare(`${right.root}/${right.path}`));
}

async function hashFile(filePath) {
  const hash = createHash("sha256");
  for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
  return hash.digest("hex");
}

function diskSpaceInfo(directory) {
  let existing = path.resolve(directory);
  while (!fs.existsSync(existing)) existing = path.dirname(existing);
  const stat = fs.statfsSync(existing);
  return { volume: String(fs.statSync(existing).dev), availableBytes: stat.bavail * stat.bsize };
}

function checkDiskSpace(writes, spaceInfo = diskSpaceInfo) {
  const volumes = new Map();
  for (const { directory, bytes } of writes) {
    const info = spaceInfo(directory);
    const total = volumes.get(info.volume) || { bytes: 0, availableBytes: info.availableBytes };
    total.bytes += bytes;
    total.availableBytes = Math.min(total.availableBytes, info.availableBytes);
    volumes.set(info.volume, total);
  }
  if ([...volumes.values()].some(({ bytes, availableBytes }) => availableBytes < bytes * 1.1)) {
    throw new Error("Insufficient free space for staged files and backups plus a 10% safety margin.");
  }
}

function privateInventory(inventory) {
  return {
    python: inventory.python || null,
    versions: inventory.versions || {},
    configuredKeys: Array.isArray(inventory.configuredKeys) ? inventory.configuredKeys : [],
    keeperTools: Array.isArray(inventory.keeperTools) ? inventory.keeperTools : [],
    codeRevision: inventory.codeRevision || null,
    dirtyWorktree: inventory.dirtyWorktree === true,
  };
}

export async function exportTransfer({ outputDir, inventory = {}, onProgress = () => {}, spaceInfo, ...options }) {
  if (!outputDir) throw new Error("An explicit output directory is required.");
  outputDir = path.resolve(outputDir);
  if (fs.existsSync(outputDir)) throw new Error("Output directory already exists; choose a new private directory.");
  const entries = planTransfer(options);
  checkDiskSpace([{ directory: outputDir, bytes: entries.reduce((total, entry) => total + entry.estimatedBytes, 0) }], spaceInfo);
  fs.mkdirSync(path.dirname(outputDir), { recursive: true });
  fs.mkdirSync(outputDir, { mode: 0o700 });
  const files = [];
  for (const entry of entries) {
    const destination = path.join(outputDir, entry.root, entry.path);
    fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 });
    onProgress(`${entry.kind}: ${entry.root}/${entry.path}`);
    if (entry.kind === "sqlite") {
      const { DatabaseSync, backup } = await import("node:sqlite");
      const source = new DatabaseSync(entry.source, { readOnly: true });
      try {
        await backup(source, destination);
      } finally {
        source.close();
      }
    } else {
      fs.copyFileSync(entry.source, destination, fs.constants.COPYFILE_EXCL);
    }
    fs.chmodSync(destination, 0o600);
    files.push({ root: entry.root, path: entry.path, kind: entry.kind, bytes: fs.statSync(destination).size, sha256: await hashFile(destination) });
  }
  const manifest = {
    format: "vizion-private-transfer",
    version: 1,
    createdAt: new Date().toISOString(),
    source: { platform: process.platform, architecture: process.arch, node: process.versions.node },
    inventory: privateInventory(inventory),
    files,
  };
  fs.writeFileSync(path.join(outputDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx", mode: 0o600 });
  return manifest;
}

export function readTransferManifest(inputDir) {
  inputDir = path.resolve(inputDir);
  const manifestFile = path.join(inputDir, "manifest.json");
  assertNoLinks(inputDir, manifestFile);
  if (fs.statSync(manifestFile).size > 10_000_000) throw new Error("Transfer manifest is too large.");
  const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
  if (manifest.format !== "vizion-private-transfer" || manifest.version !== 1 || !Array.isArray(manifest.files)) throw new Error("Unsupported transfer manifest format.");
  const seen = new Set();
  for (const entry of manifest.files) {
    if (!entry || !allowedPath(entry.root, entry.path) || !["sqlite", "file"].includes(entry.kind) || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || !/^[a-f0-9]{64}$/.test(entry.sha256)) throw new Error("Unsafe path or invalid entry outside the transfer allowlist.");
    const key = `${entry.root}/${entry.path}`.toLowerCase();
    if (seen.has(key)) throw new Error("Duplicate or case-colliding transfer path.");
    seen.add(key);
  }
  return manifest;
}

export async function importTransfer({ repoRoot, homeDir = os.homedir(), inputDir, replace = false, dryRun = false, onProgress = () => {}, spaceInfo }) {
  if (!inputDir) throw new Error("An explicit input directory is required.");
  inputDir = path.resolve(inputDir);
  const manifest = readTransferManifest(inputDir);
  const metadataDirectory = path.join(repoRoot, ".deployment");
  const inventoryPath = path.join(metadataDirectory, "source-inventory.json");
  assertNoLinks(repoRoot, inventoryPath);
  const inventoryStat = fs.lstatSync(inventoryPath, { throwIfNoEntry: false });
  if (inventoryStat && (!replace || !inventoryStat.isFile())) throw new Error("Adapter inventory already exists; use --replace only after reviewing it.");
  const targets = [];
  for (const entry of manifest.files) {
    const source = path.join(inputDir, entry.root, entry.path);
    const root = entry.root === "repo" ? repoRoot : homeDir;
    const destination = path.join(root, entry.path);
    assertNoLinks(inputDir, source);
    assertNoLinks(root, destination);
    if (!fs.existsSync(source) || !fs.statSync(source).isFile() || fs.statSync(source).size !== entry.bytes || await hashFile(source) !== entry.sha256) throw new Error(`Transfer integrity/checksum failed: ${entry.root}/${entry.path}`);
    const exists = fs.existsSync(destination);
    if (exists && (!replace || !fs.statSync(destination).isFile())) throw new Error(`Destination already exists; refusing overwrite: ${entry.root}/${entry.path}. Use --replace only after reviewing existing data.`);
    if (entry.kind === "sqlite" && [`${destination}-wal`, `${destination}-shm`].some((sidecar) => fs.lstatSync(sidecar, { throwIfNoEntry: false }))) throw new Error("Destination database sidecars or links exist. Stop writers and checkpoint/close the database before replacing it.");
    targets.push({ ...entry, source, destination, exists });
  }
  if (dryRun) return { validated: targets.length, dryRun: true };
  const backupBytes = targets.filter((entry) => entry.exists).reduce((total, entry) => total + fs.statSync(entry.destination).size, inventoryStat?.size || 0);
  const inventoryBytes = Buffer.byteLength(JSON.stringify(privateInventory(manifest.inventory || {})));
  checkDiskSpace([
    ...targets.map((entry) => ({ directory: entry.root === "repo" ? repoRoot : homeDir, bytes: entry.bytes })),
    { directory: repoRoot, bytes: backupBytes + inventoryBytes },
  ], spaceInfo);
  const backupDir = path.join(metadataDirectory, `pre-import-${Date.now()}-${randomUUID()}`);
  const staged = [];
  let inventoryTemporary;
  try {
    for (const entry of targets) {
      assertNoLinks(entry.root === "repo" ? repoRoot : homeDir, entry.destination);
      fs.mkdirSync(path.dirname(entry.destination), { recursive: true, mode: 0o700 });
      const temporary = `${entry.destination}.import-${randomUUID()}`;
      staged.push({ ...entry, temporary });
      fs.copyFileSync(entry.source, temporary, fs.constants.COPYFILE_EXCL);
      fs.chmodSync(temporary, 0o600);
      if (fs.statSync(temporary).size !== entry.bytes || await hashFile(temporary) !== entry.sha256) throw new Error(`Staged file integrity/checksum failed: ${entry.root}/${entry.path}`);
      onProgress(`staged: ${entry.root}/${entry.path}`);
    }
    assertNoLinks(repoRoot, inventoryPath);
    fs.mkdirSync(metadataDirectory, { recursive: true, mode: 0o700 });
    inventoryTemporary = `${inventoryPath}.import-${randomUUID()}`;
    fs.writeFileSync(inventoryTemporary, `${JSON.stringify(privateInventory(manifest.inventory || {}), null, 2)}\n`, { flag: "wx", mode: 0o600 });
    const previousFiles = targets.filter((entry) => entry.exists);
    if (inventoryStat) previousFiles.push({ root: "repo", path: ".deployment/source-inventory.json", destination: inventoryPath });
    for (const entry of previousFiles) {
      assertNoLinks(entry.root === "repo" ? repoRoot : homeDir, entry.destination);
      const previous = path.join(backupDir, entry.root, entry.path);
      fs.mkdirSync(path.dirname(previous), { recursive: true, mode: 0o700 });
      fs.copyFileSync(entry.destination, previous, fs.constants.COPYFILE_EXCL);
      fs.chmodSync(previous, 0o600);
    }
    for (const entry of staged) {
      assertNoLinks(entry.root === "repo" ? repoRoot : homeDir, entry.destination);
      if (Boolean(fs.lstatSync(entry.destination, { throwIfNoEntry: false })) !== entry.exists) throw new Error("Destination changed during import; stop writers and retry.");
      if (entry.kind === "sqlite" && [`${entry.destination}-wal`, `${entry.destination}-shm`].some((sidecar) => fs.lstatSync(sidecar, { throwIfNoEntry: false }))) throw new Error("Database sidecars appeared during import; stop writers and retry.");
    }
    for (const entry of staged) {
      fs.renameSync(entry.temporary, entry.destination);
      onProgress(`restored: ${entry.root}/${entry.path}`);
    }
    fs.renameSync(inventoryTemporary, inventoryPath);
    return { restored: targets.length, backupDir: previousFiles.length ? backupDir : null };
  } finally {
    for (const entry of staged) {
      if (fs.lstatSync(entry.temporary, { throwIfNoEntry: false })) fs.unlinkSync(entry.temporary);
    }
    if (inventoryTemporary && fs.lstatSync(inventoryTemporary, { throwIfNoEntry: false })) fs.unlinkSync(inventoryTemporary);
  }
}