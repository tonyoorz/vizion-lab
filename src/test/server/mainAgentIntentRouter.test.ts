// @vitest-environment node
import { describe, expect, it } from "vitest";

import { routeMainAgentIntent } from "../../../server/mainAgentIntentRouter.mjs";

describe("main agent intent router", () => {
  it("routes Chinese defect ID display requests to governed record retrieval", () => {
    expect(routeMainAgentIntent([
      { role: "user", content: "最近7天一些严重的defect，带着id展示" },
    ])).toMatchObject({
      intent: "record_query",
      policyHints: ["semantic_records_first"],
    });
  });

  it("routes Jira keyword queries to jira_query intent", () => {
    expect(routeMainAgentIntent([
      { role: "user", content: "帮我查一下 Jira 上 DTSV-123 的状态" },
    ])).toMatchObject({
      intent: "jira_query",
      policyHints: ["use_jira_mcp_tools"],
    });
  });

  it("does not route ontology IDs like T-ONTO-1 to jira_query", () => {
    expect(routeMainAgentIntent([
      { role: "user", content: "基于 T-ONTO-1 帮我创建一个回归测试用例" },
    ]).intent).not.toBe("jira_query");
  });
});