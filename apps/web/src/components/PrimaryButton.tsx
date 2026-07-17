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
>(({ loading, disabled, children, ...props }, ref) => (
  <Button ref={ref} variant="primary" disabled={loading || disabled} {...props}>
    {loading && <LoadingSpinner className="size-4" />}
    {children}
  </Button>
));
PrimaryButton.displayName = "PrimaryButton";
