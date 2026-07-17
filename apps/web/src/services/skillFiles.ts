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
}

export async function listSkillFiles(): Promise<SkillFile[]> {
  const response = await apiFetch("/captures/skill-files");
  if (!response.ok) throw new Error("Could not load Skill Files.");
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
