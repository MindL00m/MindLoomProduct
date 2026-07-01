/** Org-graph data access. Prefers the backend (`GET /org/graph`) and falls back
 *  to the directory parsed during onboarding so the chart still renders if the
 *  API isn't reachable. */

import { apiFetch } from "@/lib/api";
import type { OrgChartPerson } from "@/lib/orgChart";
import type { DirectoryPerson } from "@/lib/directory";

interface ApiOrgPerson {
  id: string;
  name: string;
  preferred_name?: string | null;
  email?: string | null;
  title?: string | null;
  department?: string | null;
  business_unit?: string | null;
  photo_url?: string | null;
  location?: string | null;
  city?: string | null;
  country?: string | null;
  groups?: string[] | null;
  status?: string | null;
  start_date?: string | null;
  manager_id?: string | null;
}

interface ApiOrgGraph {
  people: ApiOrgPerson[];
  edges: { source: string; target: string }[];
}

function fromApi(p: ApiOrgPerson): OrgChartPerson {
  return {
    id: p.id,
    managerId: p.manager_id ?? null,
    name: p.name,
    preferredName: p.preferred_name ?? undefined,
    email: p.email ?? undefined,
    title: p.title ?? undefined,
    department: p.department ?? undefined,
    businessUnit: p.business_unit ?? undefined,
    photoUrl: p.photo_url ?? undefined,
    location: p.location ?? undefined,
    city: p.city ?? undefined,
    country: p.country ?? undefined,
    groups: p.groups ?? [],
    status: p.status ?? undefined,
    startDate: p.start_date ?? undefined,
  };
}

/** Build chart people from the onboarding store's parsed directory. Email is the
 *  node id; a manager edge is kept only when the manager is also in the set. */
export function directoryToChart(people: DirectoryPerson[]): OrgChartPerson[] {
  const emails = new Set(people.map((p) => p.email));
  return people.map((p) => ({
    id: p.email,
    managerId:
      p.managerEmail && emails.has(p.managerEmail) ? p.managerEmail : null,
    name: p.name,
    preferredName: p.preferredName,
    email: p.email,
    title: p.title,
    department: p.department,
    businessUnit: p.businessUnit,
    photoUrl: p.photoUrl,
    location: p.location,
    city: p.city,
    country: p.country,
    groups: p.teams,
    status: p.status,
    startDate: p.startDate,
  }));
}

/** Fetch the org graph from the backend. Throws on any network/HTTP error so
 *  the caller can decide whether to fall back. */
export async function getOrgGraph(): Promise<OrgChartPerson[]> {
  const res = await apiFetch("/org/graph");
  if (!res.ok) throw new Error(`org graph request failed: ${res.status}`);
  const data = (await res.json()) as ApiOrgGraph;
  return data.people.map(fromApi);
}
