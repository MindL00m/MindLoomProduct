import { apiFetch } from "@/lib/api";

export interface SapStatus {
  connected: boolean;
  status: "not_connected" | "active";
  base_url?: string;
  sap_client?: string | null;
  username?: string;
  connected_at?: string;
}

export interface SapConnectInput {
  base_url: string;
  username: string;
  password: string;
  test_service: string;
  sap_client?: string;
}

async function send(path: string, fallback: string, init?: RequestInit): Promise<SapStatus> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<SapStatus>;
}

export const getSapStatus = () => send("/integrations/sap", "Could not load SAP.");

export const connectSap = (input: SapConnectInput) =>
  send("/integrations/sap/connect", "Could not connect SAP.", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, sap_client: input.sap_client || null }),
  });

export const disconnectSap = () => send("/integrations/sap", "Could not disconnect SAP.", { method: "DELETE" });
