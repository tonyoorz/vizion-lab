import { describe, expect, it } from "vitest";
import { buildDesignRequest, completeDesign } from "../../../server/cnSpeechDesignModel.mjs";

const env = {
  GLM_PROXY_API_BASE: "http://127.0.0.1:8001/v1",
  GLM_PROXY_API_KEY: "test-placeholder",
};

describe("CN Speech design transport", () => {
  it.each(["deepseek-v4-flash", "deepseek-v4-pro"])("explicitly disables thinking for %s", (model) => {
    const request = buildDesignRequest({
      model,
      messages: [{ role: "user", content: "Return design JSON" }],
    }, env);

    expect(request.body.model).toBe(model);
    expect(request.body.thinking).toEqual({ type: "disabled" });
    expect(request.body.response_format).toEqual({ type: "json_object" });
    expect(request.body.max_tokens).toBe(8192);
  });

  it("defaults new design requests to DeepSeek V4 Flash", () => {
    const request = buildDesignRequest({ messages: [{ role: "user", content: "Return design JSON" }] }, env);

    expect(request.body.model).toBe("deepseek-v4-flash");
    expect(request.body.thinking).toEqual({ type: "disabled" });
  });

  it("uses GLM-5.3 Flash explicitly with thinking disabled", () => {
    const request = buildDesignRequest({
      model: "glm-5.3-flash",
      messages: [{ role: "user", content: "Return design JSON" }],
    }, env);

    expect(request.body.model).toBe("glm-5.3-flash");
    expect(request.body.thinking).toEqual({ type: "disabled" });
    expect(request.body.response_format).toEqual({ type: "json_object" });
    expect(request.body.max_tokens).toBe(8192);
  });

  it("supports repeated calls through one reusable transport", async () => {
    const jobs: string[] = [];
    const fetchImpl = async (_url: string, options: { body: string }) => {
      jobs.push(JSON.parse(options.body).model);
      return new Response(JSON.stringify({
        model: "glm-5.3-flash",
        choices: [{ message: { content: '{"status":"ok"}' }, finish_reason: "stop" }],
      }), { status: 200 });
    };

    const first = await completeDesign({ model: "glm-5.3-flash", operation: "probe" }, { env, fetchImpl });
    const second = await completeDesign({ model: "glm-5.3-flash", operation: "probe" }, { env, fetchImpl });

    expect(jobs).toEqual(["glm-5.3-flash", "glm-5.3-flash"]);
    expect(first.design).toEqual({ status: "ok" });
    expect(second.model).toBe("glm-5.3-flash");
  });
});