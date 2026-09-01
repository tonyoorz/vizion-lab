import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import TestCasePatternDiscovery from "@/components/dashboard/chat/TestCasePatternDiscovery";
import type {
  TestCasePatternCoverageAuditResult,
  TestCasePatternDiscoveryResult,
} from "@/components/dashboard/chat/testCaseTypes";

describe("TestCasePatternDiscovery", () => {
  it("shows pattern savings, review requirements, and representative members without rendering testcase drafts", () => {
    render(
      <TestCasePatternDiscovery
        result={{
          sourceSnapshot: {
            runId: "pattern-discovery-abc123",
            sourceFingerprint: "abc123",
            generatedAt: "2026-09-01T00:00:00Z",
            keyword: "RSU",
            project: "RSU",
            matchedTotal: 1674,
            linkedExcluded: 16,
            selectedCount: 1658,
            snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
          },
          similarityThreshold: 0.84,
          summary: {
            source_count: 1658,
            eligible_count: 1500,
            needs_evidence_count: 158,
            pattern_count: 196,
            clustered_member_count: 1500,
            singleton_pattern_count: 42,
            estimated_llm_generations_avoided: 1304,
          },
          patterns: [
            {
              patternId: "pattern-1",
              confidence: 0.91,
              requiresManualReview: false,
              context: { project: "RSU", assignedEcu: "RSE", leadModel: "G68" },
              representative: {
                defectId: "2113731",
                name: "RSU activation timeout",
                project: "RSU",
                assignedEcu: "RSE",
                leadModel: "G68",
                softwareVersion: "25-07",
                detectedInRelease: "R-25-07",
                similarityToRepresentative: 1,
              },
              members: [
                {
                  defectId: "2113731",
                  name: "RSU activation timeout",
                  project: "RSU",
                  assignedEcu: "RSE",
                  leadModel: "G68",
                  softwareVersion: "25-07",
                  detectedInRelease: "R-25-07",
                  similarityToRepresentative: 1,
                },
                {
                  defectId: "2114499",
                  name: "RSU activation timeout during reboot",
                  project: "RSU",
                  assignedEcu: "RSE",
                  leadModel: "G68",
                  softwareVersion: "25-11",
                  detectedInRelease: "R-25-11",
                  similarityToRepresentative: 0.91,
                },
              ],
              parameterMatrix: {
                softwareVersions: ["25-07", "25-11"],
                detectedInReleases: ["R-25-07", "R-25-11"],
              },
              evidencePreview: "Activation times out after the restart.",
            },
            {
              patternId: "pattern-2",
              confidence: 1,
              requiresManualReview: true,
              context: { project: "RSU", assignedEcu: null, leadModel: null },
              representative: {
                defectId: "2115007",
                name: "RSU failure without ECU context",
                project: "RSU",
                assignedEcu: null,
                leadModel: null,
                softwareVersion: null,
                detectedInRelease: null,
                similarityToRepresentative: 1,
              },
              members: [],
              parameterMatrix: { softwareVersions: [], detectedInReleases: [] },
              evidencePreview: "",
            },
          ],
          needsEvidence: [
            { defectId: "2116000", name: "RSU title only", reason: "missing_reproduction_evidence" },
          ],
        }}
      />,
    );

    expect(screen.getByRole("region", { name: "可复用 testcase 模式" })).toBeInTheDocument();
    expect(screen.getByText("预计减少 1,304 次 LLM 生成")).toBeInTheDocument();
    expect(screen.getByText(/154 个复用簇 · 42 个单条候选/)).toBeInTheDocument();
    expect(screen.getByText("需人工审核")).toBeInTheDocument();
    expect(screen.getByText("D2113731")).toBeInTheDocument();
    expect(screen.getByText("2 个缺陷")).toBeInTheDocument();
    expect(screen.getByText("待补充证据 158")).toBeInTheDocument();
  });
});

