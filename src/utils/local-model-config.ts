import { getPref, setPref } from "./prefs";

export type LocalModelConfigField = "base_url" | "api_key" | "model";

export type LocalModelConfig = Partial<Record<LocalModelConfigField, string>>;

export type LocalModelConfigMap = Record<string, LocalModelConfig>;

function normalizeString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeModelConfig(value: unknown): LocalModelConfig {
  if (!value || typeof value !== "object") {
    return {};
  }

  const raw = value as Record<string, unknown>;
  const config: LocalModelConfig = {};
  const baseUrl = normalizeString(raw.base_url ?? raw.baseUrl);
  const apiKey = normalizeString(raw.api_key ?? raw.apiKey);
  const model = normalizeString(raw.model);

  if (baseUrl) {
    config.base_url = baseUrl;
  }
  if (apiKey) {
    config.api_key = apiKey;
  }
  if (model) {
    config.model = model;
  }

  return config;
}

export function readLocalModelConfigs(): LocalModelConfigMap {
  const raw = getPref("localModelConfigs") || "{}";
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return {};
    }

    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .map(([modelKey, config]) => [modelKey, normalizeModelConfig(config)])
        .filter(([, config]) => Object.keys(config).length > 0),
    );
  } catch (error) {
    ztoolkit.log("Failed to parse local model configs:", error);
    return {};
  }
}

export function getLocalModelConfig(modelKey: string): LocalModelConfig | null {
  const config = readLocalModelConfigs()[modelKey];
  return config && Object.keys(config).length > 0 ? config : null;
}

export function writeLocalModelConfigField(
  modelKey: string,
  field: LocalModelConfigField,
  value: string,
) {
  const configs = readLocalModelConfigs();
  const config = { ...(configs[modelKey] || {}) };
  const normalized = normalizeString(value);

  if (normalized) {
    config[field] = normalized;
  } else {
    delete config[field];
  }

  if (Object.keys(config).length > 0) {
    configs[modelKey] = config;
  } else {
    delete configs[modelKey];
  }

  setPref("localModelConfigs", JSON.stringify(configs));
}
