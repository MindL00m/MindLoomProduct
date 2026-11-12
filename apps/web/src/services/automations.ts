import { apiFetch } from "@/lib/api";
import { userTimezone } from "@/services/workflows";

export interface AutomationInput {
  name: string;
  label: string;
  description: string;
  required: boolean;
  example: string;
}

export interface AutomationPlan {
  summary: string;
  inputs: AutomationInput[];
  steps: { step: string; tool: string; how: string }[];
  /** Mac apps the device steps use, as the planner saw them. */
  apps?: string[];
  /** Websites the browser steps use: the ones the skill was recorded on or names. */
  sites?: string[];
  automatable: "full" | "partial" | "manual";
  notes: string;
}

export interface Automation {
  automation_id: string;
  skill_id: string;
  plan: AutomationPlan;
  auto_approve: boolean;
  interval_value: number | null;
  interval_unit: string | null;
  next_run_at: string | null;
  default_inputs: Record<string, string>;
  /** Mac apps the person allowed MindLoom to operate for this automation. */
  device_apps: string[];
  /** Websites the person allowed MindLoom to operate in Chrome for this automation. */
  browser_sites: string[];
}

export type AutomationRunStatus =
  | "queued"
  | "running"
  | "needs_approval"
  | "needs_input"
  | "waiting_device"
  | "completed"
  | "failed"
  | "stopped";

export interface AutomationLogEntry {
  at: string;
  kind: "tool" | "approval" | "input" | "device" | "message" | "finish" | "stop";
  tool?: string;
  status: "ok" | "error" | "pending" | "rejected";
  summary?: string;
  result?: string;
  outcome?: string;
}

export type DeviceTaskStatus =
  | "pending"
  | "running"
  | "needs_approval"
  | "needs_person"
  | "completed"
  | "failed"
  | "stopped";

export interface DeviceTaskLogEntry {
  at: string;
  kind: "start" | "claim" | "action" | "approval" | "person" | "finish" | "stop";
  tool?: string;
  status: "ok" | "error" | "pending" | "rejected";
  summary?: string;
  result?: string;
}

/**
 * Part of a run done outside MindLoom (shuttle/device_tasks.py): by the Mac app in the
 * allowed apps, or by the Chrome extension on the allowed sites (surface "browser").
 */
export interface DeviceTask {
  task_id: string;
  run_id: string;
  status: DeviceTaskStatus;
  title: string;
  surface: "mac" | "browser";
  /** Mac apps for a Mac task, sites for a browser task. */
  allowed_apps: string[];
  log: DeviceTaskLogEntry[];
  pending: { instruction?: string; description?: string; reason?: string; tool?: string } | null;
  summary: string | null;
  error: string | null;
  steps: number;
  created_at: string;
  updated_at: string;
}

export interface AutomationRun {
  run_id: string;
  skill_id: string;
  status: AutomationRunStatus;
  inputs: Record<string, string>;
  log: AutomationLogEntry[];
  pending: {
    tool?: string;
    arguments?: Record<string, unknown>;
    question?: string;
    steps?: string;
    details?: string | null;
    surface?: "mac" | "browser";
    device_task_id?: string | null;
  } | null;
  device_task?: DeviceTask | null;
  summary: string | null;
  error: string | null;
  trigger: string;
  created_at: string;
  updated_at: string;
}

export const ACTIVE_RUN: AutomationRunStatus[] = [
  "queued",
  "running",
  "needs_approval",
  "needs_input",
  "waiting_device",
];

async function call<T>(path: string, init: RequestInit = {}, fallback = "Request failed."): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    headers: typeof init.body === "string" ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<T>;
}

const skillPath = (skillId: string) => `/captures/skill-files/${encodeURIComponent(skillId)}/automation`;

export function getAutomation(
  skillId: string,
): Promise<{ automation: Automation | null; runs: AutomationRun[] }> {
  return call(skillPath(skillId), {}, "Could not load the automation.");
}

/** Ask MindLoom to plan the automation: which tool does each step, which inputs a run needs. */
export function prepareAutomation(skillId: string): Promise<Automation> {
  return call(skillPath(skillId), { method: "POST" }, "Could not plan the automation.");
}

export function updateAutomation(
  skillId: string,
  settings: {
    auto_approve?: boolean;
    schedule?: boolean;
    interval_value?: number;
    interval_unit?: string;
    default_inputs?: Record<string, string>;
    device_apps?: string[];
    browser_sites?: string[];
  },
): Promise<Automation> {
  return call(skillPath(skillId), { method: "PATCH", body: JSON.stringify(settings) }, "Could not save.");
}

export function startAutomationRun(skillId: string, inputs: Record<string, string>): Promise<AutomationRun> {
  return call(
    `${skillPath(skillId)}/runs`,
    { method: "POST", body: JSON.stringify({ inputs, timezone: userTimezone() }) },
    "Could not start the run.",
  );
}

export function getAutomationRun(runId: string): Promise<AutomationRun> {
  return call(`/captures/automation-runs/${encodeURIComponent(runId)}`);
}

export function replyToAutomationRun(
  runId: string,
  reply: {
    action: "approve" | "reject" | "answer" | "continue";
    arguments?: Record<string, unknown>;
    answer?: string;
    reason?: string;
  },
): Promise<AutomationRun> {
  return call(
    `/captures/automation-runs/${encodeURIComponent(runId)}/reply`,
    { method: "POST", body: JSON.stringify(reply) },
    "Could not send your reply.",
  );
}

export function stopAutomationRun(runId: string): Promise<AutomationRun> {
  return call(`/captures/automation-runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
}

const devicePath = (taskId: string) => `/captures/device-tasks/${encodeURIComponent(taskId)}`;

/** Approve or reject an action on the Mac, answer an ask (done / cant), or take over. */
export function replyToDeviceTask(
  taskId: string,
  action: "approve" | "reject" | "done" | "cant" | "takeover",
  note?: string,
): Promise<DeviceTask> {
  return call(`${devicePath(taskId)}/reply`, { method: "POST", body: JSON.stringify({ action, note }) }, "Could not send your reply.");
}
