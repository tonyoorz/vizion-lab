#!/usr/bin/env node

import { existsSync } from "node:fs";
import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { getDefaultChatModel } from "../server/chatModelConfig.mjs";
import { extractPhase09Scenario } from "../server/phase09ScenarioExtractor.mjs";
import { loadLocalEnv } from "../server/loadLocalEnv.mjs";

loadLocalEnv();

const DEFAULT_CONCURRENCY = 3;
const DEFAULT_MAX_RETRIES = 2;

function text(value) {
  return String(value ?? "").trim();
}

function positiveInteger(value, optionName) {
  const parsed = Number.parseInt(text(value), 10);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${optionName} must be a positive integer`);
  }
  return parsed;
}

function usage() {
  return [
    "Usage:",
    "  node scripts/extractPhase09Scenarios.mjs --input <inputs.jsonl> --output <outputs.jsonl>",
    "    [--model <model>] [--concurrency <n>] [--max-retries <n>] [--limit <n>] [--error-output <errors.jsonl>]",
  ].join("\n");
}

function parseArguments(argv) {
  const options = {
    input: "",
    output: "",
    errorOutput: "",
    model: getDefaultChatModel(process.env),
    concurrency: DEFAULT_CONCURRENCY,
    maxRetries: DEFAULT_MAX_RETRIES,
    limit: null,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      console.log(usage());
      process.exit(0);
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
    } else if (argument === "--output") {
      options.output = value;
    } else if (argument === "--error-output") {
      options.errorOutput = value;
    } else if (argument === "--model") {
      options.model = text(value);
    } else if (argument === "--concurrency") {
      options.concurrency = positiveInteger(value, argument);
    } else if (argument === "--max-retries") {
      options.maxRetries = positiveInteger(value, argument);
    } else if (argument === "--limit") {
      options.limit = positiveInteger(value, argument);
    } else {
      throw new Error(`Unknown option: ${argument}`);
    }
  }

  if (!options.input || !options.output) {
    throw new Error(usage());
  }
  if (!options.model) {
    throw new Error("--model must not be empty");
  }
  return options;
}

async function readJsonl(filePath, label) {
  if (!existsSync(filePath)) {
    if (label === "extraction cache") {
      return [];
    }
    throw new Error(`${label} file does not exist: ${filePath}`);
  }
  const content = await readFile(filePath, "utf8");
  const records = [];
  for (const [index, line] of content.split(/\r?\n/).entries()) {
    if (!line.trim()) {
      continue;
    }
    try {
      records.push(JSON.parse(line));
    } catch {
      throw new Error(`Invalid JSONL in ${label} at ${filePath}:${index + 1}`);
    }
  }
  return records;
}

function validateInputRecord(record, index) {
  const ticketId = text(record?.ticket_id);
  const inputFingerprint = text(record?.input_fingerprint);
  if (!ticketId || !inputFingerprint || !record?.ticket || typeof record.ticket !== "object") {
    throw new Error(`Invalid scenario extraction input record at index ${index}`);
  }
  return { ...record, ticket_id: ticketId, input_fingerprint: inputFingerprint };
}

function completedExtractionsByTicketId(records) {
  const extractions = new Map();
  for (const record of records) {
    const ticketId = text(record?.ticket_id);
    const fingerprint = text(record?.input_fingerprint);
    const extraction = record?.extraction;
    if (ticketId && fingerprint && extraction && typeof extraction === "object" && text(record?.source).toLowerCase() === "llm") {
      extractions.set(ticketId, record);
    }
  }
  return extractions;
}

async function extractWithRetry(ticketInput, options) {
  let failure = null;
  for (let attempt = 1; attempt <= options.maxRetries; attempt += 1) {
    try {
      const result = await extractPhase09Scenario({
        ticketInput,
        model: options.model,
      });
      return { result, attempt, error: null };
    } catch (error) {
      failure = error;
      console.warn(
        `Ticket ${ticketInput?.ticket_id ?? "(blank)"} attempt ${attempt}/${options.maxRetries} failed: ${text(error?.message)}`,
      );
    }
  }
  return { result: null, attempt: options.maxRetries, error: failure };
}

function jsonl(records) {
  return records.map((record) => `${JSON.stringify(record)}\n`).join("");
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const inputPath = resolve(options.input);
  const outputPath = resolve(options.output);
  const errorOutputPath = resolve(options.errorOutput || `${outputPath}.errors.jsonl`);
  const rawInputs = await readJsonl(inputPath, "input");
  const inputs = rawInputs.map(validateInputRecord);
  const cache = completedExtractionsByTicketId(await readJsonl(outputPath, "extraction cache"));
  let pendingInputs = inputs.filter((input) => {
    const cached = cache.get(input.ticket_id);
    return text(cached?.input_fingerprint) !== input.input_fingerprint;
  });
  if (options.limit != null) {
    pendingInputs = pendingInputs.slice(0, options.limit);
  }
  await mkdir(dirname(outputPath), { recursive: true });
  await mkdir(dirname(errorOutputPath), { recursive: true });
  console.log(
    `Phase 09 V2 scenario extraction: ${inputs.length} inputs, ${inputs.length - pendingInputs.length} cached, ${pendingInputs.length} pending, model ${options.model}.`,
  );

  let completed = 0;
  let failed = 0;
  const startTime = Date.now();

  for (let startIndex = 0; startIndex < pendingInputs.length; startIndex += options.concurrency) {
    const group = pendingInputs.slice(startIndex, startIndex + options.concurrency);
    const groupResults = await Promise.all(
      group.map((ticketInput) => extractWithRetry(ticketInput, options)),
    );
    const successfulRecords = [];
    const errorRecords = [];
    for (let index = 0; index < groupResults.length; index += 1) {
      const result = groupResults[index];
      const ticketInput = group[index];
      if (!result.error) {
        completed += 1;
        successfulRecords.push(
          {
            ...result.result,
            input_version: text(ticketInput.input_version),
            generated_at: new Date().toISOString(),
          },
        );
      } else {
        failed += 1;
        errorRecords.push({
          generated_at: new Date().toISOString(),
          model: options.model,
          ticket_id: ticketInput.ticket_id,
          input_fingerprint: ticketInput.input_fingerprint,
          attempts: result.attempt,
          error: text(result.error?.message) || "Unknown extraction error",
        });
      }
    }
    if (successfulRecords.length) {
      await appendFile(outputPath, jsonl(successfulRecords), "utf8");
    }
    if (errorRecords.length) {
      await appendFile(errorOutputPath, jsonl(errorRecords), "utf8");
    }
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const done = Math.min(startIndex + group.length, pendingInputs.length);
    console.log(
      `Progress ${done}/${pendingInputs.length} (${elapsed}s); ${completed} extracted, ${failed} failed.`,
    );
  }

  console.log(
    `Finished V2 scenario extraction: ${completed} completed, ${failed} failed, ${inputs.length - pendingInputs.length} reused from cache.`,
  );
  if (failed) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(text(error?.stack) || text(error?.message) || String(error));
  process.exitCode = 1;
});
