import { describe, expect, it } from "vitest";

import {
  PAIR_REVIEW_CACHE_SCHEMA_VERSION,
  PAIR_REVIEW_POLICY_VERSION,
  assertPhase09PairReviewGlmProxy,
  buildPhase09PairReviewCacheKey,
  isPhase09PairReviewQuotaExhausted,
  runPhase09PairReviewBatch,
} from "../../../server/phase09DuplicatePairReviewRunner.mjs";

const pairInput = {
  policy_version: "phase09-duplicate-pair-v1",
  pair_id: "pair-fixture",
  input_fingerprint: "a".repeat(64),
  required_evidence_ids: [
    "formal_parent:900001",
    "assigned_ecu:idcevo-25",
    "function:navigation",
  ],
  evidence: [
    {
      evidence_id: "formal_parent:900001",
      type: "formal_duplicate_parent",
      value: "900001",
    },
    {
      evidence_id: "assigned_ecu:idcevo-25",
      type: "exact_assigned_ecu",
      value: "IDCEVO-25",
    },
    {
      evidence_id: "function:navigation",
      type: "exact_function_context",
      value: "Navigation",
    },
    {
      evidence_id: "ticket_a_issue_evidence",
      type: "bounded_issue_evidence",
      ticket_side: "ticket_a",
      value: "Map remains blank after route guidance begins.",
    },
    {
      evidence_id: "ticket_b_issue_evidence",
      type: "bounded_issue_evidence",
      ticket_side: "ticket_b",
      value: "Map remains blank after route guidance begins.",
    },
  ],
  left: {},
  right: {},
};

const proxyEnvironment = {
  GLM_PROXY_API_KEY: "fixture-proxy-key",
};

