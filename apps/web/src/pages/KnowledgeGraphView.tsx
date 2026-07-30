import { useCallback, useEffect, useMemo, useState } from "react";
import { Network, RefreshCw } from "lucide-react";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { SecondaryButton } from "@/components/SecondaryButton";
import {
  getKnowledgeGraph,
  LABEL_COLORS,
  nodeCaption,
  primaryLabel,
  type GraphEdge,
  type GraphNode,
} from "@/services/graph";
import { cn } from "@/lib/utils";

const TYPE_ORDER = ["Person", "Entity", "Chunk", "Document", "Question"];
const COL_WIDTH = 200;
const ROW_HEIGHT = 64;
const NODE_R = 10;
const PAD = 48;

interface LayoutNode extends GraphNode {
  x: number;
  y: number;
  caption: string;
  color: string;
  kind: string;
}

function layoutNodes(nodes: GraphNode[]): LayoutNode[] {
  const byKind = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const kind = primaryLabel(node.labels);
    const list = byKind.get(kind) ?? [];
    list.push(node);
    byKind.set(kind, list);
  }

  const laidOut: LayoutNode[] = [];
  for (let col = 0; col < TYPE_ORDER.length; col++) {
    const kind = TYPE_ORDER[col];
    const group = byKind.get(kind) ?? [];
    group.forEach((node, row) => {
      laidOut.push({
        ...node,
        x: PAD + col * COL_WIDTH,
        y: PAD + row * ROW_HEIGHT,
        caption: nodeCaption(node),
        color: LABEL_COLORS[kind] ?? "#64748b",
        kind,
      });
    });
  }

  // Any unexpected labels go in a trailing column.
  const extras = nodes.filter((n) => !TYPE_ORDER.includes(primaryLabel(n.labels)));
  extras.forEach((node, row) => {
    const kind = primaryLabel(node.labels);
    laidOut.push({
      ...node,
      x: PAD + TYPE_ORDER.length * COL_WIDTH,
      y: PAD + row * ROW_HEIGHT,
      caption: nodeCaption(node),
      color: LABEL_COLORS[kind] ?? "#64748b",
      kind,
    });
  });

  return laidOut;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export default function KnowledgeGraphView() {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getKnowledgeGraph();
      setNodes(data.nodes);
      setEdges(data.edges);
      setTruncated(data.truncated);
      setSelectedId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load graph.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const layout = useMemo(() => layoutNodes(nodes), [nodes]);
  const byId = useMemo(() => new Map(layout.map((n) => [n.id, n])), [layout]);

  const selected = selectedId ? byId.get(selectedId) : null;

  const outgoing = useMemo(
    () => (selectedId ? edges.filter((e) => e.source === selectedId) : []),
    [edges, selectedId],
  );
  const incoming = useMemo(
    () => (selectedId ? edges.filter((e) => e.target === selectedId) : []),
    [edges, selectedId],
  );
  const relatedEdgeIds = useMemo(
    () => new Set([...outgoing, ...incoming].map((e) => e.id)),
    [outgoing, incoming],
  );
  const highlightedNodes = useMemo(
    () =>
      new Set([
        ...outgoing.map((e) => e.target),
        ...incoming.map((e) => e.source),
      ]),
    [outgoing, incoming],
  );

  const width =
    PAD * 2 +
    (TYPE_ORDER.length + (layout.some((n) => !TYPE_ORDER.includes(n.kind)) ? 1 : 0)) *
      COL_WIDTH;
  const height =
    PAD * 2 +
    Math.max(1, ...layout.map((n) => n.y)) +
    ROW_HEIGHT;

  if (loading) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <LoadingSpinner label="Loading knowledge graph…" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-center">
        <p className="text-sm text-destructive">{error}</p>
        <SecondaryButton onClick={() => void load()}>
          <RefreshCw className="size-4" /> Retry
        </SecondaryButton>
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 text-center">
        <span className="flex size-14 items-center justify-center rounded-full bg-muted text-mist-700">
          <Network className="size-7" />
        </span>
        <div>
          <h2 className="text-lg font-semibold">Graph is empty</h2>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            Ingest conversations or documents to populate Person, Chunk, Entity,
            and Document nodes in Neo4j.
          </p>
        </div>
        <SecondaryButton onClick={() => void load()}>
          <RefreshCw className="size-4" /> Refresh
        </SecondaryButton>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm text-muted-foreground">
            {nodes.length} nodes · {edges.length} edges
            {truncated && " · truncated at 400 nodes"}
          </p>
          <p className="text-xs text-muted-foreground">
            Dev/debug view — click a node to inspect metadata and highlight connected
            edges (incoming and outgoing).
          </p>
        </div>
        <SecondaryButton size="sm" onClick={() => void load()}>
          <RefreshCw className="size-4" /> Refresh
        </SecondaryButton>
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <div className="min-w-0 flex-1 overflow-auto rounded-lg border border-border bg-card">
          <svg
            width={width}
            height={height}
            className="min-w-full"
            role="img"
            aria-label="Knowledge graph visualization"
          >
            {/* Column headers */}
            {TYPE_ORDER.map((kind, col) => {
              const count = layout.filter((n) => n.kind === kind).length;
              if (count === 0) return null;
              return (
                <text
                  key={kind}
                  x={PAD + col * COL_WIDTH}
                  y={PAD - 16}
                  className="fill-muted-foreground text-[11px] font-semibold uppercase tracking-wide"
                >
                  {kind} ({count})
                </text>
              );
            })}

            {/* Edges */}
            {edges.map((edge) => {
              const from = byId.get(edge.source);
              const to = byId.get(edge.target);
              if (!from || !to) return null;
              const isRelated = relatedEdgeIds.has(edge.id);
              const dimmed = selectedId != null && !isRelated;
              return (
                <g key={edge.id}>
                  <line
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={isRelated ? "#0f766e" : "#cbd5e1"}
                    strokeWidth={isRelated ? 2.5 : 1}
                    strokeOpacity={dimmed ? 0.12 : isRelated ? 1 : 0.55}
                    markerEnd={isRelated ? "url(#arrow)" : undefined}
                  />
                  {isRelated && (
                    <text
                      x={(from.x + to.x) / 2}
                      y={(from.y + to.y) / 2 - 4}
                      textAnchor="middle"
                      className="fill-teal-800 text-[9px] font-medium"
                    >
                      {edge.type}
                    </text>
                  )}
                </g>
              );
            })}

            <defs>
              <marker
                id="arrow"
                markerWidth="8"
                markerHeight="8"
                refX="6"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 Z" fill="#0f766e" />
              </marker>
            </defs>

            {/* Nodes */}
            {layout.map((node) => {
              const isSelected = node.id === selectedId;
              const isRelated = highlightedNodes.has(node.id);
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  className="cursor-pointer"
                  onClick={() => setSelectedId(node.id)}
                >
                  <circle
                    r={isSelected ? NODE_R + 3 : NODE_R}
                    fill={node.color}
                    stroke={isSelected ? "#0f172a" : isRelated ? "#0f766e" : "#fff"}
                    strokeWidth={isSelected || isRelated ? 2.5 : 1.5}
                    opacity={selectedId && !isSelected && !isRelated ? 0.45 : 1}
                  />
                  <text
                    x={NODE_R + 8}
                    y={4}
                    className={cn(
                      "text-[11px]",
                      isSelected ? "fill-foreground font-semibold" : "fill-foreground/80",
                    )}
                  >
                    {node.caption}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>

        <aside className="w-80 shrink-0 overflow-y-auto rounded-lg border border-border bg-card p-4">
          {selected ? (
            <div className="space-y-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Selected node
                </p>
                <p className="mt-1 font-medium">{selected.caption}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {selected.labels.join(" · ")}
                </p>
                <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                  {selected.id}
                </p>
              </div>

              {incoming.length > 0 ? (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Incoming edges ({incoming.length})
                  </p>
                  <ul className="mt-2 space-y-2">
                    {incoming.map((edge) => {
                      const source = byId.get(edge.source);
                      return (
                        <li
                          key={edge.id}
                          className="rounded-md border border-teal-200 bg-teal-50/50 px-2 py-1.5 text-xs"
                        >
                          <span>{source?.caption ?? edge.source}</span>
                          <span className="text-teal-800"> → </span>
                          <span className="font-semibold text-teal-900">{edge.type}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}

              {outgoing.length > 0 ? (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Outgoing edges ({outgoing.length})
                  </p>
                  <ul className="mt-2 space-y-2">
                    {outgoing.map((edge) => {
                      const target = byId.get(edge.target);
                      return (
                        <li
                          key={edge.id}
                          className="rounded-md border border-teal-200 bg-teal-50/50 px-2 py-1.5 text-xs"
                        >
                          <span className="font-semibold text-teal-900">{edge.type}</span>
                          <span className="text-teal-800"> → </span>
                          <span>{target?.caption ?? edge.target}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}

              {incoming.length === 0 && outgoing.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No edges in this view. Entities normally have incoming{" "}
                  <span className="font-medium">RELATES_TO</span> links from Chunks
                  (those may be truncated if the graph is capped).
                </p>
              ) : null}

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Properties
                </p>
                <dl className="mt-2 space-y-2">
                  {Object.entries(selected.properties)
                    .filter(([key]) => key !== "embedding")
                    .map(([key, value]) => (
                      <div key={key}>
                        <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          {key}
                        </dt>
                        <dd className="mt-0.5 break-all font-mono text-[11px] text-foreground">
                          {formatValue(value)}
                        </dd>
                      </div>
                    ))}
                </dl>
              </div>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted-foreground">
              <Network className="mb-2 size-8 opacity-40" />
              Click a node to inspect its metadata and connected edges.
            </div>
          )}
        </aside>
      </div>

      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {TYPE_ORDER.map((kind) => (
          <span key={kind} className="inline-flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: LABEL_COLORS[kind] }}
            />
            {kind}
          </span>
        ))}
      </div>
    </div>
  );
}
