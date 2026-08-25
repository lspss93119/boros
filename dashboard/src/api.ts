import type { ApiEnvelope, ApiErrorEnvelope } from "./contracts";

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

export async function dashboardFetch<T>(path: string): Promise<ApiEnvelope<T>> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  let payload: ApiEnvelope<T> | ApiErrorEnvelope;
  try {
    payload = (await response.json()) as ApiEnvelope<T> | ApiErrorEnvelope;
  } catch {
    throw new DashboardApiError(response.status, "invalid_json");
  }
  if (!response.ok || payload.ok !== true) {
    const errorCode = payload.ok === false ? payload.error.code : "request_failed";
    throw new DashboardApiError(response.status, errorCode);
  }
  return payload;
}
