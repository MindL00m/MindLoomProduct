import { apiFetch } from "@/lib/api";

export interface SlackSettings {
  ingest_channel_ids: string[];
  ingest_mention_threads: boolean;
  answer_mentions: boolean;
  unlinked_user_policy: "ignore" | "org";
}

export interface SlackStatus {
  connected: boolean;
  status: "not_connected" | "draft" | "active" | "error";
  install_id?: string | null;
  team_name?: string | null;
  bot_user_id?: string | null;
  event_url?: string | null;
  manifest?: Record<string, unknown> | null;
  public_api_warning?: string | null;
  missing_scopes?: string[];
  granted_scopes?: string[];
  last_event_at?: string | null;
  last_error?: string | null;
  settings?: SlackSettings | null;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

async function fail(response: Response, fallback: string): Promise<never> {
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  const detail = body.detail;
  throw new Error(typeof detail === "string" ? detail : fallback);
}

export async function getSlackStatus(): Promise<SlackStatus> {
  const response = await apiFetch("/integrations/slack");
  if (!response.ok) await fail(response, "Could not load Slack.");
  return response.json() as Promise<SlackStatus>;
}

export async function createSlackDraft(): Promise<SlackStatus> {
  const response = await apiFetch("/integrations/slack/draft", { method: "POST" });
  if (!response.ok) await fail(response, "Could not start Slack setup.");
  return response.json() as Promise<SlackStatus>;
}

export async function saveSlackCredentials(botToken: string, signingSecret: string): Promise<SlackStatus> {
  const response = await apiFetch("/integrations/slack/credentials", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bot_token: botToken, signing_secret: signingSecret }),
  });
  if (!response.ok) await fail(response, "Could not save the Slack credentials.");
  return response.json() as Promise<SlackStatus>;
}

export async function listSlackChannels(): Promise<SlackChannel[]> {
  const response = await apiFetch("/integrations/slack/channels");
  if (!response.ok) await fail(response, "Could not list Slack channels.");
  const body = (await response.json()) as { channels: SlackChannel[] };
  return body.channels;
}

export async function saveSlackSettings(settings: SlackSettings): Promise<SlackStatus> {
  const response = await apiFetch("/integrations/slack/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) await fail(response, "Could not save Slack settings.");
  return response.json() as Promise<SlackStatus>;
}

export async function startSlackBackfill(days: 1 | 7 | 30): Promise<void> {
  const response = await apiFetch("/integrations/slack/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days }),
  });
  if (!response.ok) await fail(response, "Could not start the Slack import.");
}

export async function sendSlackTest(channel: string): Promise<void> {
  const response = await apiFetch("/integrations/slack/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel }),
  });
  if (!response.ok) await fail(response, "Could not send the test message.");
}

export async function disconnectSlack(): Promise<void> {
  const response = await apiFetch("/integrations/slack", { method: "DELETE" });
  if (!response.ok) await fail(response, "Could not disconnect Slack.");
}
