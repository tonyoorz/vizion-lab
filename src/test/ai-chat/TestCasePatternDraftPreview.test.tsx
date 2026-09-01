import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TestCasePatternDraftPreview from "@/components/dashboard/chat/TestCasePatternDraftPreview";

describe("TestCasePatternDraftPreview", () => {
  it("shows parameterized draft review details without offering an Octane commit action", () => {
    render(
      <TestCasePatternDraftPreview
        response={{
          sourceSnapshot: {
            runId: "pattern-discovery-abc123",
            sourceFingerprint: "abc123",
            generatedAt: "2026-09-01T00:00:00Z",
            keyword: "RSU",
            project: "RSU",
            matchedTotal: 2,
            linkedExcluded: 0,
            selectedCount: 2,
            snapshotPath: "exports/testcases/pattern-discovery/pattern-discovery-abc123.json",
          },
          results: [{
            patternId: "pattern-1",
            representativeDefectId: "2113731",
            memberDefectIds: ["2113731", "2114499"],
            memberCount: 2,
            commonAnchorTerms: ["activation", "reboot"],
            parameterMatrix: {
              softwareVersions: ["25-07", "25-11"],
              detectedInReleases: ["R-25-07", "R-25-11"],
            },
            name: "[RSU] Regression - activation after reboot",
            descriptionHtml: "<p>Pattern ID: pattern-1</p>",
            stepsText: "- [PreCon] RSU ready\n- Start activation\n- ? Completes within 30 seconds",
            verification: {
              passed: true,
              criteria: [{ name: "pattern_traceability", passed: true, evidence: "pattern recorded" }],
              feedback: "",
            },
            similarCases: [],
            generatedAt: "2026-09-01T00:00:00Z",
            error: null,
          }],
          summary: { total: 1, succeeded: 1, failed: 0 },
          artifacts: {
            patternDraftsCsvPath: "exports/testcases/pattern-discovery/pattern-drafts.csv",
            defectMappingCsvPath: "exports/testcases/pattern-discovery/defect-mapping.csv",
            testcaseLibraryXlsxPath: "exports/testcases/pattern-discovery/RSU_testcase_library.xlsx",
          },
        }}
      />,
    );

    expect(screen.getByRole("region", { name: "参数化 testcase 草稿" })).toBeInTheDocument();
    expect(screen.getByText("D2113731")).toBeInTheDocument();
    expect(screen.getByText("2 个缺陷")).toBeInTheDocument();
    expect(screen.getByText("[RSU] Regression - activation after reboot")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /D2113731.*activation after reboot/ }));
    expect(screen.getByText("pattern_traceability")).toBeInTheDocument();
    expect(screen.getByText(/草稿 CSV/)).toBeInTheDocument();
    expect(screen.getByText(/defect-mapping\.csv/)).toBeInTheDocument();
    expect(screen.getByText(/资产库 XLSX/)).toBeInTheDocument();
    expect(screen.getByText(/RSU_testcase_library\.xlsx/)).toBeInTheDocument();
    expect(screen.queryByText(/创建|提交 Octane/)).not.toBeInTheDocument();
  });
});