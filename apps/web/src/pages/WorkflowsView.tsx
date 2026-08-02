import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  Eye,
  Loader2,
  RefreshCw,
  Workflow,
  X,
} from "lucide-react";
import {
  isExtensionSkill,
  listSkillFiles,
  reviewSkillFile,
  skillSourceLabel,
  updateSkillFile,
  type SkillFile,
} from "@/services/skillFiles";
import { cn } from "@/lib/utils";

function statusStyles(status: SkillFile["status"]): string {
  if (status === "approved") return "bg-emerald-50 text-emerald-800 border-emerald-200";
  if (status === "rejected") return "bg-destructive/10 text-destructive border-destructive/20";
  return "bg-amber-50 text-amber-900 border-amber-200";
}

function summarize(skill: SkillFile): string {
  const purpose = skill.purpose?.trim();
  if (purpose) return purpose;
  if (skill.steps.length > 0) {
    return `Workflow with ${skill.steps.length} step${skill.steps.length === 1 ? "" : "s"}.`;
  }
  if (skill.source === "desktop_ax") {
    return "Captured desktop workflow awaiting review.";
  }
  return "Captured browser workflow awaiting review.";
}

function formatSkillDocument(skill: SkillFile): string {
  const lines = [
    `# ${skill.title}`,
    "",
    `Status: ${skill.status}`,
    `Source: ${skillSourceLabel(skill)}`,
    `Application: ${skill.application || "—"}`,
    `Session: ${skill.session_id}`,
    `Updated: ${new Date(skill.updated_at).toLocaleString()}`,
    "",
    "## Purpose",
    skill.purpose || "—",
    "",
    "## Context",
    ...(skill.context.length ? skill.context.map((item) => `- ${item}`) : ["—"]),
    "",
    "## Steps",
    ...(skill.steps.length
      ? skill.steps.map((step, index) => `${index + 1}. ${step}`)
      : ["—"]),
    "",
    "## Important fields",
    ...(skill.important_fields.length
      ? skill.important_fields.map((item) => `- ${item}`)
      : ["—"]),
    "",
    "## Warnings",
    ...(skill.warnings.length ? skill.warnings.map((item) => `- ${item}`) : ["—"]),
    "",
    "## Decision guidance",
    ...(skill.decision_guidance.length
      ? skill.decision_guidance.map((item) => `- ${item}`)
      : ["—"]),
    "",
    "## Follow-up questions",
    ...(skill.follow_up_questions.length
      ? skill.follow_up_questions.map((item) => `- ${item}`)
      : ["—"]),
    "",
    "## Expert notes",
    skill.expert_notes || "—",
    "",
    "## Source captures",
    skill.source_capture_ids.join(", ") || "—",
  ];
  return lines.join("\n");
}

