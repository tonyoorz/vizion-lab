import { describe, expect, it } from "vitest";
import {
  buildReusablePatternDraftExportPayload,
  buildReusablePatternPromptContext,
  buildReusablePatternVerificationPayload,
} from "../../../server/testcasePatternGeneration.mjs";

describe("buildReusablePatternPromptContext", () => {
  it("requires one parameterized testcase without claiming unreviewed member coverage", () => {
    const prompt = buildReusablePatternPromptContext({
      patternId: "pattern-1",
      representative: { defectId: "2113731", name: "RSU activation timeout" },
      memberDefectIds: ["2113731", "2114499"],
      memberCount: 2,
      commonAnchorTerms: ["activation", "reboot"],
      parameterMatrix: {
        softwareVersions: ["25-07", "25-11"],
        detectedInReleases: ["R-25-07", "R-25-11"],
      },
      evidencePreview: "Activation times out after a vehicle reboot.",
    });

    expect(prompt.systemInstructions).toContain("exactly one parameterized reusable manual testcase");
    expect(prompt.systemInstructions).toContain("Do not claim that every member defect is covered");
    expect(prompt.userContext).toContain("Pattern ID: pattern-1");
    expect(prompt.userContext).toContain("D2113731, D2114499");
    expect(prompt.userContext).toContain("software versions: 25-07, 25-11");
    expect(prompt.userContext).toContain("Common failure-mode anchors: activation, reboot");
  });
});

describe("buildReusablePatternVerificationPayload", () => {
  it("retains the selected pattern mapping when requesting quality verification", () => {
    expect(
      buildReusablePatternVerificationPayload({
        defectInfo: { defect_id: "2113731" },
        patternContext: {
          patternId: "pattern-1",
          memberDefectIds: ["2113731", "2114499"],
          parameterMatrix: { softwareVersions: ["25-07", "25-11"] },
        },
        generated: { descriptionHtml: "description", stepsText: "steps" },
      }),
    ).toEqual({
      action: "verify_reusable_testcase_pattern",
      defect_info: { defect_id: "2113731" },
      pattern_id: "pattern-1",
      member_defect_ids: ["2113731", "2114499"],
      parameter_matrix: { softwareVersions: ["25-07", "25-11"] },
      description_html: "description",
      steps_text: "steps",
    });
  });
});

describe("buildReusablePatternDraftExportPayload", () => {
  it("persists drafts against the frozen discovery run without trusting a client member map", () => {
    const drafts = [{ patternId: "pattern-1", name: "Reusable RSU testcase" }];

    expect(
      buildReusablePatternDraftExportPayload({
        runId: "pattern-discovery-abc123def4567890",
        drafts,
      }),
    ).toEqual({
      action: "export_reusable_pattern_drafts",
      run_id: "pattern-discovery-abc123def4567890",
      drafts,
    });
  });
});