it("audits only explicitly selected patterns for existing testcase candidates", async () => {
  const onAuditCoverage = vi.fn().mockResolvedValue(undefined);
  const result: TestCasePatternDiscoveryResult = {
    sourceSnapshot: {
      runId: "pattern-discovery-abc123",
      sourceFingerprint: "abc123",
      generatedAt: "2026-09-01T00:00:00Z",
      keyword: "RSU",
      project: "RSU",
      matchedTotal: 1,
      linkedExcluded: 0,
      selectedCount: 1,
      snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
    },
    similarityThreshold: 0.84,
    summary: {
      source_count: 1,
      eligible_count: 1,
      needs_evidence_count: 0,
      pattern_count: 1,
      clustered_member_count: 1,
      singleton_pattern_count: 1,
      estimated_llm_generations_avoided: 0,
    },
    patterns: [{
      patternId: "pattern-1",
      confidence: 1,
      requiresManualReview: false,
      context: { project: "RSU", assignedEcu: "RSE", leadModel: "G68" },
      representative: {
        defectId: "2113731",
        name: "RSU activation timeout",
        project: "RSU",
        assignedEcu: "RSE",
        leadModel: "G68",
        softwareVersion: "25-07",
        detectedInRelease: "R-25-07",
        similarityToRepresentative: 1,
      },
      members: [{
        defectId: "2113731",
        name: "RSU activation timeout",
        project: "RSU",
        assignedEcu: "RSE",
        leadModel: "G68",
        softwareVersion: "25-07",
        detectedInRelease: "R-25-07",
        similarityToRepresentative: 1,
      }],
      parameterMatrix: { softwareVersions: ["25-07"], detectedInReleases: ["R-25-07"] },
      evidencePreview: "Activation times out after restart.",
    }],
    needsEvidence: [],
  };

  render(<TestCasePatternDiscovery result={result} onAuditCoverage={onAuditCoverage} />);

  fireEvent.click(screen.getByRole("checkbox", { name: "选择模式 pattern-1" }));
  fireEvent.click(screen.getByRole("button", { name: "检查已有 testcase 候选 (1)" }));

  expect(onAuditCoverage).toHaveBeenCalledWith(["pattern-1"]);
});

it("generates a reusable draft only after the selected pattern has audit evidence", async () => {
  const onGenerateDrafts = vi.fn().mockResolvedValue(undefined);
  const result: TestCasePatternDiscoveryResult = {
    sourceSnapshot: {
      runId: "pattern-discovery-abc123",
      sourceFingerprint: "abc123",
      generatedAt: "2026-09-01T00:00:00Z",
      keyword: "RSU",
      project: "RSU",
      matchedTotal: 1,
      linkedExcluded: 0,
      selectedCount: 1,
      snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
    },
    similarityThreshold: 0.84,
    summary: {
      source_count: 1,
      eligible_count: 1,
      needs_evidence_count: 0,
      pattern_count: 1,
      clustered_member_count: 1,
      singleton_pattern_count: 1,
      estimated_llm_generations_avoided: 0,
    },
    patterns: [{
      patternId: "pattern-1",
      confidence: 1,
      requiresManualReview: false,
      context: { project: "RSU", assignedEcu: "RSE", leadModel: "G68" },
      representative: {
        defectId: "2113731",
        name: "RSU activation timeout",
        project: "RSU",
        assignedEcu: "RSE",
        leadModel: "G68",
        softwareVersion: "25-07",
        detectedInRelease: "R-25-07",
        similarityToRepresentative: 1,
      },
      members: [{
        defectId: "2113731",
        name: "RSU activation timeout",
        project: "RSU",
        assignedEcu: "RSE",
        leadModel: "G68",
        softwareVersion: "25-07",
        detectedInRelease: "R-25-07",
        similarityToRepresentative: 1,
      }],
      parameterMatrix: { softwareVersions: ["25-07"], detectedInReleases: ["R-25-07"] },
      evidencePreview: "Activation times out after restart.",
    }],
    needsEvidence: [],
  };
  const auditResult: TestCasePatternCoverageAuditResult = {
    sourceSnapshot: result.sourceSnapshot,
    audits: [{
      patternId: "pattern-1",
      directLinks: [],
      semanticCandidates: [],
      recommendedDisposition: "review_generate_reusable_testcase",
    }],
    summary: {
      audited_pattern_count: 1,
      direct_linked_member_count: 0,
      semantic_candidate_pattern_count: 0,
      review_existing_testcase_count: 0,
      review_generate_reusable_testcase_count: 1,
      requires_manual_review_count: 0,
    },
    auditPath: "exports/testcases/pattern-discovery/audit.json",
  };

  render(
    <TestCasePatternDiscovery
      result={result}
      auditResult={auditResult}
      onAuditCoverage={vi.fn().mockResolvedValue(undefined)}
      onGenerateDrafts={onGenerateDrafts}
    />,
  );

  fireEvent.click(screen.getByRole("checkbox", { name: "选择模式 pattern-1" }));
  fireEvent.click(screen.getByRole("button", { name: "生成参数化草稿 (1)" }));

  expect(onGenerateDrafts).toHaveBeenCalledWith(["pattern-1"]);
});

