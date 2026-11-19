// @vitest-environment node
import { describe, expect, it } from "vitest";

import { routeMainAgentIntent, selectMainAgentToolset } from "../../../server/mainAgentIntentRouter.mjs";
import { MAIN_AGENT_TOOLS } from "../../../server/mainAgentTools.mjs";

describe("main agent intent router", () => {
  it("routes analytics queries to general intent with the full governed tool set", () => {
    const routed = routeMainAgentIntent([
      { role: "user", content: "最近7天一些严重的defect，带着id展示" },
    ]);
    expect(routed.intent).toBe("general");
    expect(routed.policyHints).toEqual(["use_tool_policy"]);
  });

  it("routes Jira keyword queries to general intent (model picks Jira tools)", () => {
    const routed = routeMainAgentIntent([
      { role: "user", content: "帮我查一下 Jira 上 DTSV-123 的状态" },
    ]);
    expect(routed.intent).toBe("general");
    // The model now decides to use Jira MCP tools — no hardcoded jira_query intent.
    expect(routed.policyHints).toEqual(["use_tool_policy"]);
  });

  it("does not route ontology IDs like T-ONTO-1 to jira_query", () => {
    const routed = routeMainAgentIntent([
      { role: "user", content: "基于 T-ONTO-1 帮我创建一个回归测试用例" },
    ]);
    expect(routed.intent).not.toBe("jira_query");
    expect(routed.intent).toBe("general");
  });

  it("returns the full governed tool set for non-direct queries", () => {
    const toolset = selectMainAgentToolset(
      [{ role: "user", content: "按ECU看缺陷分布" }],
      MAIN_AGENT_TOOLS,
    );
    expect(toolset.tools).toHaveLength(MAIN_AGENT_TOOLS.length);
    expect(toolset.toolNames).toEqual(
      MAIN_AGENT_TOOLS.map((t) => t.function?.name).filter(Boolean),
    );
  });

  it("leaves greeting interpretation and tool choice to the conversation model", () => {
    const routed = routeMainAgentIntent([
      { role: "user", content: "你好" },
    ]);
    expect(routed.intent).toBe("general");
    const toolset = selectMainAgentToolset(
      [{ role: "user", content: "你好" }],
      MAIN_AGENT_TOOLS,
    );
    expect(toolset.tools).toHaveLength(MAIN_AGENT_TOOLS.length);
  });
});
