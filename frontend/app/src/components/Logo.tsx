import { BrainCircuit } from "lucide-react";
import { cn } from "@/lib/utils";

/** Company Brain wordmark + glyph. */
export function Logo({
  className,
  showText = true,
}: {
  className?: string;
  showText?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-sm">
        <BrainCircuit className="size-5" aria-hidden="true" />
      </span>
      {showText && (
        <span className="text-base font-semibold tracking-tight text-foreground">
          Company Brain
        </span>
      )}
    </div>
  );
}