export default function WorkflowsView() {
  const [skills, setSkills] = useState<SkillFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [draftNames, setDraftNames] = useState<Record<string, string>>({});
  const [viewing, setViewing] = useState<SkillFile | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listSkillFiles();
      const extensionSkills = rows.filter(isExtensionSkill);
      setSkills(extensionSkills);
      setDraftNames(
        Object.fromEntries(extensionSkills.map((skill) => [skill.skill_id, skill.title])),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load workflows.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const proposedCount = useMemo(
    () => skills.filter((skill) => skill.status === "proposed").length,
    [skills],
  );

  async function saveName(skill: SkillFile) {
    const nextTitle = (draftNames[skill.skill_id] ?? skill.title).trim();
    if (!nextTitle || nextTitle === skill.title) return;
    setBusyId(skill.skill_id);
    setError(null);
    try {
      const updated = await updateSkillFile(skill.skill_id, { title: nextTitle });
      setSkills((rows) =>
        rows.map((row) => (row.skill_id === updated.skill_id ? updated : row)),
      );
      setDraftNames((names) => ({ ...names, [updated.skill_id]: updated.title }));
      if (viewing?.skill_id === updated.skill_id) setViewing(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename skill.");
    } finally {
      setBusyId(null);
    }
  }

  async function review(skill: SkillFile, status: "approved" | "rejected") {
    const titled = {
      ...skill,
      title: (draftNames[skill.skill_id] ?? skill.title).trim() || skill.title,
    };
    setBusyId(skill.skill_id);
    setError(null);
    try {
      if (titled.title !== skill.title) {
        await updateSkillFile(skill.skill_id, { title: titled.title });
      }
      const updated = await reviewSkillFile(titled, status);
      setSkills((rows) =>
        rows.map((row) => (row.skill_id === updated.skill_id ? updated : row)),
      );
      setDraftNames((names) => ({ ...names, [updated.skill_id]: updated.title }));
      if (viewing?.skill_id === updated.skill_id) setViewing(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not review skill.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading && skills.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading workflows…
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Workflows</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Name and review Skill Files from the desktop capture agent or browser extension.
            {proposedCount > 0
              ? ` ${proposedCount} awaiting approval.`
              : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm text-foreground hover:bg-muted"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {skills.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-6 py-16 text-center">
          <Workflow className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium text-foreground">No workflow skills yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            End a desktop capture session with “Create Skill File”, or create one from the Chrome extension.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((skill) => {
            const busy = busyId === skill.skill_id;
            const draftName = draftNames[skill.skill_id] ?? skill.title;
            const dirty = draftName.trim() !== skill.title;
            return (
              <article
                key={skill.skill_id}
                className="flex flex-col rounded-lg border border-border bg-card p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize",
                        statusStyles(skill.status),
                      )}
                    >
                      {skill.status}
                    </span>
                    <span className="rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                      {skillSourceLabel(skill)}
                    </span>
                  </div>
                  <span className="text-[11px] text-muted-foreground">
                    {skill.application || skillSourceLabel(skill)}
                  </span>
                </div>

                <label className="mt-3 block text-xs font-medium text-muted-foreground">
                  Skill name
                  <input
                    value={draftName}
                    onChange={(event) =>
                      setDraftNames((names) => ({
                        ...names,
                        [skill.skill_id]: event.target.value,
                      }))
                    }
                    disabled={busy}
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-semibold text-foreground outline-none focus:border-primary"
                  />
                </label>

                <p className="mt-3 line-clamp-4 flex-1 text-sm leading-relaxed text-muted-foreground">
                  {summarize(skill)}
                </p>

                <p className="mt-2 text-xs text-muted-foreground">
                  {skill.steps.length} step{skill.steps.length === 1 ? "" : "s"}
                  {skill.source_capture_ids.length
                    ? ` · ${skill.source_capture_ids.length} capture${skill.source_capture_ids.length === 1 ? "" : "s"}`
                    : ""}
                </p>

                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy || !dirty}
                    onClick={() => void saveName(skill)}
                    className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground disabled:opacity-40"
                  >
                    Save name
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setViewing(skill)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground"
                  >
                    <Eye className="size-3.5" />
                    View full skill file
                  </button>
                </div>

                {skill.status === "proposed" && (
                  <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void review(skill, "approved")}
                      className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                    >
                      {busy ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Check className="size-3.5" />
                      )}
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void review(skill, "rejected")}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground disabled:opacity-50"
                    >
                      <X className="size-3.5" />
                      Reject
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {viewing && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-foreground/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Full skill file"
          onClick={() => setViewing(null)}
        >
          <div
            className="flex max-h-[85dvh] w-full max-w-3xl flex-col rounded-lg border border-border bg-background shadow-lg"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold text-foreground">{viewing.title}</h3>
                <p className="text-xs capitalize text-muted-foreground">{viewing.status}</p>
              </div>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setViewing(null)}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-4 py-4 font-mono text-xs leading-relaxed text-foreground">
              {formatSkillDocument(viewing)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
