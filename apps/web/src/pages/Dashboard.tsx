import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  Network,
  Plug,
  Settings,
  Menu,
  MessageSquareText,
  Upload,
  X,
  LayoutGrid,
  CheckCircle2,
  Circle,
  Bell,
  BookOpenCheck,
} from "lucide-react";
import { Logo } from "@/components/Logo";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SecondaryButton } from "@/components/SecondaryButton";
import OrganizationView from "@/pages/OrganizationView";
import UploadData from "@/pages/UploadData";
import AskView from "@/pages/AskView";
import AppsView from "@/pages/AppsView";
import KnowledgeGraphView from "@/pages/KnowledgeGraphView";
import ExpertInbox from "@/pages/ExpertInbox";
import SkillFiles from "@/pages/SkillFiles";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";
import { getOrgSummary, type OrgSummary } from "@/services/auth";
import { getExpertInboxCount } from "@/services/reviews";

type ViewId =
  | "overview"
  | "ask"
  | "expert-inbox"
  | "skill-files"
  | "upload"
  | "organization"
  | "apps"
  | "graph"
  | "sources"
  | "settings";

const NAV: { id: ViewId; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "ask", label: "Ask", icon: MessageSquareText },
  { id: "expert-inbox", label: "Expert inbox", icon: Bell },
  { id: "skill-files", label: "Skill Files", icon: BookOpenCheck },
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
  const onboardingSummary = useOnboarding((s) => s.summary);
  const orgName = useSession((s) => s.orgName);
  const email = useSession((s) => s.email);
  const role = useSession((s) => s.role);
  const clearSession = useSession((s) => s.clearSession);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [view, setView] = useState<ViewId>("overview");
  const [orgSummary, setOrgSummary] = useState<OrgSummary | null>(null);
  const [expertNotifications, setExpertNotifications] = useState(0);
  const isAdmin = role === "admin";
  const availableNav = NAV.filter(
    (item) => isAdmin || (item.id !== "apps" && item.id !== "graph"),
  );

  const tabParam = searchParams.get("tab");
  const connectedParam = searchParams.get("connected");
  const setupParam = searchParams.get("setup");
  const errorParam = searchParams.get("error");

  useEffect(() => {
    if (tabParam === "apps" && isAdmin) setView("apps");
    if (tabParam === "organization") setView("organization");
  }, [tabParam, isAdmin]);

  useEffect(() => {
    void getOrgSummary()
      .then(setOrgSummary)
      .catch(() => setOrgSummary(onboardingSummary));
  }, [onboardingSummary]);

  useEffect(() => {
    const refresh = () => void getExpertInboxCount().then(setExpertNotifications);
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!isAdmin && view === "apps") setView("overview");
  }, [isAdmin, view]);

  function clearOAuthParams() {
    const next = new URLSearchParams(searchParams);
    next.delete("connected");
    next.delete("setup");
    next.delete("error");
    setSearchParams(next, { replace: true });
  }

  const activeLabel = availableNav.find((n) => n.id === view)?.label ?? "Overview";

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
          {availableNav.map((item) => (
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
              {item.id === "expert-inbox" && expertNotifications > 0 && (
                <span className="ml-auto min-w-5 rounded-full bg-destructive px-1.5 text-center text-xs text-destructive-foreground">
                  {expertNotifications > 99 ? "99+" : expertNotifications}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="border-t border-border p-4">
          <p className="truncate text-sm font-medium">{orgName || "Organization"}</p>
          <p className="truncate text-xs text-muted-foreground">{email}</p>
          <p className="mt-1 text-xs capitalize text-muted-foreground">{role}</p>
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
                Welcome to Loom
              </h2>
              <p className="mt-1 text-muted-foreground">
                {isAdmin
                  ? "Start by connecting approved company knowledge. The employee directory can be added later."
                  : "Ask questions and explore the company knowledge you are allowed to access."}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                { label: "People", value: orgSummary?.people ?? 0 },
                { label: "Departments", value: orgSummary?.departments ?? 0 },
                { label: "Groups", value: orgSummary?.groups ?? 0 },
                { label: "Knowledge sources", value: 0 },
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

            {isAdmin ? (
              <Card>
                <CardHeader><CardTitle>Set up Loom</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  <SetupItem
                    complete={false}
                    title="Connect company knowledge"
                    description="Connect approved Google or Microsoft locations, or upload documents."
                    action="Connect a source"
                    onClick={() => {
                      setView("apps");
                      setSearchParams({ tab: "apps" }, { replace: true });
                    }}
                  />
                  <SetupItem
                    complete={(orgSummary?.people ?? 0) > 0}
                    title="Add your employee directory"
                    description="Optional — builds the org chart, identifies experts, and supports department access."
                    action="Add directory"
                    onClick={() => setView("organization")}
                  />
                  <SetupItem
                    complete={false}
                    title="Invite employees"
                    description="Do this after Loom contains useful knowledge."
                    action="Coming later"
                  />
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardHeader><CardTitle>Start using Loom</CardTitle></CardHeader>
                <CardContent>
                  <p className="mb-4 text-sm text-muted-foreground">
                    Your administrator manages company connections and permissions.
                  </p>
                  <SecondaryButton onClick={() => setView("ask")}>
                    <MessageSquareText className="size-4" /> Ask Loom
                  </SecondaryButton>
                </CardContent>
              </Card>
            )}
          </main>
        )}

        {view === "ask" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <AskView />
          </main>
        )}

        {view === "expert-inbox" && (
          <main className="flex-1 p-4 lg:p-8">
            <ExpertInbox onCountChange={setExpertNotifications} />
          </main>
        )}

        {view === "skill-files" && (
          <main className="flex-1 p-4 lg:p-8">
            <SkillFiles />
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

        {view === "apps" && isAdmin && (
          <main className="flex-1 p-4 lg:p-8">
            <AppsView
              setupProvider={
                setupParam === "google_workspace" ||
                setupParam === "microsoft_teams" ||
                setupParam === "zoom"
                  ? setupParam
                  : null
              }
              oauthStatus={
                connectedParam === "google_workspace" ||
                connectedParam === "microsoft_teams" ||
                connectedParam === "zoom"
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
          view !== "expert-inbox" &&
          view !== "skill-files" &&
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

function SetupItem({
  complete,
  title,
  description,
  action,
  onClick,
}: {
  complete: boolean;
  title: string;
  description: string;
  action: string;
  onClick?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-border p-4">
      {complete ? (
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" />
      ) : (
        <Circle className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
      </div>
      <SecondaryButton size="sm" onClick={onClick} disabled={!onClick}>
        {action}
      </SecondaryButton>
    </div>
  );
}
