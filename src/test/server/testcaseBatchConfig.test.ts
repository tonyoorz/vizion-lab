import { describe, expect, it } from "vitest";
import { resolveTestcaseBatchConfig } from "../../../server/testcaseBatchConfig.mjs";

describe("resolveTestcaseBatchConfig", () => {
  it("uses high-throughput defaults for bulk testcase generation", () => {
    expect(resolveTestcaseBatchConfig({})).toEqual({ batchSize: 60, concurrency: 12 });
  });

  it("bounds environment overrides to protect the local service and model gateway", () => {
    expect(resolveTestcaseBatchConfig({ TESTCASE_GENERATION_BATCH_SIZE: "1000", TESTCASE_GENERATION_CONCURRENCY: "99" })).toEqual({
      batchSize: 100,
      concurrency: 16,
    });
  });
});