/** Backend API configuration and authenticated fetch wrapper. */

import { getOrgId, getUserId } from "@/store/session";

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  "http://localhost:8000";

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
    return await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (err) {
    const reason = err instanceof Error ? err.message : "network error";
    throw new Error(
      `Cannot reach the API at ${API_BASE} (${reason}). Is the server running?`,
    );
  }
}
