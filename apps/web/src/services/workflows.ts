/** Recurring workflows: emails sent from the user's Gmail at a fixed interval. */

import { apiFetch } from "@/lib/api";

export type IntervalUnit = "minutes" | "hours" | "days" | "weeks";

export interface RecurringEmailRun {
  run_id: string;
  ran_at: string;
  status: "sent" | "failed";
  error?: string | null;
  subject: string;
  body_preview: string;
}

export interface RecurringEmail {
  workflow_id: string;
  name: string;
  recipient_email: string;
  recipient_name?: string | null;
  subject: string;
  body: string;
  refresh_instructions?: string | null;
  interval_value: number;
  interval_unit: IntervalUnit;
  /** Human label, e.g. "every week". */
  repeats: string;
  timezone?: string | null;
  status: "active" | "paused" | "error";
  next_run_at: string;
  last_run_at?: string | null;
  run_count: number;
  consecutive_failures: number;
  last_error?: string | null;
  last_run_status?: "sent" | "failed" | null;
  runs?: RecurringEmailRun[];
}

export interface CreateRecurringEmailInput {
  recipient_email: string;
  recipient_name?: string | null;
  subject: string;
  body: string;
  interval_value: number;
  interval_unit: IntervalUnit;
  start_at?: string | null;
  refresh_instructions?: string | null;
  timezone?: string | null;
}

async function request<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiFetch(`/workflows/recurring-emails${path}`, {
    ...init,
    headers: typeof init.body === "string" ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listRecurringEmails(): Promise<RecurringEmail[]> {
  return request("", { method: "GET" }, "Could not load recurring workflows.");
}

export function getRecurringEmail(id: string): Promise<RecurringEmail> {
  return request(`/${encodeURIComponent(id)}`, { method: "GET" }, "Could not load workflow.");
}

export function createRecurringEmail(input: CreateRecurringEmailInput): Promise<RecurringEmail> {
  return request("", { method: "POST", body: JSON.stringify(input) }, "Could not schedule the email.");
}

export function updateRecurringEmail(
  id: string,
  changes: Partial<Pick<RecurringEmail, "status" | "subject" | "body" | "interval_value" | "interval_unit" | "recipient_email">>,
): Promise<RecurringEmail> {
  return request(
    `/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(changes) },
    "Could not update the workflow.",
  );
}

export function sendRecurringEmailNow(id: string): Promise<RecurringEmail> {
  return request(`/${encodeURIComponent(id)}/send-now`, { method: "POST" }, "Could not send the email.");
}

export function deleteRecurringEmail(id: string): Promise<void> {
  return request(`/${encodeURIComponent(id)}`, { method: "DELETE" }, "Could not delete the workflow.");
}

export function userTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

export function formatInterval(value: number, unit: IntervalUnit): string {
  const singular = unit.slice(0, -1);
  return value === 1 ? `every ${singular}` : `every ${value} ${unit}`;
}
