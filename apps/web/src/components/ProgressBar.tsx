import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export interface ProgressBarProps {
  /** 0–100. */
  value: number;
  className?: string;
  /** Render the numeric percentage to the right of the bar. */
  showValue?: boolean;
  label?: string;
}

/** Smooth, accessible determinate progress bar. */
export function ProgressBar({
  value,
  className,
  showValue = false,
  label = "Progress",
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div
        className="h-2.5 w-full overflow-hidden rounded-full bg-mist-200/70"
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <motion.div
          className="h-full rounded-full bg-primary"
          initial={{ width: 0 }}
          animate={{ width: `${clamped}%` }}
          transition={{ ease: "easeOut", duration: 0.35 }}
        />
      </div>
      {showValue && (
        <span className="w-10 shrink-0 text-right text-sm font-medium tabular-nums text-muted-foreground">
          {Math.round(clamped)}%
        </span>
      )}
    </div>
  );
}
