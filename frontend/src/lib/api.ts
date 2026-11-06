// Typed client for the backend. Contract: contracts/api.md (v0).

export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** Contract error body: {"error": {"code": "<snake_case>", "message": "<human text>"}} */
export interface ApiErrorBody {
  error: { code: string; message: string };
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const err = (value as { error?: unknown }).error;
  return (
    typeof err === "object" &&
    err !== null &&
    typeof (err as { code?: unknown }).code === "string" &&
    typeof (err as { message?: unknown }).message === "string"
  );
}

/**
 * Call a backend route under /api. Throws ApiError on a non-2xx response
 * (code "network_error" when the backend is unreachable).
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  // Only label bodies as JSON; a bodyless GET with Content-Type forces a CORS preflight.
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}/api${path}`, { ...init, headers });
  } catch (cause) {
    throw new ApiError(
      0,
      "network_error",
      cause instanceof Error ? cause.message : "Network request failed",
    );
  }

  const body: unknown = await res.json().catch(() => null);

  if (!res.ok) {
    if (isApiErrorBody(body)) {
      throw new ApiError(res.status, body.error.code, body.error.message);
    }
    throw new ApiError(res.status, "http_error", `HTTP ${res.status}`);
  }
  return body as T;
}

// --- Endpoints ---------------------------------------------------------

/** GET /api/health -> 200 */
export interface Health {
  status: "ok";
  version: string;
}

export function getHealth(init?: RequestInit): Promise<Health> {
  return apiFetch<Health>("/health", { cache: "no-store", ...init });
}
