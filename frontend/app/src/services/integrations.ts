/** Workspace app integrations (Google Calendar, etc.). */

import { apiFetch } from "@/lib/api";

export interface IntegrationInfo {
  provider: string;
  label: string;
  connected: boolean;
  account_email?: string | null;
  connected_at?: string | null;
}

export interface IntegrationsListResponse {
  integrations: IntegrationInfo[];
  oauth_enabled: boolean;
  microsoft_oauth_enabled: boolean;
}

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end?: string | null;
  all_day: boolean;
  location?: string | null;
  html_link?: string | null;
}

export interface CalendarEventsResponse {
  account_email?: string | null;
  events: CalendarEvent[];
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

export async function startGoogleCalendarOAuth(): Promise<string> {
  const res = await apiFetch("/integrations/google/calendar/authorize");
  if (!res.ok) throw new Error(await parseError(res, "Could not start Google sign-in."));
  const data = await res.json();
  return data.authorization_url as string;
}

export async function startGoogleWorkspaceOAuth(): Promise<string> {
  const res = await apiFetch("/integrations/google/workspace/authorize");
  if (!res.ok) throw new Error(await parseError(res, "Could not start Google Workspace sign-in."));
  const data = await res.json();
  return data.authorization_url as string;
}

export async function connectGoogleCalendarDev(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations/google/calendar/connect-dev", {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not connect Google Calendar."));
  return res.json();
}

export async function connectGoogleWorkspaceDev(): Promise<IntegrationsListResponse> {
  const res = await apiFetch("/integrations/google/workspace/connect-dev", {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not connect Google Workspace."));
  return res.json();
}

export async function disconnectGoogleCalendar(): Promise<void> {
  const res = await apiFetch("/integrations/google/calendar", { method: "DELETE" });
  if (!res.ok) throw new Error(await parseError(res, "Could not disconnect."));
}

export async function fetchCalendarEvents(): Promise<CalendarEventsResponse> {
  const res = await apiFetch("/integrations/google/calendar/events");
  if (!res.ok) throw new Error(await parseError(res, "Could not load calendar events."));
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

export function formatEventTime(event: CalendarEvent): string {
  if (event.all_day) {
    return new Date(event.start).toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  }
  const start = new Date(event.start);
  const end = event.end ? new Date(event.end) : null;
  const datePart = start.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  const timeFmt: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };
  const startTime = start.toLocaleTimeString(undefined, timeFmt);
  if (!end) return `${datePart} · ${startTime}`;
  const endTime = end.toLocaleTimeString(undefined, timeFmt);
  return `${datePart} · ${startTime} – ${endTime}`;
}
