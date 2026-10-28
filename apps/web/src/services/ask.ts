/** Ask API: query the knowledge graph and receive a cited, routed answer. */

import { apiFetch } from "@/lib/api";

export interface Citation {
  chunk_id: string;
  document_id: string;
  source: string;
  source_label: string;
  original_filename?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  page_number?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  row_range?: [number, number] | null;
  section_title?: string | null;
  table_cell?: string | null;
  sheet_name?: string | null;
  cell_range?: string | null;
  /** Pre-rendered, human-readable citation string, e.g. "Source: Report, report.pdf, pages 2-3". */
  label?: string;
}

export interface Source {
  chunk_id: string;
  raw_text: string;
  summary: string;
  speakers: string[];
  start_time: string;
  end_time: string;
  knowledge_type: string;
  confidence: string;
  similarity_score: number;
  citation?: Citation | null;
}

export interface Expert {
  name: string;
  reason: string;
  relationship_count: number;
}

export interface MessageablePerson {
  user_id?: string;
  name: string;
  email: string;
  title?: string | null;
  department?: string | null;
}

export interface ProposedExpertMessage {
  recipient_user_id: string;
  recipient_name: string;
  recipient_email: string;
  message: string;
  candidates?: MessageablePerson[];
}

/** A LoomBot Files file attached to an email draft. */
export interface EmailAttachmentRef {
  file_id: string;
  name: string;
  kind?: string;
  size_bytes?: number;
}

export interface ProposedEmail {
  recipient_email: string;
  recipient_name?: string;
  recipient_user_id?: string | null;
  cc_email?: string;
  bcc_email?: string;
  /** One email per To address, so recipients never see each other. */
  send_separately?: boolean;
  subject: string;
  body: string;
  /** True when the user can send from a connected mailbox (Gmail or Outlook). */
  google_connected?: boolean;
  email_provider?: "gmail" | "outlook" | "";
  candidates?: MessageablePerson[];
  attachments?: EmailAttachmentRef[];
}

export interface ProposedPullRequest {
  owner: string;
  repo: string;
  path: string;
  base_branch: string;
  branch_name: string;
  old_content: string;
  new_content: string;
  file_sha?: string | null;
  pr_title: string;
  pr_body?: string;
  commit_message?: string;
  html_url?: string | null;
}

export interface ProposedWorkspaceMember {
  user_id: string;
  name: string;
  email: string;
  reason?: string;
}

export interface ProposedWorkspaceUnmatched {
  name: string;
  email?: string | null;
  reason?: string;
}

export interface ProposedWorkspace {
  name: string;
  purpose: string;
  context_md: string;
  loombot_mode?: "context_only" | "org_knowledge";
  members?: ProposedWorkspaceMember[];
  unmatched_people?: ProposedWorkspaceUnmatched[];
}

/** A LoomBot Files CSV that Ask created or changed during a turn. */
export interface FileChange {
  file_id: string;
  name: string;
  action: "created" | "updated" | "monitoring";
  kind?: "csv" | "txt" | "docx" | "xlsx" | "pdf";
  summary?: string;
  row_count: number;
  column_count: number;
  /** "suggested" means LoomBot is asking whether to keep the file updated. */
  monitoring?: "on" | "off" | "suggested";
  monitor_instructions?: string | null;
  /** Original file name when this file is an updated version of it. */
  based_on?: string | null;
  changes?: { before: string; after: string; reason?: string; sources: string[] }[];
  warnings?: string[];
}

/** A repeating email Ask drafted; scheduled only after the user approves it. */
export interface ProposedRecurringEmail {
  recipient_email: string;
  recipient_name?: string | null;
  subject: string;
  body: string;
  interval_value: number;
  interval_unit: "minutes" | "hours" | "days" | "weeks";
  start_at?: string | null;
  refresh_instructions?: string | null;
  google_connected: boolean;
  candidates: { user_id?: string; name: string; email: string }[];
}

/** A self-updating status tracker Ask started (or found) for a request. */
export interface StatusTrackerCard {
  tracker_id: string;
  title: string;
  status: "on_track" | "at_risk" | "blocked" | "done" | "unknown";
  summary: string;
  created: boolean;
  update_count: number;
}

/** One public web page a web search returned; answers cite it as [WEB: n] (1-based). */
export interface WebResult {
  title: string;
  url: string;
  snippet?: string;
  published_date?: string | null;
}

export interface MeetingAttendee {
  email: string;
  name?: string;
  user_id?: string | null;
}

export interface MeetingTimeOption {
  start: string;
  end: string;
  label?: string;
}

/** A Google Calendar meeting LoomBot drafted; nothing is scheduled until the user approves. */
export interface ProposedMeeting {
  title: string;
  description?: string;
  start?: string | null;
  end?: string | null;
  duration_minutes: number;
  timezone?: string | null;
  attendees: MeetingAttendee[];
  location?: string;
  add_video_call: boolean;
  alternatives: MeetingTimeOption[];
  unknown_availability?: string[];
  calendar_connected: boolean;
  can_schedule: boolean;
  calendar_note?: string | null;
  candidates?: MessageablePerson[];
}

export interface QueryResponse {
  answer: string;
  sources: Source[];
  expert?: Expert | null;
  expert_request_created?: boolean;
  confidence: "high" | "medium" | "low";
  routed: boolean;
  routed_reason?: string | null;
  proposed_message?: ProposedExpertMessage | null;
  proposed_email?: ProposedEmail | null;
  proposed_pull_request?: ProposedPullRequest | null;
  proposed_workspace?: ProposedWorkspace | null;
  file_changes?: FileChange[];
  status_tracker?: StatusTrackerCard | null;
  proposed_recurring_email?: ProposedRecurringEmail | null;
  proposed_meeting?: ProposedMeeting | null;
  /** True when any part of the answer came from a public web search. */
  web_search_used?: boolean;
  web_search_query?: string | null;
  web_results?: WebResult[];
  /** The chat model picked for this question's difficulty, when routing applied. */
  model_used?: string | null;
  model_tier?: "simple" | "moderate" | "complex" | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface EphemeralDocument {
  document_id: string;
  filename: string;
  text: string;
}

export async function extractFileForChat(file: File): Promise<EphemeralDocument> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch("/files/extract", { method: "POST", body: form });
  if (!res.ok) {
    let detail = `Could not read file (${res.status})`;
    try {
      const body = await res.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  const data = await res.json();
  return {
    document_id: data.document_id,
    filename: data.filename,
    text: data.text,
  };
}

export async function askQuestion(
  question: string,
  history: ChatMessage[] = [],
  ephemeralDocuments: EphemeralDocument[] = [],
  options: { attachedFileNames?: string[]; timezone?: string } = {},
): Promise<QueryResponse> {
  const res = await apiFetch("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      history,
      ephemeral_documents: ephemeralDocuments,
      attached_file_names: options.attachedFileNames ?? [],
      timezone: options.timezone ?? null,
    }),
  });
  if (!res.ok) {
    let detail = `Query failed (${res.status})`;
    try {
      const body = await res.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** Best-effort human-readable citation for a source. */
export function citationText(source: Source): string {
  if (source.citation?.label) return source.citation.label;
  const who = source.speakers.length ? source.speakers.join(", ") : "Conversation";
  const when = source.start_time ? new Date(source.start_time).toLocaleDateString() : "";
  return when ? `${who} · ${when}` : who;
}
