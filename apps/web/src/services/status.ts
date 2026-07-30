/** Status board API: open projects, reports, and action items. */

import { apiFetch } from "@/lib/api";

export type StatusItemKind = "project" | "issue" | "action_item";

export interface StatusEvidence {
  chunk_id: string;
  summary: string;
  source: string;
  source_label: string;
  knowledge_type?: string;
  end_time?: string | null;
  excerpt?: string;
}

export interface StatusProject {
  entity_id: string;
  name: string;
  work_status: "open" | "closed";
  current_status?: string;
  last_signal_at?: string | null;
  closed_at?: string | null;
  recent_updates?: StatusEvidence[];
  evidence: StatusEvidence[];
}

export interface StatusIssue {
  issue_id: string;
  title: string;
  kind: "problem_report" | "status_update";
  status: "open" | "closed";
  project?: string | null;
  created_at?: string | null;
  last_seen_at?: string | null;
  closed_at?: string | null;
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
  closed_at?: string | null;
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

export async function finishStatusItem(
  kind: StatusItemKind,
  itemId: string,
): Promise<void> {
  const res = await apiFetch(
    `/status/${kind}/${encodeURIComponent(itemId)}/finish`,
    { method: "POST" },
  );
  if (!res.ok) {
    let detail = `Could not mark item finished (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* ignore non-JSON */
    }
    throw new Error(detail);
  }
}
