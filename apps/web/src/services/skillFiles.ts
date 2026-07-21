import { apiFetch } from "@/lib/api";

export interface SkillFile {
  skill_id: string;
  session_id: string;
  title: string;
  purpose: string;
  application: string;
  context: string[];
  steps: string[];
  important_fields: string[];
  warnings: string[];
  decision_guidance: string[];
  follow_up_questions: string[];
  source_capture_ids: string[];
  status: "proposed" | "approved" | "rejected";
  expert_notes: string;
  updated_at: string;
  created_at?: string;
}

export function isExtensionSkill(skill: SkillFile): boolean {
  return !skill.session_id.startsWith("expert-request:");
}

export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "needs_input";

export interface WorkflowRunStep {
  text: string;
  screenshot_url: string | null;
}

export interface WorkflowRun {
  run_id: string;
  skill_id: string;
  skill_title: string;
  application: string;
  status: WorkflowRunStatus;
  model: string;
  browser_profile: string;
  prompt: string;
  result_text: string;
  summary: string;
  stop_reason: string;
  steps: WorkflowRunStep[];
  screenshots: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

export function isRunTerminal(status: WorkflowRunStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "needs_input";
}

export async function runWorkflow(skillId: string): Promise<WorkflowRun> {
  const response = await apiFetch(`/workflows/${skillId}/runs`, { method: "POST" });
  if (!response.ok) {
    let detail = "Could not start this workflow run.";
    try {
      const body = await response.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function getWorkflowRun(runId: string): Promise<WorkflowRun> {
  const response = await apiFetch(`/workflows/runs/${runId}`);
  if (!response.ok) throw new Error("Could not load the workflow run.");
  return response.json();
}

export async function listWorkflowRuns(skillId: string): Promise<WorkflowRun[]> {
  const response = await apiFetch(`/workflows/${skillId}/runs`);
  if (!response.ok) throw new Error("Could not load workflow runs.");
  return response.json();
}

export async function listSkillFiles(): Promise<SkillFile[]> {
  const response = await apiFetch("/captures/skill-files");
  if (!response.ok) throw new Error("Could not load Skill Files.");
  return response.json();
}

export async function updateSkillFile(
  skillId: string,
  patch: {
    title?: string;
    purpose?: string;
    application?: string;
    expert_notes?: string;
  },
): Promise<SkillFile> {
  const response = await apiFetch(`/captures/skill-files/${skillId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    let detail = "Could not update this Skill File.";
    try {
      const body = await response.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function reviewSkillFile(
  skill: SkillFile,
  status: "approved" | "rejected",
): Promise<SkillFile> {
  const response = await apiFetch(`/captures/skill-files/${skill.skill_id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status,
      title: skill.title,
      purpose: skill.purpose,
      steps: skill.steps,
      important_fields: skill.important_fields,
      warnings: skill.warnings,
      decision_guidance: skill.decision_guidance,
      expert_notes: skill.expert_notes,
    }),
  });
  if (!response.ok) throw new Error("Could not review this Skill File.");
  return response.json();
}
