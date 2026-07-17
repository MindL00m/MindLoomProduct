/** Org-chart data model + a tidy-tree (forest) layout for the REPORTS_TO graph.
 *
 *  The reporting graph is a forest: each person has at most one manager, so we
 *  lay out each tree top-down and place trees side by side. The algorithm is
 *  cycle-safe (a node is only placed once) and handles people whose manager is
 *  missing from the set (they become roots). */

export interface OrgChartPerson {
  id: string;
  managerId: string | null;
  name: string;
  preferredName?: string;
  email?: string;
  title?: string;
  department?: string;
  businessUnit?: string;
  photoUrl?: string;
  location?: string;
  city?: string;
  country?: string;
  groups: string[];
  status?: string;
  startDate?: string;
}

export interface LaidOutNode {
  person: OrgChartPerson;
  /** Top-left pixel coordinates of the node box. */
  x: number;
  y: number;
  depth: number;
}

export interface OrgEdge {
  from: string; // report (child)
  to: string; // manager (parent)
}

export interface OrgLayout {
  nodes: LaidOutNode[];
  edges: OrgEdge[];
  width: number;
  height: number;
}

// Node + spacing geometry, shared with the renderer.
export const NODE_W = 184;
export const NODE_H = 76;
export const H_GAP = 28;
export const V_GAP = 72;

const COL = NODE_W + H_GAP;
const ROW = NODE_H + V_GAP;

export function computeOrgLayout(people: OrgChartPerson[]): OrgLayout {
  const byId = new Map(people.map((p) => [p.id, p]));

  // Children keyed by manager id (only managers present in the set count).
  const children = new Map<string, string[]>();
  const roots: string[] = [];
  for (const p of people) {
    const hasManager = p.managerId != null && byId.has(p.managerId);
    if (hasManager) {
      const list = children.get(p.managerId as string) ?? [];
      list.push(p.id);
      children.set(p.managerId as string, list);
    } else {
      roots.push(p.id);
    }
  }

  // Stable ordering for deterministic layouts.
  const byName = (a: string, b: string) =>
    (byId.get(a)?.name ?? "").localeCompare(byId.get(b)?.name ?? "");
  roots.sort(byName);
  for (const list of children.values()) list.sort(byName);

  const gridX = new Map<string, number>();
  const depthOf = new Map<string, number>();
  const visited = new Set<string>();
  let cursor = 0;

  // Returns the assigned grid-x for `id` (parents center over their children).
  function place(id: string, depth: number): number {
    if (visited.has(id)) return gridX.get(id) ?? cursor;
    visited.add(id);
    depthOf.set(id, depth);

    const kids = (children.get(id) ?? []).filter((k) => !visited.has(k));
    let x: number;
    if (kids.length === 0) {
      x = cursor++;
    } else {
      const xs = kids.map((k) => place(k, depth + 1));
      x = (xs[0] + xs[xs.length - 1]) / 2;
    }
    gridX.set(id, x);
    return x;
  }

  for (const root of roots) place(root, 0);
  // Defensive: place any node trapped in a cycle that was never reached.
  for (const p of people) if (!visited.has(p.id)) place(p.id, 0);

  const nodes: LaidOutNode[] = people.map((p) => ({
    person: p,
    x: (gridX.get(p.id) ?? 0) * COL,
    y: (depthOf.get(p.id) ?? 0) * ROW,
    depth: depthOf.get(p.id) ?? 0,
  }));

  const edges: OrgEdge[] = people
    .filter((p) => p.managerId != null && byId.has(p.managerId))
    .map((p) => ({ from: p.id, to: p.managerId as string }));

  const maxX = nodes.reduce((m, n) => Math.max(m, n.x), 0);
  const maxY = nodes.reduce((m, n) => Math.max(m, n.y), 0);

  return {
    nodes,
    edges,
    width: maxX + NODE_W,
    height: maxY + NODE_H,
  };
}

/** Two-letter initials for the avatar fallback. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Deterministic brandy accent color per node, for avatar fallbacks. */
export function avatarColor(id: string): string {
  const palette = ["#DD700B", "#7C7D75", "#B85C09", "#8F4707", "#5b5c55"];
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}
