import { apiFetch } from "@/lib/api";
import type { GuideStep } from "@/services/skillFiles";

export type SkillRunStatus = "pending" | "running" | "waiting" | "completed" | "stopped" | "failed";
export type SkillRunWaitReason = "no_anchor" | "needs_value" | "action_failed" | "agent_offline";

/** A run of an approved Skill File. The web app only starts, watches, and stops it. */
export interface SkillRun {
  run_id: string;
  skill_id: string;
  status: SkillRunStatus;
  step_index: number;
  wait_reason: SkillRunWaitReason | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  guide?: { title: string; steps: GuideStep[] } | null;
}

export function isActiveRun(run: SkillRun | null | undefined): run is SkillRun {
  return Boolean(run) && ["pending", "running", "waiting"].includes(run!.status);
}

async function runRequest(path: string, init?: RequestInit): Promise<SkillRun> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    let detail = "Could not reach the run.";
    try {
      const body = await response.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep */
    }
    throw new Error(detail);
  }
  return response.json();
}

export function startSkillRun(skillId: string): Promise<SkillRun> {
  return runRequest("/captures/skill-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill_id: skillId }),
  });
}

export function getSkillRun(runId: string): Promise<SkillRun> {
  return runRequest(`/captures/skill-runs/${encodeURIComponent(runId)}`);
}

export function stopSkillRun(runId: string): Promise<SkillRun> {
  return runRequest(`/captures/skill-runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
}

export async function listSkillRuns(): Promise<SkillRun[]> {
  const response = await apiFetch("/captures/skill-runs");
  if (!response.ok) throw new Error("Could not load skill runs.");
  return response.json();
}
