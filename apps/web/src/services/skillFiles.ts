import { apiFetch } from "@/lib/api";

export type SkillSource = "browser" | "desktop_ax" | "expert";

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
  source?: SkillSource;
  status: "proposed" | "approved" | "rejected";
  expert_notes: string;
  updated_at: string;
  created_at?: string;
}

/** Workflow skills from browser extension or desktop AX agent (excludes expert Q&A). */
export function isExtensionSkill(skill: SkillFile): boolean {
  if (skill.source === "expert") return false;
  return !skill.session_id.startsWith("expert-request:");
}

export function skillSourceLabel(skill: SkillFile): string {
  if (skill.source === "desktop_ax") return "Desktop";
  if (skill.source === "expert") return "Expert";
  return "Browser";
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
