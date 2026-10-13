/** Shared types for onboarding and setup UI. */

export type ProviderId = "google" | "microsoft" | "okta" | "csv";

export interface OAuthResult {
  connected: true;
  /** Email of the admin who authorized the connection. */
  account: string;
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
