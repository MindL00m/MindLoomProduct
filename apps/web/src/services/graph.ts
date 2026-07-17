/** Knowledge graph debug export from Neo4j. */

import { apiFetch } from "@/lib/api";

export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export async function getKnowledgeGraph(): Promise<KnowledgeGraphData> {
  const res = await apiFetch("/graph/debug");
  if (!res.ok) {
    let detail = `Graph request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    if (res.status === 404) {
      throw new Error(
        "Knowledge graph API not found. Rebuild the API container: docker compose up -d --build api",
      );
    }
    throw new Error(detail);
  }
  return res.json();
}

export function primaryLabel(labels: string[]): string {
  const order = ["Person", "Entity", "Chunk", "Document", "Question"];
  for (const label of order) {
    if (labels.includes(label)) return label;
  }
  return labels[0] ?? "Node";
}

export function nodeCaption(node: GraphNode): string {
  const p = node.properties;
  const label = primaryLabel(node.labels);
  if (label === "Person") {
    return String(p.name ?? p.canonical_name ?? p.canonical_email ?? node.id);
  }
  if (label === "Chunk") {
    const summary = p.summary as string | undefined;
    if (summary) return summary.length > 48 ? `${summary.slice(0, 48)}…` : summary;
    const raw = p.raw_text as string | undefined;
    if (raw) return raw.length > 48 ? `${raw.slice(0, 48)}…` : raw;
  }
  if (label === "Document") {
    return String(p.source_label ?? p.original_filename ?? p.document_id ?? node.id);
  }
  if (label === "Entity") {
    return String(p.name ?? p.canonical_name ?? node.id);
  }
  if (label === "Question") {
    const text = String(p.text ?? node.id);
    return text.length > 48 ? `${text.slice(0, 48)}…` : text;
  }
  return node.id.length > 24 ? `${node.id.slice(0, 24)}…` : node.id;
}

export const LABEL_COLORS: Record<string, string> = {
  Person: "#2563eb",
  Entity: "#9333ea",
  Chunk: "#16a34a",
  Document: "#ea580c",
  Question: "#ca8a04",
};
