import { Link } from "react-router-dom";
import { ArrowRight, Chrome } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DesktopAgentDownload } from "@/components/DesktopAgentDownload";
import { dashboardHref } from "@/lib/navigation";

const STEPS = [
  "Capture a session on your Mac or in Chrome. You choose which apps or tabs are recorded.",
  "MindLoom drafts a Skill File from the session: steps, important fields, warnings, and decision points.",
  "You or an expert review, name, and approve it in Automated workflows. Only approved workflows are published to Ask.",
];

export default function LoomCaptureView({ pendingWorkflows = 0 }: { pendingWorkflows?: number }) {
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">MindLoom Capture</h2>
        <p className="mt-1 text-muted-foreground">
          Record how work actually gets done, so knowledge that was never written down becomes
          answerable.
        </p>
      </div>

      <ol className="grid gap-3 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li key={step} className="rounded-lg border border-border bg-card p-4">
            <span className="flex size-6 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
              {index + 1}
            </span>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step}</p>
          </li>
        ))}
      </ol>

      <DesktopAgentDownload />

      <Card>
        <CardHeader className="flex-row items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <Chrome className="size-6" />
          </span>
          <div className="min-w-0 flex-1">
            <CardTitle className="text-lg">MindLoom Capture for Chrome</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Takes screenshots of the active tab only while capture is on. Screenshots wait on
              your device until you approve them, and you can redact or discard any of them
              before upload.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            The extension is not in the Chrome Web Store yet. An administrator can load it from{" "}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">apps/browser-extension</code>{" "}
            at <code className="rounded bg-muted px-1 py-0.5 text-xs">chrome://extensions</code>{" "}
            (Developer mode, then Load unpacked).
          </p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
        <p className="text-sm text-muted-foreground">
          {pendingWorkflows > 0
            ? `${pendingWorkflows} captured workflow${pendingWorkflows === 1 ? " is" : "s are"} waiting for review.`
            : "Captured sessions are reviewed under Shuttle."}
        </p>
        <Link
          to={dashboardHref("shuttle")}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
        >
          Review automated workflows
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      </div>
    </div>
  );
}
