/** Files tab sources beyond your own files: Google Drive (live) and email attachments. */

import { apiFetch } from "@/lib/api";
import type { FilePreview, LoomFile } from "@/services/files";

async function request<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await apiFetch(`/loombot/sources${path}`, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<T>;
}

async function blob(path: string, fallback: string): Promise<Blob> {
  const response = await apiFetch(`/loombot/sources${path}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.blob();
}

export type SourceKind =
  | "folder" | "docx" | "xlsx" | "pdf" | "csv" | "txt" | "pptx" | "image" | "other";

export interface DriveItem {
  id: string;
  name: string;
  mime_type: string;
  kind: SourceKind;
  type_label: string;
  modified_at?: string | null;
  size_bytes?: number | null;
  web_view_link?: string | null;
  owner?: string | null;
  can_preview: boolean;
  can_import: boolean;
  download_name?: string;
}

export interface DrivePage {
  files: DriveItem[];
  next_page_token?: string | null;
}

/** Shapes the Files tab can show for something that is not one of your own files. */
export type SourcePreview =
  | (FilePreview & { item?: DriveItem })
  | { kind: "image" | "unsupported"; item?: DriveItem }
  | { kind: "text"; content: string; item?: DriveItem };

export function listDrive(options: { folderId?: string; search?: string; pageToken?: string } = {}): Promise<DrivePage> {
  const params = new URLSearchParams();
  if (options.folderId) params.set("folder_id", options.folderId);
  if (options.search?.trim()) params.set("search", options.search.trim());
  if (options.pageToken) params.set("page_token", options.pageToken);
  const query = params.toString();
  return request(`/drive${query ? `?${query}` : ""}`, { method: "GET" }, "Could not load your Google Drive.");
}

export function previewDriveFile(id: string): Promise<SourcePreview> {
  return request(`/drive/${encodeURIComponent(id)}/preview`, { method: "GET" }, "Could not preview this file.");
}

export function downloadDriveFile(id: string): Promise<Blob> {
  return blob(`/drive/${encodeURIComponent(id)}/download`, "Could not download this file.");
}

export function importDriveFile(id: string): Promise<LoomFile> {
  return request(`/drive/${encodeURIComponent(id)}/import`, { method: "POST" }, "Could not save this file to Files.");
}

export interface EmailAttachmentItem {
  id: string;
  filename: string;
  kind: string;
  /** Set when Files can store the original (pdf, docx, xlsx, csv, txt). */
  file_kind?: "csv" | "txt" | "docx" | "xlsx" | "pdf" | null;
  from?: string | null;
  subject?: string;
  sent_at?: string | null;
  provider: "gmail" | "outlook";
  email_url?: string | null;
}

export interface EmailAttachmentDetail extends EmailAttachmentItem {
  text: string;
  text_truncated: boolean;
  read_with?: string | null;
  size_bytes?: number | null;
}

export function listEmailAttachments(search?: string): Promise<{
  attachments: EmailAttachmentItem[];
  mailboxes: string[];
  note?: string | null;
}> {
  const query = search?.trim() ? `?search=${encodeURIComponent(search.trim())}` : "";
  return request(`/email-attachments${query}`, { method: "GET" }, "Could not load files from your email.");
}

export function getEmailAttachment(id: string): Promise<EmailAttachmentDetail> {
  return request(`/email-attachments/${encodeURIComponent(id)}`, { method: "GET" }, "Could not open this attachment.");
}

export function downloadEmailAttachment(id: string): Promise<Blob> {
  return blob(`/email-attachments/${encodeURIComponent(id)}/download`, "Could not download the original file.");
}

export function importEmailAttachment(id: string): Promise<LoomFile> {
  return request(`/email-attachments/${encodeURIComponent(id)}/import`, { method: "POST" }, "Could not save this attachment to Files.");
}

/** Save a blob to the user's computer. */
export function saveBlob(data: Blob, name: string): void {
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
