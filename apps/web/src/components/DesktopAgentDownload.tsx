import { useEffect, useMemo, useState } from "react";
import { Check, Monitor, Terminal } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PrimaryButton } from "@/components/PrimaryButton";
import {
  DESKTOP_AGENT_DOWNLOAD_URL,
  isAbsoluteUrl,
  loomCaptureInstallCommand,
} from "@/lib/desktopAgent";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";

type Availability = "checking" | "ready" | "missing";

async function probeDownload(url: string): Promise<boolean> {
  const res = await fetch(url, { method: "HEAD" });
  if (!res.ok) return false;
  const type = (res.headers.get("content-type") || "").toLowerCase();
  // SPA fallback would serve index.html instead of the zip.
  if (type.includes("text/html")) return false;
  return true;
}

export function DesktopAgentDownload({
  compact = false,
  hideInstallLink: _hideInstallLink = false,
  className,
}: {
  compact?: boolean;
  hideInstallLink?: boolean;
  className?: string;
}) {
  const role = useSession((s) => s.role);
  const isAdmin = role === "admin";
  const [origin, setOrigin] = useState("");
  const [copied, setCopied] = useState(false);
  const [availability, setAvailability] = useState<Availability>(
    isAbsoluteUrl(DESKTOP_AGENT_DOWNLOAD_URL) ? "ready" : "checking",
  );

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    if (isAbsoluteUrl(DESKTOP_AGENT_DOWNLOAD_URL)) {
      setAvailability("ready");
      return;
    }
    let cancelled = false;
    void probeDownload(DESKTOP_AGENT_DOWNLOAD_URL)
      .then((ok) => {
        if (!cancelled) setAvailability(ok ? "ready" : "missing");
      })
      .catch(() => {
        if (!cancelled) setAvailability("missing");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const ready = availability === "ready";
  const installCommand = useMemo(
    () => (origin ? loomCaptureInstallCommand(origin, DESKTOP_AGENT_DOWNLOAD_URL) : ""),
    [origin],
  );

  async function copyInstallCommand() {
    if (!installCommand) return;
    await navigator.clipboard.writeText(installCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Card className={cn(className)}>
      <CardHeader className={cn("flex-row items-start gap-3", compact && "p-4")}>
        <span className="flex size-11 items-center justify-center rounded-lg border">
          <Monitor className="size-6" />
        </span>
        <div className="min-w-0 flex-1">
          <CardTitle className="text-lg">MindLoom Capture for Mac</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Menu-bar agent that captures allowlisted app activity on your Mac and
            uploads task summaries to MindLoom. macOS 13 or later.
          </p>
        </div>
      </CardHeader>
      <CardContent className={cn("space-y-3", compact && "p-4 pt-0")}>
        {availability === "checking" && (
          <p className="text-sm text-muted-foreground">Checking for a Mac download…</p>
        )}
        {availability === "missing" && (
          <p className="text-sm text-muted-foreground">
            The Mac app is not published on this site yet.
            {isAdmin
              ? " Package it on a Mac with your public web URL, then rebuild the frontend so the zip is included."
              : " Ask an administrator to publish MindLoom Capture."}
          </p>
        )}
        {ready && !compact && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Install from Terminal. It downloads the app, puts it in your
              Applications folder, and opens it.
            </p>
            <ol className="list-decimal space-y-1 pl-4 text-sm text-muted-foreground">
              <li>Copy the command below and paste it into Terminal, then press Return.</li>
              <li>Enable Accessibility when asked, quit, and reopen.</li>
              <li>Sign in with Google from the agent window.</li>
            </ol>
            {installCommand ? (
              <pre className="overflow-x-auto rounded-md border bg-muted/40 p-2 text-[11px] leading-snug text-foreground">
                {installCommand}
              </pre>
            ) : null}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          {ready ? (
            <PrimaryButton type="button" onClick={() => void copyInstallCommand()} disabled={!installCommand}>
              {copied ? <Check className="size-4" /> : <Terminal className="size-4" />}
              {copied ? "Copied" : "Copy Terminal install"}
            </PrimaryButton>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
