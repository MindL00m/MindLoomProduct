import { Check } from "lucide-react";
import { motion } from "framer-motion";
import type { WizardStep } from "@/lib/steps";
import { cn } from "@/lib/utils";

export interface StepIndicatorProps {
  /** Steps to render. */
  steps: WizardStep[];
  /** Zero-based index of the active step. */
  current: number;
}

/** Horizontal stepper shown above the wizard card. Compact on mobile (dots
 *  only), labelled from `sm` upward. */
export function StepIndicator({ steps, current }: StepIndicatorProps) {
  return (
    <nav aria-label="Setup progress" className="mx-auto w-full max-w-2xl">
      <ol className="flex items-center justify-center">
        {steps.map((step, i) => {
          const isDone = i < current;
          const isActive = i === current;
          const isLast = i === steps.length - 1;
          return (
            <li
              key={step.id}
              className={cn("flex items-center", !isLast && "flex-1")}
              aria-current={isActive ? "step" : undefined}
            >
              <div className="flex flex-col items-center gap-1.5">
                <div
                  className={cn(
                    "flex size-7 items-center justify-center rounded-full border text-xs font-semibold transition-colors",
                    isDone &&
                      "border-primary bg-primary text-primary-foreground",
                    isActive &&
                      "border-primary bg-primary/10 text-primary ring-4 ring-primary/10",
                    !isDone &&
                      !isActive &&
                      "border-border bg-background text-muted-foreground",
                  )}
                >
                  {isDone ? (
                    <Check className="size-3.5" aria-hidden="true" />
                  ) : (
                    i + 1
                  )}
                </div>
                <span
                  className={cn(
                    "hidden text-[11px] font-medium sm:block",
                    isActive ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
              </div>
              {!isLast && (
                <div className="mx-1.5 h-0.5 flex-1 overflow-hidden rounded-full bg-border sm:mx-2">
                  <motion.div
                    className="h-full bg-primary"
                    initial={false}
                    animate={{ width: isDone ? "100%" : "0%" }}
                    transition={{ duration: 0.3 }}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
