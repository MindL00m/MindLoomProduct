/** Ingestion API: upload conversation JSON or PDF documents and poll job status.
 *
 *  Both endpoints enqueue a background job and return a job id; callers poll
 *  {@link getJobStatus} until the job completes or fails. */

import { apiFetch } from "@/lib/api";

export interface IngestionResult {
  total_messages: number;
  total_chunks: number;
  chunks_by_type: Record<string, number>;
  failed_chunks: number;
  duration_seconds: number;
}

export type JobState = "queued" | "processing" | "complete" | "failed";

export interface JobStatus {
  job_id: string;
  status: JobState;
  conversation_id: string;
  progress?: string | null;
  result?: IngestionResult | null;
  error?: string | null;
}

async function detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return (body?.detail as string) || fallback;
  } catch {
    return fallback;
  }
}

/** Upload a PDF for structure-aware chunking + ingestion. */
export async function uploadPdf(file: File): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch("/ingest/pdf", {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await detail(res, `Upload failed (${res.status})`));
  return res.json();
}

export interface DocumentMetadata {
  title?: string;
  author?: string;
  owners?: string[];
  source_created_at?: string;
  source_updated_at?: string;
  source_application?: string;
  source_location?: string;
  department?: string;
  project?: string;
  folder_path?: string;
  version?: string;
  contributors?: string[];
  permissions?: string[];
  visibility?: "private" | "organization";
  source_url?: string;
}

export interface WhatsAppPreview {
  message_count: number;
  participant_count: number;
  participants: string[];
  first_message_at: string;
  last_message_at: string;
  sample_messages: { speaker: string; timestamp: string; text: string }[];
  ignored_notice: string;
}

export async function previewWhatsAppExport(
  file: File,
  timezoneName: string,
): Promise<WhatsAppPreview> {
  const form = new FormData();
  form.append("file", file);
  form.append("timezone_name", timezoneName);
  const res = await apiFetch("/integrations/whatsapp/preview", {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await detail(res, "Could not preview WhatsApp export."));
  return res.json();
}

export async function importWhatsAppExport(
  file: File,
  timezoneName: string,
  metadata: DocumentMetadata,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("timezone_name", timezoneName);
  form.append("metadata_json", JSON.stringify(metadata));
  const res = await apiFetch("/integrations/whatsapp/import", {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await detail(res, "Could not import WhatsApp export."));
  return res.json();
}

export async function uploadDocument(
  file: File,
  metadata: DocumentMetadata = {},
): Promise<{ job_id: string; job_ids?: string[]; files?: string[] }> {
  const form = new FormData();
  form.append("file", file);
  form.append("metadata_json", JSON.stringify(metadata));
  const res = await apiFetch("/ingest/document", { method: "POST", body: form });
  if (!res.ok) throw new Error(await detail(res, `Upload failed (${res.status})`));
  return res.json();
}

/** Upload a canonical conversation JSON file for ingestion. */
export async function uploadConversationJson(
  file: File,
  options: { visibility?: "private" | "organization" } = {},
): Promise<{ job_id: string }> {
  const text = await file.text();
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("File is not valid JSON.");
  }
  const visibility = options.visibility ?? "private";
  const qs = new URLSearchParams({ visibility });
  const res = await apiFetch(`/ingest/conversation?${qs.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res, `Upload failed (${res.status})`));
  return res.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await apiFetch(`/ingest/status/${jobId}`);
  if (res.status === 502 || res.status === 503) {
    throw new Error(`Status check failed (${res.status})`);
  }
  if (!res.ok) throw new Error(`Status check failed (${res.status})`);
  return res.json();
}

/** Poll a job until it reaches a terminal state, invoking `onUpdate` each tick. */
export async function pollJob(
  jobId: string,
  onUpdate: (status: JobStatus) => void,
  { intervalMs = 1500, maxAttempts = 600 }: { intervalMs?: number; maxAttempts?: number } = {},
): Promise<JobStatus> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const status = await getJobStatus(jobId);
      onUpdate(status);
      if (status.status === "complete" || status.status === "failed") {
        return status;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      // API recreate / nginx 502 while the worker is still running.
      if (/\(502\)|\(503\)/.test(msg) && attempt < maxAttempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
        continue;
      }
      throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("Timed out waiting for ingestion to finish.");
}

function isPdf(file: File): boolean {
  return file.name.toLowerCase().endsWith(".pdf") || file.type === "application/pdf";
}

function isJson(file: File): boolean {
  return file.name.toLowerCase().endsWith(".json") || file.type === "application/json";
}

function isDocument(file: File): boolean {
  return /\.(pdf|docx|pptx|xlsx|csv|txt|md|log|jsonl|png|jpe?g|gif|webp|zip)$/i.test(file.name);
}

/** Ingest a file into the org knowledge graph (PDF or conversation JSON). */
export async function ingestFileToGraph(
  file: File,
  onUpdate?: (status: JobStatus) => void,
): Promise<JobStatus> {
  if (isPdf(file)) {
    const { job_id } = await uploadPdf(file);
    return pollJob(job_id, onUpdate ?? (() => {}));
  }
  if (isJson(file)) {
    const { job_id } = await uploadConversationJson(file);
    return pollJob(job_id, onUpdate ?? (() => {}));
  }
  if (isDocument(file)) {
    const queued = await uploadDocument(file);
    // A ZIP is one job per document inside it.
    const jobs = queued.job_ids?.length ? queued.job_ids : [queued.job_id];
    for (const job of jobs.slice(0, -1)) await pollJob(job, () => {});
    return pollJob(jobs[jobs.length - 1], onUpdate ?? (() => {}));
  }
  throw new Error("Unsupported document type.");
}

export { isPdf, isJson, isDocument };
