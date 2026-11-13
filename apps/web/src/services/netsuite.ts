import { apiFetch } from "@/lib/api";

export interface NetSuiteStatus {
  connected: boolean;
  status: "not_connected" | "pending" | "active";
  account_id?: string;
  client_id?: string | null;
  certificate_id?: string | null;
  /** The public certificate to upload in NetSuite. The private key never leaves MindLoom. */
  certificate_pem?: string;
  certificate_expires_at?: string;
  connected_at?: string | null;
}

async function send(path: string, fallback: string, init?: RequestInit): Promise<NetSuiteStatus> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<NetSuiteStatus>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getNetSuiteStatus = () => send("/integrations/netsuite", "Could not load NetSuite.");

export const startNetSuiteSetup = (accountId: string) =>
  send("/integrations/netsuite/setup", "Could not start NetSuite setup.", json("POST", { account_id: accountId }));

export const connectNetSuite = (clientId: string, certificateId: string) =>
  send(
    "/integrations/netsuite/connect",
    "Could not connect NetSuite.",
    json("POST", { client_id: clientId, certificate_id: certificateId }),
  );

export const disconnectNetSuite = () =>
  send("/integrations/netsuite", "Could not disconnect NetSuite.", { method: "DELETE" });
