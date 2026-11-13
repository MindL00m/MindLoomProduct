import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Check,
  CircleDashed,
  Clock,
  Globe,
  Hand,
  Loader2,
  MessageCircleQuestion,
  Monitor,
  Play,
  RefreshCw,
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
  replyToDeviceTask,
  startAutomationRun,
  stopAutomationRun,
  updateAutomation,
  type Automation,
  type AutomationLogEntry,
  type AutomationRun,
  type DeviceOS,
  type DeviceTask,
} from "@/services/automations";
import type { SkillFile } from "@/services/skillFiles";
import { formatInterval, type IntervalUnit } from "@/services/workflows";
import { cn } from "@/lib/utils";

const POLL_MS = 2_000;

const TOOL_LABEL: Record<string, string> = {
  send_email: "Send email",
  create_meeting: "Book meeting",
  http_request: "Call web API",
  netsuite_query: "Read NetSuite",
  sap_read: "Read SAP",
  sap_write: "Change SAP record",
  netsuite_record: "NetSuite record",
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
  hand_off_to_browser: "In Chrome",
  finish: "Finish",
};

function toolLabel(tool?: string): string {
  if (!tool) return "Step";
  return TOOL_LABEL[tool] ?? tool.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * "Run with MindLoom": MindLoom performs the Skill File itself with its toolbox
 * (knowledge, email, files and spreadsheets, templates, calendar, web, web APIs).
 * Outbound actions wait for Approve; missing values are asked for; steps in other
 * apps are done by the Mac app or MindLoom for Windows in the apps the person allowed
 * ("On your computer"), and steps on websites by the Chrome extension on the sites the
 * person allowed ("In Chrome").
 */
export function SkillAutomation({ skill, isOwner }: { skill: SkillFile; isOwner: boolean }) {
  const [open, setOpen] = useState(false);
  const [automation, setAutomation] = useState<Automation | null>(null);
  const [run, setRun] = useState<AutomationRun | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "plan" | "start" | "reply" | "settings">("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const data = await getAutomation(skill.skill_id);
    setAutomation(data.automation);
    if (data.automation) setInputs((cur) => ({ ...data.automation!.default_inputs, ...cur }));
    const latest = data.runs[0];
    if (latest && ACTIVE_RUN.includes(latest.status)) setRun(await getAutomationRun(latest.run_id));
  }, [skill.skill_id]);

  useEffect(() => {
    void load().catch((err: Error) => setError(err.message));
  }, [load]);

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
            <Loader2 className="size-3 animate-spin" />
            {run && run.status !== "queued" && run.status !== "running" ? STATUS_TEXT[run.status].toLowerCase() : "running"}
          </span>
        )}
        {automation?.next_run_at && automation.interval_value && automation.interval_unit && (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary"
            title={`Next run ${new Date(automation.next_run_at).toLocaleString()}`}
          >
            <Clock className="size-3" />
            Runs {formatInterval(automation.interval_value, automation.interval_unit as IntervalUnit)}
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

              {(plan?.steps.some((s) => s.tool === "hand_off_to_device") ||
                (plan?.apps?.length ?? 0) > 0 ||
                automation.device_apps.length > 0) && (
                <DeviceApps
                  automation={automation}
                  busy={busy === "settings"}
                  onSave={(apps) =>
                    void act("settings", async () =>
                      setAutomation(await updateAutomation(skill.skill_id, { device_apps: apps })),
                    )
                  }
                  onSystem={(system) =>
                    void act("settings", async () =>
                      setAutomation(await updateAutomation(skill.skill_id, { device_os: system })),
                    )
                  }
                />
              )}

              {(plan?.steps.some((s) => s.tool === "hand_off_to_browser") ||
                (plan?.sites?.length ?? 0) > 0 ||
                (automation.browser_sites?.length ?? 0) > 0) && (
                <BrowserSites
                  automation={automation}
                  busy={busy === "settings"}
                  onSave={(sites) =>
                    void act("settings", async () =>
                      setAutomation(await updateAutomation(skill.skill_id, { browser_sites: sites })),
                    )
                  }
                />
              )}

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
                  Run once
                </button>
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => void act("plan", async () => setAutomation(await prepareAutomation(skill.skill_id)))}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium"
                >
                  <RefreshCw className="size-3.5" /> Re-plan
                </button>
              </div>

              <AutomationSettings
                automation={automation}
                isOwner={isOwner}
                inputs={inputs}
                busy={busy === "settings"}
                onSave={(settings) =>
                  void act("settings", async () => setAutomation(await updateAutomation(skill.skill_id, settings)))
                }
              />
            </>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}
          {run && (
            <RunView
              run={run}
              busy={busy === "reply"}
              onReply={(reply) => void act("reply", async () => setRun(await replyToAutomationRun(run.run_id, reply)))}
              onStop={() => void act("reply", async () => setRun(await stopAutomationRun(run.run_id)))}
              onDeviceReply={(taskId, action) =>
                void act("reply", async () => {
                  await replyToDeviceTask(taskId, action);
                  setRun(await getAutomationRun(run.run_id));
                })
              }
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The desktop apps MindLoom may operate for this automation, and which computer does it:
 * the Mac (Loom Capture) or a Windows PC (MindLoom for Windows).
 */
function DeviceApps({
  automation,
  busy,
  onSave,
  onSystem,
}: {
  automation: Automation;
  busy: boolean;
  onSave: (apps: string[]) => void;
  onSystem: (system: DeviceOS) => void;
}) {
  const [extra, setExtra] = useState("");
  const system: DeviceOS = automation.device_os === "windows" ? "windows" : "mac";
  const agent = system === "windows" ? "MindLoom for Windows" : "the Loom Capture app";
  const allowed = automation.device_apps;
  const isAllowed = (app: string) => allowed.some((a) => a.toLowerCase() === app.toLowerCase());
  const candidates = [...allowed];
  for (const app of automation.plan.apps ?? []) if (!isAllowed(app)) candidates.push(app);
  const toggle = (app: string, on: boolean) =>
    onSave(on ? [...allowed, app] : allowed.filter((a) => a.toLowerCase() !== app.toLowerCase()));
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3 text-xs">
      <p className="flex items-center gap-1.5 font-semibold">
        <Monitor className="size-3.5" /> On your computer
      </p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-muted-foreground">These steps run on:</span>
        {(["mac", "windows"] as const).map((option) => (
          <label key={option} className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name={`device-os-${automation.automation_id}`}
              checked={system === option}
              disabled={busy}
              onChange={() => onSystem(option)}
            />
            {option === "mac" ? "A Mac" : "A Windows PC"}
          </label>
        ))}
      </div>
      <p className="text-muted-foreground">
        Let MindLoom do the steps in these apps through {agent}, in the background where it can. Sending,
        deleting, paying and anything else that can't be undone waits for your approval. It never types
        passwords or touches other apps.
      </p>
      {candidates.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {candidates.map((app) => (
            <label key={app} className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={isAllowed(app)} disabled={busy} onChange={(e) => toggle(app, e.target.checked)} />
              {app}
            </label>
          ))}
        </div>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const app = extra.trim();
          if (app && !isAllowed(app)) onSave([...allowed, app]);
          setExtra("");
        }}
      >
        <input
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder={system === "windows" ? "Another app, e.g. Excel or TallyPrime" : "Another app, e.g. Numbers"}
          maxLength={60}
          className="w-48 rounded border border-border px-2 py-1"
        />
        <button type="submit" disabled={busy || !extra.trim()} className="rounded-md border border-border px-2.5 py-1 disabled:opacity-50">
          Allow
        </button>
      </form>
      {allowed.length === 0 && (
        <p className="text-muted-foreground">No apps allowed: you'll do those steps yourself and click Done.</p>
      )}
    </div>
  );
}

