import { apiFetch } from "@/lib/api";
import type { QueryResponse } from "@/services/ask";

export interface Workspace {
  workspace_id: string;
  name: string;
  kind: "org_wide" | "group" | string;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
  member_count: number;
  last_message?: string | null;
  last_message_at?: string | null;
  last_sender_name?: string | null;
  purpose?: string | null;
  context_md?: string | null;
  context_synced_at?: string | null;
  loombot_mode?: "context_only" | "org_knowledge" | string;
}

export interface WorkspaceMessage {
  message_id: string;
  sender_user_id?: string | null;
  sender_type: "user" | "bot" | "system" | string;
  sender_name: string;
  body: string;
  created_at: string;
  /**
   * For Loombot replies: the full Ask response for the member who asked. Everyone
   * else gets a shared view (answer, web results, and one-line card summaries),
   * because sources and drafts can come from the asker's private mail and files.
   */
  payload?: LoombotPayload | null;
  /** The member who asked Loombot; only they can act on the reply's cards. */
  requested_by?: string | null;
  /** What the asker did on the cards (sent, scheduled, undone); only sent to the asker. */
  card_state?: Record<string, unknown> | null;
}

export type LoombotPayload = QueryResponse & {
  /** Set on the shared view other members receive. */
  shared_view?: boolean;
  card_summaries?: string[];
  /** What the asker did on the cards, e.g. "meeting scheduled". */
  card_outcomes?: string[];
};

export interface WorkspaceMember {
  user_id: string;
  name: string;
  email: string;
  role: string;
}

export interface CreateWorkspaceInput {
  name: string;
  member_user_ids?: string[];
  purpose?: string | null;
  context_md?: string | null;
  loombot_mode?: "context_only" | "org_knowledge" | string | null;
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const response = await apiFetch("/workspaces");
  if (!response.ok) throw new Error("Could not load workspaces.");
  return response.json();
}

export async function createWorkspace(
  nameOrInput: string | CreateWorkspaceInput,
  memberUserIds: string[] = [],
): Promise<Workspace> {
  const body: CreateWorkspaceInput =
    typeof nameOrInput === "string"
      ? { name: nameOrInput, member_user_ids: memberUserIds }
      : {
          name: nameOrInput.name,
          member_user_ids: nameOrInput.member_user_ids ?? [],
          purpose: nameOrInput.purpose,
          context_md: nameOrInput.context_md,
          loombot_mode: nameOrInput.loombot_mode,
        };
  const response = await apiFetch("/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "Could not create the workspace.";
    try {
      const payload = await response.json();
      detail = (payload?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function resyncWorkspaceContext(
  workspaceId: string,
): Promise<Workspace> {
  const response = await apiFetch(`/workspaces/${workspaceId}/resync-context`, {
    method: "POST",
  });
  if (!response.ok) {
    let detail = "Could not resync CONTEXT.md.";
    try {
      const payload = await response.json();
      detail = (payload?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function listWorkspaceMessages(
  workspaceId: string,
): Promise<WorkspaceMessage[]> {
  const response = await apiFetch(`/workspaces/${workspaceId}/messages`);
  if (!response.ok) throw new Error("Could not load workspace messages.");
  return response.json();
}

export async function listWorkspaceMembers(
  workspaceId: string,
): Promise<WorkspaceMember[]> {
  const response = await apiFetch(`/workspaces/${workspaceId}/members`);
  if (!response.ok) throw new Error("Could not load workspace members.");
  return response.json();
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json();
    return typeof payload?.detail === "string" ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}

export async function sendWorkspaceMessage(
  workspaceId: string,
  message: string,
  options: { timezone?: string; attachedFileNames?: string[] } = {},
): Promise<{ message: WorkspaceMessage; bot_message?: WorkspaceMessage | null }> {
  const response = await apiFetch(`/workspaces/${workspaceId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      timezone: options.timezone ?? null,
      attached_file_names: options.attachedFileNames ?? [],
    }),
  });
  if (!response.ok) throw new Error(await errorDetail(response, "Could not send the message."));
  return response.json();
}

/** Add signed-in colleagues to an existing workspace. */
export async function addWorkspaceMembers(
  workspaceId: string,
  memberUserIds: string[],
): Promise<{ added: WorkspaceMember[]; already_members: string[]; members: WorkspaceMember[] }> {
  const response = await apiFetch(`/workspaces/${workspaceId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_user_ids: memberUserIds }),
  });
  if (!response.ok) throw new Error(await errorDetail(response, "Could not add members."));
  return response.json();
}

export interface WorkspaceInvite {
  invite_id: string;
  email: string;
  created_at: string | null;
  invited_by?: string | null;
}

export interface InviteByEmailResult {
  added: WorkspaceMember[];
  already_members: string[];
  invited: string[];
  already_invited: string[];
  outside_organization: string[];
  invalid: string[];
  invite_emails_sent: number;
  members: WorkspaceMember[];
  invites: WorkspaceInvite[];
}

/**
 * Add people by email. Signed-in colleagues join now; colleagues on the company
 * domain who have not signed in yet get an invite and join on first sign-in.
 */
export async function addWorkspaceMembersByEmail(
  workspaceId: string,
  emails: string[],
  sendEmail = true,
): Promise<InviteByEmailResult> {
  const response = await apiFetch(`/workspaces/${workspaceId}/members/by-email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ emails, send_email: sendEmail }),
  });
  if (!response.ok) throw new Error(await errorDetail(response, "Could not add people."));
  return response.json();
}

export async function listWorkspaceInvites(workspaceId: string): Promise<WorkspaceInvite[]> {
  const response = await apiFetch(`/workspaces/${workspaceId}/invites`);
  if (!response.ok) throw new Error("Could not load invites.");
  return response.json();
}

export async function cancelWorkspaceInvite(
  workspaceId: string,
  inviteId: string,
): Promise<{ invites: WorkspaceInvite[] }> {
  const response = await apiFetch(
    `/workspaces/${workspaceId}/invites/${encodeURIComponent(inviteId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(await errorDetail(response, "Could not cancel the invite."));
  return response.json();
}

/** Leave the workspace (your own id) or, as its creator, remove someone. */
export async function removeWorkspaceMember(
  workspaceId: string,
  memberUserId: string,
): Promise<{ left: boolean; members: WorkspaceMember[] }> {
  const response = await apiFetch(
    `/workspaces/${workspaceId}/members/${encodeURIComponent(memberUserId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(await errorDetail(response, "Could not remove the member."));
  return response.json();
}

/** Save what the asker did on a Loombot reply's cards, so every member sees it. */
export async function saveCardState(
  workspaceId: string,
  messageId: string,
  state: Record<string, unknown>,
): Promise<WorkspaceMessage> {
  const response = await apiFetch(
    `/workspaces/${workspaceId}/messages/${encodeURIComponent(messageId)}/card-state`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state }),
    },
  );
  if (!response.ok) throw new Error(await errorDetail(response, "Could not save the card."));
  return response.json();
}
