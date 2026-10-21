/** LoomBot Files: the signed-in user's private CSV files. */

import { apiFetch } from "@/lib/api";

export interface LoomFileMeta {
  file_id: string;
  name: string;
  row_count: number;
  column_count: number;
  last_edited_by: "user" | "loombot";
  can_undo: boolean;
  created_at: string;
  updated_at: string;
  /** When on, newly arriving information matching the instructions updates the file. */
  monitor_enabled: boolean;
  /** Stored even while off when LoomBot suggested monitoring and is waiting for a yes. */
  monitor_instructions?: string | null;
  monitor_started_at?: string | null;
}

export interface LoomFileEvent {
  event_id: string;
  summary: string;
  rows_added: number;
  cells_updated: number;
  source_chunk_ids: string[];
  source_label?: string | null;
  source_url?: string | null;
  created_at: string;
}

export interface LoomFile extends LoomFileMeta {
  content: string;
}

async function request<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiFetch(`/loombot/files${path}`, {
    ...init,
    headers: init.body
      ? { "Content-Type": "application/json", ...(init.headers ?? {}) }
      : init.headers,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listFiles(): Promise<LoomFileMeta[]> {
  return request("", { method: "GET" }, "Could not load files.");
}

export function getFile(fileId: string): Promise<LoomFile> {
  return request(`/${encodeURIComponent(fileId)}`, { method: "GET" }, "Could not open file.");
}

export function createFile(name: string, content = ""): Promise<LoomFile> {
  return request(
    "",
    { method: "POST", body: JSON.stringify({ name, content }) },
    "Could not create file.",
  );
}

export function updateFile(
  fileId: string,
  changes: { content?: string; name?: string },
): Promise<LoomFile> {
  return request(
    `/${encodeURIComponent(fileId)}`,
    { method: "PUT", body: JSON.stringify(changes) },
    "Could not save file.",
  );
}

export function undoFile(fileId: string): Promise<LoomFile> {
  return request(
    `/${encodeURIComponent(fileId)}/undo`,
    { method: "POST" },
    "Could not undo the last change.",
  );
}

export function deleteFile(fileId: string): Promise<void> {
  return request(`/${encodeURIComponent(fileId)}`, { method: "DELETE" }, "Could not delete file.");
}

export function setFileMonitoring(
  fileId: string,
  enabled: boolean,
  instructions?: string,
): Promise<LoomFile> {
  return request(
    `/${encodeURIComponent(fileId)}/monitoring`,
    { method: "PUT", body: JSON.stringify({ enabled, instructions: instructions || null }) },
    "Could not change automatic updates.",
  );
}

export function listFileEvents(fileId: string): Promise<LoomFileEvent[]> {
  return request(
    `/${encodeURIComponent(fileId)}/events`,
    { method: "GET" },
    "Could not load automatic changes.",
  );
}
