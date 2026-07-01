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

/** Upload a canonical conversation JSON file for ingestion. */
export async function uploadConversationJson(
  file: File,
): Promise<{ job_id: string }> {
  const text = await file.text();
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("File is not valid JSON.");
  }
  const res = await apiFetch("/ingest/conversation", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res, `Upload failed (${res.status})`));
  return res.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await apiFetch(`/ingest/status/${jobId}`);
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
    const status = await getJobStatus(jobId);
    onUpdate(status);
    if (status.status === "complete" || status.status === "failed") {
      return status;
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
  throw new Error("Unsupported file type for the knowledge graph. Use PDF or JSON.");
}

export { isPdf, isJson };
