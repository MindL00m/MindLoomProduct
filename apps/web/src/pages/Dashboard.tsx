import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Activity,
  Home,
  Network,
  Menu,
  MessageSquareText,
  Upload,
  X,
  MessagesSquare,
  Workflow,
  Users,
} from "lucide-react";
import { Logo } from "@/components/Logo";
import UploadData from "@/pages/UploadData";
import AskView from "@/pages/AskView";
import KnowledgeGraphView from "@/pages/KnowledgeGraphView";
import HomePage from "@/pages/HomePage";
import ExpertMessages from "@/pages/ExpertMessages";
import StatusView from "@/pages/StatusView";
import WorkflowsView from "@/pages/WorkflowsView";
import WorkspacesView from "@/pages/WorkspacesView";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";
import { getOrgSummary, type OrgSummary } from "@/services/auth";
import { getExpertInboxCount } from "@/services/reviews";
import { isExtensionSkill, listSkillFiles } from "@/services/skillFiles";

type ViewId =
  | "home"
  | "status"
  | "ask"
  | "messages"
  | "workspaces"
  | "workflows"
  | "upload"
  | "graph";

const NAV: { id: ViewId; label: string; icon: typeof Home }[] = [
  { id: "home", label: "Home", icon: Home },
  { id: "status", label: "Status", icon: Activity },
  { id: "ask", label: "Ask", icon: MessageSquareText },
  { id: "messages", label: "Expert Messages", icon: MessagesSquare },
  { id: "workspaces", label: "Workspaces", icon: Users },
  { id: "workflows", label: "Workflows", icon: Workflow },
  { id: "upload", label: "Upload", icon: Upload },
  { id: "graph", label: "Knowledge Graph", icon: Network },
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
  const [view, setView] = useState<ViewId>("home");
  const [orgSummary, setOrgSummary] = useState<OrgSummary | null>(null);
  const [expertNotifications, setExpertNotifications] = useState(0);
  const [workflowNotifications, setWorkflowNotifications] = useState(0);
  const isAdmin = role === "admin";
  const availableNav = NAV.filter(
    (item) => isAdmin || item.id !== "graph",
  );

  const tabParam = searchParams.get("tab");
  const connectedParam = searchParams.get("connected");
  const setupParam = searchParams.get("setup");
  const errorParam = searchParams.get("error");

  useEffect(() => {
    if (tabParam === "apps" || tabParam === "organization" || tabParam === "home") {
      setView("home");
    }
    if (tabParam === "status") setView("status");
    if (tabParam === "messages") setView("messages");
    if (tabParam === "workspaces") setView("workspaces");
    if (tabParam === "workflows") setView("workflows");
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
    const refresh = () =>
      void listSkillFiles()
        .then((rows) =>
          setWorkflowNotifications(
            rows.filter(
              (skill) => isExtensionSkill(skill) && skill.status === "proposed",
            ).length,
          ),
        )
        .catch(() => setWorkflowNotifications(0));
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!isAdmin && view === "graph") setView("home");
  }, [isAdmin, view]);

  function clearOAuthParams() {
    const next = new URLSearchParams(searchParams);
    next.delete("connected");
    next.delete("setup");
    next.delete("error");
    setSearchParams(next, { replace: true });
  }

  const activeLabel = availableNav.find((n) => n.id === view)?.label ?? "Home";

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
              {item.id === "messages" && expertNotifications > 0 && (
                <span className="ml-auto min-w-5 rounded-full bg-destructive px-1.5 text-center text-xs text-destructive-foreground">
                  {expertNotifications > 99 ? "99+" : expertNotifications}
                </span>
              )}
              {item.id === "workflows" && workflowNotifications > 0 && (
                <span className="ml-auto min-w-5 rounded-full bg-destructive px-1.5 text-center text-xs text-destructive-foreground">
                  {workflowNotifications > 99 ? "99+" : workflowNotifications}
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

        {view === "home" && (
          <main className="flex-1 p-4 lg:p-8">
            <HomePage
              summary={orgSummary}
              isAdmin={isAdmin}
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

        {view === "status" && (
          <main className="flex min-h-0 flex-1 flex-col overflow-hidden p-4 lg:p-8">
            <StatusView />
          </main>
        )}

        {view === "ask" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <AskView />
          </main>
        )}

        {view === "messages" && (
          <main className="h-[calc(100dvh-4rem)] p-4 lg:p-6">
            <ExpertMessages onCountChange={setExpertNotifications} />
          </main>
        )}

        {view === "workspaces" && (
          <main className="h-[calc(100dvh-4rem)] p-4 lg:p-6">
            <WorkspacesView />
          </main>
        )}

        {view === "workflows" && (
          <main className="flex-1 overflow-y-auto p-4 lg:p-8">
            <WorkflowsView />
          </main>
        )}

        {view === "upload" && (
          <main className="flex-1 p-4 lg:p-8">
            <UploadData />
          </main>
        )}

        {view === "graph" && isAdmin && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <KnowledgeGraphView />
          </main>
        )}

      </div>
    </div>
  );
}
