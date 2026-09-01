import { describe, expect, it } from "vitest";

import {
  mergePatternCoverageAuditResults,
  splitPatternIdsForCoverageAudit,
} from "@/components/dashboard/chat/patternCoverageAudit";

const sourceSnapshot = {
  runId: "pattern-discovery-abc123",
  sourceFingerprint: "abc123",
  generatedAt: "2026-09-01T00:00:00Z",
  keyword: "RSU",
  project: "RSU",
  matchedTotal: 2,
  linkedExcluded: 0,
  selectedCount: 2,
  snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
};

describe("splitPatternIdsForCoverageAudit", () => {
  it("splits large selections into bounded unique audit requests", () => {
    const patternIds = Array.from({ length: 41 }, (_, index) => `pattern-${index + 1}`);

    expect(splitPatternIdsForCoverageAudit([...patternIds, "pattern-1"])).toEqual([
      patternIds.slice(0, 20),
      patternIds.slice(20, 40),
      patternIds.slice(40),
    ]);
  });
});

describe("mergePatternCoverageAuditResults", () => {
  it("preserves every batch result and recalculates the summary from audited patterns", () => {
    const result = mergePatternCoverageAuditResults([
      {
        sourceSnapshot,
        audits: [{
          patternId: "pattern-1",
          directLinks: [{ defectId: "1", testcases: [] }],
          semanticCandidates: [],
          recommendedDisposition: "review_existing_testcase",
        }],
        summary: {
          audited_pattern_count: 1,
          direct_linked_member_count: 1,
          semantic_candidate_pattern_count: 0,
          review_existing_testcase_count: 1,
          review_generate_reusable_testcase_count: 0,
          requires_manual_review_count: 0,
        },
        auditPath: "first.json",
      },
      {
        sourceSnapshot,
        audits: [{
          patternId: "pattern-2",
          directLinks: [],
          semanticCandidates: [{ testId: "900", name: "RSU reference", source: "semantic", score: 0.9 }],
          recommendedDisposition: "review_generate_reusable_testcase",
        }],
        summary: {
          audited_pattern_count: 1,
          direct_linked_member_count: 0,
          semantic_candidate_pattern_count: 1,
          review_existing_testcase_count: 0,
          review_generate_reusable_testcase_count: 1,
          requires_manual_review_count: 0,
        },
        auditPath: "second.json",
      },
    ]);

    expect(result.audits.map((audit) => audit.patternId)).toEqual(["pattern-1", "pattern-2"]);
    expect(result.auditPaths).toEqual(["first.json", "second.json"]);
    expect(result.summary).toEqual({
      audited_pattern_count: 2,
      direct_linked_member_count: 1,
      semantic_candidate_pattern_count: 1,
      review_existing_testcase_count: 1,
      review_generate_reusable_testcase_count: 1,
      requires_manual_review_count: 0,
    });
  });
});