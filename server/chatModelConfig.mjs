const LEGACY_DEFAULT_MODELS = [
  "deepseek-v4-flash",
  "qwen3.5-397b-a17b",
  "glm-5",
];

const GLM_PROXY_DEFAULT_MODELS = [
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "qwen3.7-max",
  "qwen3.7-flash",
  "glm-5.1",
];

const DEFAULT_GLM_PROXY_BASE_URL = "http://127.0.0.1:8001/v1";

// Beacon personal channel (e.g. doubao-seed-2.1-turbo). Uses a dedicated
// access code distinct from project-golive — the gateway rejects golive keys
// on the personal endpoint and vice versa. Mirrors the GLM-proxy auth_mode
// "beacon_personal" so vizion-lab can call doubao directly without the proxy.
const DEFAULT_BEACON_PERSONAL_ENDPOINT = "https://beaconapi.bmwbrill.cn/personal/v1/chat/completions";
const DOUBAO_PERSONAL_MODEL = "doubao-seed-2.1-turbo";

// Beacon project-golive channel (glm-5.1, deepseek-v4-*, qwen3.7-*). Routes
// directly to the golive endpoint with a Bearer golive key, bypassing the GLM
// proxy. The upstream model name often differs from the dropdown id, so map
// them explicitly (mirrors GLM-proxy's upstream_model field).
const DEFAULT_BEACON_GOLIVE_ENDPOINT = "https://beaconapi.bmwbrill.cn/project-golive/v1/chat/completions";
const BEACON_GOLIVE_MODELS = {
  "glm-5.1": "GLM-5.1",
  "deepseek-v4-pro": "DeepSeek-V4-Pro",
  "deepseek-v4-flash": "DeepSeek-V4-Flash",
  "qwen3.7-max": "qwen3.7-max",
  "qwen3.7-flash": "qwen3.7-flash",
};

const DEFAULT_ENDPOINTS = {
  "deepseek-v4-flash": "https://aistudio.bmwbrill.cn/api/service/ssl/170/lm-platform/llm/v2/chat/completions",
  "qwen3.5-397b-a17b": "https://aistudio.bmwbrill.cn/api/service/164/ernie/v2/chat/completions",
  "glm-5": "https://aistudio.bmwbrill.cn/api/service/163/ernie/v2/chat/completions",
};

function readFirst(env, keys) {
  for (const key of keys) {
    const value = String(env?.[key] ?? "").trim();
    if (value) {
      return value;
    }
  }

  return "";
}

function parseEndpointMap(env) {
  const raw = readFirst(env, ["DUPSEARCH_CHAT_MODEL_ENDPOINTS", "CHAT_MODEL_ENDPOINTS"]);
  if (!raw) {
    return { ...DEFAULT_ENDPOINTS };
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ...DEFAULT_ENDPOINTS };
    }

    const mapped = {};
    for (const [key, value] of Object.entries(parsed)) {
      const normalizedKey = String(key || "").trim().toLowerCase();
      const normalizedValue = String(value || "").trim();
      if (!normalizedKey || !normalizedValue) {
        continue;
      }
      mapped[normalizedKey] = normalizedValue;
    }

    return Object.keys(mapped).length ? mapped : { ...DEFAULT_ENDPOINTS };
  } catch {
    return { ...DEFAULT_ENDPOINTS };
  }
}

function resolveGlmProxyBaseUrl(env) {
  const configuredBaseUrl = readFirst(env, [
    "DUPSEARCH_GLM_PROXY_API_BASE",
    "GLM_PROXY_API_BASE",
    "GLM_PROXY_BASE_URL",
    "GLM_PROXY_URL",
  ]);
  if (configuredBaseUrl) {
    return configuredBaseUrl;
  }

  return readFirst(env, ["GLM_PROXY_API_KEY"]) ? DEFAULT_GLM_PROXY_BASE_URL : "";
}

function usesGlmProxy(env) {
  return Boolean(resolveGlmProxyBaseUrl(env));
}

function readBeaconPersonalApiKey(env) {
  return readFirst(env, [
    "DUPSEARCH_BEACON_PERSONAL_API_KEY",
    "BEACON_PERSONAL_API_KEY",
  ]);
}

