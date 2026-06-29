import { motion } from "framer-motion";

/** Lightweight organization/network graphic used on the Welcome screen.
 *  Nodes gently pulse to feel alive without being distracting. */
export function OrgNetworkIllustration({ className }: { className?: string }) {
  const nodes = [
    { cx: 100, cy: 26, r: 13, fill: "hsl(var(--primary))" },
    { cx: 44, cy: 74, r: 10, fill: "#7C7D75" },
    { cx: 156, cy: 74, r: 10, fill: "#ADACA7" },
    { cx: 26, cy: 116, r: 7, fill: "#ADACA7" },
    { cx: 74, cy: 120, r: 7, fill: "#ADACA7" },
    { cx: 134, cy: 120, r: 7, fill: "#7C7D75" },
    { cx: 174, cy: 116, r: 7, fill: "#ADACA7" },
  ];
  const edges = [
    [0, 1],
    [0, 2],
    [1, 3],
    [1, 4],
    [2, 5],
    [2, 6],
  ];

  return (
    <svg
      viewBox="0 0 200 140"
      className={className}
      role="img"
      aria-label="Organization network"
    >
      <g stroke="hsl(var(--border))" strokeWidth={2}>
        {edges.map(([a, b], i) => (
          <line
            key={i}
            x1={nodes[a].cx}
            y1={nodes[a].cy}
            x2={nodes[b].cx}
            y2={nodes[b].cy}
          />
        ))}
      </g>
      {nodes.map((n, i) => (
        <motion.circle
          key={i}
          cx={n.cx}
          cy={n.cy}
          r={n.r}
          fill={n.fill}
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: i * 0.08, type: "spring", stiffness: 260, damping: 18 }}
          style={{ transformOrigin: `${n.cx}px ${n.cy}px` }}
        />
      ))}
    </svg>
  );
}
