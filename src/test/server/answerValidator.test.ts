import { describe, expect, it } from "vitest";

import * as answerValidator from "../../../server/answerValidator.mjs";
import { buildCitationContractContext, validateAnswerContract, validateAnswerTextCitations } from "../../../server/answerValidator.mjs";
import { createOntologyRegistry } from "../../../server/ontology/registry.mjs";

describe("answer validator", () => {
  const registry = createOntologyRegistry();
  const evidence = [{ toolCallId: "call-1", tool: "query_semantic_metrics", ontologyVersion: "v1", schemaFingerprint: registry.fingerprint }];
  const semanticEvidence = [{
    toolCallId: "call-1",
    tool: "query_semantic_metrics",
    ontologyVersion: "v1",
    schemaFingerprint: registry.fingerprint,
    evidence: { kind: "semantic_metric_result", metricValues: { "defect.count": 1 } },
    data: [{ defect_id: "D-1", "product.ecu": "HU", "defect.count": 1 }],
  }];

  it("requires citations to bind to real tool evidence when claim-evidence binding is approved", () => {
    expect(validateAnswerContract({ answer: { answer_text: "缺陷数是 12", citations: [] }, evidence, registry })).toEqual(
      expect.objectContaining({ valid: false, violations: ["ANSWER_CITATION_REQUIRED"] }),
    );

    expect(validateAnswerContract({ answer: { answer_text: "缺陷数是 12", citations: [{ toolCallId: "missing" }] }, evidence, registry })).toEqual(
      expect.objectContaining({ valid: false, violations: ["ANSWER_CITATION_UNKNOWN_TOOL_CALL:missing"] }),
    );

    expect(validateAnswerContract({ answer: { answer_text: "缺陷数是 12", citations: [{ toolCallId: "call-1" }] }, evidence, registry })).toEqual(
      expect.objectContaining({ valid: true, violations: [] }),
    );
  });

  it("offers optional citation markup without making it an answer requirement", () => {
    const context = buildCitationContractContext({ evidence, registry });

    expect(context).toContain('<cite source="call-1">claim</cite>');
    expect(context).toContain("advisory");
    expect(context).not.toContain("Format every numeric or factual data claim");
  });

  it("blocks causal claims that observational analytics evidence cannot support", () => {
    expect(validateAnswerContract({
      answer: {
        answer_text: 'ECU A 导致缺陷数上升 <cite source="call-1">evidence</cite>',
        citations: [{ toolCallId: "call-1" }],
      },
      evidence,
      registry,
    })).toEqual(expect.objectContaining({
      valid: false,
      violations: ["ANSWER_CAUSAL_CLAIM_UNSUPPORTED"],
    }));

    expect(validateAnswerContract({
      answer: {
        answer_text: 'ECU A 与缺陷数上升相关，但现有数据不能证明因果 <cite source="call-1">evidence</cite>',
        citations: [{ toolCallId: "call-1" }],
      },
      evidence,
      registry,
    })).toEqual(expect.objectContaining({ valid: true, violations: [] }));
    });

  it("accepts cited numeric and record claims supported by semantic evidence", () => {
    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">缺陷数是 1，记录为 D-1</cite>',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    })).toEqual({ valid: true, violations: [] });
  });

  it("does not treat Markdown ordered-list markers as factual claims", () => {
    expect(validateAnswerTextCitations({
      text: '1. <cite source="call-1">Confirmed count: 1; record D-1.</cite>\n2. Review the authorized records.',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    })).toEqual({ valid: true, violations: [] });
    expect(validateAnswerTextCitations({
      text: '1. <cite source="call-1">Confirmed count: 1.</cite>\n2. Total count: 999.',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_NUMERIC_CLAIM_UNCITED");
  });

  it("does not exempt a factual number in a Markdown heading", () => {
    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">### 999. Total defects</cite>',
      evidence: semanticEvidence, registry, evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_NUMERIC_CLAIM_UNSUPPORTED:call-1");
    expect(validateAnswerTextCitations({
      text: '### 999. Total defects\n<cite source="call-1">Confirmed count: 1.</cite>',
      evidence: semanticEvidence, registry, evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_NUMERIC_CLAIM_UNCITED");
  });

  it("does not infer that text is non-factual merely because it has no digits", () => {
    const request = {
      text: "All defects are resolved.", evidence: semanticEvidence, registry,
      evidenceGate: { status: "pass" }, allowUncitedDiscussion: true,
    };
    expect(validateAnswerTextCitations(request).violations).toContain("ANSWER_CITATION_REQUIRED");
  });

  it("accepts dates from the actual query scope but not invented dates", () => {
    const scopedEvidence = [{ ...semanticEvidence[0], scope: {
      timeScopes: [{ start: "2026-11-10", end: "2026-11-16", fieldId: "time.defect_creation_date" }],
    } }];
    expect(validateAnswerTextCitations({
      text: 'Window: 2026-11-10 to 2026-11-16. <cite source="call-1">Confirmed count: 1.</cite>',
      evidence: scopedEvidence, registry, evidenceGate: { status: "pass" },
    })).toEqual({ valid: true, violations: [] });
    expect(validateAnswerTextCitations({
      text: 'Window: 2025-11-10. <cite source="call-1">Confirmed count: 1.</cite>',
      evidence: scopedEvidence, registry, evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_NUMERIC_CLAIM_UNCITED");
  });

  it("rejects a cited numeric claim that does not match its evidence", () => {
    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">缺陷数是 999999</cite>',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    })).toEqual(expect.objectContaining({
      valid: false,
      violations: expect.arrayContaining(["ANSWER_NUMERIC_CLAIM_UNSUPPORTED:call-1"]),
    }));
  });

  it("rejects uncited numbers, unknown record identifiers, and causal wording", () => {
    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">范围已确认</cite>，缺陷数是 1',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_NUMERIC_CLAIM_UNCITED");

    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">记录为 D-9</cite>',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_RECORD_CLAIM_UNSUPPORTED:call-1");

    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">由于 ECU 配置导致缺陷数是 1</cite>',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "pass" },
    }).violations).toContain("ANSWER_CAUSAL_CLAIM_UNSUPPORTED");
  });

  it("rejects semantic claims when the upstream evidence gate is blocked", () => {
    expect(validateAnswerTextCitations({
      text: '<cite source="call-1">缺陷数是 1</cite>',
      evidence: semanticEvidence,
      registry,
      evidenceGate: { status: "blocked", violations: ["SEMANTIC_SOURCE_REVISION_MISSING"] },
    })).toEqual(expect.objectContaining({
      valid: false,
      violations: expect.arrayContaining(["ANSWER_SEMANTIC_EVIDENCE_BLOCKED"]),
    }));
  });

  it("can render only verified semantic facts when a model draft cannot be published", () => {
    expect(answerValidator).toHaveProperty("buildVerifiedEvidenceSummary", expect.any(Function));
    const render = Reflect.get(answerValidator, "buildVerifiedEvidenceSummary");
    const text = render({ evidence: semanticEvidence, registry, evidenceGate: { status: "pass" } });
    expect(text).toContain('<cite source="call-1">');
    expect(validateAnswerTextCitations({ text, evidence: semanticEvidence, registry, evidenceGate: { status: "pass" } })).toEqual({ valid: true, violations: [] });
    expect(render({ evidence: semanticEvidence, registry, evidenceGate: { status: "blocked" } })).toBeNull();
  });
});