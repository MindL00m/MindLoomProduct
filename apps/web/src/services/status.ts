/** Status board API: open projects, reports, and action items. */

import { apiFetch } from "@/lib/api";

export interface StatusEvidence {
  chunk_id: string;
  summary: string;
  source: string;
  source_label: string;
}

export interface StatusProject {
  entity_id: string;
  name: string;
  work_status: "open" | "closed";
  last_signal_at?: string | null;
  evidence: StatusEvidence[];
}

export interface StatusIssue {
  issue_id: string;
  title: string;
  kind: "problem_report" | "status_update";
  status: "open" | "closed";
  project?: string | null;
  last_seen_at?: string | null;
  evidence: StatusEvidence[];
}

export interface StatusActionItem {
  action_item_id: string;
  text: string;
  status: "open" | "done" | "cancelled";
  assignee?: string | null;
  project?: string | null;
  created_at?: string | null;
  last_signal_at?: string | null;
  evidence: StatusEvidence[];
}

export interface OpenStatus {
  projects: StatusProject[];
  issues: StatusIssue[];
  action_items: StatusActionItem[];
}

export async function getOpenStatus(): Promise<OpenStatus> {
  const res = await apiFetch("/status/open");
  if (!res.ok) {
    throw new Error(`Could not load status board (${res.status})`);
  }
  return res.json();
}
