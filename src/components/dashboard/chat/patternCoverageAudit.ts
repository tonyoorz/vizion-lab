import type {
  TestCasePatternCoverageAudit,
  TestCasePatternCoverageAuditResult,
} from "./testCaseTypes";

export const PATTERN_COVERAGE_AUDIT_BATCH_SIZE = 20;

export function splitPatternIdsForCoverageAudit(
  patternIds: string[],
  batchSize = PATTERN_COVERAGE_AUDIT_BATCH_SIZE,
) {
  const normalizedBatchSize = Number.isInteger(batchSize) && batchSize > 0
    ? batchSize
    : PATTERN_COVERAGE_AUDIT_BATCH_SIZE;
  const uniquePatternIds = [...new Set(patternIds.map((patternId) => String(patternId || "").trim()).filter(Boolean))];
  const batches: string[][] = [];
  for (let index = 0; index < uniquePatternIds.length; index += normalizedBatchSize) {
    batches.push(uniquePatternIds.slice(index, index + normalizedBatchSize));
  }
  return batches;
}

export function mergePatternCoverageAuditResults(
  batchResults: TestCasePatternCoverageAuditResult[],
): TestCasePatternCoverageAuditResult {
  if (batchResults.length === 0) {
    throw new Error("at least one coverage audit result is required");
  }

  const sourceSnapshot = batchResults[0].sourceSnapshot;
  const auditsByPatternId = new Map<string, TestCasePatternCoverageAudit>();
  const auditPaths = new Set<string>();
  for (const batchResult of batchResults) {
    if (batchResult.sourceSnapshot.runId !== sourceSnapshot.runId) {
      throw new Error("coverage audit results must share a discovery snapshot");
    }
    if (batchResult.auditPath) auditPaths.add(batchResult.auditPath);
    for (const auditPath of batchResult.auditPaths || []) auditPaths.add(auditPath);
    for (const audit of batchResult.audits) {
      auditsByPatternId.set(audit.patternId, audit);
    }
  }

  const audits = [...auditsByPatternId.values()];
  const directLinkedDefectIds = new Set(
    audits.flatMap((audit) => audit.directLinks.map((directLink) => directLink.defectId)),
  );
  const reviewExistingTestcaseCount = audits.filter(
    (audit) => audit.recommendedDisposition === "review_existing_testcase",
  ).length;
  const reviewGenerateReusableTestcaseCount = audits.filter(
    (audit) => audit.recommendedDisposition === "review_generate_reusable_testcase",
  ).length;
  const requiresManualReviewCount = audits.filter(
    (audit) => audit.recommendedDisposition === "requires_manual_review",
  ).length;

  return {
    sourceSnapshot,
    audits,
    summary: {
      audited_pattern_count: audits.length,
      direct_linked_member_count: directLinkedDefectIds.size,
      semantic_candidate_pattern_count: audits.filter((audit) => audit.semanticCandidates.length > 0).length,
      review_existing_testcase_count: reviewExistingTestcaseCount,
      review_generate_reusable_testcase_count: reviewGenerateReusableTestcaseCount,
      requires_manual_review_count: requiresManualReviewCount,
    },
    auditPath: [...auditPaths][0] || "",
    auditPaths: [...auditPaths],
  };
}