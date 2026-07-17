import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface LoadingSpinnerProps {
  className?: string;
  label?: string;
}

/** Accessible spinning indicator. Provide `label` when used standalone. */
export function LoadingSpinner({ className, label }: LoadingSpinnerProps) {
  return (
    <span role="status" className="inline-flex items-center gap-2">
      <Loader2
        className={cn("size-5 animate-spin text-current", className)}
        aria-hidden="true"
      />
      <span className={label ? "text-sm text-muted-foreground" : "sr-only"}>
        {label ?? "Loading"}
      </span>
    </span>
  );
}
