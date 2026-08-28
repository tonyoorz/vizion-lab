import { describe, expect, it } from "vitest";

import { MAIN_AGENT_TOOLS } from "../../../server/mainAgentTools.mjs";
import {
  createMainAgentToolRegistry,
  selectMainAgentToolset,
  shouldPlanMainAgentTools,
} from "../../../server/mainAgentToolRegistry.mjs";

const ALL_TOOL_NAMES = MAIN_AGENT_TOOLS.map((tool) => tool.function?.name).filter(Boolean);

describe("main agent tool registry", () => {
  it("returns the full governed tool set for write/update/delete queries", () => {
    const registry = createMainAgentToolRegistry(MAIN_AGENT_TOOLS);
    const selected = registry.selectToolset([{ role: "user", content: "Octane defect 字段能不能更新？能不能删除缺陷单？" }]);

    // Model-driven: all governed tools are available; model picks the right ones.
    expect(selected.intent).toBe("general");
    expect(selected.toolNames).toEqual(ALL_TOOL_NAMES);
    expect(selected.tools.map((tool) => tool.function.name)).toEqual(selected.toolNames);
    // query_analytics is now allowed (model may choose it).
    expect(registry.validateToolCall({ function: { name: "query_analytics" } }, selected)).toEqual(
      expect.objectContaining({ allowed: true }),
    );
  });

  it("triggers planning for analytics-shaped queries", () => {
    const messages = [{ role: "user", content: "DTSV 当前风险怎么看？" }];

    expect(shouldPlanMainAgentTools(messages)).toBe(true);
    // Intent is now general — model decides which tools to use.
    expect(selectMainAgentToolset(messages).intent).toBe("general");
  });

  it("returns full tool set for Chinese defect ID display wording", () => {
    const messages = [{ role: "user", content: "最近7天一些严重的defect，带着id展示" }];

    expect(shouldPlanMainAgentTools(messages)).toBe(true);
    const selected = selectMainAgentToolset(messages);
    expect(selected.intent).toBe("general");
    expect(selected.toolNames).toEqual(expect.arrayContaining(["query_semantic_records", "query_analytics"]));
  });

  it("returns full tool set for DTSV internal testing-group comparisons", () => {
    const selected = selectMainAgentToolset([
      { role: "user", content: "对比 DTSV_China 内部测试小组的通过率和缺陷发现率" },
    ]);

    expect(selected.intent).toBe("general");
    expect(selected.toolNames).toContain("query_testing_team_fv_analysis");
  });
});
