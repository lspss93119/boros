import type { ApiEnvelope } from "./contracts";

export class DashboardApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(`Dashboard API error: ${code}`);
    this.name = "DashboardApiError";
    this.status = status;
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export async function dashboardFetch<T>(path: string): Promise<ApiEnvelope<T>> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new DashboardApiError(response.status, "invalid_json");
  }
  if (!isRecord(payload)) {
    throw new DashboardApiError(response.status, "invalid_envelope");
  }
  if (!response.ok || payload.ok !== true) {
    const rawError = payload.error;
    const errorCode =
      isRecord(rawError) && typeof rawError.code === "string"
        ? rawError.code
        : "request_failed";
    throw new DashboardApiError(response.status, errorCode);
  }
  if (!("data" in payload) || !("meta" in payload)) {
    throw new DashboardApiError(response.status, "invalid_envelope");
  }
  return payload as unknown as ApiEnvelope<T>;
}