function resolveBeaconPersonalEndpoint(env) {
  const configured = readFirst(env, [
    "DUPSEARCH_BEACON_PERSONAL_ENDPOINT",
    "BEACON_PERSONAL_ENDPOINT",
  ]);
  return configured || DEFAULT_BEACON_PERSONAL_ENDPOINT;
}

// Doubao and other personal-channel models route directly to the beacon
// personal endpoint with a Bearer personal key, bypassing the GLM proxy.
function resolveBeaconPersonalModel(model) {
  const normalized = String(model || "").trim().toLowerCase();
  if (normalized === DOUBAO_PERSONAL_MODEL) {
    return DOUBAO_PERSONAL_MODEL;
  }
  return "";
}

function readBeaconGoliveApiKey(env) {
  return readFirst(env, [
    "DUPSEARCH_BEACON_API_KEY",
    "BEACON_API_KEY",
  ]);
}

function resolveBeaconGoliveEndpoint(env) {
  const configured = readFirst(env, [
    "DUPSEARCH_BEACON_GOLIVE_ENDPOINT",
    "BEACON_GOLIVE_ENDPOINT",
  ]);
  return configured || DEFAULT_BEACON_GOLIVE_ENDPOINT;
}

// GoLive models route directly to the project-golive beacon channel with a
// Bearer golive key. Returns the upstream model name (which may differ from
// the dropdown id) or "" if the model is not a golive model.
function resolveBeaconGoliveUpstreamModel(model) {
  const normalized = String(model || "").trim().toLowerCase();
  for (const [dropdownId, upstreamModel] of Object.entries(BEACON_GOLIVE_MODELS)) {
    if (dropdownId.toLowerCase() === normalized) {
      return upstreamModel;
    }
  }
  return "";
}

// Multimodal models accept image_url/file_data content parts natively, so the
// OCR-based image-to-text expansion must be skipped for them — otherwise the
// image is replaced by OCR text and the model never sees the actual image.
const MULTIMODAL_MODEL_IDS = new Set([DOUBAO_PERSONAL_MODEL]);

export function isMultimodalChatModel(model) {
  const normalized = String(model || "").trim().toLowerCase();
  return MULTIMODAL_MODEL_IDS.has(normalized);
}

export function getChatModelOptions(env = process.env) {
  const envRaw = readFirst(env, [
    "DUPSEARCH_CHAT_MODEL_OPTIONS",
    "CHAT_MODEL_OPTIONS",
    "VITE_DUPSEARCH_CHAT_MODEL_OPTIONS",
  ]);
  const envModels = envRaw
    ? envRaw.split(",").map((item) => item.trim()).filter(Boolean)
    : [];
  const defaultModels = usesGlmProxy(env) ? GLM_PROXY_DEFAULT_MODELS : LEGACY_DEFAULT_MODELS;
  const merged = [...envModels, ...defaultModels];
  const seen = new Set();
  const ordered = [];

  for (const modelName of merged) {
    const normalized = String(modelName || "").trim();
    if (!normalized) {
      continue;
    }

    const key = normalized.toLowerCase();
    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    ordered.push(normalized);
  }

  return ordered.length ? ordered : [defaultModels[0]];
}

export function getDefaultChatModel(env = process.env) {
  return getChatModelOptions(env)[0];
}

