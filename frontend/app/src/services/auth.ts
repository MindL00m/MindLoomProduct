/** Auth API: simulated Google sign-in and organization creation. */

import { apiFetch } from "@/lib/api";

export interface AuthSession {
  org_id: string;
  org_name: string;
  user_id: string;
  email: string;
  name?: string | null;
  photo_url?: string | null;
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

export async function googleSignIn(
  email: string,
  name?: string,
): Promise<AuthSession> {
  const res = await apiFetch(
    "/auth/google/signin",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, name }),
    },
    { skipAuth: true },
  );
  if (!res.ok) {
    let detail = `Sign-in failed (${res.status})`;
    try {
      const body = await res.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new AuthError(detail, res.status);
  }
  return res.json();
}

export async function createOrg(params: {
  name: string;
  domain: string;
  adminEmail: string;
  adminName?: string;
}): Promise<AuthSession> {
  const res = await apiFetch(
    "/orgs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        domain: params.domain,
        admin_email: params.adminEmail,
        admin_name: params.adminName,
      }),
    },
    { skipAuth: true },
  );
  if (!res.ok) {
    let detail = `Could not create organization (${res.status})`;
    try {
      const body = await res.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new AuthError(detail, res.status);
  }
  return res.json();
}

export async function getOrgSummary(): Promise<OrgSummary> {
  const res = await apiFetch("/org/summary");
  if (!res.ok) throw new Error(`Summary request failed (${res.status})`);
  return res.json();
}
