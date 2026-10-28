import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Check,
  CircleDashed,
  Clock,
  Hand,
  Loader2,
  MessageCircleQuestion,
  Monitor,
  Play,
  RefreshCw,
  Settings2,
  Square,
  X,
  Zap,
} from "lucide-react";
import {
  ACTIVE_RUN,
  getAutomation,
  getAutomationRun,
  prepareAutomation,
  replyToAutomationRun,
  startAutomationRun,
  stopAutomationRun,
  updateAutomation,
  type Automation,
  type AutomationLogEntry,
  type AutomationRun,
} from "@/services/automations";
import type { SkillFile } from "@/services/skillFiles";
import { cn } from "@/lib/utils";

const POLL_MS = 2_000;

const TOOL_LABEL: Record<string, string> = {
  send_email: "Send email",
  create_meeting: "Book meeting",
  http_request: "Call web API",
  update_spreadsheet: "Update spreadsheet",
  calculate: "Calculate",
  search_knowledge: "Search knowledge",
  search_emails: "Search email",
  read_file: "Read file",
  write_document: "Write file",
  write_csv_file: "Write CSV",
  fill_template: "Fill template",
  create_shipment_documents: "Shipment documents",
  web_search: "Web search",
  ask_user: "Ask you",
  hand_off_to_device: "On your computer",
  finish: "Finish",
};

