/** Auth API: Google ID-token sign-in and organization creation. */

import { apiFetch } from "@/lib/api";

export interface AuthSession {
  org_id: string;
  org_name: string;
  user_id: string;
  email: string;
  name?: string | null;
  photo_url?: string | null;
  role: "admin" | "member";
  access_token: string;
}

export interface OrgSummary {
  organization: string;
  people: number;
  departments: number;
  groups: number;
}

export class AuthError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "AuthError";
  }
}

async function parseAuthError(res: Response, fallback: string): Promise<AuthError> {
  let detail = `${fallback} (${res.status})`;
  try {
    const body = await res.json();
    const raw = body?.detail;
    if (typeof raw === "string" && raw.trim()) {
      detail = raw;
    } else if (Array.isArray(raw) && raw.length > 0) {
      // FastAPI / Pydantic validation errors
      detail = raw
        .map((item: { msg?: string }) => item?.msg)
        .filter(Boolean)
        .join("; ") || detail;
    } else if (raw && typeof raw === "object") {
      detail = JSON.stringify(raw);
    }
  } catch {
    /* keep default */
  }
  return new AuthError(detail, res.status);
}

export async function googleSignIn(idToken: string): Promise<AuthSession> {
  const res = await apiFetch(
    "/auth/google/signin",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: idToken }),
    },
    { skipAuth: true },
  );
  if (!res.ok) {
    throw await parseAuthError(res, "Sign-in failed");
  }
  return res.json();
}

export async function createOrg(params: {
  name: string;
  domain: string;
  idToken: string;
}): Promise<AuthSession> {
  const res = await apiFetch(
    "/orgs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        domain: params.domain,
        id_token: params.idToken,
      }),
    },
    { skipAuth: true },
  );
  if (!res.ok) {
    throw await parseAuthError(res, "Could not create organization");
  }
  return res.json();
}

export async function getOrgSummary(): Promise<OrgSummary> {
  const res = await apiFetch("/org/summary");
  if (!res.ok) throw new Error(`Summary request failed (${res.status})`);
  return res.json();
}

export async function getFreshSession(): Promise<AuthSession> {
  const res = await apiFetch("/auth/me");
  if (!res.ok) {
    throw await parseAuthError(res, "Session expired");
  }
  return res.json();
}

/** Map an API auth session into the client session store shape. */
export function toClientSession(session: AuthSession) {
  return {
    orgId: session.org_id,
    orgName: session.org_name,
    userId: session.user_id,
    email: session.email,
    name: session.name,
    photoUrl: session.photo_url,
    role: session.role,
    accessToken: session.access_token,
  };
}