it("supports selecting reusable clusters or every pattern without individual clicks", async () => {
  const onAuditCoverage = vi.fn().mockResolvedValue(undefined);
  const makePattern = (
    patternId: string,
    memberCount: number,
    requiresManualReview = false,
  ) => ({
    patternId,
    confidence: 0.9,
    requiresManualReview,
    context: { project: "RSU", assignedEcu: "RSE", leadModel: "G68" },
    representative: {
      defectId: `${patternId}-representative`,
      name: "RSU activation timeout",
      project: "RSU",
      assignedEcu: "RSE",
      leadModel: "G68",
      softwareVersion: "25-07",
      detectedInRelease: "R-25-07",
      similarityToRepresentative: 1,
    },
    members: Array.from({ length: memberCount }, (_, index) => ({
      defectId: `${patternId}-${index + 1}`,
      name: "RSU activation timeout",
      project: "RSU",
      assignedEcu: "RSE",
      leadModel: "G68",
      softwareVersion: "25-07",
      detectedInRelease: "R-25-07",
      similarityToRepresentative: 1,
    })),
    parameterMatrix: { softwareVersions: ["25-07"], detectedInReleases: ["R-25-07"] },
    evidencePreview: "Activation times out after restart.",
  });
  const result: TestCasePatternDiscoveryResult = {
    sourceSnapshot: {
      runId: "pattern-discovery-abc123",
      sourceFingerprint: "abc123",
      generatedAt: "2026-09-01T00:00:00Z",
      keyword: "RSU",
      project: "RSU",
      matchedTotal: 4,
      linkedExcluded: 0,
      selectedCount: 4,
      snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
    },
    similarityThreshold: 0.84,
    summary: {
      source_count: 4,
      eligible_count: 4,
      needs_evidence_count: 0,
      pattern_count: 3,
      clustered_member_count: 4,
      singleton_pattern_count: 1,
      estimated_llm_generations_avoided: 1,
    },
    patterns: [
      makePattern("pattern-reusable", 2),
      makePattern("pattern-singleton", 1),
      makePattern("pattern-manual", 2, true),
    ],
    needsEvidence: [],
  };

  render(<TestCasePatternDiscovery result={result} onAuditCoverage={onAuditCoverage} />);

  fireEvent.click(screen.getByRole("button", { name: "全选复用簇 (1)" }));
  expect(screen.getByRole("button", { name: "检查已有 testcase 候选 (1)" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "检查已有 testcase 候选 (1)" }));
  expect(onAuditCoverage).toHaveBeenCalledWith(["pattern-reusable"]);

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "全选全部 (3)" })).toBeEnabled();
  });

  fireEvent.click(screen.getByRole("button", { name: "全选全部 (3)" }));
  expect(screen.getByRole("button", { name: "检查已有 testcase 候选 (3)" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "清空选择" }));
  expect(screen.getByRole("button", { name: "检查已有 testcase 候选 (0)" })).toBeDisabled();
});