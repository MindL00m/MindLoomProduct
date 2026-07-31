/** GitHub PR approval API for Ask-proposed file changes. */

import { apiFetch } from "@/lib/api";
import type { ProposedPullRequest } from "@/services/ask";

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
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}