describe("Phase 09 duplicate pair-review runner", () => {
  it("binds a cache key to the pair evidence, policy, and requested model", () => {
    const deepseekKey = buildPhase09PairReviewCacheKey(pairInput, "deepseek-v4-pro");

    expect(deepseekKey).toHaveLength(64);
    expect(deepseekKey).not.toBe(
      buildPhase09PairReviewCacheKey(pairInput, "qwen3.7-max"),
    );
    expect(deepseekKey).not.toBe(
      buildPhase09PairReviewCacheKey(
        { ...pairInput, policy_version: "phase09-duplicate-pair-v2" },
        "deepseek-v4-pro",
      ),
    );
  });

  it("rejects a direct Beacon route even when a GLM proxy credential also exists", () => {
    expect(() =>
      assertPhase09PairReviewGlmProxy("deepseek-v4-pro", {
        ...proxyEnvironment,
        BEACON_API_KEY: "fixture-beacon-key",
      }),
    ).toThrow("GLM Proxy");
  });

  it("reuses only a matching completed model record and requests the other independent review", async () => {
    const cacheKey = buildPhase09PairReviewCacheKey(pairInput, "deepseek-v4-pro");
    const cacheRecords = [
      {
        schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
        cache_key: cacheKey,
        pair_id: pairInput.pair_id,
        input_fingerprint: pairInput.input_fingerprint,
        policy_version: pairInput.policy_version,
        review_policy_version: PAIR_REVIEW_POLICY_VERSION,
        model: "deepseek-v4-pro",
        answer_model: "deepseek-v4-pro",
        source: "llm",
        status: "completed",
        attempts: 1,
        generated_at: "2026-01-01T00:00:00.000Z",
        outcome: {
          verdict: "DISTINCT",
          confidence: 0.98,
          positive_evidence_ids: [],
          contradiction_evidence_ids: ["ticket_a_issue_evidence"],
          rationale: "The supplied symptoms differ.",
        },
      },
    ];
    const calls: string[] = [];

    const result = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      cacheRecords,
      environment: proxyEnvironment,
      requestCompletion: async ({ model }: { model: string }) => {
        calls.push(model);
        return {
          answerModel: model,
          content: JSON.stringify({
            verdict: "INSUFFICIENT_EVIDENCE",
            confidence: 0.4,
            positive_evidence_ids: [],
            contradiction_evidence_ids: [],
            rationale: "The retained evidence does not establish one shared defect.",
          }),
        };
      },
      now: () => "2026-02-01T00:00:00.000Z",
    });

    expect(calls).toEqual(["qwen3.7-max"]);
    expect(result.cached_count).toBe(1);
    expect(result.new_records).toHaveLength(1);
    expect(result.new_records[0]).toMatchObject({
      schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
      pair_id: pairInput.pair_id,
      input_fingerprint: pairInput.input_fingerprint,
      policy_version: pairInput.policy_version,
      model: "qwen3.7-max",
      answer_model: "qwen3.7-max",
      status: "completed",
      attempts: 1,
      generated_at: "2026-02-01T00:00:00.000Z",
      outcome: {
        verdict: "INSUFFICIENT_EVIDENCE",
      },
    });
  });

  it("does not repeat an invalid cached review unless retryInvalid is explicitly enabled", async () => {
    const deepseekCacheKey = buildPhase09PairReviewCacheKey(pairInput, "deepseek-v4-pro");
    const qwenCacheKey = buildPhase09PairReviewCacheKey(pairInput, "qwen3.7-max");
    const cacheRecords = [
      {
        schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
        cache_key: deepseekCacheKey,
        pair_id: pairInput.pair_id,
        input_fingerprint: pairInput.input_fingerprint,
        policy_version: pairInput.policy_version,
        review_policy_version: PAIR_REVIEW_POLICY_VERSION,
        model: "deepseek-v4-pro",
        answer_model: "deepseek-v4-pro",
        source: "llm",
        status: "invalid_response",
        attempts: 1,
        generated_at: "2026-01-01T00:00:00.000Z",
        error_code: "INVALID_RESPONSE",
        error: "LLM returned invalid JSON",
        response_sha256: "b".repeat(64),
        response_characters: 20,
      },
      {
        schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
        cache_key: qwenCacheKey,
        pair_id: pairInput.pair_id,
        input_fingerprint: pairInput.input_fingerprint,
        policy_version: pairInput.policy_version,
        review_policy_version: PAIR_REVIEW_POLICY_VERSION,
        model: "qwen3.7-max",
        answer_model: "qwen3.7-max",
        source: "llm",
        status: "completed",
        attempts: 1,
        generated_at: "2026-01-01T00:00:00.000Z",
        outcome: {
          verdict: "INSUFFICIENT_EVIDENCE",
          confidence: 0.4,
          positive_evidence_ids: [],
          contradiction_evidence_ids: [],
          rationale: "The retained evidence does not establish one shared defect.",
        },
      },
    ];
    const calls: string[] = [];
    const requestCompletion = async ({ model }: { model: string }) => {
      calls.push(model);
      return {
        answerModel: model,
        content: JSON.stringify({
          verdict: "DISTINCT",
          confidence: 0.98,
          positive_evidence_ids: [],
          contradiction_evidence_ids: ["ticket_a_issue_evidence"],
          rationale: "The supplied symptoms differ.",
        }),
      };
    };

    const skipped = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      cacheRecords,
      environment: proxyEnvironment,
      requestCompletion,
    });

    expect(calls).toEqual([]);
    expect(skipped.cached_count).toBe(1);
    expect(skipped.skipped_error_count).toBe(1);
    expect(skipped.new_records).toEqual([]);

    const retried = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      cacheRecords,
      environment: proxyEnvironment,
      requestCompletion,
      retryInvalid: true,
    });

    expect(calls).toEqual(["deepseek-v4-pro"]);
    expect(retried.skipped_error_count).toBe(0);
    expect(retried.new_records[0]).toMatchObject({
      model: "deepseek-v4-pro",
      status: "completed",
      outcome: { verdict: "DISTINCT" },
    });
  });

  it("retries one invalid response only when retryInvalid is enabled", async () => {
    let deepseekCalls = 0;
    let retryPrompt = "";
    const result = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      environment: proxyEnvironment,
      maxAttempts: 2,
      retryInvalid: true,
      requestCompletion: async ({ model, messages }: { model: string; messages: Array<{ content: string }> }) => {
        if (model === "deepseek-v4-pro") {
          deepseekCalls += 1;
          if (deepseekCalls === 2) {
            retryPrompt = messages[0].content;
          }
          if (deepseekCalls === 1) {
            return {
              answerModel: model,
              content: JSON.stringify({
                verdict: "DISTINCT",
                confidence: 0.98,
                positive_evidence_ids: ["unknown_evidence"],
                contradiction_evidence_ids: [],
                rationale: "The first response has an invalid citation.",
              }),
            };
          }
        }
        return {
          answerModel: model,
          content: JSON.stringify({
            verdict: "DISTINCT",
            confidence: 0.98,
            positive_evidence_ids: [],
            contradiction_evidence_ids: ["ticket_a_issue_evidence"],
            rationale: "The supplied symptoms differ.",
          }),
        };
      },
    });

    expect(deepseekCalls).toBe(2);
  expect(retryPrompt).toContain("previous response failed local schema validation");
    expect(result.invalid_response_count).toBe(0);
    expect(result.completed_count).toBe(2);
    expect(result.new_records).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          model: "deepseek-v4-pro",
          status: "completed",
          attempts: 2,
        }),
      ]),
    );
  });

  it("does not reuse a completed result written under another reviewer policy", async () => {
    const calls: string[] = [];
    const result = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      environment: proxyEnvironment,
      cacheRecords: [
        {
          schema_version: PAIR_REVIEW_CACHE_SCHEMA_VERSION,
          cache_key: buildPhase09PairReviewCacheKey(pairInput, "deepseek-v4-pro"),
          pair_id: pairInput.pair_id,
          input_fingerprint: pairInput.input_fingerprint,
          policy_version: pairInput.policy_version,
          review_policy_version: "phase09-duplicate-pair-reviewer-v3",
          model: "deepseek-v4-pro",
          status: "completed",
          outcome: {
            verdict: "DISTINCT",
            confidence: 0.98,
            positive_evidence_ids: [],
            contradiction_evidence_ids: ["ticket_a_issue_evidence"],
            rationale: "The supplied symptoms differ.",
          },
        },
      ],
      requestCompletion: async ({ model }: { model: string }) => {
        calls.push(model);
        return {
          answerModel: model,
          content: JSON.stringify({
            verdict: "DISTINCT",
            confidence: 0.98,
            positive_evidence_ids: [],
            contradiction_evidence_ids: ["ticket_a_issue_evidence"],
            rationale: "The supplied symptoms differ.",
          }),
        };
      },
    });

    expect(calls).toEqual(["deepseek-v4-pro", "qwen3.7-max"]);
    expect(result.cached_count).toBe(0);
    expect(result.completed_count).toBe(2);
  });

  it("stops a qwen-first pair after an insufficient-quota response instead of spending an unmatched second review", async () => {
    const calls: string[] = [];
    const result = await runPhase09PairReviewBatch({
      pairInputs: [pairInput],
      environment: proxyEnvironment,
      models: ["qwen3.7-max", "deepseek-v4-pro"],
      stopOnQuotaExhausted: true,
      requestCompletion: async ({ model }: { model: string }) => {
        calls.push(model);
        if (model === "qwen3.7-max") {
          throw new Error(
            "Chat request failed (429): insufficient_quota Allocated quota exceeded",
          );
        }
        throw new Error("DeepSeek must not be called after the Qwen quota circuit opens");
      },
    });

    expect(calls).toEqual(["qwen3.7-max"]);
    expect(result.quota_exhausted).toBe(true);
    expect(result.request_error_count).toBe(1);
    expect(result.new_records).toHaveLength(1);
    expect(isPhase09PairReviewQuotaExhausted(result.new_records[0])).toBe(true);
  });
});