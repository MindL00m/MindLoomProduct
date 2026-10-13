/** GitHub connector + Ask PR approval API. */

import { apiFetch } from "@/lib/api";
import type { ProposedPullRequest } from "@/services/ask";

export type GitHubCapability = "metadata" | "contents" | "pull_requests";

export interface GitHubRepoPreview {
  full_name: string;
  description?: string | null;
  private?: boolean;
  default_branch?: string;
  html_url?: string;
  language?: string | null;
}

export interface GitHubInspectResult {
  login: string;
  name?: string;
  html_url?: string;
  repositories: GitHubRepoPreview[];
}

export interface GitHubConnection {
  connected: boolean;
  provider?: string;
  account_login?: string;
  capabilities?: GitHubCapability[];
  repositories?: string[];
  selected_resource_count?: number;
  setup_status?: string;
}

export interface OpenPullRequestResult {
  status: string;
  owner: string;
  repo: string;
  path: string;
  branch: string;
  base_branch: string;
  pr_number?: number;
  pr_url?: string;
  commit_sha?: string;
}

export async function approveProposedPullRequest(
  proposal: ProposedPullRequest,
): Promise<OpenPullRequestResult> {
  const res = await apiFetch("/github/pull-requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      owner: proposal.owner,
      repo: proposal.repo,
      path: proposal.path,
      base_branch: proposal.base_branch,
      branch_name: proposal.branch_name,
      new_content: proposal.new_content,
      file_sha: proposal.file_sha ?? null,
      pr_title: proposal.pr_title,
      pr_body: proposal.pr_body ?? "",
      commit_message: proposal.commit_message || proposal.pr_title,
    }),
  });
  if (!res.ok) {
    let detail = `Could not open pull request (${res.status})`;
    try {
      const body = await res.json();
      detail = (typeof body?.detail === "string" && body.detail) || detail;
    } catch {
      if (res.status === 502 || res.status === 503) {
        detail = "The API was briefly unreachable. Try opening the pull request again.";
      }
    }
    throw new Error(detail);
  }
  return res.json();
}

async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    return fallback;
  } catch {
    return fallback;
  }
}

export async function inspectGitHubToken(token: string): Promise<GitHubInspectResult> {
  const res = await apiFetch("/github/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not inspect this GitHub token."));
  return res.json();
}

export async function getGitHubConnection(): Promise<GitHubConnection> {
  const res = await apiFetch("/github/connection");
  if (!res.ok) throw new Error(await parseError(res, "Could not load GitHub connection."));
  return res.json();
}

export async function connectGitHub(input: {
  token: string;
  capabilities: GitHubCapability[];
  repositories: string[];
}): Promise<GitHubConnection> {
  const res = await apiFetch("/github/connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not save the GitHub connection."));
  return res.json();
}

export async function updateGitHubConnection(input: {
  capabilities: GitHubCapability[];
  repositories: string[];
}): Promise<GitHubConnection> {
  const res = await apiFetch("/github/connection", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await parseError(res, "Could not update GitHub permissions."));
  return res.json();
}

export async function disconnectGitHub(): Promise<void> {
  const res = await apiFetch("/github/connection", { method: "DELETE" });
  if (!res.ok) throw new Error(await parseError(res, "Could not disconnect GitHub."));
}