/** The websites MindLoom may operate in Chrome for this automation (the extension does the work). */
function BrowserSites({
  automation,
  busy,
  onSave,
}: {
  automation: Automation;
  busy: boolean;
  onSave: (sites: string[]) => void;
}) {
  const [extra, setExtra] = useState("");
  const allowed = automation.browser_sites ?? [];
  const isAllowed = (site: string) => allowed.includes(site.toLowerCase());
  const candidates = [...allowed];
  for (const site of automation.plan.sites ?? []) if (!isAllowed(site)) candidates.push(site);
  const toggle = (site: string, on: boolean) =>
    onSave(on ? [...allowed, site] : allowed.filter((s) => s !== site.toLowerCase()));
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3 text-xs">
      <p className="flex items-center gap-1.5 font-semibold">
        <Globe className="size-3.5" /> In Chrome
      </p>
      <p className="text-muted-foreground">
        Let MindLoom do the steps on these websites through the MindLoom Chrome extension: it reads the
        page, clicks and fills in fields in a tab of its own. Sending, submitting, deleting, paying and
        anything else that can't be undone waits for your approval, and so does attaching one of your
        Files. It never types passwords or codes, and never opens other sites. Allow each site in the
        extension too.
      </p>
      {candidates.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {candidates.map((site) => (
            <label key={site} className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={isAllowed(site)} disabled={busy} onChange={(e) => toggle(site, e.target.checked)} />
              {site}
            </label>
          ))}
        </div>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const site = extra.trim();
          if (site) onSave([...allowed, site]);
          setExtra("");
        }}
      >
        <input
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder="Another site, e.g. erp.example.com"
          maxLength={253}
          className="w-64 rounded border border-border px-2 py-1"
        />
        <button type="submit" disabled={busy || !extra.trim()} className="rounded-md border border-border px-2.5 py-1 disabled:opacity-50">
          Allow
        </button>
      </form>
      {allowed.length === 0 && (
        <p className="text-muted-foreground">No sites allowed: you'll do those steps yourself and click Done.</p>
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
  const tooFrequent = (Number(every) || 1) * UNIT_MINUTES[unit as IntervalUnit] < 15;
  return (
    <div className="space-y-3 rounded-md border border-border bg-background p-3 text-xs">
      <div className="space-y-1.5">
        <p className="flex items-center gap-1.5 font-semibold">
          <Clock className="size-3.5 text-muted-foreground" /> Run automatically
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <span>Every</span>
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
            disabled={busy || tooFrequent}
            onClick={() =>
              onSave({ schedule: true, interval_value: Number(every) || 1, interval_unit: unit, default_inputs: inputs })
            }
            className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50"
          >
            {scheduled ? "Update schedule" : "Turn on"}
          </button>
          {scheduled && (
            <button type="button" disabled={busy} onClick={() => onSave({ schedule: false })} className="rounded-md border border-border px-2.5 py-1">
              Stop repeating
            </button>
          )}
        </div>
        {tooFrequent && <p className="text-destructive">Workflows can run at most every 15 minutes.</p>}
        <p className="text-muted-foreground">
          {scheduled
            ? `On: runs ${formatInterval(automation.interval_value ?? 1, (automation.interval_unit ?? "days") as IntervalUnit)}, next ${new Date(automation.next_run_at!).toLocaleString()}, with the inputs above. Each run only handles what is new since the last one.`
            : "Off: it runs only when you click Run once."}
        </p>
      </div>
      <label className="flex items-start gap-2 border-t border-border pt-3">
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
      {scheduled && !automation.auto_approve && (
        <p className="text-muted-foreground">
          Runs that send email or book meetings wait here for your approval until you turn this on.
        </p>
      )}
    </div>
  );
}

const UNIT_MINUTES: Record<IntervalUnit, number> = { minutes: 1, hours: 60, days: 1440, weeks: 10080 };

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

type DeviceReply = Parameters<typeof replyToDeviceTask>[1];

function RunView({
  run,
  busy,
  onReply,
  onStop,
  onDeviceReply,
}: {
  run: AutomationRun;
  busy: boolean;
  onReply: (reply: Parameters<typeof replyToAutomationRun>[1]) => void;
  onStop: () => void;
  onDeviceReply: (taskId: string, action: DeviceReply) => void;
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
      {run.status === "waiting_device" &&
        (run.device_task ? (
          <DeviceTaskCard
            task={run.device_task}
            busy={busy}
            onReply={(action) => onDeviceReply(run.device_task!.task_id, action)}
            onDoneMyself={() => onReply({ action: "continue" })}
          />
        ) : (
          <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs">
            <p className="flex items-start gap-1.5">
              {run.pending?.surface === "browser" ? (
                <Globe className="mt-0.5 size-3.5 shrink-0" />
              ) : (
                <Monitor className="mt-0.5 size-3.5 shrink-0" />
              )}
              <span>
                Do these steps {run.pending?.surface === "browser" ? "in your browser" : "on your computer"}:{" "}
                <b>{run.pending?.steps}</b>
                {run.pending?.details ? <> ({run.pending.details})</> : null}. To let MindLoom do them next
                time, allow the {run.pending?.surface === "browser" ? "sites" : "apps"} under{" "}
                <b>{run.pending?.surface === "browser" ? "In Chrome" : "On your computer"}</b>.
              </span>
            </p>
            <button type="button" disabled={busy} onClick={() => onReply({ action: "continue" })} className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground">
              Done, continue
            </button>
          </div>
        ))}
      {run.summary && !active && <p className="text-xs">{run.summary}</p>}
      {run.error && <p className="text-xs text-destructive">{run.error}</p>}
    </div>
  );
}

const STALE_MS = 2 * 60_000;

/** Live view of the Mac app or Chrome doing its part: what it's doing, and anything waiting on you. */
function DeviceTaskCard({
  task,
  busy,
  onReply,
  onDoneMyself,
}: {
  task: DeviceTask;
  busy: boolean;
  onReply: (action: DeviceReply) => void;
  onDoneMyself: () => void;
}) {
  const apps = task.allowed_apps.join(", ");
  const browser = task.surface === "browser";
  const windows = task.surface === "windows";
  const computer = windows ? "your PC" : "your Mac";
  const agent = windows ? "MindLoom for Windows" : "Loom Capture";
  const where = browser ? "in Chrome" : `on ${computer}`;
  const quiet = Date.now() - new Date(task.updated_at).getTime() > STALE_MS;
  const actions = task.log
    .filter((e) => e.kind === "action" || e.kind === "approval" || e.kind === "person" || e.kind === "record")
    .slice(-8);
  return (
    <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs">
      <p className="flex items-start gap-1.5">
        {browser ? <Globe className="mt-0.5 size-3.5 shrink-0" /> : <Monitor className="mt-0.5 size-3.5 shrink-0" />}
        <span>
          {task.status === "pending" ? (
            browser ? (
              <>
                Waiting for the MindLoom extension in Chrome to start <b>{task.title}</b> on {apps}.
                {quiet && " Open Chrome with the MindLoom extension signed in; it picks up tasks from the last 30 minutes."}
              </>
            ) : (
              <>
                Waiting for {agent} on {computer} to start <b>{task.title}</b> in {apps}.
                {quiet && ` Open ${agent} and sign in; it picks up tasks from the last 30 minutes.`}
              </>
            )
          ) : task.status === "running" ? (
            browser ? (
              <>
                MindLoom is working on {apps} in a Chrome tab of its own: <b>{task.title}</b>.
                {quiet && " The extension hasn't reported for a while; check Chrome is still open."}
              </>
            ) : (
              <>
                MindLoom is working in {apps} on {computer}: <b>{task.title}</b>.
                {quiet && ` ${agent} hasn't reported for a while; check it's still open.`}
              </>
            )
          ) : task.status === "needs_approval" ? (
            <>MindLoom wants to do something {where} that may not be undoable.</>
          ) : task.status === "needs_person" ? (
            <>MindLoom needs you to do something {where}.</>
          ) : (
            <>{task.summary || task.error || `Finished ${where}.`}</>
          )}
        </span>
      </p>
      {task.progress && task.progress.total > 0 && (
        <p className="font-medium">
          Record {Math.min(task.progress.done + 1, task.progress.total)} of {task.progress.total}
          {task.progress.done > 0 ? ` (${task.progress.done} finished)` : ""}
        </p>
      )}
      {actions.length > 0 && (
        <ol className="space-y-0.5 border-l-2 border-amber-300 pl-2">
          {actions.map((entry, i) => (
            <li key={i} className={cn(entry.status === "error" && "text-destructive", entry.status === "rejected" && "line-through")}>
              {entry.summary}
              {entry.status === "error" && entry.result ? ` (${entry.result})` : ""}
            </li>
          ))}
        </ol>
      )}
      {task.status === "needs_approval" && task.pending && (
        <div className="space-y-2 rounded-md border-2 border-primary bg-brand-50 p-2">
          <p className="font-semibold">Approve: {task.pending.description}</p>
          {task.pending.reason && <p className="text-muted-foreground">{task.pending.reason}</p>}
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => onReply("approve")} className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground">
              Approve
            </button>
            <button type="button" disabled={busy} onClick={() => onReply("reject")} className="rounded-md border border-border bg-background px-2.5 py-1">
              Reject
            </button>
          </div>
        </div>
      )}
      {task.status === "needs_person" && task.pending && (
        <div className="space-y-2 rounded-md border-2 border-primary bg-brand-50 p-2">
          <p className="font-semibold">{task.pending.instruction}</p>
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => onReply("done")} className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground">
              Done
            </button>
            <button type="button" disabled={busy} onClick={() => onReply("cant")} className="rounded-md border border-border bg-background px-2.5 py-1">
              I can't
            </button>
          </div>
        </div>
      )}
      <button type="button" disabled={busy} onClick={onDoneMyself} className="text-muted-foreground underline-offset-2 hover:underline">
        I did these steps myself, continue
      </button>
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
