import { useEffect, useState } from "react";
import { Loader2, Play, Square } from "lucide-react";
import { stepRunsAutomatically, type GuideStep, type SkillFile } from "@/services/skillFiles";
import {
  getSkillRun,
  isActiveRun,
  startSkillRun,
  stopSkillRun,
  type SkillRun,
  type SkillRunWaitReason,
} from "@/services/skillRuns";
import { cn } from "@/lib/utils";

const POLL_MS = 2_000;
const OPEN_APP_HINT_AFTER_MS = 6_000;
/** Loom Capture only starts runs this recent (SkillRunWalker.maxPendingAgeSeconds). */
const PICKUP_WINDOW_MS = 10 * 60_000;

const WAIT_TEXT: Record<SkillRunWaitReason, string> = {
  no_anchor: "This step has no recorded control, so it's yours to do.",
  needs_value: "This step needs a value. Loom never types or picks values.",
  action_failed: "Loom couldn't do this step.",
  agent_offline: "The MindLoom Chrome extension didn't pick up this step.",
};

function skillSteps(skill: SkillFile): GuideStep[] {
  return skill.guide_steps?.length ? skill.guide_steps : skill.steps.map((text) => ({ text }));
}

function describe(run: SkillRun, steps: GuideStep[], now: number): { text: string; detail?: string } {
  const position = `step ${Math.min(run.step_index + 1, steps.length)} of ${steps.length}`;
  const step = steps[run.step_index];
  switch (run.status) {
    case "pending": {
      const age = now - Date.parse(run.created_at);
      if (age > PICKUP_WINDOW_MS) {
        return {
          text: "Loom Capture didn't pick this run up within 10 minutes, so it won't start.",
          detail: "Stop it and run again with the Mac app open.",
        };
      }
      if (age > OPEN_APP_HINT_AFTER_MS) {
        return {
          text: "Waiting for the MindLoom Mac app.",
          detail: "Open Loom Capture on your Mac and sign in. It starts the run within a few seconds.",
        };
      }
      return { text: "Starting…" };
    }
    case "running":
      return {
        text: `Running ${position}${step ? `: ${step.text}` : ""}`,
        detail: step?.browser_anchor
          ? "The MindLoom Chrome extension is doing this step."
          : "Loom Capture is doing this step.",
      };
    case "waiting":
      return {
        text: `Paused at ${position}${step ? `: ${step.text}` : ""}`,
        detail: [
          run.wait_reason ? WAIT_TEXT[run.wait_reason] : "",
          run.error ?? "",
          "Click Next in the Loom Capture overlay when it's done.",
        ]
          .filter(Boolean)
          .join(" "),
      };
    case "completed":
      return { text: "Run complete." };
    case "stopped":
      return { text: "Run stopped." };
    default:
      return { text: `Run failed: ${run.error || "unknown error"}` };
  }
}

/**
 * Starts and watches a run of an approved skill. The web app never performs a step:
 * Loom Capture (Mac) walks the run and the Chrome extension does browser steps.
 */
export function SkillRunControls({ skill, initialRun }: { skill: SkillFile; initialRun?: SkillRun | null }) {
  const [run, setRun] = useState<SkillRun | null>(initialRun ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  // Adopt a run found on refresh (e.g. started in another tab) unless one is live here.
  useEffect(() => {
    setRun((current) =>
      initialRun && initialRun.run_id !== current?.run_id && !isActiveRun(current) ? initialRun : current,
    );
  }, [initialRun]);

  const active = isActiveRun(run);
  const runId = run?.run_id;
  useEffect(() => {
    if (!active || !runId) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      getSkillRun(runId)
        .then(setRun)
        .catch(() => {
          /* keep the last known state; the next poll retries */
        });
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, runId]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const created = await startSkillRun(skill.skill_id);
      setNow(Date.now());
      setRun(await getSkillRun(created.run_id).catch(() => created));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the run.");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      setRun({ ...run, ...(await stopSkillRun(run.run_id)) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not stop the run.");
    } finally {
      setBusy(false);
    }
  }

  const steps = run?.guide?.steps ?? skillSteps(skill);
  const automatic = steps.filter(stepRunsAutomatically).length;
  const status = run ? describe(run, steps, now) : null;

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        {active ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void stop()}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Square className="size-3.5" />}
            Stop run
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => void start()}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run
          </button>
        )}
        <span className="text-[11px] text-muted-foreground">
          {automatic
            ? `${automatic} of ${steps.length} step${steps.length === 1 ? "" : "s"} run automatically; the rest wait for your Next.`
            : "Loom Capture shows each step; you click Next."}
        </span>
      </div>
      {status && (
        <div
          className={cn(
            "mt-2 rounded-md border px-3 py-2 text-xs",
            run?.status === "failed"
              ? "border-destructive/20 bg-destructive/10 text-destructive"
              : run?.status === "waiting" || (run?.status === "pending" && status.detail)
                ? "border-amber-200 bg-amber-50 text-amber-950"
                : "border-border bg-muted/40 text-foreground",
          )}
          role="status"
        >
          <p className="font-medium">{status.text}</p>
          {status.detail && <p className="mt-0.5 opacity-80">{status.detail}</p>}
        </div>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
