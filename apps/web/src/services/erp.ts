import { apiFetch } from "@/lib/api";

export type ErpProvider = "netsuite" | "sap";

/** A NetSuite or SAP record change Ask drafted. Nothing changes until it is approved. */
export interface ProposedErpChange {
  system: ErpProvider;
  action: "create" | "update";
  /** NetSuite record type, or SAP OData path. */
  target: string;
  record_id?: string | null;
  fields: Record<string, unknown>;
}

export interface ErpImport {
  import_id: string;
  provider: ErpProvider;
  name: string;
  query: string;
  options: Record<string, string>;
  max_rows: number;
  visibility: "me" | "org";
  last_run_at: string | null;
  last_status: "ok" | "error" | null;
  last_error: string | null;
  last_rows: number | null;
}

export interface ErpImportInput {
  provider: ErpProvider;
  name: string;
  query: string;
  options?: Record<string, string>;
  max_rows: number;
  visibility: "me" | "org";
}

async function send<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : fallback);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const applyErpChange = (change: ProposedErpChange) =>
  send<{ created?: boolean; updated?: boolean; id?: string | null; location?: string | null }>(
    "/integrations/erp/apply",
    "Could not make the change.",
    json(change),
  );

export const listErpImports = () => send<ErpImport[]>("/integrations/erp/imports", "Could not load imports.");

export const createErpImport = (input: ErpImportInput) =>
  send<ErpImport>("/integrations/erp/imports", "Could not save the import.", json(input));

export const runErpImport = (importId: string) =>
  send<{ job_id: string }>(`/integrations/erp/imports/${encodeURIComponent(importId)}/run`, "Could not start the import.", {
    method: "POST",
  });

export const deleteErpImport = (importId: string) =>
  send<{ deleted: boolean }>(`/integrations/erp/imports/${encodeURIComponent(importId)}`, "Could not delete the import.", {
    method: "DELETE",
  });

/** One change MindLoom made, or tried to make, in NetSuite or SAP. Field names only, never values. */
export interface ErpChange {
  change_id: string;
  system: ErpProvider;
  action: "create" | "update";
  target: string;
  record_id: string | null;
  field_names: string;
  source: "automation" | "ask";
  run_id: string | null;
  ok: boolean;
  detail: string | null;
  created_at: string;
}

export const listErpChanges = (limit = 30) =>
  send<ErpChange[]>(`/integrations/erp/changes?limit=${limit}`, "Could not load recent changes.");
