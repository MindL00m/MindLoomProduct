import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { LoadingSpinner } from "./LoadingSpinner";

export interface PrimaryButtonProps extends Omit<ButtonProps, "variant"> {
  /** Show a spinner and disable the button. */
  loading?: boolean;
}

/** Brand-coloured primary action button with a built-in loading state. */
export const PrimaryButton = React.forwardRef<
  HTMLButtonElement,
  PrimaryButtonProps
>(({ loading, disabled, children, asChild, ...props }, ref) => {
  // Radix Slot (asChild) requires exactly one element child. Never inject the
  // spinner alongside that child — it breaks <PrimaryButton asChild><Link/>.
  if (asChild) {
    return (
      <Button
        ref={ref}
        variant="primary"
        disabled={loading || disabled}
        asChild
        {...props}
      >
        {children}
      </Button>
    );
  }
  return (
    <Button ref={ref} variant="primary" disabled={loading || disabled} {...props}>
      {loading && <LoadingSpinner className="size-4" />}
      {children}
    </Button>
  );
});
PrimaryButton.displayName = "PrimaryButton";
