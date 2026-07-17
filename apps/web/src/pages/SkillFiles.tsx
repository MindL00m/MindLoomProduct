import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  listSkillFiles,
  reviewSkillFile,
  type SkillFile,
} from "@/services/skillFiles";

export default function SkillFiles() {
  const [skills, setSkills] = useState<SkillFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    try {
      setSkills(await listSkillFiles());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function update(skillId: string, patch: Partial<SkillFile>) {
    setSkills((rows) =>
      rows.map((row) => row.skill_id === skillId ? { ...row, ...patch } : row),
    );
  }

  async function decide(skill: SkillFile, status: "approved" | "rejected") {
    setBusy(skill.skill_id);
    setMessage(null);
    try {
      await reviewSkillFile(skill, status);
      setMessage(
        status === "approved"
          ? "Skill File approved and added to searchable knowledge."
          : "Skill File rejected. Its screenshots remain excluded from answers.",
      );
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Review failed.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <div className="flex justify-center py-16"><Loader2 className="animate-spin" /></div>;
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Skill Files</h2>
        <p className="mt-1 text-muted-foreground">
          Review workflows inferred from approved Chrome screenshot sessions.
        </p>
      </div>
      {message && <p className="rounded-md border p-3 text-sm">{message}</p>}
      {skills.length === 0 && <Card><CardContent className="p-6 text-sm text-muted-foreground">No proposed Skill Files yet.</CardContent></Card>}
      {skills.map((skill) => (
        <Card key={skill.skill_id}>
          <CardHeader>
            <CardTitle>{skill.title}</CardTitle>
            <p className="text-sm text-muted-foreground">{skill.application} · {skill.status}</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <textarea
              className="w-full rounded-md border bg-background p-3 text-sm"
              value={skill.purpose}
              onChange={(event) => update(skill.skill_id, { purpose: event.target.value })}
            />
            <ol className="space-y-2">
              {skill.steps.map((step, index) => (
                <li key={index} className="flex gap-2 text-sm">
                  <span>{index + 1}.</span>
                  <input
                    className="w-full rounded border bg-background px-2 py-1"
                    value={step}
                    onChange={(event) => {
                      const steps = [...skill.steps];
                      steps[index] = event.target.value;
                      update(skill.skill_id, { steps });
                    }}
                  />
                </li>
              ))}
            </ol>
            {skill.follow_up_questions.length > 0 && (
              <div className="rounded-md bg-muted p-3 text-sm">
                <p className="font-medium">AI follow-up questions</p>
                {skill.follow_up_questions.map((question) => <p key={question}>• {question}</p>)}
              </div>
            )}
            <textarea
              className="w-full rounded-md border bg-background p-3 text-sm"
              placeholder="Answer follow-up questions or add expert corrections"
              value={skill.expert_notes}
              onChange={(event) => update(skill.skill_id, { expert_notes: event.target.value })}
            />
            {skill.status === "proposed" && (
              <div className="flex gap-2">
                <SecondaryButton disabled={busy === skill.skill_id} onClick={() => void decide(skill, "rejected")}>Reject</SecondaryButton>
                <PrimaryButton disabled={busy === skill.skill_id} onClick={() => void decide(skill, "approved")}>
                  {busy === skill.skill_id && <Loader2 className="size-4 animate-spin" />}
                  Approve and publish
                </PrimaryButton>
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
