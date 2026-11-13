import { apiFetch } from "@/lib/api";

export type SapSystem = "s4hana" | "business_one";
export type SapAuth = "basic" | "oauth" | "certificate";
export type SapVia = "direct" | "relay";

export interface SapStatus {
  connected: boolean;
  status: "not_connected" | "active";
  base_url?: string;
  sap_client?: string | null;
  username?: string;
  system?: SapSystem;
  auth?: SapAuth;
  via?: SapVia;
  company_db?: string | null;
  token_url?: string | null;
  connected_at?: string;
}

export interface SapConnectInput {
  base_url: string;
  system: SapSystem;
  auth: SapAuth;
  via: SapVia;
  username?: string;
  password?: string;
  test_service?: string;
  sap_client?: string;
  token_url?: string;
  client_id?: string;
  client_secret?: string;
  certificate_pem?: string;
  private_key_pem?: string;
  company_db?: string;
  /** The code MindLoom for Windows shows on the PC that runs the relay. */
  relay_code?: string;
}

export interface RelayStatus {
  online: boolean;
  last_seen_at: string | null;
  paired?: boolean;
}

async function send<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<T>;
}

export const getSapStatus = () => send<SapStatus>("/integrations/sap", "Could not load SAP.");

export function connectSap(input: SapConnectInput) {
  // Empty fields are left out so the server sees "not given", not "".
  const body = Object.fromEntries(Object.entries(input).filter(([, value]) => value !== "" && value != null));
  return send<SapStatus>("/integrations/sap/connect", "Could not connect SAP.", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const disconnectSap = () =>
  send<SapStatus>("/integrations/sap", "Could not disconnect SAP.", { method: "DELETE" });

export const getRelayStatus = () => send<RelayStatus>("/relay/status", "Could not check the relay.");
