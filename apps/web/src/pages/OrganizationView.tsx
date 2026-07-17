import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Network, RefreshCw, Users } from "lucide-react";
import { OrgChart } from "@/components/OrgChart";
import { EmployeeProfile } from "@/components/EmployeeProfile";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { getOrgGraph, directoryToChart } from "@/services/org";
import type { OrgChartPerson } from "@/lib/orgChart";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";

type LoadState = "loading" | "ready" | "empty";

export default function OrganizationView() {
  const directory = useOnboarding((s) => s.directory);
  const isAdmin = useSession((s) => s.role === "admin");
  const [people, setPeople] = useState<OrgChartPerson[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState<"server" | "local">("server");

  async function load() {
    setState("loading");
    // Prefer the backend; fall back to the directory parsed during onboarding.
    try {
      const fromServer = await getOrgGraph();
      if (fromServer.length > 0) {
        setPeople(fromServer);
        setSource("server");
        setState("ready");
        return;
      }
    } catch {
      // ignore — fall through to local
    }
    const local = directoryToChart(directory);
    setPeople(local);
    setSource("local");
    setState(local.length > 0 ? "ready" : "empty");
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byId = useMemo(
    () => new Map(people.map((p) => [p.id, p])),
    [people],
  );
  const selected = selectedId ? (byId.get(selectedId) ?? null) : null;
  const manager =
    selected?.managerId != null ? (byId.get(selected.managerId) ?? null) : null;
  const reports = useMemo(
    () =>
      selected
        ? people.filter((p) => p.managerId === selected.id)
        : [],
    [people, selected],
  );

  if (state === "loading") {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <LoadingSpinner label="Loading organization…" />
      </div>
    );
  }

  if (state === "empty") {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 text-center">
        <span className="flex size-14 items-center justify-center rounded-full bg-muted text-mist-700">
          <Network className="size-7" />
        </span>
        <div>
          <h2 className="text-lg font-semibold">No people yet</h2>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            Import your directory to visualise your organization and the
            reporting relationships between employees.
          </p>
        </div>
        {isAdmin ? (
          <PrimaryButton asChild>
            <Link to="/setup/csv?return=organization">Add employee directory</Link>
          </PrimaryButton>
        ) : (
          <p className="text-sm text-muted-foreground">
            Your organization administrator can add the employee directory later.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            Organization
          </h2>
          <p className="mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <Users className="size-3.5" />
            {people.length} {people.length === 1 ? "person" : "people"}
            <span className="text-mist-400">·</span>
            {source === "server" ? "Live from graph" : "From imported file"}
          </p>
        </div>
        <div className="flex gap-2">
          {isAdmin && (
            <PrimaryButton asChild>
              <Link to="/setup/csv?return=organization">Update directory</Link>
            </PrimaryButton>
          )}
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <RefreshCw className="size-3.5" />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-card">
        <div className="min-w-0 flex-1">
          <OrgChart
            people={people}
            selectedId={selectedId}
            onSelect={(id) => setSelectedId((cur) => (cur === id ? null : id))}
          />
        </div>
        <EmployeeProfile
          person={selected}
          manager={manager}
          reports={reports}
          onClose={() => setSelectedId(null)}
          onSelectPerson={(id) => setSelectedId(id)}
        />
      </div>
    </div>
  );
}
