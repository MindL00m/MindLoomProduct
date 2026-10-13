export type ViewId =
  | "home"
  | "ask"
  | "workspaces"
  | "messages"
  | "import"
  | "capture"
  | "shuttle";

const VIEW_IDS: readonly ViewId[] = [
  "home",
  "ask",
  "workspaces",
  "messages",
  "import",
  "capture",
  "shuttle",
];

/** Older `?tab=` values still used by OAuth redirects and saved links. */
const TAB_ALIASES: Record<string, ViewId> = {
  dashboard: "home",
  organization: "home",
  status: "home",
  graph: "home",
  apps: "import",
  connectors: "import",
  upload: "import",
  workflows: "shuttle",
};

export function resolveView(tab: string | null): ViewId {
  if (!tab) return "home";
  if ((VIEW_IDS as readonly string[]).includes(tab)) return tab as ViewId;
  return TAB_ALIASES[tab] ?? "home";
}

export function dashboardHref(view: ViewId): string {
  return `/dashboard?tab=${view}`;
}
