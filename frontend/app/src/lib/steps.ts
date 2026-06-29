/** Wizard step sequences. The flow branches after "Choose Source": the Google
 *  path has OAuth + permissions steps, while the CSV path has a single upload
 *  step. Both rejoin at Sync → Ready. */

import type { ProviderId } from "@/services/types";

export interface WizardStep {
  id: string;
  label: string;
  path: string;
}

export type WizardFlow = "google" | "csv";

const SHARED_HEAD: WizardStep[] = [
  { id: "welcome", label: "Welcome", path: "/setup" },
  { id: "org", label: "Organization", path: "/setup/org" },
  { id: "source", label: "Source", path: "/setup/source" },
];

const SHARED_TAIL: WizardStep[] = [
  { id: "sync", label: "Sync", path: "/setup/sync" },
  { id: "complete", label: "Ready", path: "/setup/complete" },
];

const GOOGLE_STEPS: WizardStep[] = [
  ...SHARED_HEAD,
  { id: "connect", label: "Connect", path: "/setup/google" },
  { id: "permissions", label: "Permissions", path: "/setup/permissions" },
  ...SHARED_TAIL,
];

const CSV_STEPS: WizardStep[] = [
  ...SHARED_HEAD,
  { id: "upload", label: "Upload", path: "/setup/csv" },
  ...SHARED_TAIL,
];

/** Decide which flow to render the indicator for, given the current route and
 *  the (possibly null) selected provider. The route wins for path-specific
 *  pages so the indicator is correct even before state hydrates. */
export function flowForContext(
  pathname: string,
  provider: ProviderId | null,
): WizardFlow {
  if (pathname.startsWith("/setup/csv")) return "csv";
  if (
    pathname.startsWith("/setup/google") ||
    pathname.startsWith("/setup/permissions")
  ) {
    return "google";
  }
  return provider === "csv" ? "csv" : "google";
}

export function getSteps(flow: WizardFlow): WizardStep[] {
  return flow === "csv" ? CSV_STEPS : GOOGLE_STEPS;
}

export function stepIndexForPath(
  steps: WizardStep[],
  pathname: string,
): number {
  const idx = steps.findIndex((s) => s.path === pathname);
  return idx === -1 ? 0 : idx;
}
