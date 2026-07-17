import { cn } from "@/lib/utils";

/** Loom wordmark from brand assets. */
export function Logo({
  className,
  showText = false,
}: {
  className?: string;
  /** When true, show a text label beside the logo (the asset already includes LOOM). */
  showText?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <img
        src="/loom-logo.png"
        alt="Loom"
        className="h-9 w-auto object-contain"
      />
      {showText && (
        <span className="text-base font-semibold tracking-tight text-foreground">
          Loom
        </span>
      )}
    </div>
  );
}
