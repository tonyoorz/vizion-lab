import { describe, expect, it } from "vitest";

import { buildChatCompletionRequest, getChatModelOptions } from "../../../server/chatModelConfig.mjs";

const sampleTools = [
  {
    type: "function",
    function: {
      name: "get_current_status",
      description: "Return the current dashboard status.",
      parameters: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
    },
  },
];

const sampleToolChoice = {
  type: "function",
  function: { name: "get_current_status" },
};

describe("buildChatCompletionRequest tool calling options", () => {
  it("routes supported GoLive chat models through the local GLM proxy", () => {
    const env = {
      GLM_PROXY_API_KEY: "glm-proxy-test-key",
    };

    expect(getChatModelOptions(env)).toEqual([
      "deepseek-v4-pro",
      "deepseek-v4-flash",
      "qwen3.7-max",
      "qwen3.7-flash",
      "glm-5.1",
    ]);

    const request = buildChatCompletionRequest({
      selectedModel: "qwen3.7-flash",
      messages: [{ role: "user", content: "Reply with OK only." }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(false);
    expect(request.url).toBe("http://127.0.0.1:8001/v1/chat/completions");
    expect(request.headers.Authorization).toBe("Bearer glm-proxy-test-key");
    expect(request.body.model).toBe("qwen3.7-flash");
  });

  it("requests streamed usage metadata for OpenAI-compatible chat streams", () => {
    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-v4-flash",
      messages: [{ role: "user", content: "stream with usage" }],
      env: {
        DUPSEARCH_CHAT_ACCESS_CODE: "test-access-code",
      },
      stream: true,
    });

    expect(request.body.stream_options).toEqual({ include_usage: true });
  });

  it("passes OpenAI-compatible tools through to internal company endpoints", () => {
    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-v4-flash",
      messages: [{ role: "user", content: "Use the tool." }],
      env: {
        DUPSEARCH_CHAT_ACCESS_CODE: "test-access-code",
      },
      tools: sampleTools,
      toolChoice: sampleToolChoice,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.body.tools).toEqual(sampleTools);
    expect(request.body.tool_choice).toEqual(sampleToolChoice);
  });

  it("passes OpenAI-compatible tools through to external chat completion endpoints", () => {
    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-chat",
      messages: [{ role: "user", content: "Use the tool." }],
      env: {
        DUPSEARCH_CHAT_MODEL_ENDPOINTS: "{}",
        DUPSEARCH_CHAT_API_KEY: "test-api-key",
        DUPSEARCH_CHAT_API_BASE: "https://example.test/v1",
      },
      tools: sampleTools,
      toolChoice: "auto",
    });

    expect(request.config.usesInternalEndpoint).toBe(false);
    expect(request.body.tools).toEqual(sampleTools);
    expect(request.body.tool_choice).toBe("auto");
  });

  it("omits tool fields when no tools are provided", () => {
    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-v4-flash",
      messages: [{ role: "user", content: "plain chat" }],
      env: {
        DUPSEARCH_CHAT_ACCESS_CODE: "test-access-code",
      },
    });

    expect(request.body).not.toHaveProperty("tools");
    expect(request.body).not.toHaveProperty("tool_choice");
  });

  it("routes doubao-seed-2.1-turbo directly to beacon personal endpoint", () => {
    const env = {
      BEACON_PERSONAL_API_KEY: "personal-test-key",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "doubao-seed-2.1-turbo",
      messages: [{ role: "user", content: "Reply with OK only." }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.url).toBe("https://beaconapi.bmwbrill.cn/personal/v1/chat/completions");
    expect(request.headers.authorization).toBe("Bearer personal-test-key");
    expect(request.body.model).toBe("doubao-seed-2.1-turbo");
  });

  it("passes multimodal image_url parts through to doubao personal endpoint", () => {
    const env = {
      BEACON_PERSONAL_API_KEY: "personal-test-key",
    };

    const multimodalMessages = [
      {
        role: "user",
        content: [
          { type: "text", text: "What is in this image?" },
          { type: "image_url", image_url: { url: "data:image/png;base64,iVBORw0KGgo=" } },
        ],
      },
    ];

    const request = buildChatCompletionRequest({
      selectedModel: "doubao-seed-2.1-turbo",
      messages: multimodalMessages,
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.headers.authorization).toBe("Bearer personal-test-key");
    expect(request.body.messages).toEqual(multimodalMessages);
  });

  it("falls through when doubao personal key is not configured", () => {
    const env = {
      // No BEACON_PERSONAL_API_KEY, no GLM proxy, no endpoint map → generic fallback
      DUPSEARCH_CHAT_API_KEY: "fallback-key",
      DUPSEARCH_CHAT_API_BASE: "https://fallback.example/v1",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "doubao-seed-2.1-turbo",
      messages: [{ role: "user", content: "hi" }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(false);
    expect(request.url).toBe("https://fallback.example/v1/chat/completions");
  });

  it("routes deepseek-v4-pro directly to beacon golive endpoint with rewritten model name", () => {
    const env = {
      BEACON_API_KEY: "golive-test-key",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-v4-pro",
      messages: [{ role: "user", content: "Reply with OK only." }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.url).toBe("https://beaconapi.bmwbrill.cn/project-golive/v1/chat/completions");
    expect(request.headers.authorization).toBe("Bearer golive-test-key");
    expect(request.body.model).toBe("DeepSeek-V4-Pro");
  });

  it("routes glm-5.1 directly to beacon golive endpoint with rewritten model name", () => {
    const env = {
      BEACON_API_KEY: "golive-test-key",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "glm-5.1",
      messages: [{ role: "user", content: "Reply with OK only." }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.url).toBe("https://beaconapi.bmwbrill.cn/project-golive/v1/chat/completions");
    expect(request.headers.authorization).toBe("Bearer golive-test-key");
    expect(request.body.model).toBe("GLM-5.1");
  });

  it("routes qwen3.7-max to beacon golive without rewriting the model name", () => {
    const env = {
      BEACON_API_KEY: "golive-test-key",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "qwen3.7-max",
      messages: [{ role: "user", content: "Reply with OK only." }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.url).toBe("https://beaconapi.bmwbrill.cn/project-golive/v1/chat/completions");
    expect(request.headers.authorization).toBe("Bearer golive-test-key");
    expect(request.body.model).toBe("qwen3.7-max");
  });

  it("falls back to GLM proxy when BEACON_API_KEY is not set for golive models", () => {
    const env = {
      GLM_PROXY_API_KEY: "glm-proxy-test-key",
      // No BEACON_API_KEY → golive branch falls through to GLM proxy
    };

    const request = buildChatCompletionRequest({
      selectedModel: "glm-5.1",
      messages: [{ role: "user", content: "hi" }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(false);
    expect(request.url).toBe("http://127.0.0.1:8001/v1/chat/completions");
    expect(request.headers.Authorization).toBe("Bearer glm-proxy-test-key");
    expect(request.body.model).toBe("glm-5.1");
  });

  it("keeps the ACCESSCODE model body unchanged when upstreamModel is empty", () => {
    const env = {
      DUPSEARCH_CHAT_ACCESS_CODE: "test-access-code",
    };

    const request = buildChatCompletionRequest({
      selectedModel: "deepseek-v4-flash",
      messages: [{ role: "user", content: "hi" }],
      env,
    });

    expect(request.config.usesInternalEndpoint).toBe(true);
    expect(request.config.upstreamModel).toBe("");
    expect(request.body.model).toBe("deepseek-v4-flash");
  });
});