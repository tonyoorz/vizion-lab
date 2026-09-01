import { describe, expect, it } from "vitest";
import { buildFilterDefectBridgePayload } from "../../../server/testcaseFilterRequest.mjs";

describe("buildFilterDefectBridgePayload", () => {
  it("forwards ids_only requests so select-all receives all matching IDs", () => {
    expect(
      buildFilterDefectBridgePayload({
        keyword: "RSU",
        project: "",
        limit: 50,
        offset: 0,
        idsOnly: true,
      }),
    ).toEqual({
      action: "search_defects_by_filter",
      keyword: "RSU",
      project: "",
      limit: 50,
      offset: 0,
      ids_only: true,
    });
  });
});