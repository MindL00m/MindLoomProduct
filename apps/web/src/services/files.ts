/** LoomBot Files: the signed-in user's private text, Word, Excel, PDF, and PowerPoint files. */

import { apiFetch } from "@/lib/api";

export type FileKind =
  | "csv"
  | "txt"
  | "md"
  | "json"
  | "docx"
  | "xlsx"
  | "pdf"
  | "pptx"
  | ImageKind;

export type ImageKind = "png" | "jpg" | "jpeg" | "gif" | "webp";
export const IMAGE_KINDS: FileKind[] = ["png", "jpg", "jpeg", "gif", "webp"];

/** Kinds stored as text (1 MB limit); the rest are binary (15 MB). */
export const TEXT_KINDS: FileKind[] = ["csv", "txt", "md", "json"];

/** Extensions the Files tab accepts for upload. */
export const FILE_UPLOAD_ACCEPT =
  ".csv,.txt,.md,.json,.docx,.xlsx,.pdf,.pptx,text/csv,text/plain,text/markdown," +
  "application/json,application/pdf," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet," +
  "application/vnd.openxmlformats-officedocument.presentationml.presentation," +
  ".png,.jpg,.jpeg,.gif,.webp,image/png,image/jpeg,image/gif,image/webp";

export function kindFromName(name: string): FileKind | null {
  const match = /\.(csv|txt|md|json|docx|xlsx|pdf|pptx|png|jpe?g|gif|webp)$/i.exec(name.trim());
  return match ? (match[1].toLowerCase() as FileKind) : null;
}

export interface LoomFileMeta {
  file_id: string;
  name: string;
  kind: FileKind;
  mime_type: string;
  size_bytes: number;
  /** Set on files generated as an updated version of another file. */
  based_on_file_id?: string | null;
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
  /** Set when a colleague shared this copy in MindLoom. */
  shared_by_name?: string | null;
  shared_note?: string | null;
  shared_at?: string | null;
  /** False until the owner has seen the "shared with you" notification. */
  share_seen?: boolean;
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
    headers:
      typeof init.body === "string"
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

/**
 * Kinds that can be created from text: Word, PDF, and PowerPoint from Markdown,
 * Excel from Markdown tables or CSV.
 */
export type WritableKind = Exclude<FileKind, ImageKind>;

export function createFile(
  name: string,
  content = "",
  kind: WritableKind = "csv",
): Promise<LoomFile> {
  return request(
    "",
    { method: "POST", body: JSON.stringify({ name, content, kind }) },
    "Could not create file.",
  );
}

/** Upload a CSV, TXT, DOCX, XLSX, or PDF file. */
export function uploadFile(file: File, name?: string): Promise<LoomFile> {
  const form = new FormData();
  form.append("file", file);
  if (name?.trim()) form.append("name", name.trim());
  return request("/upload", { method: "POST", body: form }, "Could not upload file.");
}

/** Replace a file with a new version of the same type (undoable). */
export function replaceFileUpload(fileId: string, file: File): Promise<LoomFile> {
  const form = new FormData();
  form.append("file", file);
  return request(
    `/${encodeURIComponent(fileId)}/upload`,
    { method: "PUT", body: form },
    "Could not upload the new version.",
  );
}

export async function downloadFileBlob(fileId: string): Promise<Blob> {
  const response = await apiFetch(`/loombot/files/${encodeURIComponent(fileId)}/download`);
  if (!response.ok) throw new Error("Could not download the file.");
  return response.blob();
}

/** Save a file to the user's computer. */
export async function saveFileToDisk(fileId: string, name: string): Promise<void> {
  const blob = await downloadFileBlob(fileId);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type FilePreview =
  | { kind: "docx"; blocks: DocBlock[]; markdown?: string }
  | { kind: "xlsx"; sheets: SheetPreview[] }
  | { kind: "pdf"; pages: number }
  | { kind: "pptx"; slides: SlidePreview[] }
  | { kind: "image"; height: number; width: number }
  | { kind: "csv" | "txt" | "md" | "json"; content: string };

export interface SlidePreview {
  number: number;
  title: string;
  lines: string[];
  notes: string;
}

export type DocBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph" | "list_item"; text: string }
  | { type: "table"; rows: string[][] };

export interface SheetPreview {
  name: string;
  rows: string[][];
  truncated: boolean;
}

export function getFilePreview(fileId: string): Promise<FilePreview> {
  return request(`/${encodeURIComponent(fileId)}/preview`, { method: "GET" }, "Could not preview file.");
}

/**
 * Open a CSV or XLSX file in desktop Excel. Excel cannot send the user's
 * session, so the API issues a 10-minute signed link that Excel downloads.
 */
export async function openInExcel(fileId: string): Promise<void> {
  const { url } = await request<{ url: string }>(
    `/${encodeURIComponent(fileId)}/open-link`,
    { method: "POST" },
    "Could not prepare the file for Excel.",
  );
  window.location.href = `ms-excel:ofe|u|${url}`;
}

export interface DocumentChange {
  before: string;
  after: string;
  reason?: string;
  sources: string[];
}

export interface UpdatedVersionResult {
  changed: boolean;
  file: LoomFileMeta | null;
  based_on: string;
  summary: string;
  changes: DocumentChange[];
  warnings: string[];
}

/** Create an updated copy of a file from MindLoom's knowledge. */
export function createUpdatedVersion(
  fileId: string,
  instructions?: string,
): Promise<UpdatedVersionResult> {
  return request(
    `/${encodeURIComponent(fileId)}/updated-version`,
    { method: "POST", body: JSON.stringify({ instructions: instructions?.trim() || null }) },
    "Could not create an updated version.",
  );
}

/** Save new content (CSV, text, or Markdown for a Word document) and/or rename. Undoable. */
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

export interface ShareResult {
  emailed: string[];
  shared_in_mindloom: { user_id: string; name: string; email: string; file_name: string }[];
  not_in_mindloom: string[];
}

/** Email a file as an attachment and/or copy it into colleagues' Files in MindLoom. */
export function shareFile(
  fileId: string,
  input: { recipients: string[]; sendEmail: boolean; shareInMindloom: boolean; message?: string },
): Promise<ShareResult> {
  return request(
    `/${encodeURIComponent(fileId)}/share`,
    {
      method: "POST",
      body: JSON.stringify({
        recipients: input.recipients,
        send_email: input.sendEmail,
        share_in_mindloom: input.shareInMindloom,
        message: input.message?.trim() || null,
      }),
    },
    "Could not share the file.",
  );
}

export interface UnseenShare {
  file_id: string;
  name: string;
  kind: FileKind;
  shared_by_name: string;
  shared_note?: string | null;
  shared_at: string;
}

export function listUnseenShares(): Promise<UnseenShare[]> {
  return request("/shares/unseen", { method: "GET" }, "Could not load shared files.");
}

/** Clear "shared with you" notifications (all, or just these files). */
export function markSharesSeen(fileIds?: string[]): Promise<{ cleared: number }> {
  return request(
    "/shares/seen",
    { method: "POST", body: JSON.stringify({ file_ids: fileIds ?? null }) },
    "Could not update notifications.",
  );
}
