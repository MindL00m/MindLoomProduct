/**
 * Mock onboarding services.
 *
 * Every function here is async and resolves after an artificial delay so the UI
 * exercises real loading / error states. They are intentionally the ONLY place
 * that talks to a "backend".
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * TODO(backend): Replace the mock bodies below with real API calls. Suggested
 * mapping against the FastAPI service (ingestion/main.py):
 *
 *   connectGoogleWorkspace()  ->  GET  /auth/google/start  (redirect) then
 *                                 GET  /auth/google/callback returns OAuthResult
 *   uploadCsvDirectory(rows)  ->  POST /ingest/directory   (returns job_id)
 *   getSyncProgress(jobId)    ->  GET  /ingest/status/{job_id}
 *   finishSetup()             ->  GET  /org/summary
 *
 * Keep the return shapes (see services/types.ts) identical so no UI changes are
 * needed. Search for "TODO(backend)" to find every seam.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { sleep } from "@/lib/utils";
import { API_BASE } from "@/lib/api";
import { summarizeDirectory, type DirectoryPerson } from "@/lib/directory";
import {
  SetupError,
  type OAuthResult,
  type SetupSummary,
  type SyncSnapshot,
  type SyncStage,
} from "./types";

/**
 * Test hooks: set these flags from the browser console (or a future debug menu)
 * to force a particular failure path, e.g. `window.__cbMock.failOAuth = true`.
 */
export interface MockControls {
  failOAuth: boolean;
  failNetwork: boolean;
  failSync: boolean;
}

export const mockControls: MockControls = {
  failOAuth: false,
  failNetwork: false,
  failSync: false,
};

declare global {
  interface Window {
    __cbMock?: MockControls;
  }
}
if (typeof window !== "undefined") {
  window.__cbMock = mockControls;
}

/**
 * Simulate the Google OAuth consent flow.
 *
 * TODO(backend): replace with a real OAuth redirect + callback exchange.
 */
export async function connectGoogleWorkspace(
  domain: string,
): Promise<OAuthResult> {
  await sleep(1800);

  if (mockControls.failNetwork) {
    throw new SetupError(
      "network_timeout",
      "We couldn't reach Google. Check your connection and try again.",
    );
  }
  if (mockControls.failOAuth) {
    throw new SetupError(
      "oauth_cancelled",
      "Authorization was cancelled before access was granted.",
    );
  }

  const safeDomain = domain || "example.com";
  return {
    connected: true,
    account: `admin@${safeDomain}`,
    accessToken: "mock-google-access-token",
  };
}

/** Server response from POST /ingest/directory (see backend models.py). */
export interface DirectoryIngestResult {
  people_upserted: number;
  departments: number;
  groups: number;
  reporting_links: number;
}

/** Convert a parsed person (camelCase) into the backend payload (snake_case),
 *  dropping undefined fields so optional columns stay absent. */
function toApiPerson(p: DirectoryPerson): Record<string, unknown> {
  const out: Record<string, unknown> = {
    name: p.name,
    email: p.email,
    status: p.status,
    groups: p.teams,
  };
  const optional: Record<string, string | undefined> = {
    user_id: p.userId,
    preferred_name: p.preferredName,
    photo_url: p.photoUrl,
    title: p.title,
    department: p.department,
    business_unit: p.businessUnit,
    employee_type: p.employeeType,
    manager_email: p.managerEmail,
    org_unit: p.orgUnit,
    location: p.location,
    city: p.city,
    country: p.country,
    desk_location: p.deskLocation,
    start_date: p.startDate,
  };
  for (const [k, v] of Object.entries(optional)) {
    if (v !== undefined && v !== "") out[k] = v;
  }
  return out;
}

/**
 * Upload a parsed directory to the backend. Heavy parsing/validation already
 * happened client-side (see lib/directory.ts); this performs the real
 * POST /ingest/directory call, which upserts Person nodes and wires REPORTS_TO.
 *
 * The `mockControls.failNetwork` hook short-circuits with a timeout for testing
 * the retry UI without a backend.
 */
