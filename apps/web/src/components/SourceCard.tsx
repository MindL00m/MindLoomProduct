import { Check } from "lucide-react";
import { StatusBadge } from "./StatusBadge";
import { cn } from "@/lib/utils";

export interface SourceCardProps {
  title: string;
  description: string;
  icon: React.ReactNode;
  available?: boolean;
  selected?: boolean;
  onSelect?: () => void;
}

/** Selectable identity-source tile. Unavailable sources are dimmed and show a
 *  "Coming Soon" badge; they are not focusable/clickable. */
export function SourceCard({
  title,
  description,
  icon,
  available = false,
  selected = false,
  onSelect,
}: SourceCardProps) {
  const interactive = available && !!onSelect;

  return (
    <button
      type="button"
      disabled={!interactive}
      aria-pressed={interactive ? selected : undefined}
      onClick={interactive ? onSelect : undefined}
      className={cn(
        "group relative flex w-full items-start gap-4 rounded-md border p-4 text-left transition-all",
        interactive &&
          "cursor-pointer hover:border-brand-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        selected
          ? "border-primary bg-brand-50/50 ring-1 ring-primary"
          : "border-border bg-card",
        !available && "cursor-not-allowed opacity-60",
      )}
    >
      <div className="flex size-11 shrink-0 items-center justify-center rounded-md border border-border bg-background">
        {icon}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold text-foreground">{title}</h3>
          {available ? (
            <StatusBadge tone="available" dot>
              Available
            </StatusBadge>
          ) : (
            <StatusBadge tone="soon">Coming Soon</StatusBadge>
          )}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>

      {selected && (
        <span className="absolute right-3 top-3 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Check className="size-3" aria-hidden="true" />
          <span className="sr-only">Selected</span>
        </span>
      )}
    </button>
  );
}
