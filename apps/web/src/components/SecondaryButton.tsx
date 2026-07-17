import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";

/** Neutral, outlined secondary action button. */
export const SecondaryButton = React.forwardRef<
  HTMLButtonElement,
  Omit<ButtonProps, "variant">
>((props, ref) => <Button ref={ref} variant="secondary" {...props} />);
SecondaryButton.displayName = "SecondaryButton";
