/** Status trackers: Ask-requested status reports that update themselves. */

import { apiFetch } from "@/lib/api";

export type TrackerStatus = "on_track" | "at_risk" | "blocked" | "done" | "unknown";

export interface StatusTrackerSummary {
  tracker_id: string;
  title: string;
  topic: string;
  status: TrackerStatus;
  summary: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
  last_update_at?: string | null;
  update_count: number;
}

export interface StatusTrackerUpdate {
  update_id: string;
  kind: "created" | "history" | "update";
  summary: string;
  status?: TrackerStatus | null;
  occurred_at: string;
  created_at: string;
  source_chunk_ids: string[];
  source_label?: string | null;
  source_url?: string | null;
}

export interface StatusTracker extends StatusTrackerSummary {
  updates: StatusTrackerUpdate[];
}

async function request<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiFetch(`/status/trackers${path}`, {
    ...init,
    headers: init.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listTrackers(): Promise<StatusTrackerSummary[]> {
  return request("", { method: "GET" }, "Could not load status trackers.");
}

export function getTracker(trackerId: string): Promise<StatusTracker> {
  return request(`/${encodeURIComponent(trackerId)}`, { method: "GET" }, "Could not load tracker.");
}

export function createTracker(subject: string): Promise<StatusTracker & { created: boolean }> {
  return request(
    "",
    { method: "POST", body: JSON.stringify({ subject }) },
    "Could not start tracking.",
  );
}

export function setTrackerArchived(trackerId: string, archived: boolean): Promise<StatusTracker> {
  return request(
    `/${encodeURIComponent(trackerId)}`,
    { method: "PATCH", body: JSON.stringify({ archived }) },
    "Could not update tracker.",
  );
}

export function deleteTracker(trackerId: string): Promise<void> {
  return request(`/${encodeURIComponent(trackerId)}`, { method: "DELETE" }, "Could not stop tracking.");
}

export const STATUS_LABELS: Record<TrackerStatus, string> = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked",
  done: "Done",
  unknown: "No status yet",
};
