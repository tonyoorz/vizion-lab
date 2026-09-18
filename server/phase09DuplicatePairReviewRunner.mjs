import { createHash } from "node:crypto";

import { resolveChatModelConfig } from "./chatModelConfig.mjs";
import { requestCompanyChatCompletion } from "./companyChat.mjs";
import { reviewPhase09DuplicatePair } from "./phase09DuplicatePairReviewer.mjs";

export const PAIR_REVIEW_CACHE_SCHEMA_VERSION = "phase09-duplicate-pair-review-cache-v1";
export const PAIR_REVIEW_POLICY_VERSION = "phase09-duplicate-pair-reviewer-v4";
export const PHASE09_PAIR_REVIEW_MODELS = ["deepseek-v4-pro", "qwen3.7-max"];

const MAX_ERROR_CHARS = 1_000;

function text(value) {
  return String(value ?? "").trim();
}

function positiveInteger(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalModel(model) {
  const normalized = text(model).toLowerCase();
  const matched = PHASE09_PAIR_REVIEW_MODELS.find(
    (candidate) => candidate.toLowerCase() === normalized,
  );
  if (!matched) {
    throw new Error(
      `Phase 09 duplicate review model must be one of: ${PHASE09_PAIR_REVIEW_MODELS.join(", ")}`,
    );
  }
  return matched;
}

function pairIdentity(pairInput) {
  const pairId = text(pairInput?.pair_id);
  const inputFingerprint = text(pairInput?.input_fingerprint);
  const policyVersion = text(pairInput?.policy_version);
  if (!pairId || !inputFingerprint || !policyVersion) {
    throw new Error(
      "Phase 09 duplicate review input requires pair_id, input_fingerprint, and policy_version",
    );
  }
  return { pairId, inputFingerprint, policyVersion };
}

function cacheKeyForIdentity({ pairId, inputFingerprint, policyVersion }, model) {
  return sha256(
    JSON.stringify({
      pair_id: pairId,
      input_fingerprint: inputFingerprint,
      policy_version: policyVersion,
      review_policy_version: PAIR_REVIEW_POLICY_VERSION,
      model: canonicalModel(model),
    }),
  );
}

function configuredGlmProxyBaseUrl(environment) {
  for (const key of [
    "DUPSEARCH_GLM_PROXY_API_BASE",
    "GLM_PROXY_API_BASE",
    "GLM_PROXY_BASE_URL",
    "GLM_PROXY_URL",
  ]) {
    const value = text(environment?.[key]);
    if (value) {
      return value;
    }
  }
  return text(environment?.GLM_PROXY_API_KEY) ? "http://127.0.0.1:8001/v1" : "";
}

function normalizedBaseUrl(value) {
  return text(value).replace(/\/+$/, "");
}

function timestamp(now) {
  const value = now();
  return value instanceof Date ? value.toISOString() : text(value) || new Date().toISOString();
}

function boundedError(error) {
  const message = text(error?.message) || "Unknown duplicate pair review error";
  return message.length <= MAX_ERROR_CHARS ? message : `${message.slice(0, MAX_ERROR_CHARS - 3)}...`;
}

function failureStatus(error, responseContent) {
  const message = boundedError(error);
  if (responseContent || /^LLM returned /i.test(message)) {
    return "invalid_response";
  }
  if (/^Phase 09 pair |^Phase 09 duplicate review input /i.test(message)) {
    return "input_error";
  }
  return "request_error";
}

export function isPhase09PairReviewQuotaExhausted(record) {
  return (
    text(record?.status) === "request_error"
    && /(?:insufficient_quota|allocated quota exceeded)/i.test(text(record?.error))
  );
}

function cacheRecordsByKey(cacheRecords) {
  const completed = new Map();
  const errors = new Map();
  for (const record of Array.isArray(cacheRecords) ? cacheRecords : []) {
    try {
      const identity = pairIdentity(record);
      const expectedCacheKey = cacheKeyForIdentity(identity, record.model);
      if (
        text(record.cache_key) !== expectedCacheKey
        || text(record.review_policy_version) !== PAIR_REVIEW_POLICY_VERSION
      ) {
        continue;
      }
      if (text(record?.status) === "completed" && record?.outcome && typeof record.outcome === "object") {
        completed.set(expectedCacheKey, record);
      } else if (["invalid_response", "request_error", "input_error"].includes(text(record?.status))) {
        errors.set(expectedCacheKey, record);
      }
    } catch {
      // Malformed historic rows remain auditable but cannot be reused.
    }
  }
  return { completed, errors };
}

function shouldRetryCachedError(record, retryInvalid, retryRequestErrors) {
  if (text(record?.status) === "invalid_response") {
    return Boolean(retryInvalid);
  }
  if (text(record?.status) === "request_error") {
    return Boolean(retryRequestErrors);
  }
  return false;
}

function ensureUniquePairInputs(pairInputs) {
  const seenPairIds = new Set();
  for (const pairInput of pairInputs) {
    const { pairId } = pairIdentity(pairInput);
    if (seenPairIds.has(pairId)) {
      throw new Error(`Duplicate Phase 09 duplicate review pair_id: ${pairId}`);
    }
    seenPairIds.add(pairId);
  }
}

function createRecord({
  identity,
  cacheKey,
  model,
  answerModel,
  status,
  attempts,
  generatedAt,
  outcome,
  error,
  responseContent,
}) {
  const base = {
    schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
    cache_key: cacheKey,
    pair_id: identity.pairId,
    input_fingerprint: identity.inputFingerprint,
    policy_version: identity.policyVersion,
    review_policy_version: PAIR_REVIEW_POLICY_VERSION,
    model,
    answer_model: answerModel || model,
    source: "llm",
    status,
    attempts,
    generated_at: generatedAt,
  };
  if (status === "completed") {
    return { ...base, outcome };
  }
  const response = text(responseContent);
  return {
    ...base,
    error_code: status.toUpperCase(),
    error: boundedError(error),
    response_sha256: response ? sha256(response) : "",
    response_characters: response.length,
  };
}

async function reviewOne({
  pairInput,
  model,
  requestCompletion,
  now,
  maxAttempts,
  retryInvalid,
  retryRequestErrors,
}) {
  const identity = pairIdentity(pairInput);
  const cacheKey = cacheKeyForIdentity(identity, model);
  let failure = null;
  let responseContent = "";
  let answerModel = "";
  let correctionAttempt = false;
  for (let attempts = 1; attempts <= maxAttempts; attempts += 1) {
    responseContent = "";
    try {
      const review = await reviewPhase09DuplicatePair({
        pairInput,
        model,
        correctionAttempt,
        requestCompletion: async (request) => {
          const response = await requestCompletion(request);
          responseContent = text(response?.content);
          answerModel = text(response?.answerModel);
          return response;
        },
      });
      return createRecord({
        identity,
        cacheKey,
        model,
        answerModel: text(review?.model) || answerModel,
        status: "completed",
        attempts,
        generatedAt: timestamp(now),
        outcome: {
          verdict: review.verdict,
          confidence: review.confidence,
          positive_evidence_ids: review.positive_evidence_ids,
          contradiction_evidence_ids: review.contradiction_evidence_ids,
          verified_required_evidence_ids: review.verified_required_evidence_ids,
          rationale: review.rationale,
        },
      });
    } catch (error) {
      failure = error;
      const status = failureStatus(error, responseContent);
      const shouldRetry = attempts < maxAttempts && (
        (status === "invalid_response" && retryInvalid)
        || (status === "request_error" && retryRequestErrors)
      );
      if (!shouldRetry) {
        return createRecord({
          identity,
          cacheKey,
          model,
          answerModel,
          status,
          attempts,
          generatedAt: timestamp(now),
          error,
          responseContent,
        });
      }
      correctionAttempt = correctionAttempt || status === "invalid_response";
    }
  }

  throw failure || new Error("Phase 09 duplicate pair review exhausted without a result");
}

export function buildPhase09PairReviewCacheKey(pairInput, model) {
  return cacheKeyForIdentity(pairIdentity(pairInput), model);
}

export function summarizePhase09PairReviewCache({ pairInputs, cacheRecords = [] }) {
  if (!Array.isArray(pairInputs)) {
    throw new Error("Phase 09 duplicate review pairInputs must be an array");
  }
  ensureUniquePairInputs(pairInputs);
  const inputsByPairId = new Map(
    pairInputs.map((pairInput) => [pairIdentity(pairInput).pairId, pairInput]),
  );
  const latestRecords = new Map();
  for (const record of Array.isArray(cacheRecords) ? cacheRecords : []) {
    try {
      const recordIdentity = pairIdentity(record);
      const pairInput = inputsByPairId.get(recordIdentity.pairId);
      if (!pairInput) {
        continue;
      }
      const inputIdentity = pairIdentity(pairInput);
      const model = canonicalModel(record.model);
      if (
        recordIdentity.inputFingerprint !== inputIdentity.inputFingerprint
        || recordIdentity.policyVersion !== inputIdentity.policyVersion
        || text(record.review_policy_version) !== PAIR_REVIEW_POLICY_VERSION
        || text(record.cache_key) !== cacheKeyForIdentity(inputIdentity, model)
      ) {
        continue;
      }
      latestRecords.set(`${inputIdentity.pairId}|${model}`, record);
    } catch {
      // Historic or malformed rows are retained on disk but excluded from current coverage.
    }
  }

  const statusCounts = {
    completed: 0,
    invalid_response: 0,
    request_error: 0,
    input_error: 0,
    invalid_record: 0,
    not_run: 0,
  };
  let dualCompletedPairs = 0;
  for (const pairInput of pairInputs) {
    const { pairId } = pairIdentity(pairInput);
    const statuses = PHASE09_PAIR_REVIEW_MODELS.map((model) => {
      const status = text(latestRecords.get(`${pairId}|${model}`)?.status);
      const knownStatus = Object.hasOwn(statusCounts, status) && status !== "not_run";
      const resolvedStatus = knownStatus ? status : status ? "invalid_record" : "not_run";
      statusCounts[resolvedStatus] += 1;
      return resolvedStatus;
    });
    if (statuses.every((status) => status === "completed")) {
      dualCompletedPairs += 1;
    }
  }
  return {
    review_policy_version: PAIR_REVIEW_POLICY_VERSION,
    expected_review_slots: pairInputs.length * PHASE09_PAIR_REVIEW_MODELS.length,
    latest_review_slots: latestRecords.size,
    completed_review_slots: statusCounts.completed,
    invalid_response_slots: statusCounts.invalid_response,
    request_error_slots: statusCounts.request_error,
    input_error_slots: statusCounts.input_error,
    invalid_record_slots: statusCounts.invalid_record,
    not_run_slots: statusCounts.not_run,
    dual_completed_pairs: dualCompletedPairs,
    incomplete_pairs: pairInputs.length - dualCompletedPairs,
  };
}

export function assertPhase09PairReviewGlmProxy(model, environment = process.env) {
  const selectedModel = canonicalModel(model);
  const configuredBaseUrl = normalizedBaseUrl(configuredGlmProxyBaseUrl(environment));
  const config = resolveChatModelConfig(selectedModel, environment);
  if (
    !configuredBaseUrl
    || config.usesInternalEndpoint
    || config.endpoint
    || normalizedBaseUrl(config.baseUrl) !== configuredBaseUrl
  ) {
    throw new Error(
      `Phase 09 duplicate review model ${selectedModel} must route through the configured GLM Proxy`,
    );
  }
  return config;
}

export async function runPhase09PairReviewBatch({
  pairInputs,
  cacheRecords = [],
  models = PHASE09_PAIR_REVIEW_MODELS,
  environment = process.env,
  requestCompletion = requestCompanyChatCompletion,
  now = () => new Date(),
  retryInvalid = false,
  retryRequestErrors = false,
  maxAttempts = 1,
  stopOnQuotaExhausted = false,
}) {
  if (!Array.isArray(pairInputs)) {
    throw new Error("Phase 09 duplicate review pairInputs must be an array");
  }
  const selectedModels = models.map(canonicalModel);
  if (
    selectedModels.length !== PHASE09_PAIR_REVIEW_MODELS.length
    || new Set(selectedModels).size !== PHASE09_PAIR_REVIEW_MODELS.length
  ) {
    throw new Error("Phase 09 duplicate review requires both independent approved models");
  }
  selectedModels.forEach((model) => assertPhase09PairReviewGlmProxy(model, environment));
  ensureUniquePairInputs(pairInputs);

  const cachedByKey = cacheRecordsByKey(cacheRecords);
  const reusedRecords = [];
  const skippedErrorRecords = [];
  const newRecords = [];
  let quotaExhausted = false;
  reviewPairs:
  for (const pairInput of pairInputs) {
    const identity = pairIdentity(pairInput);
    for (const model of selectedModels) {
      const cacheKey = cacheKeyForIdentity(identity, model);
      const cached = cachedByKey.completed.get(cacheKey);
      if (cached) {
        reusedRecords.push({ ...cached, reused_from_cache: true });
        continue;
      }
      const cachedError = cachedByKey.errors.get(cacheKey);
      if (cachedError && !shouldRetryCachedError(cachedError, retryInvalid, retryRequestErrors)) {
        skippedErrorRecords.push({ ...cachedError, skipped_from_cache: true });
      } else {
        const record = await reviewOne({
          pairInput,
          model,
          requestCompletion,
          now,
          maxAttempts: positiveInteger(maxAttempts, 1),
          retryInvalid: Boolean(retryInvalid),
          retryRequestErrors: Boolean(retryRequestErrors),
        });
        newRecords.push(record);
        if (stopOnQuotaExhausted && isPhase09PairReviewQuotaExhausted(record)) {
          quotaExhausted = true;
          break reviewPairs;
        }
      }
    }
  }

  return {
    cached_count: reusedRecords.length,
    skipped_error_count: skippedErrorRecords.length,
    requested_count: newRecords.length,
    quota_exhausted: quotaExhausted,
    completed_count: newRecords.filter((record) => record.status === "completed").length,
    invalid_response_count: newRecords.filter((record) => record.status === "invalid_response").length,
    request_error_count: newRecords.filter((record) => record.status === "request_error").length,
    input_error_count: newRecords.filter((record) => record.status === "input_error").length,
    reused_records: reusedRecords,
    skipped_error_records: skippedErrorRecords,
    new_records: newRecords,
  };
}