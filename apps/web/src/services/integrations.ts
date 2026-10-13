/** Workspace app integrations (Google Calendar, etc.). */

import { apiFetch } from "@/lib/api";

export interface IntegrationInfo {
  provider: string;
  label: string;
  connected: boolean;
  account_email?: string | null;
  connected_at?: string | null;
  setup_status: "not_connected" | "setup_required" | "importing" | "active" | "paused" | "warning" | "error";
  selected_resource_count: number;
  last_synced_at?: string | null;
}

export type ConnectionProvider = "google_workspace" | "microsoft_teams" | "zoom";

export interface ConnectionResource {
  id: string;
  name: string;
  kind: "drive" | "folder" | "site" | "library" | "team" | "channel" | "mailbox" | "calendar" | "chat" | "recording" | "transcript";
  parent_id?: string | null;
  selectable: boolean;
  warning?: string | null;
}

export interface ConnectionPolicy {
  included_resource_ids: string[];
  excluded_resource_ids: string[];
  include_history: boolean;
  history_start_date?: string | null;
  sync_frequency: "realtime" | "hourly" | "daily";
  access_mode: "respect_source_permissions" | "organization" | "selected";
  allowed_departments: string[];
  allowed_user_ids: string[];
}

export interface ConnectionPreview {
  provider: ConnectionProvider;
  selected_resources: number;
  estimated_items: number;
  estimated_size_bytes: number;
  permission_warnings: string[];
  unsupported_items: number;
  count_is_exact: boolean;
  scanned_items: number;
}

export interface ConnectionPolicyResponse extends ConnectionPolicy {
  provider: ConnectionProvider;
  status: string;
  updated_at: string;
  initial_job_ids: string[];
}

export interface IntegrationsListResponse {
  integrations: IntegrationInfo[];
  oauth_enabled: boolean;
  microsoft_oauth_enabled: boolean;
  zoom_oauth_enabled: boolean;
  dev_integrations_allowed: boolean;
}

export interface WorkspaceWatchResponse {
  provider: "gmail" | "drive";
  account_email: string;
  cursor?: string | null;
  expiration?: string | null;
  status: string;
}

export interface WorkspaceSyncStartResponse {
  job_id: string;
  status: "queued";
  source: "gmail" | "drive";
}

export interface TeamsWatchRequest {
  team_id: string;
  channel_id: string;
}

export interface TeamsWatchResponse {
  provider: "teams";
  resource: string;
  subscription_id?: string | null;
  expiration?: string | null;
  status: string;
}

export interface TeamsSyncStartResponse {
  job_id: string;
  status: "queued";
  source: "teams";
}

async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ") || fallback;
    }
    return fallback;
  } catch {
    return fallback;
  }
}

export async function listIntegrations(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations");
  if (!res.ok) throw new Error(await parseError(res, "Could not load apps."));
  return res.json();
}

export async function startGoogleWorkspaceOAuth(): Promise<string> {
  const res = await apiFetch("/integrations/google/workspace/authorize");
  if (!res.ok) throw new Error(await parseError(res, "Could not start Google Workspace sign-in."));
  const data = await res.json();
  return data.authorization_url as string;
}

export async function connectGoogleWorkspaceDev(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations/google/workspace/connect-dev", {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not connect Google Workspace."));
  return res.json();
}

export async function watchGmail(): Promise<WorkspaceWatchResponse> {
  const res = await apiFetch("/integrations/google/workspace/gmail/watch", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res, "Could not start Gmail watch."));
  return res.json();
}

export async function watchDrive(): Promise<WorkspaceWatchResponse> {
  const res = await apiFetch("/integrations/google/workspace/drive/watch", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res, "Could not start Drive watch."));
  return res.json();
}

export async function syncGmailNow(maxResults = 25): Promise<WorkspaceSyncStartResponse> {
  const res = await apiFetch(`/integrations/google/workspace/gmail/sync?max_results=${maxResults}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not queue Gmail sync."));
  return res.json();
}

export async function syncDriveNow(maxResults = 25): Promise<WorkspaceSyncStartResponse> {
  const res = await apiFetch(`/integrations/google/workspace/drive/sync?max_results=${maxResults}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not queue Drive sync."));
  return res.json();
}

export async function startMicrosoftTeamsOAuth(): Promise<string> {
  const res = await apiFetch("/integrations/microsoft/teams/authorize");
  if (!res.ok) throw new Error(await parseError(res, "Could not start Microsoft sign-in."));
  const data = await res.json();
  return data.authorization_url as string;
}

export async function connectMicrosoftTeamsDev(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations/microsoft/teams/connect-dev", {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not connect Microsoft Teams."));
  return res.json();
}

export async function startZoomOAuth(): Promise<string> {
  const res = await apiFetch("/integrations/zoom/authorize");
  if (!res.ok) throw new Error(await parseError(res, "Could not start Zoom sign-in."));
  return (await res.json()).authorization_url as string;
}

export async function connectZoomDev(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations/zoom/connect-dev", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res, "Could not connect Zoom."));
  return res.json();
}

export async function watchMicrosoftTeams(
  request: TeamsWatchRequest,
): Promise<TeamsWatchResponse> {
  const res = await apiFetch("/integrations/microsoft/teams/watch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not start Teams watch."));
  return res.json();
}

export async function syncMicrosoftTeamsNow(
  maxResults = 25,
): Promise<TeamsSyncStartResponse> {
  const res = await apiFetch(`/integrations/microsoft/teams/sync?max_results=${maxResults}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not queue Teams sync."));
  return res.json();
}

export async function discoverConnectionResources(
  provider: ConnectionProvider,
): Promise<ConnectionResource[]> {
  const res = await apiFetch(`/integrations/${provider}/setup/resources`);
  if (!res.ok) throw new Error(await parseError(res, "Could not load available content."));
  const data = await res.json();
  return data.resources as ConnectionResource[];
}

export async function getConnectionPolicy(
  provider: ConnectionProvider,
): Promise<ConnectionPolicyResponse | null> {
  const res = await apiFetch(`/integrations/${provider}/setup/policy`);
  if (!res.ok) throw new Error(await parseError(res, "Could not load connection settings."));
  return res.json();
}

export async function previewConnection(
  provider: ConnectionProvider,
  policy: ConnectionPolicy,
): Promise<ConnectionPreview> {
  const res = await apiFetch(`/integrations/${provider}/setup/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not preview this connection."));
  return res.json();
}

export async function confirmConnection(
  provider: ConnectionProvider,
  policy: ConnectionPolicy,
): Promise<ConnectionPolicyResponse> {
  const res = await apiFetch(`/integrations/${provider}/setup/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not start this connection."));
  return res.json();
}

export async function setConnectionPaused(
  provider: ConnectionProvider,
  paused: boolean,
): Promise<ConnectionPolicyResponse> {
  const res = await apiFetch(
    `/integrations/${provider}/setup/${paused ? "pause" : "resume"}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(await parseError(res, "Could not update this connection."));
  return res.json();
}

export async function disconnectConnection(provider: ConnectionProvider): Promise<void> {
  const res = await apiFetch(`/integrations/${provider}/setup`, { method: "DELETE" });
  if (!res.ok) throw new Error(await parseError(res, "Could not disconnect this workspace."));
}