export function resolveChatModelConfig(selectedModel, env = process.env) {
  const model = String(selectedModel || "").trim() || getDefaultChatModel(env);
  const glmProxyBaseUrl = resolveGlmProxyBaseUrl(env);

  // Direct beacon personal route (e.g. doubao-seed-2.1-turbo). Does not need
  // the GLM proxy: the personal endpoint accepts a Bearer personal key and a
  // plain OpenAI body. Falls through if the personal key is not configured.
  const beaconPersonalModel = resolveBeaconPersonalModel(model);
  const beaconPersonalApiKey = beaconPersonalModel ? readBeaconPersonalApiKey(env) : "";
  if (beaconPersonalModel && beaconPersonalApiKey) {
    return {
      model,
      endpoint: resolveBeaconPersonalEndpoint(env),
      apiKey: beaconPersonalApiKey,
      accessCode: "",
      authScheme: "Bearer",
      credential: beaconPersonalApiKey,
      baseUrl: "",
      usesInternalEndpoint: true,
      upstreamModel: "",
    };
  }

  // Direct beacon project-golive route (glm-5.1, deepseek-v4-*, qwen3.7-*).
  // Bypasses the GLM proxy when the golive key is configured. The upstream
  // model name may differ from the dropdown id and is rewritten in the body.
  const beaconGoliveUpstreamModel = resolveBeaconGoliveUpstreamModel(model);
  const beaconGoliveApiKey = beaconGoliveUpstreamModel ? readBeaconGoliveApiKey(env) : "";
  if (beaconGoliveUpstreamModel && beaconGoliveApiKey) {
    return {
      model,
      endpoint: resolveBeaconGoliveEndpoint(env),
      apiKey: beaconGoliveApiKey,
      accessCode: "",
      authScheme: "Bearer",
      credential: beaconGoliveApiKey,
      baseUrl: "",
      usesInternalEndpoint: true,
      upstreamModel: beaconGoliveUpstreamModel,
    };
  }

  const endpointMap = glmProxyBaseUrl ? {} : parseEndpointMap(env);
  const endpointTemplate = String(endpointMap[model.toLowerCase()] || "").trim();
  const accessCode = readFirst(env, [
    "DUPSEARCH_CHAT_ACCESS_CODE",
    "ACCESS_CODE",
    "DEEPSEEK_ACCESS_CODE",
  ]);
  const endpoint = endpointTemplate
    ? endpointTemplate.replace("{access_code}", accessCode)
    : "";
  const apiKey = readFirst(env, [
    "GLM_PROXY_API_KEY",
    "DUPSEARCH_CHAT_API_KEY",
    "API_KEY",
    "DEEPSEEK_API_KEY",
  ]);
  const authScheme = endpoint ? "ACCESSCODE" : "Bearer";
  const credential = endpoint ? accessCode : apiKey;
  const baseUrl = endpoint
    ? ""
    : glmProxyBaseUrl ||
      readFirst(env, ["DUPSEARCH_CHAT_API_BASE", "DEEPSEEK_API_BASE"]) ||
      "https://api.deepseek.com/v1";

  return {
    model,
    endpoint,
    apiKey,
    accessCode,
    authScheme,
    credential,
    baseUrl,
    usesInternalEndpoint: Boolean(endpoint),
    upstreamModel: "",
  };
}

function buildToolRequestFields(tools, toolChoice) {
  if (!Array.isArray(tools) || tools.length === 0) {
    return {};
  }

  return {
    tools,
    ...(toolChoice == null ? {} : { tool_choice: toolChoice }),
  };
}

function buildStreamRequestFields(stream) {
  return stream ? { stream_options: { include_usage: true } } : {};
}

export function buildChatCompletionRequest({
  selectedModel,
  messages,
  env = process.env,
  stream = false,
  tools,
  toolChoice,
  maxTokens,
}) {
  const config = resolveChatModelConfig(selectedModel || "", env);
  const toolRequestFields = buildToolRequestFields(tools, toolChoice);
  const streamRequestFields = buildStreamRequestFields(stream);
  // Only override the default token limit when the caller explicitly requests one.
  const tokenLengthOverride = maxTokens != null ? { max_token_length: maxTokens } : {};
  const maxTokensOverride = maxTokens != null ? { max_tokens: maxTokens } : {};

  if (config.usesInternalEndpoint) {
    return {
      url: config.endpoint,
      headers: {
        accept: "application/json",
        authorization: `${config.authScheme} ${config.credential}`.trim(),
        "Content-Type": "application/json",
      },
      body: {
        model: config.upstreamModel || config.model,
        messages,
        temperature: 0.2,
        max_token_length: 2048,
        stream,
        ...streamRequestFields,
        ...toolRequestFields,
        ...tokenLengthOverride,
      },
      config,
    };
  }

  const trimmedBaseUrl = config.baseUrl.replace(/\/+$/, "");
  const url = trimmedBaseUrl.endsWith("/chat/completions")
    ? trimmedBaseUrl
    : `${trimmedBaseUrl}/chat/completions`;

  return {
    url,
    headers: {
      "Content-Type": "application/json",
      Authorization: `${config.authScheme} ${config.credential}`.trim(),
    },
    body: {
      model: config.model,
      temperature: 0.2,
      max_tokens: 900,
      messages,
      stream,
      ...streamRequestFields,
      ...toolRequestFields,
      ...maxTokensOverride,
    },
    config,
  };
}
