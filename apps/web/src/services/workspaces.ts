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

export async function listWorkspaces(): Promise<Workspace[]> {
  const response = await apiFetch("/workspaces");
  if (!response.ok) throw new Error("Could not load workspaces.");
  return response.json();
}

export async function createWorkspace(
  name: string,
  memberUserIds: string[] = [],
): Promise<Workspace> {
  const response = await apiFetch("/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, member_user_ids: memberUserIds }),
  });
  if (!response.ok) throw new Error("Could not create the workspace.");
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