function toolLabel(tool?: string): string {
  if (!tool) return "Step";
  return TOOL_LABEL[tool] ?? tool.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * "Run with MindLoom": MindLoom performs the Skill File itself with its toolbox
 * (knowledge, email, files and spreadsheets, templates, calendar, web, web APIs).
 * Outbound actions wait for Approve; missing values are asked for; steps that
 * need clicking in another app go to the Mac app or Chrome extension.
 */
export function SkillAutomation({ skill, isOwner }: { skill: SkillFile; isOwner: boolean }) {
  const [open, setOpen] = useState(false);
  const [automation, setAutomation] = useState<Automation | null>(null);
  const [run, setRun] = useState<AutomationRun | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "plan" | "start" | "reply" | "settings">("");
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);

  const load = useCallback(async () => {
    const data = await getAutomation(skill.skill_id);
    setAutomation(data.automation);
    if (data.automation) setInputs((cur) => ({ ...data.automation!.default_inputs, ...cur }));
    const latest = data.runs[0];
    if (latest && ACTIVE_RUN.includes(latest.status)) setRun(await getAutomationRun(latest.run_id));
  }, [skill.skill_id]);

  useEffect(() => {
    if (open) void load().catch((err: Error) => setError(err.message));
  }, [open, load]);

  const active = Boolean(run && ACTIVE_RUN.includes(run.status));
  useEffect(() => {
    if (!active || !run) return;
    const timer = window.setInterval(() => {
      void getAutomationRun(run.run_id).then(setRun).catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, run?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act<T>(kind: typeof busy, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(kind);
    setError("");
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      return undefined;
    } finally {
      setBusy("");
    }
  }

  const plan = automation?.plan;
  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground"
        >
          <Zap className="size-3.5" /> Run with MindLoom
        </button>
        {active && !open && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" /> running
          </span>
        )}
      </div>

      {open && (
        <div className="mt-3 space-y-3 rounded-md border border-border bg-muted/30 p-3 text-sm">
          {!automation ? (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                MindLoom reads this skill and works out which of its tools does each step (email,
                files and spreadsheets, templates, calendar, web, web APIs). You see the plan before
                anything runs.
              </p>
              <button
                type="button"
                disabled={busy !== ""}
                onClick={() => void act("plan", async () => setAutomation(await prepareAutomation(skill.skill_id)))}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
              >
                {busy === "plan" ? <Loader2 className="size-3.5 animate-spin" /> : <Bot className="size-3.5" />}
                Plan the automation
              </button>
            </div>
          ) : (
            <>
              <div>
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium">{plan?.summary || "Automation plan"}</p>
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
                      plan?.automatable === "full"
                        ? "bg-emerald-100 text-emerald-800"
                        : plan?.automatable === "manual"
                          ? "bg-muted text-muted-foreground"
                          : "bg-amber-100 text-amber-800",
                    )}
                  >
                    {plan?.automatable === "full" ? "Fully automatic" : plan?.automatable === "manual" ? "Mostly manual" : "Partly automatic"}
                  </span>
                </div>
                <ol className="mt-2 space-y-1 text-xs">
                  {plan?.steps.map((s, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="w-4 shrink-0 text-muted-foreground">{i + 1}.</span>
                      <span className="flex-1">
                        {s.step}
                        <span className="ml-1 rounded bg-background px-1.5 py-0.5 text-[10px] font-medium text-brand-700">
                          {toolLabel(s.tool)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
                {plan?.notes && <p className="mt-1 text-xs text-muted-foreground">{plan.notes}</p>}
              </div>

              {plan && plan.inputs.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {plan.inputs.map((input) => (
                    <label key={input.name} className="block text-xs font-medium">
                      {input.label}
                      {input.required && <span className="text-destructive"> *</span>}
                      <input
                        value={inputs[input.name] ?? ""}
                        onChange={(e) => setInputs((cur) => ({ ...cur, [input.name]: e.target.value }))}
                        placeholder={input.example || input.description}
                        title={input.description}
                        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm font-normal"
                      />
                    </label>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy !== "" || active}
                  onClick={() =>
                    void act("start", async () => setRun(await startAutomationRun(skill.skill_id, inputs)))
                  }
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {busy === "start" ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                  Run now
                </button>
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => void act("plan", async () => setAutomation(await prepareAutomation(skill.skill_id)))}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium"
                >
                  <RefreshCw className="size-3.5" /> Re-plan
                </button>
                <button
                  type="button"
                  onClick={() => setShowSettings((v) => !v)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium"
                >
                  <Settings2 className="size-3.5" /> Schedule & approvals
                </button>
              </div>

              {showSettings && (
                <AutomationSettings
                  automation={automation}
                  isOwner={isOwner}
                  inputs={inputs}
                  busy={busy === "settings"}
                  onSave={(settings) =>
                    void act("settings", async () => setAutomation(await updateAutomation(skill.skill_id, settings)))
                  }
                />
              )}
            </>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}
          {run && (
            <RunView
              run={run}
              busy={busy === "reply"}
              onReply={(reply) => void act("reply", async () => setRun(await replyToAutomationRun(run.run_id, reply)))}
              onStop={() => void act("reply", async () => setRun(await stopAutomationRun(run.run_id)))}
            />
          )}
        </div>
      )}
    </div>
  );
}

function AutomationSettings({
  automation,
  isOwner,
  inputs,
  busy,
  onSave,
}: {
  automation: Automation;
  isOwner: boolean;
  inputs: Record<string, string>;
  busy: boolean;
  onSave: (settings: Parameters<typeof updateAutomation>[1]) => void;
}) {
  const [every, setEvery] = useState(String(automation.interval_value ?? 1));
  const [unit, setUnit] = useState(automation.interval_unit ?? "days");
  const scheduled = Boolean(automation.next_run_at);
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3 text-xs">
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={automation.auto_approve}
          disabled={!isOwner || busy}
          onChange={(e) => onSave({ auto_approve: e.target.checked })}
          className="mt-0.5"
        />
        <span>
          Don't ask me to approve emails, meetings and web API changes
          <span className="block text-muted-foreground">
            {isOwner
              ? "Only for Skill Files you created. Every action is still logged."
              : "Only the person who created this Skill File can turn this on."}
          </span>
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Clock className="size-3.5 text-muted-foreground" />
        <span>Run every</span>
        <input
          value={every}
          onChange={(e) => setEvery(e.target.value.replace(/\D/g, ""))}
          className="w-14 rounded border border-border px-1.5 py-1"
        />
        <select value={unit} onChange={(e) => setUnit(e.target.value)} className="rounded border border-border px-1.5 py-1">
          {["minutes", "hours", "days", "weeks"].map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            onSave({ schedule: true, interval_value: Number(every) || 1, interval_unit: unit, default_inputs: inputs })
          }
          className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50"
        >
          {scheduled ? "Update schedule" : "Schedule"}
        </button>
        {scheduled && (
          <button type="button" disabled={busy} onClick={() => onSave({ schedule: false })} className="rounded-md border border-border px-2.5 py-1">
            Stop schedule
          </button>
        )}
      </div>
      {scheduled && (
        <p className="text-muted-foreground">
          Next run {new Date(automation.next_run_at!).toLocaleString()}, with the inputs above. Scheduled
          runs still wait for your approval unless auto-approve is on.
        </p>
      )}
    </div>
  );
}

const STATUS_TEXT: Record<AutomationRun["status"], string> = {
  queued: "Starting…",
  running: "MindLoom is working…",
  needs_approval: "Waiting for your approval",
  needs_input: "MindLoom has a question",
  waiting_device: "Waiting for steps on your computer",
  completed: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

function RunView({
  run,
  busy,
  onReply,
  onStop,
}: {
  run: AutomationRun;
  busy: boolean;
  onReply: (reply: Parameters<typeof replyToAutomationRun>[1]) => void;
  onStop: () => void;
}) {
  const active = ACTIVE_RUN.includes(run.status);
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold">
          {active ? <Loader2 className="size-3.5 animate-spin" /> : run.status === "completed" ? <Check className="size-3.5 text-emerald-600" /> : <AlertTriangle className="size-3.5 text-amber-600" />}
          {STATUS_TEXT[run.status]}
        </p>
        {active && (
          <button type="button" disabled={busy} onClick={onStop} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive">
            <Square className="size-3" /> Stop
          </button>
        )}
      </div>
      <ol className="max-h-64 space-y-1 overflow-y-auto text-xs">
        {run.log.map((entry, i) => (
          <LogLine key={i} entry={entry} />
        ))}
      </ol>
      {run.status === "needs_approval" && run.pending && <ApprovalCard run={run} busy={busy} onReply={onReply} />}
      {run.status === "needs_input" && run.pending && <QuestionCard question={run.pending.question ?? ""} busy={busy} onReply={onReply} />}
      {run.status === "waiting_device" && (
        <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs">
          <p className="flex items-start gap-1.5">
            <Monitor className="mt-0.5 size-3.5 shrink-0" />
            <span>
              Do these steps on your computer: <b>{run.pending?.steps}</b>
              {run.pending?.device_run_id
                ? " The MindLoom Mac app or Chrome extension will guide you through them."
                : ""}
            </span>
          </p>
          <button type="button" disabled={busy} onClick={() => onReply({ action: "continue" })} className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground">
            Done, continue
          </button>
        </div>
      )}
      {run.summary && !active && <p className="text-xs">{run.summary}</p>}
      {run.error && <p className="text-xs text-destructive">{run.error}</p>}
    </div>
  );
}

function LogLine({ entry }: { entry: AutomationLogEntry }) {
  const Icon =
    entry.status === "pending"
      ? entry.kind === "input"
        ? MessageCircleQuestion
        : entry.kind === "device"
          ? Monitor
          : Hand
      : entry.status === "ok"
        ? Check
        : entry.status === "rejected"
          ? X
          : entry.kind === "message"
            ? CircleDashed
            : AlertTriangle;
  return (
    <li className="flex gap-1.5">
      <Icon
        className={cn(
          "mt-0.5 size-3 shrink-0",
          entry.status === "ok" ? "text-emerald-600" : entry.status === "error" ? "text-destructive" : "text-muted-foreground",
        )}
      />
      <span className="min-w-0 flex-1">
        <b>{entry.kind === "message" ? "Note" : toolLabel(entry.tool)}</b>{" "}
        <span className="break-words text-muted-foreground">{entry.summary}</span>
      </span>
    </li>
  );
}

function ApprovalCard({
  run,
  busy,
  onReply,
}: {
  run: AutomationRun;
  busy: boolean;
  onReply: (reply: Parameters<typeof replyToAutomationRun>[1]) => void;
}) {
  const [args, setArgs] = useState<Record<string, unknown>>(run.pending?.arguments ?? {});
  const tool = run.pending?.tool;
  const editable = Object.entries(args).filter(([, v]) => typeof v === "string" || typeof v === "number");
  return (
    <div className="space-y-2 rounded-md border-2 border-primary bg-brand-50 p-2 text-xs">
      <p className="font-semibold">Approve: {toolLabel(tool)}</p>
      {editable.map(([key, value]) => (
        <label key={key} className="block">
          <span className="font-medium">{key.replace(/_/g, " ")}</span>
          {String(value).length > 80 || key === "body" ? (
            <textarea
              rows={5}
              value={String(value)}
              onChange={(e) => setArgs((cur) => ({ ...cur, [key]: e.target.value }))}
              className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1"
            />
          ) : (
            <input
              value={String(value)}
              onChange={(e) => setArgs((cur) => ({ ...cur, [key]: e.target.value }))}
              className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1"
            />
          )}
        </label>
      ))}
      {Object.entries(args)
        .filter(([, v]) => typeof v !== "string" && typeof v !== "number")
        .map(([key, value]) => (
          <p key={key} className="break-words">
            <span className="font-medium">{key.replace(/_/g, " ")}:</span> {JSON.stringify(value)}
          </p>
        ))}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={() => onReply({ action: "approve", arguments: args })} className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground">
          Approve
        </button>
        <button type="button" disabled={busy} onClick={() => onReply({ action: "reject" })} className="rounded-md border border-border bg-background px-2.5 py-1">
          Reject
        </button>
      </div>
    </div>
  );
}

function QuestionCard({
  question,
  busy,
  onReply,
}: {
  question: string;
  busy: boolean;
  onReply: (reply: Parameters<typeof replyToAutomationRun>[1]) => void;
}) {
  const [answer, setAnswer] = useState("");
  return (
    <div className="space-y-2 rounded-md border-2 border-primary bg-brand-50 p-2 text-xs">
      <p className="font-semibold">{question}</p>
      <textarea rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1" />
      <button
        type="button"
        disabled={busy || !answer.trim()}
        onClick={() => onReply({ action: "answer", answer })}
        className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50"
      >
        Send answer
      </button>
    </div>
  );
}
