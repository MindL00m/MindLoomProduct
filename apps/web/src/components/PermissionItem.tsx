import { Check } from "lucide-react";

export interface PermissionItemProps {
  title: string;
  description: string;
  optional?: boolean;
}

/** A single requested-permission row with a green check, title and rationale. */
export function PermissionItem({
  title,
  description,
  optional,
}: PermissionItemProps) {
  return (
    <li className="flex items-start gap-3 py-3">
      <span
        className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-success/10 text-success"
        aria-hidden="true"
      >
        <Check className="size-3.5" strokeWidth={3} />
      </span>
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          {title}
          {optional && (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Optional
            </span>
          )}
        </p>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </li>
  );
}
