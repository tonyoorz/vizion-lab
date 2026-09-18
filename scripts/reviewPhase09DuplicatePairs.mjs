#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import {
  PHASE09_PAIR_REVIEW_MODELS,
  PAIR_REVIEW_POLICY_VERSION,
  assertPhase09PairReviewGlmProxy,
  runPhase09PairReviewBatch,
  summarizePhase09PairReviewCache,
} from "../server/phase09DuplicatePairReviewRunner.mjs";

const EXECUTION_MODEL_ORDER = ["qwen3.7-max", "deepseek-v4-pro"];

function text(value) {
  return String(value ?? "").trim();
}

function positiveInteger(value, optionName, allowZero = false) {
  const parsed = Number.parseInt(text(value), 10);
  if (!Number.isInteger(parsed) || parsed < (allowZero ? 0 : 1)) {
    throw new Error(`${optionName} must be ${allowZero ? "a non-negative" : "a positive"} integer`);
  }
  return parsed;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function usage() {
  return [
    "Usage:",
    "  node scripts/reviewPhase09DuplicatePairs.mjs --input <pairs.jsonl> --cache <reviews.cache.jsonl>",
    "    [--manifest <run-manifest.json>] [--limit <n>] [--offset <n>] [--max-attempts <n>]",
    "    [--retry-invalid] [--retry-request-errors] [--dry-run]",
  ].join("\n");
}

function parseArguments(argv) {
  const options = {
    input: "",
    cache: "",
    manifest: "",
    limit: null,
    offset: 0,
    maxAttempts: 1,
    retryInvalid: false,
    retryRequestErrors: false,
    dryRun: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      console.log(usage());
      process.exit(0);
    }
    if (argument === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (argument === "--retry-invalid") {
      options.retryInvalid = true;
      continue;
    }
    if (argument === "--retry-request-errors") {
      options.retryRequestErrors = true;
      continue;
    }
    if (!argument.startsWith("--")) {
      throw new Error(`Unexpected argument: ${argument}`);
    }
    const value = argv[index + 1];
    if (value == null || value.startsWith("--")) {
      throw new Error(`Missing value for ${argument}`);
    }
    index += 1;
    if (argument === "--input") {
      options.input = value;
    } else if (argument === "--cache") {
      options.cache = value;
    } else if (argument === "--manifest") {
      options.manifest = value;
    } else if (argument === "--limit") {
      options.limit = positiveInteger(value, argument);
    } else if (argument === "--offset") {
      options.offset = positiveInteger(value, argument, true);
    } else if (argument === "--max-attempts") {
      options.maxAttempts = positiveInteger(value, argument);
    } else {
      throw new Error(`Unknown option: ${argument}`);
    }
  }
  if (!options.input || !options.cache) {
    throw new Error(usage());
  }
  return options;
}

async function readJsonl(filePath, label, allowMissing = false) {
  if (!existsSync(filePath)) {
    if (allowMissing) {
      return { raw: "", records: [] };
    }
    throw new Error(`${label} file does not exist: ${filePath}`);
  }
  const raw = await readFile(filePath, "utf8");
  const records = [];
  for (const [index, line] of raw.split(/\r?\n/).entries()) {
    if (!line.trim()) {
      continue;
    }
    try {
      records.push(JSON.parse(line));
    } catch {
      throw new Error(`Invalid JSONL in ${label} at ${filePath}:${index + 1}`);
    }
  }
  return { raw, records };
}

function validatePairInputs(records) {
  const seenPairIds = new Set();
  for (const [index, record] of records.entries()) {
    const pairId = text(record?.pair_id);
    if (!pairId || !text(record?.input_fingerprint) || !text(record?.policy_version)) {
      throw new Error(`Invalid Phase 09 pair input record at index ${index}`);
    }
    if (seenPairIds.has(pairId)) {
      throw new Error(`Duplicate Phase 09 pair input pair_id: ${pairId}`);
    }
    seenPairIds.add(pairId);
  }
  return records;
}

function jsonl(records) {
  return records.map((record) => `${JSON.stringify(record)}\n`).join("");
}

function baseManifest({ inputPath, inputRaw, cachePath, inputs, selectedInputs, mode }) {
  return {
    schema_version: "phase09-duplicate-pair-review-run-v1",
    mode,
    input_path: inputPath,
    input_sha256: sha256(inputRaw),
    input_records: inputs.length,
    selected_pair_records: selectedInputs.length,
    cache_path: cachePath,
    requested_models: PHASE09_PAIR_REVIEW_MODELS,
    review_policy_version: PAIR_REVIEW_POLICY_VERSION,
    policy_versions: [...new Set(inputs.map((record) => text(record.policy_version)))].sort(),
    generated_at: new Date().toISOString(),
  };
}

async function writeManifest(filePath, manifest) {
  await mkdir(dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const inputPath = resolve(options.input);
  const cachePath = resolve(options.cache);
  const manifestPath = resolve(options.manifest || `${cachePath}.manifest.json`);
  const { raw: inputRaw, records: rawInputs } = await readJsonl(inputPath, "input");
  const inputs = validatePairInputs(rawInputs);
  const selectedInputs = inputs.slice(options.offset, options.limit == null ? undefined : options.offset + options.limit);
  PHASE09_PAIR_REVIEW_MODELS.forEach((model) => assertPhase09PairReviewGlmProxy(model));
  const { records: cacheRecords } = await readJsonl(cachePath, "review cache", true);

  const manifest = baseManifest({
    inputPath,
    inputRaw,
    cachePath,
    inputs,
    selectedInputs,
    mode: options.dryRun ? "dry_run" : "run",
  });
  if (options.dryRun) {
    await writeManifest(manifestPath, {
      ...manifest,
      cached_count: 0,
      skipped_error_count: 0,
      requested_count: selectedInputs.length * PHASE09_PAIR_REVIEW_MODELS.length,
      completed_count: 0,
      invalid_response_count: 0,
      request_error_count: 0,
      input_error_count: 0,
      cache_summary: summarizePhase09PairReviewCache({ pairInputs: inputs, cacheRecords }),
    });
    console.log(`Dry run: ${selectedInputs.length} pair inputs, ${selectedInputs.length * PHASE09_PAIR_REVIEW_MODELS.length} independent model reviews.`);
    return;
  }

  await mkdir(dirname(cachePath), { recursive: true });
  const totals = {
    cached_count: 0,
    skipped_error_count: 0,
    requested_count: 0,
    completed_count: 0,
    invalid_response_count: 0,
    request_error_count: 0,
    input_error_count: 0,
  };
  for (let index = 0; index < selectedInputs.length; index += 1) {
    const result = await runPhase09PairReviewBatch({
      pairInputs: [selectedInputs[index]],
      cacheRecords,
      models: EXECUTION_MODEL_ORDER,
      maxAttempts: options.maxAttempts,
      retryInvalid: options.retryInvalid,
      retryRequestErrors: options.retryRequestErrors,
      stopOnQuotaExhausted: true,
    });
    for (const key of Object.keys(totals)) {
      totals[key] += result[key];
    }
    if (result.new_records.length) {
      await appendFile(cachePath, jsonl(result.new_records), "utf8");
      cacheRecords.push(...result.new_records);
    }
    if ((index + 1) % 10 === 0 || index + 1 === selectedInputs.length) {
      console.log(
        `Progress ${index + 1}/${selectedInputs.length} pairs; ${totals.completed_count} completed, ${totals.invalid_response_count + totals.request_error_count + totals.input_error_count} newly unresolved, ${totals.cached_count} cached.`,
      );
    }
    await writeManifest(manifestPath, {
      ...manifest,
      ...totals,
      processed_pair_records: index + 1,
      cache_summary: summarizePhase09PairReviewCache({ pairInputs: inputs, cacheRecords }),
    });
    if (result.quota_exhausted) {
      await writeManifest(manifestPath, {
        ...manifest,
        ...totals,
        processed_pair_records: index + 1,
        cache_summary: summarizePhase09PairReviewCache({ pairInputs: inputs, cacheRecords }),
        stopped_reason: "qwen3.7-max_insufficient_quota",
        stopped_pair_id: selectedInputs[index].pair_id,
      });
      console.error(
        `Stopped after Qwen quota exhaustion at pair ${selectedInputs[index].pair_id}; no unmatched DeepSeek review was requested for that pair.`,
      );
      process.exitCode = 1;
      return;
    }
  }
  console.log(
    `Finished Phase 09 duplicate pair review: ${totals.completed_count} completed, ${totals.invalid_response_count + totals.request_error_count + totals.input_error_count} newly unresolved, ${totals.cached_count} cached, ${totals.skipped_error_count} prior unresolved skipped.`,
  );
  if (totals.invalid_response_count || totals.request_error_count || totals.input_error_count || totals.skipped_error_count) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(text(error?.stack) || text(error?.message) || String(error));
  process.exitCode = 1;
});