export async function uploadCsvDirectory(
  people: DirectoryPerson[],
  source = "csv",
): Promise<{ result: DirectoryIngestResult; summary: SetupSummary }> {
  if (mockControls.failNetwork) {
    await sleep(800);
    throw new SetupError(
      "network_timeout",
      "We couldn't upload your file. Check your connection and try again.",
    );
  }

  let result: DirectoryIngestResult;
  try {
    const res = await fetch(`${API_BASE}/ingest/directory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, people: people.map(toApiPerson) }),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new SetupError(
        "network_timeout",
        `Import failed (${res.status}). ${detail.slice(0, 140)}`,
      );
    }
    result = (await res.json()) as DirectoryIngestResult;
  } catch (err) {
    if (err instanceof SetupError) throw err;
    throw new SetupError(
      "network_timeout",
      "We couldn't reach the server. Make sure the API is running, then retry.",
    );
  }

  // Prefer the authoritative server counts; fall back to client-side totals.
  const counts = summarizeDirectory(people);
  return {
    result,
    summary: {
      organization: "",
      people: result.people_upserted ?? counts.people,
      departments: result.departments ?? counts.departments,
      groups: result.groups ?? counts.groups,
    },
  };
}

type SyncMode = "google" | "directory";

const STAGE_LABELS: Record<SyncMode, Omit<SyncStage, "status">[]> = {
  google: [
    { id: "connected", label: "Organization connected" },
    { id: "users", label: "Importing users" },
    { id: "groups", label: "Importing groups" },
    { id: "graph", label: "Building organization graph" },
    { id: "workspace", label: "Preparing workspace" },
  ],
  directory: [
    { id: "connected", label: "File uploaded" },
    { id: "users", label: "Importing employees" },
    { id: "groups", label: "Importing teams" },
    { id: "graph", label: "Building organization graph" },
    { id: "workspace", label: "Preparing workspace" },
  ],
};

/** Map an overall 0–100 progress value to per-stage statuses. */
function stagesForProgress(progress: number, mode: SyncMode): SyncStage[] {
  const base = STAGE_LABELS[mode];
  // First stage ("connected") is done immediately; the remaining four split the
  // bar evenly between 0 and 100.
  const worker = base.length - 1;
  const completed = Math.floor((progress / 100) * worker);

  return base.map((stage, i) => {
    if (i === 0) return { ...stage, status: "done" };
    const workerIndex = i - 1;
    let status: SyncStage["status"] = "queued";
    if (workerIndex < completed || progress >= 100) status = "done";
    else if (workerIndex === completed) status = "active";
    return { ...stage, status };
  });
}

export interface SyncOptions {
  /** Which stage-label set to animate. */
  mode?: SyncMode;
  /** Pre-computed summary (CSV flow). When omitted, finishSetup() is used. */
  summary?: SetupSummary;
}

/** Initial stage list for a mode, exposed so the page can render before the
 *  first progress tick. */
export function initialStages(mode: SyncMode = "google"): SyncStage[] {
  return stagesForProgress(0, mode).map((s, i) =>
    i === 1 ? { ...s, status: "active" } : s,
  );
}

/**
 * Drive a fake sync from 0 → 100 over ~8 seconds, invoking `onUpdate` every few
 * hundred ms. Resolves once complete; rejects if the (mock) sync is set to fail.
 *
 * TODO(backend): poll GET /ingest/status/{job_id} instead of the timer below and
 * translate the job payload into a SyncSnapshot.
 */
export async function getSyncProgress(
  onUpdate: (snapshot: SyncSnapshot) => void,
  options: SyncOptions = {},
): Promise<SetupSummary> {
  const mode = options.mode ?? "google";
  const totalMs = 8000;
  const tick = 250;
  const steps = totalMs / tick;
  const failAt = mockControls.failSync ? Math.floor(steps * 0.55) : -1;

  for (let i = 1; i <= steps; i++) {
    await sleep(tick);

    if (i === failAt) {
      onUpdate({
        progress: Math.round((i / steps) * 100),
        stages: stagesForProgress((i / steps) * 100, mode),
        done: false,
      });
      throw new SetupError(
        "sync_failed",
        "Synchronization failed while importing your directory.",
      );
    }

    const progress = Math.min(100, Math.round((i / steps) * 100));
    onUpdate({
      progress,
      stages: stagesForProgress(progress, mode),
      done: progress >= 100,
    });
  }

  return options.summary ?? finishSetup();
}

/**
 * Finalize setup and return the organization summary shown on the last screen.
 *
 * TODO(backend): replace with GET /org/summary.
 */
export async function finishSetup(): Promise<SetupSummary> {
  await sleep(400);
  return {
    organization: "Acme Inc",
    people: 127,
    departments: 8,
    groups: 24,
  };
}
