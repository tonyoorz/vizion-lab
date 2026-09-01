export function buildPatternDiscoveryBridgePayload({ keyword, project, defectIds, similarityThreshold }) {
  return {
    action: "discover_defect_patterns",
    keyword,
    project,
    defect_ids: defectIds,
    similarity_threshold: similarityThreshold,
  };
}

export function buildPatternCoverageAuditBridgePayload({ runId, patternIds }) {
  return {
    action: "audit_defect_pattern_coverage",
    run_id: runId,
    pattern_ids: patternIds,
  };
}

export function buildReusablePatternPreparationBridgePayload({ runId, patternIds }) {
  return {
    action: "prepare_reusable_testcase_patterns",
    run_id: runId,
    pattern_ids: patternIds,
  };
}