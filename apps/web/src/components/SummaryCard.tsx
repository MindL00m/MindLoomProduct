import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface SummaryRow {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
}

export interface SummaryCardProps {
  rows: SummaryRow[];
  className?: string;
}

/** Key/value summary used on the "Organization Ready" screen. */
export function SummaryCard({ rows, className }: SummaryCardProps) {
  return (
    <Card className={cn("divide-y divide-border overflow-hidden", className)}>
      <dl>
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex items-center justify-between gap-4 px-5 py-3.5"
          >
            <dt className="flex items-center gap-2.5 text-sm text-muted-foreground">
              {row.icon}
              {row.label}
            </dt>
            <dd className="text-sm font-semibold tabular-nums text-foreground">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
