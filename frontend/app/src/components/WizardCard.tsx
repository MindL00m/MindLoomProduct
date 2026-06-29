import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface WizardCardProps {
  title: string;
  subtitle?: string;
  /** Optional element rendered above the title (icon/illustration). */
  media?: React.ReactNode;
  children?: React.ReactNode;
  /** Footer actions, typically Back/Continue buttons. */
  footer?: React.ReactNode;
  className?: string;
  /** Center the header text (used by Welcome / Complete). */
  centered?: boolean;
}

/** Standard centered content card for a single wizard step. */
export function WizardCard({
  title,
  subtitle,
  media,
  children,
  footer,
  className,
  centered,
}: WizardCardProps) {
  return (
    <Card className={cn("p-6 sm:p-8", className)}>
      <div className={cn("space-y-2", centered && "text-center")}>
        {media && (
          <div className={cn("mb-4", centered && "flex justify-center")}>
            {media}
          </div>
        )}
        <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
          {title}
        </h1>
        {subtitle && (
          <p className="text-pretty text-sm text-muted-foreground sm:text-base">
            {subtitle}
          </p>
        )}
      </div>

      {children && <div className="mt-6">{children}</div>}

      {footer && <div className="mt-8">{footer}</div>}
    </Card>
  );
}
