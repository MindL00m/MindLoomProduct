import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  Network,
  Plug,
  Settings,
  Plus,
  Menu,
  MessageSquareText,
  Upload,
  X,
  LayoutGrid,
} from "lucide-react";
import { FileSpreadsheet } from "lucide-react";
import { Logo } from "@/components/Logo";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { SecondaryButton } from "@/components/SecondaryButton";
import { GoogleIcon } from "@/components/icons";
import OrganizationView from "@/pages/OrganizationView";
import UploadData from "@/pages/UploadData";
import AskView from "@/pages/AskView";
import AppsView from "@/pages/AppsView";
import KnowledgeGraphView from "@/pages/KnowledgeGraphView";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";

type ViewId =
  | "overview"
  | "ask"
  | "upload"
  | "organization"
  | "apps"
  | "graph"
  | "sources"
  | "settings";

const NAV: { id: ViewId; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "ask", label: "Ask", icon: MessageSquareText },
  { id: "upload", label: "Upload", icon: Upload },
  { id: "organization", label: "Organization", icon: Users },
  { id: "apps", label: "Apps", icon: LayoutGrid },
  { id: "graph", label: "Knowledge Graph", icon: Network },
  { id: "sources", label: "Sources", icon: Plug },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function Dashboard() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { summary, selectedProvider, csvFileName } = useOnboarding();
  const orgName = useSession((s) => s.orgName);
  const email = useSession((s) => s.email);
  const clearSession = useSession((s) => s.clearSession);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [view, setView] = useState<ViewId>("overview");

  const tabParam = searchParams.get("tab");
  const connectedParam = searchParams.get("connected");
  const errorParam = searchParams.get("error");

  useEffect(() => {
    if (tabParam === "apps") setView("apps");
  }, [tabParam]);

  function clearOAuthParams() {
    const next = new URLSearchParams(searchParams);
    next.delete("connected");
    next.delete("error");
    setSearchParams(next, { replace: true });
  }

  const activeLabel = NAV.find((n) => n.id === view)?.label ?? "Overview";

  const isCsv = selectedProvider === "csv";
  const sourceName = isCsv ? "CSV Upload" : "Google Workspace";
  const sourceSub = isCsv
    ? (csvFileName ?? "Imported from file")
    : "Last sync: Just now";
  const sourceIcon = isCsv ? (
    <FileSpreadsheet className="size-6 text-brand" />
  ) : (
    <GoogleIcon className="size-6" />
  );

  return (
    <div className="flex min-h-dvh bg-muted/40">
      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-border bg-background transition-transform lg:static lg:translate-x-0",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center justify-between px-5">
          <Logo />
          <button
            className="lg:hidden"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
          >
            <X className="size-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-2" aria-label="Primary">
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => {
                setView(item.id);
                setMobileNavOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                view === item.id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )}
            >
              <item.icon className="size-4" aria-hidden="true" />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="border-t border-border p-4">
          <p className="truncate text-sm font-medium">{orgName || "Organization"}</p>
          <p className="truncate text-xs text-muted-foreground">{email}</p>
        </div>
      </aside>

      {mobileNavOpen && (
        <div
          className="fixed inset-0 z-20 bg-foreground/20 lg:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center gap-3 border-b border-border bg-background px-4 lg:px-8">
          <button
            className="lg:hidden"
            aria-label="Open navigation"
            onClick={() => setMobileNavOpen(true)}
          >
            <Menu className="size-5" />
          </button>
          <h1 className="text-sm font-semibold text-foreground">{activeLabel}</h1>
          <button
            type="button"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground"
            onClick={() => {
              clearSession();
              navigate("/setup");
            }}
          >
            Sign out
          </button>
        </header>

        {view === "overview" && (
          <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 p-4 lg:p-8">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight">
                Welcome to Company Brain
              </h2>
              <p className="mt-1 text-muted-foreground">
                Your organization graph is live. Connect more sources to enrich
                it.
              </p>
            </div>

            {/* Stat tiles */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                { label: "People", value: summary?.people ?? 127 },
                { label: "Departments", value: summary?.departments ?? 8 },
                { label: "Groups", value: summary?.groups ?? 24 },
                { label: "Sources", value: 1 },
              ].map((s) => (
                <Card key={s.label}>
                  <CardContent className="p-4">
                    <p className="text-sm text-muted-foreground">{s.label}</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">
                      {s.value}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* Connected sources */}
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Connected Sources</CardTitle>
                {/* Disabled per spec — single source connected in this demo. */}
                <SecondaryButton
                  size="sm"
                  onClick={() => {
                    setView("apps");
                    setSearchParams({ tab: "apps" }, { replace: true });
                  }}
                >
                  <Plus />
                  Connect Another Source
                </SecondaryButton>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-4 rounded-md border border-border p-4">
                  <span className="flex size-10 items-center justify-center rounded-md border border-border bg-background">
                    {sourceIcon}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{sourceName}</p>
                    <p className="truncate text-sm text-muted-foreground">
                      {sourceSub}
                    </p>
                  </div>
                  <StatusBadge tone="healthy" dot>
                    Healthy
                  </StatusBadge>
                </div>
              </CardContent>
            </Card>
          </main>
        )}

        {view === "ask" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <AskView />
          </main>
        )}

        {view === "upload" && (
          <main className="flex-1 p-4 lg:p-8">
            <UploadData />
          </main>
        )}

        {view === "organization" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <OrganizationView />
          </main>
        )}

        {view === "apps" && (
          <main className="flex-1 p-4 lg:p-8">
            <AppsView
              oauthStatus={
                connectedParam === "google_calendar" ||
                connectedParam === "google_workspace" ||
                connectedParam === "microsoft_teams"
                  ? "connected"
                  : errorParam
                    ? "error"
                    : null
              }
              oauthError={errorParam}
              onOAuthHandled={clearOAuthParams}
            />
          </main>
        )}

        {view === "graph" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <KnowledgeGraphView />
          </main>
        )}

        {view !== "overview" &&
          view !== "organization" &&
          view !== "upload" &&
          view !== "ask" &&
          view !== "apps" &&
          view !== "graph" && (
            <main className="flex flex-1 flex-col items-center justify-center p-8 text-center">
              <p className="text-sm text-muted-foreground">
                {activeLabel} is coming soon.
              </p>
            </main>
          )}
      </div>
    </div>
  );
}
