export interface TestCaseVerificationCriterion {
  name: string;
  passed: boolean;
  evidence: string;
}

export interface TestCaseVerification {
  passed: boolean;
  criteria: TestCaseVerificationCriterion[];
  feedback: string;
}

export interface TestCaseSimilarCase {
  testId: string;
  name: string;
  score: number;
}

export interface TestCaseResult {
  defectId: string;
  defectName: string;
  defectSeverity: string;
  defectSoftwareVersion?: string;
  defectAssignedEcu?: string;
  defectLeadModel?: string;
  name: string;
  descriptionHtml: string;
  stepsText: string;
  verification: TestCaseVerification;
  similarCases: TestCaseSimilarCase[];
  generatedAt: string;
}

/** A defect candidate returned by semantic search (before test-case generation). */
export interface TestCaseDefectCandidate {
  defectId: string;
  name: string;
  project: string | null;
  pu: string | null;
  statusPhase: string | null;
  similarity: number;
  confidenceLabel: string;
  snippet: string;
}

/**
 * A defect returned by topic-filter search (POST /api/create-testcase/filter-defects).
 *
 * Unlike TestCaseDefectCandidate (similarity top-K), these are ALL defects
 * matching a project/name keyword that do NOT already have a linked test case.
 * Paginated — the response carries `total` so the UI can page through large
 * result sets (e.g. ~1,661 RSU defects without test cases).
 */
export interface TestCaseFilteredDefect {
  defectId: string;
  name: string;
  project: string | null;
  phase: string | null;
  statusPhase: string | null;
  /** Always false — the backend excludes defects that already have a linked test case. */
  hasLinkedTest: boolean;
}

/** Response from POST /api/create-testcase/filter-defects. */
export interface TestCaseFilterResponse {
  success: boolean;
  keyword: string;
  project: string | null;
  total: number;
  offset: number;
  limit: number;
  candidates: TestCaseFilteredDefect[];
  summary: {
    matched_total: number;
    linked_excluded: number;
    without_linked_testcase: number;
  };
  error?: string;
}

export interface TestCasePatternMember {
  defectId: string;
  name: string;
  project: string | null;
  assignedEcu: string | null;
  leadModel: string | null;
  softwareVersion: string | null;
  detectedInRelease: string | null;
  similarityToRepresentative: number;
}

export interface TestCasePattern {
  patternId: string;
  confidence: number;
  requiresManualReview: boolean;
  context: {
    project: string | null;
    assignedEcu: string | null;
    leadModel: string | null;
  };
  representative: TestCasePatternMember;
  members: TestCasePatternMember[];
  parameterMatrix: {
    softwareVersions: string[];
    detectedInReleases: string[];
  };
  commonAnchorTerms?: string[];
  evidencePreview: string;
}

export interface TestCasePatternDiscoveryResult {
  sourceSnapshot: {
    runId: string;
    sourceFingerprint: string;
    generatedAt: string;
    policyVersion?: string;
    keyword: string;
    project: string | null;
    matchedTotal: number;
    linkedExcluded: number;
    selectedCount: number;
    snapshotPath: string;
  };
  similarityThreshold: number;
  summary: {
    source_count: number;
    eligible_count: number;
    needs_evidence_count: number;
    pattern_count: number;
    clustered_member_count: number;
    singleton_pattern_count: number;
    estimated_llm_generations_avoided: number;
  };
  patterns: TestCasePattern[];
  needsEvidence: Array<{
    defectId: string;
    name: string;
    reason: string;
  }>;
}

export interface TestCasePatternCoverageCandidate {
  testId: string;
  name: string;
  source: "direct" | "semantic";
  score?: number;
  team?: string;
  release?: string;
  subtype?: string;
}

export interface TestCasePatternCoverageAudit {
  patternId: string;
  directLinks: Array<{
    defectId: string;
    testcases: TestCasePatternCoverageCandidate[];
  }>;
  semanticCandidates: TestCasePatternCoverageCandidate[];
  recommendedDisposition:
    | "requires_manual_review"
    | "review_existing_testcase"
    | "review_generate_reusable_testcase";
}

export interface TestCasePatternCoverageAuditResult {
  sourceSnapshot: TestCasePatternDiscoveryResult["sourceSnapshot"];
  audits: TestCasePatternCoverageAudit[];
  summary: {
    audited_pattern_count: number;
    direct_linked_member_count: number;
    semantic_candidate_pattern_count: number;
    review_existing_testcase_count: number;
    review_generate_reusable_testcase_count: number;
    requires_manual_review_count: number;
  };
  auditPath: string;
  auditPaths?: string[];
}

export interface TestCasePatternDraft {
  patternId: string;
  representativeDefectId: string;
  memberDefectIds: string[];
  memberCount: number;
  commonAnchorTerms: string[];
  parameterMatrix: {
    softwareVersions?: string[];
    detectedInReleases?: string[];
  };
  name: string;
  descriptionHtml: string;
  stepsText: string;
  verification: TestCaseVerification;
  similarCases: TestCaseSimilarCase[];
  generatedAt: string;
  error: string | null;
}

export interface TestCasePatternDraftResponse {
  sourceSnapshot: TestCasePatternDiscoveryResult["sourceSnapshot"];
  results: TestCasePatternDraft[];
  summary: { total: number; succeeded: number; failed: number };
  artifacts?: {
    patternDraftsCsvPath: string;
    defectMappingCsvPath: string;
    testcaseLibraryXlsxPath: string;
  };
  artifactError?: string;
}

/** Response from POST /api/create-testcase/export-csv. */
export interface TestCaseCsvExportResponse {
  success: boolean;
  csv_path: string;
  row_count: number;
  errors: number;
  error?: string;
}

/**
 * A single item in the batch test-case flow.
 *
 * Extends TestCaseResult with batch-specific UI state: selection toggle,
 * per-item commit lifecycle, and error tracking (generation or commit).
 */
export interface TestCaseBatchItem extends TestCaseResult {
  selected: boolean;
  error: string | null;
  commitState: "idle" | "submitting" | "success" | "error";
  commitError: string;
  octaneUrl?: string;
}

/** Response shape from POST /api/create-testcase/prepare-batch. */
export interface TestCaseBatchPrepareResponse {
  success: boolean;
  results: TestCaseBatchItem[];
  summary: { total: number; succeeded: number; failed: number };
  error?: string;
}

/** A single result row from POST /api/create-testcase/commit-batch. */
export interface TestCaseBatchCommitResult {
  defectId: string;
  success: boolean;
  test_id?: string;
  octane_url?: string;
  error: string | null;
}

/** Response shape from POST /api/create-testcase/commit-batch. */
export interface TestCaseBatchCommitResponse {
  success: boolean;
  results: TestCaseBatchCommitResult[];
  summary: { total: number; succeeded: number; failed: number };
  error?: string;
}

/**
 * Real-time progress for long-running batch operations (export CSV, generate
 * batch). Shown as a progress bar inside DefectCandidateList so the user can
 * see what's happening during a 30-minute, 1,600-defect generation.
 */
export interface BatchProgress {
  phase: "generating" | "clustering" | "exporting" | "done" | "error" | "cancelled";
  current: number;      // drafts generated so far
  total: number;        // total defects to process
  batchIndex: number;   // current batch (1-indexed)
  totalBatches: number; // total number of batches
  error?: string;
}
