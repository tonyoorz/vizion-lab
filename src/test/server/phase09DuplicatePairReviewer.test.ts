import { describe, expect, it } from "vitest";

import {
  buildPhase09PairReviewMessages,
  parsePhase09PairReviewResponse,
  reviewPhase09DuplicatePair,
  resolveDualPhase09PairReview,
} from "../../../server/phase09DuplicatePairReviewer.mjs";

const pairInput = {
  pair_id: "pair-fixture",
  input_fingerprint: "fixture-fingerprint",
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
      ticket_id: "D-1",
      value: "Observed map remains blank after route guidance starts.",
    },
    {
      evidence_id: "ticket_b_issue_evidence",
      type: "bounded_issue_evidence",
      ticket_side: "ticket_b",
      ticket_id: "D-2",
      value: "Observed map remains blank after route guidance starts.",
    },
  ],
  left: {
    title: "Navigation map remains blank",
    issue_evidence: "Observed map remains blank after route guidance starts.",
    process_evidence: "Child duplicate linked to the same parent.",
  },
  right: {
    title: "Map remains blank after navigation starts",
    issue_evidence: "Observed map remains blank after route guidance starts.",
    process_evidence: "Child duplicate linked to the same parent.",
  },
};

const sameResponse = JSON.stringify({
  verdict: "SAME_UNDERLYING_DEFECT",
  confidence: 0.98,
  positive_evidence_ids: [
    "formal_parent:900001",
    "assigned_ecu:idcevo-25",
    "function:navigation",
    "ticket_a_issue_evidence",
  ],
  contradiction_evidence_ids: [],
  rationale: "同一父缺陷、ECU和导航现象一致。",
});

describe("Phase 09 duplicate pair reviewer", () => {
  it("builds a pair-isolated JSON request that treats ticket text as untrusted evidence", () => {
    const messages = buildPhase09PairReviewMessages(pairInput);

    expect(messages).toHaveLength(2);
    expect(messages[0].content).toContain("untrusted data");
    expect(messages[0].content).toContain("SAME_UNDERLYING_DEFECT");
    expect(messages[0].content).toContain("Never merge tickets");
    expect(messages[0].content).toContain("production/customer escape");
    expect(messages[0].content).toContain("Copy each citation ID exactly");
    expect(messages[0].content).toContain("program independently verifies required_evidence_ids");
    expect(messages[0].content).toContain("at least one supporting ticket evidence ID");
    expect(messages[0].content).toContain("Never invent a comment, history, or ticket-specific citation ID");
    expect(messages[0].content).toContain("under 320 characters");
    expect(messages[0].content).toContain("personal data");
    expect(messages[1].content).toContain("pair-fixture");
    expect(messages[1].content).toContain("formal_parent:900001");
    const userPayload = JSON.parse(messages[1].content);
    expect(userPayload).not.toHaveProperty("ticket_a");
    expect(userPayload).not.toHaveProperty("ticket_b");
    expect(userPayload.allowed_evidence_ids).toEqual(
      pairInput.evidence.map((item) => item.evidence_id),
    );
    expect(userPayload.evidence).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ evidence_id: "ticket_a_issue_evidence" }),
        expect.objectContaining({ evidence_id: "ticket_b_issue_evidence" }),
      ]),
    );
    expect(JSON.stringify(userPayload.evidence)).not.toContain('"ticket_id"');
  });

  it("records program-verified hard evidence separately from model semantic citations", () => {
    const review = parsePhase09PairReviewResponse(sameResponse, pairInput);

    expect(review).toMatchObject({
      verdict: "SAME_UNDERLYING_DEFECT",
      confidence: 0.98,
      contradiction_evidence_ids: [],
      verified_required_evidence_ids: pairInput.required_evidence_ids,
    });
    expect(review.positive_evidence_ids).toEqual([
      ...pairInput.required_evidence_ids,
      "ticket_a_issue_evidence",
    ]);

    const omittedHardCitations = parsePhase09PairReviewResponse(
      JSON.stringify({
        ...JSON.parse(sameResponse),
        positive_evidence_ids: ["ticket_a_issue_evidence"],
      }),
      pairInput,
    );
    expect(omittedHardCitations).toMatchObject({
      positive_evidence_ids: ["ticket_a_issue_evidence"],
      verified_required_evidence_ids: pairInput.required_evidence_ids,
    });

    expect(() =>
      parsePhase09PairReviewResponse(
        JSON.stringify({
          ...JSON.parse(sameResponse),
          positive_evidence_ids: pairInput.required_evidence_ids,
        }),
        pairInput,
      ),
    ).toThrow("semantic supporting evidence");
  });

  it("allows automatic merging only when both independent reviews confirm the same evidence-backed verdict", () => {
    const primary = parsePhase09PairReviewResponse(sameResponse, pairInput);
    const secondary = parsePhase09PairReviewResponse(sameResponse, pairInput);

    expect(resolveDualPhase09PairReview({ primary, secondary })).toEqual({
      decision: "AUTO_MERGE_DUPLICATE",
      reason: "dual_same_underlying_defect",
    });

    expect(
      resolveDualPhase09PairReview({
        primary,
        secondary: {
          ...secondary,
          verdict: "INSUFFICIENT_EVIDENCE",
          confidence: 0.8,
        },
      }),
    ).toEqual({
      decision: "QUARANTINE",
      reason: "reviewer_disagreement_or_insufficient_evidence",
    });
  });

  it("uses the dedicated structured-completion route for pair reviews", async () => {
    const calls: Array<Record<string, unknown>> = [];

    await reviewPhase09DuplicatePair({
      pairInput,
      model: "fixture-model",
      requestCompletion: async (request: Record<string, unknown>) => {
        calls.push(request);
        return { answerModel: "fixture-model", content: sameResponse };
      },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      model: "fixture-model",
      skipDashboardSystemPrompt: true,
    });
  });
});