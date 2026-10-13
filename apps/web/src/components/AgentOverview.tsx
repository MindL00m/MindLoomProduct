import { Link } from "react-router-dom";
import { ArrowRight, Bot, ScanSearch, Send, ShieldCheck, Waypoints } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { dashboardHref } from "@/lib/navigation";

type SetupLink = {
  label: string;
  /** In-app route, or a `#id` anchor on the home page. */
  to: string;
  adminOnly?: boolean;
};

type Agent = {
  name: string;
  role: string;
  icon: typeof Bot;
  description: string;
  links: SetupLink[];
};

const AGENTS: Agent[] = [
  {
    name: "LoomBot",
    role: "Ask and collaborate",
    icon: Bot,
    description:
      "How people reach the company brain. Ask in plain language and get answers cited from your documents, conversations, and approved workflows. In Workspaces, mention @Loombot to answer from company knowledge or from a workspace's CONTEXT.md. Questions it can't answer confidently are routed to the right expert.",
    links: [
      { label: "Ask your first question", to: dashboardHref("ask") },
      { label: "Create a workspace", to: dashboardHref("workspaces") },
      { label: "Check expert messages", to: dashboardHref("messages") },
      { label: "Choose the chat model", to: "#chat-model", adminOnly: true },
    ],
  },
  {
    name: "Weaver",
    role: "Capture",
    icon: Waypoints,
    description:
      "Weaves knowledge into the brain. Connect Google Workspace, Microsoft 365, Zoom, and GitHub, upload documents and chat exports, and record how work is done with MindLoom Capture on your Mac or in Chrome.",
    links: [
      { label: "Import knowledge", to: dashboardHref("import") },
      { label: "Install MindLoom Capture", to: dashboardHref("capture") },
    ],
  },
  {
    name: "Keeper",
    role: "Keep true",
    icon: ShieldCheck,
    description:
      "Keeps the brain accurate and private. It enforces organization and source permissions, queues conflicting or ageing knowledge for review, and turns expert answers into versioned knowledge.",
    links: [
      {
        label: "Update the employee directory",
        to: "/setup/csv?return=organization",
        adminOnly: true,
      },
      { label: "Answer expert requests", to: dashboardHref("messages") },
      {
        label: "Resolve the knowledge review queue",
        to: dashboardHref("import"),
        adminOnly: true,
      },
    ],
  },
  {
    name: "Lens",
    role: "Understand",
    icon: ScanSearch,
    description:
      "Reads the graph so you don't have to. Home shows open projects, issues, and action items with their latest evidence alongside the organization dashboard.",
    links: [
      { label: "View company status", to: dashboardHref("home") },
    ],
  },
  {
    name: "Shuttle",
    role: "Act",
    icon: Send,
    description:
      "Carries approved work out. It turns MindLoom Capture sessions into workflows you review and publish, delivers expert requests over Gmail, Outlook, and Teams, and opens GitHub pull requests only after you approve them.",
    links: [
      { label: "Review automated workflows", to: dashboardHref("shuttle") },
      { label: "Connect GitHub for pull requests", to: dashboardHref("import") },
    ],
  },
];

const linkClass =
  "inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm";

export function AgentOverview({ isAdmin }: { isAdmin: boolean }) {
  return (
    <section aria-labelledby="agents-heading" className="space-y-4">
      <div>
        <h2 id="agents-heading" className="text-xl font-semibold tracking-tight">
          Your MindLoom agents
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          People reach the company brain through LoomBot, and four agents keep it running.
          Use the links to set each one up.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {AGENTS.map((agent) => {
          const links = agent.links.filter((link) => isAdmin || !link.adminOnly);
          return (
            <Card key={agent.name} className="flex flex-col">
              <CardContent className="flex flex-1 flex-col p-5">
                <div className="flex items-center gap-3">
                  <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <agent.icon className="size-5" aria-hidden="true" />
                  </span>
                  <div>
                    <h3 className="font-semibold leading-tight">{agent.name}</h3>
                    <p className="text-xs text-muted-foreground">{agent.role}</p>
                  </div>
                </div>
                <p className="mt-3 flex-1 text-sm leading-relaxed text-muted-foreground">
                  {agent.description}
                </p>
                <ul className="mt-4 space-y-1.5 border-t border-border pt-3">
                  {links.map((link) => (
                    <li key={link.label}>
                      {link.to.startsWith("#") ? (
                        <a href={link.to} className={linkClass}>
                          {link.label}
                          <ArrowRight className="size-3.5" aria-hidden="true" />
                        </a>
                      ) : (
                        <Link to={link.to} className={linkClass}>
                          {link.label}
                          <ArrowRight className="size-3.5" aria-hidden="true" />
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </section>
  );
}
