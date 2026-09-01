import { describe, expect, it } from "vitest";
import {
  buildPatternCoverageAuditBridgePayload,
  buildPatternDiscoveryBridgePayload,
  buildReusablePatternPreparationBridgePayload,
} from "../../../server/testcasePatternDiscoveryRequest.mjs";

describe("buildPatternDiscoveryBridgePayload", () => {
  it("preserves the selected defects and similarity policy for read-only pattern discovery", () => {
    expect(
      buildPatternDiscoveryBridgePayload({
        keyword: "RSU",
        project: "",
        defectIds: ["D2113731", "2114499"],
        similarityThreshold: 0.84,
      }),
    ).toEqual({
      action: "discover_defect_patterns",
      keyword: "RSU",
      project: "",
      defect_ids: ["D2113731", "2114499"],
      similarity_threshold: 0.84,
    });
  });
});

describe("buildPatternCoverageAuditBridgePayload", () => {
  it("preserves only the persisted discovery run and explicitly selected patterns", () => {
    expect(
      buildPatternCoverageAuditBridgePayload({
        runId: "pattern-discovery-abc123def4567890",
        patternIds: ["pattern-1", "pattern-2"],
      }),
    ).toEqual({
      action: "audit_defect_pattern_coverage",
      run_id: "pattern-discovery-abc123def4567890",
      pattern_ids: ["pattern-1", "pattern-2"],
    });
  });
});

describe("buildReusablePatternPreparationBridgePayload", () => {
  it("prepares only user-selected patterns from the persisted discovery snapshot", () => {
    expect(
      buildReusablePatternPreparationBridgePayload({
        runId: "pattern-discovery-abc123def4567890",
        patternIds: ["pattern-1"],
      }),
    ).toEqual({
      action: "prepare_reusable_testcase_patterns",
      run_id: "pattern-discovery-abc123def4567890",
      pattern_ids: ["pattern-1"],
    });
  });
});