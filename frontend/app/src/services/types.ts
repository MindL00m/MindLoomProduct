/** Shared types for the (mock) onboarding services. These mirror the shapes a
 *  real backend would return, so swapping the mock implementation for real
 *  fetch() calls later requires no changes in the UI layer. */

export type ProviderId = "google" | "microsoft" | "okta" | "csv";

export interface OAuthResult {
  connected: true;
  /** Email of the admin who authorized the connection. */
  account: string;
  /** Opaque token placeholder (never a real secret in the mock). */
  accessToken: string;
}

export type SyncStageId =
  | "connected"
  | "users"
  | "groups"
  | "graph"
  | "workspace";

export interface SyncStage {
  id: SyncStageId;
  label: string;
  status: "done" | "active" | "queued";
}

export interface SyncSnapshot {
  /** 0–100 overall progress. */
  progress: number;
  stages: SyncStage[];
  done: boolean;
}

export interface SetupSummary {
  organization: string;
  people: number;
  departments: number;
  groups: number;
}

/** Error categories the UI knows how to render a tailored retry screen for. */
export type SetupErrorKind =
  | "oauth_cancelled"
  | "network_timeout"
  | "sync_failed"
  | "csv_invalid";

export class SetupError extends Error {
  kind: SetupErrorKind;
  constructor(kind: SetupErrorKind, message: string) {
    super(message);
    this.name = "SetupError";
    this.kind = kind;
  }
}
