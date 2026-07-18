/** Backend API configuration and authenticated fetch wrapper. */

import { getOrgId, getUserId, useSession } from "@/store/session";

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (import.meta.env.DEV ? "/api" : "http://localhost:8000");

const STALE_SESSION_DETAILS = new Set([
  "Invalid or unknown organization.",
  "Invalid user for this organization.",
  "Missing organization context.",
  "Missing user context.",
]);

/** Drop a persisted session that the API no longer recognizes (e.g. after DB reset). */
function clearStaleSessionIfNeeded(res: Response, skipAuth: boolean): void {
  if (skipAuth || res.status !== 401 || typeof window === "undefined") return;
  void res
    .clone()
    .json()
    .then((body: { detail?: unknown }) => {
      const detail = typeof body?.detail === "string" ? body.detail : "";
      if (!STALE_SESSION_DETAILS.has(detail)) return;
      useSession.getState().clearSession();
      if (!window.location.pathname.startsWith("/setup")) {
        window.location.assign("/setup");
      }
    })
    .catch(() => {
      /* ignore non-JSON 401 bodies */
    });
}

/** Fetch with ``X-Org-Id`` and ``X-User-Id`` when a session exists. Set ``skipAuth`` for public endpoints. */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
  { skipAuth = false }: { skipAuth?: boolean } = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!skipAuth) {
    const orgId = getOrgId();
    if (orgId) headers.set("X-Org-Id", orgId);
    const userId = getUserId();
    if (userId) headers.set("X-User-Id", userId);
  }
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
    clearStaleSessionIfNeeded(res, skipAuth);
    return res;
  } catch (err) {
    const reason = err instanceof Error ? err.message : "network error";
    throw new Error(
      `Cannot reach the API at ${API_BASE} (${reason}). Is the server running?`,
    );
  }
}
