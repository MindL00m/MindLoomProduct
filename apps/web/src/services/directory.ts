/** Real directory ingest API (CSV / directory import). */

import { apiFetch } from "@/lib/api";
import { summarizeDirectory, type DirectoryPerson } from "@/lib/directory";
import { getOrgSummary } from "@/services/auth";
import { SetupError, type SetupSummary } from "./types";

/** Server response from POST /ingest/directory. */
export interface DirectoryIngestResult {
  people_upserted: number;
  departments: number;
  groups: number;
  reporting_links: number;
}

/** Convert a parsed person (camelCase) into the backend payload (snake_case). */
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
 * Upload a parsed directory via POST /ingest/directory (synchronous upsert).
 * Prefers GET /org/summary for the returned summary when available.
 */
export async function uploadCsvDirectory(
  people: DirectoryPerson[],
  source = "csv",
): Promise<{ result: DirectoryIngestResult; summary: SetupSummary }> {
  let result: DirectoryIngestResult;
  try {
    const res = await apiFetch("/ingest/directory", {
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

  const counts = summarizeDirectory(people);
  let summary: SetupSummary = {
    organization: "",
    people: result.people_upserted ?? counts.people,
    departments: result.departments ?? counts.departments,
    groups: result.groups ?? counts.groups,
  };

  try {
    const orgSummary = await getOrgSummary();
    summary = {
      organization: orgSummary.organization,
      people: orgSummary.people,
      departments: orgSummary.departments,
      groups: orgSummary.groups,
    };
  } catch {
    /* keep ingest counts if summary fetch fails */
  }

  return { result, summary };
}
