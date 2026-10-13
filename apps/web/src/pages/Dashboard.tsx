import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Bot,
  Home,
  Menu,
  MessageSquareText,
  MessagesSquare,
  Monitor,
  Plug,
  Send,
  Users,
  Waypoints,
  X,
} from "lucide-react";
import { Logo } from "@/components/Logo";
import AskView from "@/pages/AskView";
import HomePage from "@/pages/HomePage";
import ExpertMessages from "@/pages/ExpertMessages";
import ImportView from "@/pages/ImportView";
import LoomCaptureView from "@/pages/LoomCaptureView";
import WorkflowsView from "@/pages/WorkflowsView";
import WorkspacesView from "@/pages/WorkspacesView";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";
import { resolveView, type ViewId } from "@/lib/navigation";
import { getOrgSummary, type OrgSummary } from "@/services/auth";
import { getExpertInboxCount } from "@/services/reviews";
import { isExtensionSkill, listSkillFiles } from "@/services/skillFiles";

type NavItem = { id: ViewId; label: string; icon: typeof Home };
type NavSection = {
  id: string;
  label: string;
  icon: typeof Home;
  view?: ViewId;
  items?: NavItem[];
};

const NAV: NavSection[] = [
  {
    id: "home",
    label: "Home",
    icon: Home,
    view: "home",
  },
  {
    id: "loombot",
    label: "LoomBot",
    icon: Bot,
    items: [
      { id: "ask", label: "Ask", icon: MessageSquareText },
      { id: "workspaces", label: "Workspaces", icon: Users },
      { id: "messages", label: "Expert Messages", icon: MessagesSquare },
    ],
  },
  {
    id: "weaver",
    label: "Weaver",
    icon: Waypoints,
    items: [
      { id: "import", label: "Import", icon: Plug },
      { id: "capture", label: "MindLoom Capture", icon: Monitor },
    ],
  },
  {
    id: "shuttle",
    label: "Shuttle",
    icon: Send,
    view: "shuttle",
  },
];

const OAUTH_PROVIDERS = ["google_workspace", "microsoft_teams", "zoom"] as const;
type OAuthProvider = (typeof OAUTH_PROVIDERS)[number];

function asOAuthProvider(value: string | null): OAuthProvider | null {
  return OAUTH_PROVIDERS.includes(value as OAuthProvider) ? (value as OAuthProvider) : null;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const onboardingSummary = useOnboarding((s) => s.summary);
  const orgName = useSession((s) => s.orgName);
  const email = useSession((s) => s.email);
  const role = useSession((s) => s.role);
  const clearSession = useSession((s) => s.clearSession);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [orgSummary, setOrgSummary] = useState<OrgSummary | null>(null);
  const [expertNotifications, setExpertNotifications] = useState(0);
  const [workflowNotifications, setWorkflowNotifications] = useState(0);
  const isAdmin = role === "admin";
  const view = resolveView(searchParams.get("tab"));
  const setupParam = asOAuthProvider(searchParams.get("setup"));
  const connectedParam = asOAuthProvider(searchParams.get("connected"));
  const errorParam = searchParams.get("error");

  function openView(id: ViewId) {
    setSearchParams({ tab: id });
    setMobileNavOpen(false);
  }

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

  function clearOAuthParams() {
    const next = new URLSearchParams(searchParams);
    next.delete("connected");
    next.delete("setup");
    next.delete("error");
    setSearchParams(next, { replace: true });
  }

  const activeSection =
    NAV.find(
      (section) =>
        section.view === view || section.items?.some((item) => item.id === view),
    ) ?? NAV[0];
  const activeItem = activeSection.items?.find((item) => item.id === view);

  function badgeFor(id: ViewId): number {
    if (id === "messages") return expertNotifications;
    if (id === "shuttle") return workflowNotifications;
    return 0;
  }

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
        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-2" aria-label="Primary">
          {NAV.map((section) =>
            section.view ? (
              <button
                key={section.id}
                type="button"
                aria-current={view === section.view ? "page" : undefined}
                onClick={() => openView(section.view!)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  view === section.view
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                <section.icon className="size-4" aria-hidden="true" />
                {section.label}
                {badgeFor(section.view) > 0 && (
                  <span className="ml-auto min-w-5 rounded-full bg-destructive px-1.5 text-center text-xs text-destructive-foreground">
                    {badgeFor(section.view) > 99 ? "99+" : badgeFor(section.view)}
                  </span>
                )}
              </button>
            ) : (
            <div key={section.id} role="group" aria-labelledby={`nav-${section.id}`}>
              <p
                id={`nav-${section.id}`}
                className="flex items-center gap-2 px-3 pb-1 text-xs font-semibold text-foreground/70"
              >
                <section.icon className="size-3.5" aria-hidden="true" />
                {section.label}
              </p>
              <div className="space-y-0.5">
                {section.items?.map((item) => {
                  const badge = badgeFor(item.id);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      aria-current={view === item.id ? "page" : undefined}
                      onClick={() => openView(item.id)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-md py-2 pl-6 pr-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        view === item.id
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                      )}
                    >
                      <item.icon className="size-4" aria-hidden="true" />
                      {item.label}
                      {badge > 0 && (
                        <span className="ml-auto min-w-5 rounded-full bg-destructive px-1.5 text-center text-xs text-destructive-foreground">
                          {badge > 99 ? "99+" : badge}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
            ),
          )}
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
          <h1 className="text-sm font-semibold text-foreground">
            {activeItem ? (
              <>
                <span className="text-muted-foreground">{activeSection.label}</span>
                <span className="mx-1.5 text-muted-foreground/60" aria-hidden="true">/</span>
                {activeItem.label}
              </>
            ) : (
              activeSection.label
            )}
          </h1>
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
            <HomePage summary={orgSummary} isAdmin={isAdmin} />
          </main>
        )}

        {view === "ask" && (
          <main className="flex h-[calc(100dvh-4rem)] flex-col p-4 lg:p-6">
            <AskView />
          </main>
        )}

        {view === "workspaces" && (
          <main className="h-[calc(100dvh-4rem)] p-4 lg:p-6">
            <WorkspacesView />
          </main>
        )}

        {view === "messages" && (
          <main className="h-[calc(100dvh-4rem)] p-4 lg:p-6">
            <ExpertMessages onCountChange={setExpertNotifications} />
          </main>
        )}

        {view === "import" && (
          <main className="flex-1 p-4 lg:p-8">
            <ImportView
              isAdmin={isAdmin}
              setupProvider={setupParam}
              oauthStatus={connectedParam ? "connected" : errorParam ? "error" : null}
              oauthError={errorParam}
              onOAuthHandled={clearOAuthParams}
            />
          </main>
        )}

        {view === "capture" && (
          <main className="flex-1 p-4 lg:p-8">
            <LoomCaptureView pendingWorkflows={workflowNotifications} />
          </main>
        )}

        {view === "shuttle" && (
          <main className="flex-1 overflow-y-auto p-4 lg:p-8">
            <WorkflowsView />
          </main>
        )}
      </div>
    </div>
  );
}
