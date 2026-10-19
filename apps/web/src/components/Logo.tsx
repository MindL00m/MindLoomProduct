import { cn } from "@/lib/utils";

/** MindLoom mark with its wordmark. */
export function Logo({
  className,
  showText = true,
}: {
  className?: string;
  /** When true, show the MindLoom wordmark beside the mark. */
  showText?: boolean;
}) {
  return (
    <a
      href="https://mindloomai.org"
      target="_blank"
      rel="noreferrer"
      className={cn("flex items-center gap-2 rounded-md hover:opacity-80", className)}
    >
      <img
        src="/mindloom-logo.png"
        alt="MindLoom"
        className="h-9 w-9 rounded-lg object-contain"
      />
      {showText && (
        <span className="text-base font-semibold tracking-tight">
          MindLoom
        </span>
      )}
    </a>
  );
}
