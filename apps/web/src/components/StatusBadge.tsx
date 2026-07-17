import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold",
  {
    variants: {
      tone: {
        available:
          "bg-success/10 text-success ring-1 ring-inset ring-success/20",
        soon: "bg-muted text-muted-foreground ring-1 ring-inset ring-border",
        healthy:
          "bg-success/10 text-success ring-1 ring-inset ring-success/20",
        brand: "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200",
        neutral: "bg-secondary text-secondary-foreground ring-1 ring-inset ring-border",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface StatusBadgeProps
  extends VariantProps<typeof badgeVariants> {
  children: React.ReactNode;
  className?: string;
  /** Optional leading dot indicator. */
  dot?: boolean;
}

export function StatusBadge({ tone, dot, className, children }: StatusBadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone }), className)}>
      {dot && (
        <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {children}
    </span>
  );
}
