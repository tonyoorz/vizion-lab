import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

const temporaryDirectories: string[] = [];
const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const scriptPath = resolve(workspaceRoot, "scripts/reviewPhase09DuplicatePairs.mjs");

const pairInput = {
  policy_version: "phase09-duplicate-pair-v1",
  pair_id: "pair-cli-fixture",
  input_fingerprint: "c".repeat(64),
  required_evidence_ids: [
    "formal_parent:900001",
    "assigned_ecu:idcevo-25",
    "function:navigation",
  ],
  evidence: [
    { evidence_id: "formal_parent:900001", type: "formal_duplicate_parent", value: "900001" },
    { evidence_id: "assigned_ecu:idcevo-25", type: "exact_assigned_ecu", value: "IDCEVO-25" },
    { evidence_id: "function:navigation", type: "exact_function_context", value: "Navigation" },
  ],
  left: {},
  right: {},
};

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("reviewPhase09DuplicatePairs CLI", () => {
  it("writes a dry-run manifest after validating the source and GLM Proxy route without sending model requests", async () => {
    const directory = await mkdtemp(resolve(tmpdir(), "phase09-pair-review-"));
    temporaryDirectories.push(directory);
    const inputPath = resolve(directory, "pairs.jsonl");
    const cachePath = resolve(directory, "reviews.cache.jsonl");
    const manifestPath = resolve(directory, "run-manifest.json");
    await writeFile(inputPath, `${JSON.stringify(pairInput)}\n`, "utf8");

    const output = execFileSync(
      process.execPath,
      [
        scriptPath,
        "--input",
        inputPath,
        "--cache",
        cachePath,
        "--manifest",
        manifestPath,
        "--dry-run",
      ],
      {
        cwd: workspaceRoot,
        encoding: "utf8",
        env: {
          ...process.env,
          GLM_PROXY_API_KEY: "fixture-proxy-key",
          BEACON_API_KEY: "",
          DUPSEARCH_BEACON_API_KEY: "",
        },
      },
    );

    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    expect(output).toContain("Dry run");
    expect(manifest).toMatchObject({
      schema_version: "phase09-duplicate-pair-review-run-v1",
      mode: "dry_run",
      input_records: 1,
      selected_pair_records: 1,
      requested_models: ["deepseek-v4-pro", "qwen3.7-max"],
      review_policy_version: "phase09-duplicate-pair-reviewer-v4",
      policy_versions: ["phase09-duplicate-pair-v1"],
      cache_summary: {
        expected_review_slots: 2,
        latest_review_slots: 0,
        completed_review_slots: 0,
        not_run_slots: 2,
        dual_completed_pairs: 0,
        incomplete_pairs: 1,
      },
    });
  });
});