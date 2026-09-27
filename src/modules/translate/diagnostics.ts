import type { TranslationFailureDiagnostic } from "../../types";
import { getString } from "../../utils/locale";

type FailurePhase = "poll" | "upload" | "download" | "translation";

const LOCALIZED_CODES = new Set([
  "model_credit_exhausted",
  "model_region_unavailable",
  "model_auth_failed",
  "model_permission_denied",
  "model_not_found",
  "model_quota_exhausted",
  "model_configuration_error",
  "transport_rate_limited",
  "transport_unavailable",
  "transport_connection_failed",
  "transport_connect_timeout",
  "transport_timeout_unknown_billing",
  "transport_unknown",
  "budget_request_limit",
  "budget_cost_limit",
  "resource_download_failed",
  "backend_dependency_missing",
  "translation_incomplete",
  "pdf_output_invalid",
  "ocr_safety_error",
  "audit_corrupt",
  "uploaded_pdf_missing",
  "backend_unreachable",
  "backend_job_lost",
  "backend_auth_failed",
  "upload_failed",
  "download_failed",
  "translation_failed",
]);

function inferFailureCode(message: string, phase: FailurePhase): string {
  const normalized = message.toLowerCase();
  for (const code of LOCALIZED_CODES) {
    if (normalized.includes(code)) {
      return code;
    }
  }
  if (
    normalized.includes("failed to fetch") ||
    normalized.includes("networkerror") ||
    normalized.includes("network error") ||
    normalized.includes("connection refused")
  ) {
    return "backend_unreachable";
  }
  if (normalized.includes("job not found")) {
    return "backend_job_lost";
  }
  if (
    normalized.includes("unauthorized") ||
    normalized.includes("http error 401")
  ) {
    return "backend_auth_failed";
  }
  if (phase === "upload") {
    return "upload_failed";
  }
  if (phase === "download") {
    return "download_failed";
  }
  return "translation_failed";
}

function localizedDiagnostic(code: string) {
  if (!LOCALIZED_CODES.has(code)) {
    return undefined;
  }
  const key = `diagnostic-${code.replace(/_/g, "-")}`;
  return {
    reason: getString(key),
    suggestion: getString(key, "action"),
  };
}

export function buildTranslationFailure({
  diagnostic,
  fallback,
  phase = "translation",
  pdfId,
}: {
  diagnostic?: TranslationFailureDiagnostic | null;
  fallback?: string;
  phase?: FailurePhase;
  pdfId?: string;
}): { diagnostic: TranslationFailureDiagnostic; message: string } {
  const fallbackMessage = fallback?.trim() || getString("diagnostic-unknown");
  const code = diagnostic?.code || inferFailureCode(fallbackMessage, phase);
  const localized = localizedDiagnostic(code);
  const resolved: TranslationFailureDiagnostic = {
    code,
    category: diagnostic?.category || phase,
    reason: localized?.reason || diagnostic?.reason || fallbackMessage,
    suggestion:
      localized?.suggestion ||
      diagnostic?.suggestion ||
      getString("diagnostic-unknown", "action"),
    retryable: diagnostic?.retryable ?? true,
    details: diagnostic?.details || fallbackMessage,
  };
  const lines = [
    `[${resolved.code}] ${getString("diagnostic-reason-label")} ${resolved.reason}`,
    `${getString("diagnostic-action-label")} ${resolved.suggestion}`,
  ];
  if (pdfId) {
    lines.push(`${getString("diagnostic-task-id-label")} ${pdfId}`);
  }
  return { diagnostic: resolved, message: lines.join("\n") };
}

export function showTranslationFailure(message: string) {
  new ztoolkit.ProgressWindow(addon.data.config.addonName)
    .createLine({ text: message, type: "error" })
    .show();
}
