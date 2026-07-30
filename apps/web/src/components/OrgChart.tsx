import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import {
  NODE_H,
  NODE_W,
  computeOrgLayout,
  type LaidOutNode,
  type OrgChartPerson,
} from "@/lib/orgChart";
import { Avatar } from "./EmployeeProfile";
import { cn } from "@/lib/utils";

export interface OrgChartProps {
  people: OrgChartPerson[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

interface Transform {
  x: number;
  y: number;
  k: number;
}

const MIN_K = 0.3;
const MAX_K = 2.2;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

/** Pan/zoom-able SVG org chart. Nodes are HTML cards (via <foreignObject>) so
 *  they reuse the app's styling and are keyboard-focusable. */
export function OrgChart({ people, selectedId, onSelect }: OrgChartProps) {
  const layout = useMemo(() => computeOrgLayout(people), [people]);
  const nodeById = useMemo(
    () => new Map(layout.nodes.map((n) => [n.person.id, n])),
    [layout],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, k: 1 });
  const initialized = useRef(false);

  // Track container size.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setSize({
        w: entry.contentRect.width,
        h: entry.contentRect.height,
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = () => {
    if (!size.w || !size.h || !layout.width || !layout.height) return;
    const pad = 60;
    const k = clampK(
      Math.min(
        (size.w - pad) / layout.width,
        (size.h - pad) / layout.height,
        1.1,
      ),
    );
    setTransform({
      x: (size.w - layout.width * k) / 2,
      y: Math.max(24, (size.h - layout.height * k) / 2),
      k,
    });
  };

  // Fit once we first know both the layout and the container size.
  useLayoutEffect(() => {
    if (initialized.current) return;
    if (size.w && size.h && layout.width) {
      fit();
      initialized.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, layout]);

  // --- pan ---
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(
    null,
  );
  const svgRef = useRef<SVGSVGElement>(null);
  function onPointerDown(e: React.PointerEvent) {
    // Ignore non-primary buttons; node cards stopPropagation themselves.
    if (e.button !== 0) return;
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      tx: transform.x,
      ty: transform.y,
    };
    // Capture on the SVG so moves keep firing even over foreignObject HTML
    // (which otherwise can fire pointerleave on the SVG mid-drag).
    svgRef.current?.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: React.PointerEvent) {
    // Snapshot before setState: React may run the updater after endPan has
    // already cleared drag.current (e.g. pointerup / pointercancel).
    const d = drag.current;
    if (!d) return;
    setTransform((t) => ({
      ...t,
      x: d.tx + (e.clientX - d.x),
      y: d.ty + (e.clientY - d.y),
    }));
  }
  function endPan(e?: React.PointerEvent) {
    if (e && svgRef.current?.hasPointerCapture?.(e.pointerId)) {
      svgRef.current.releasePointerCapture(e.pointerId);
    }
    drag.current = null;
  }

  // --- zoom ---
  function onWheel(e: React.WheelEvent) {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    setTransform((t) => {
      const k = clampK(t.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
      const ratio = k / t.k;
      return { k, x: px - (px - t.x) * ratio, y: py - (py - t.y) * ratio };
    });
  }
  function zoomBy(factor: number) {
    setTransform((t) => {
      const k = clampK(t.k * factor);
      const ratio = k / t.k;
      const cx = size.w / 2;
      const cy = size.h / 2;
      return { k, x: cx - (cx - t.x) * ratio, y: cy - (cy - t.y) * ratio };
    });
  }

  const edgePath = (parent: LaidOutNode, child: LaidOutNode) => {
    const sx = parent.x + NODE_W / 2;
    const sy = parent.y + NODE_H;
    const ex = child.x + NODE_W / 2;
    const ey = child.y;
    const my = (sy + ey) / 2;
    return `M${sx},${sy} C${sx},${my} ${ex},${my} ${ex},${ey}`;
  };

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden bg-grid"
    >
      {/* Controls */}
      <div className="absolute right-3 top-3 z-10 flex flex-col gap-1 rounded-md border border-border bg-card/90 p-1 shadow-sm backdrop-blur">
        <ControlButton label="Zoom in" onClick={() => zoomBy(1.2)}>
          <Plus className="size-4" />
        </ControlButton>
        <ControlButton label="Zoom out" onClick={() => zoomBy(1 / 1.2)}>
          <Minus className="size-4" />
        </ControlButton>
        <ControlButton label="Fit to screen" onClick={fit}>
          <Maximize2 className="size-4" />
        </ControlButton>
      </div>

      <svg
        ref={svgRef}
        className="size-full cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        onWheel={onWheel}
      >
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          {/* Edges */}
          {layout.edges.map((edge) => {
            const child = nodeById.get(edge.from);
            const parent = nodeById.get(edge.to);
            if (!child || !parent) return null;
            const active =
              selectedId === edge.from || selectedId === edge.to;
            return (
              <path
                key={`${edge.from}->${edge.to}`}
                d={edgePath(parent, child)}
                fill="none"
                stroke={active ? "hsl(var(--primary))" : "hsl(var(--border))"}
                strokeWidth={active ? 2.5 : 1.5}
              />
            );
          })}

          {/* Nodes */}
          {layout.nodes.map((n) => (
            <foreignObject
              key={n.person.id}
              x={n.x}
              y={n.y}
              width={NODE_W}
              height={NODE_H}
              style={{ overflow: "visible" }}
            >
              <NodeCard
                node={n}
                selected={selectedId === n.person.id}
                onSelect={onSelect}
              />
            </foreignObject>
          ))}
        </g>
      </svg>

      <p className="pointer-events-none absolute bottom-2 left-3 text-xs text-muted-foreground">
        Drag to pan · scroll to zoom · click a person for details
      </p>
    </div>
  );
}

function NodeCard({
  node,
  selected,
  onSelect,
}: {
  node: LaidOutNode;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const { person } = node;
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${person.name}${person.title ? `, ${person.title}` : ""}`}
      // Stop pan from starting when interacting with a node.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={() => onSelect(person.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(person.id);
        }
      }}
      style={{ height: NODE_H }}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2.5 rounded-md border bg-card px-3 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-primary ring-2 ring-primary/30"
          : "border-border hover:border-mist-400 hover:shadow",
      )}
    >
      <Avatar person={person} size={40} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold leading-tight text-foreground">
          {person.preferredName || person.name}
        </p>
        {person.title && (
          <p className="truncate text-xs text-muted-foreground">
            {person.title}
          </p>
        )}
        {person.department && (
          <p className="truncate text-[11px] text-mist-700">
            {person.department}
          </p>
        )}
      </div>
    </div>
  );
}

function ControlButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="flex size-8 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </button>
  );
}
