import { apiFetch } from "@/lib/api";

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
  sender_type: "user" | "bot" | string;
  sender_name: string;
  body: string;
  created_at: string;
}

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

export async function sendWorkspaceMessage(
  workspaceId: string,
  message: string,
): Promise<{ message: WorkspaceMessage; bot_message?: WorkspaceMessage | null }> {
  const response = await apiFetch(`/workspaces/${workspaceId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) throw new Error("Could not send the message.");
  return response.json();
}
