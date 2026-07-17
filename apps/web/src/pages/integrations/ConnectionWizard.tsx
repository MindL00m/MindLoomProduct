import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronLeft, Loader2 } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  confirmConnection,
  discoverConnectionResources,
  getConnectionPolicy,
  previewConnection,
  type ConnectionPolicy,
  type ConnectionPreview,
  type ConnectionProvider,
  type ConnectionResource,
} from "@/services/integrations";

const EMPTY_POLICY: ConnectionPolicy = {
  included_resource_ids: [],
  excluded_resource_ids: [],
  include_history: true,
  history_start_date: null,
  sync_frequency: "realtime",
  access_mode: "respect_source_permissions",
  allowed_departments: [],
  allowed_user_ids: [],
};

const STEPS = ["Choose content", "Import rules", "Search access", "Preview"];

export function ConnectionWizard({
  provider,
  onCancel,
  onComplete,
}: {
  provider: ConnectionProvider;
  onCancel: () => void;
  onComplete: (jobId?: string) => void;
}) {
  const [step, setStep] = useState(0);
  const [resources, setResources] = useState<ConnectionResource[]>([]);
  const [policy, setPolicy] = useState<ConnectionPolicy>(EMPTY_POLICY);
  const [preview, setPreview] = useState<ConnectionPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label =
    provider === "google_workspace"
      ? "Google Workspace"
      : provider === "microsoft_teams"
        ? "Microsoft 365"
        : "Zoom";

  useEffect(() => {
    let active = true;
    Promise.all([discoverConnectionResources(provider), getConnectionPolicy(provider)])
      .then(([available, saved]) => {
        if (!active) return;
        setResources(available);
        if (saved) {
          setPolicy({
            included_resource_ids: saved.included_resource_ids,
            excluded_resource_ids: saved.excluded_resource_ids,
            include_history: saved.include_history,
            history_start_date: saved.history_start_date,
            sync_frequency: saved.sync_frequency,
            access_mode: saved.access_mode,
            allowed_departments: saved.allowed_departments,
            allowed_user_ids: saved.allowed_user_ids,
          });
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Setup could not be loaded."))
      .finally(() => setLoading(false));
    return () => {
      active = false;
    };
  }, [provider]);

  const roots = useMemo(() => resources.filter((resource) => !resource.parent_id), [resources]);

  function toggleResource(id: string) {
    setPolicy((current) => {
      const selected = new Set(current.included_resource_ids);
      if (selected.has(id)) selected.delete(id);
      else selected.add(id);
      return { ...current, included_resource_ids: [...selected] };
    });
  }

  async function continueFlow() {
    setError(null);
    if (step === 0 && policy.included_resource_ids.length === 0) {
      setError("Choose at least one location or channel.");
      return;
    }
    if (step < 3) {
      setStep(step + 1);
      return;
    }
    setBusy(true);
    try {
      const result = await confirmConnection(provider, policy);
      onComplete(result.initial_job_ids[0]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection could not be started.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (step !== 3) return;
    setBusy(true);
    setPreview(null);
    previewConnection(provider, policy)
      .then(setPreview)
      .catch((err) => setError(err instanceof Error ? err.message : "Preview failed."))
      .finally(() => setBusy(false));
  }, [step, provider, policy]);

  return (
    <Card className="border-brand-200">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-brand-700">Continuous knowledge connection</p>
            <CardTitle className="mt-1">Set up {label}</CardTitle>
          </div>
          <button className="text-sm text-muted-foreground hover:text-foreground" onClick={onCancel}>
            Close
          </button>
        </div>
        <div className="grid grid-cols-4 gap-2 pt-4">
          {STEPS.map((name, index) => (
            <div key={name}>
              <div className={`h-1 rounded ${index <= step ? "bg-brand" : "bg-muted"}`} />
              <p className="mt-1 hidden text-xs text-muted-foreground sm:block">{name}</p>
            </div>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading ? (
          <div className="flex justify-center py-10"><Loader2 className="animate-spin" /></div>
        ) : step === 0 ? (
          <div className="space-y-3">
            <div>
              <h3 className="font-medium">Choose approved content</h3>
              <p className="text-sm text-muted-foreground">
                Only selected locations will be imported. You can change this later.
              </p>
            </div>
            {roots.map((root) => (
              <ResourceRow
                key={root.id}
                resource={root}
                children={resources.filter((item) => item.parent_id === root.id)}
                selected={new Set(policy.included_resource_ids)}
                onToggle={toggleResource}
              />
            ))}
          </div>
        ) : step === 1 ? (
          <div className="space-y-5">
            <div>
              <h3 className="font-medium">Historical information</h3>
              <label className="mt-3 flex gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={policy.include_history}
                  onChange={(event) => setPolicy({ ...policy, include_history: event.target.checked })}
                />
                Import existing approved content
              </label>
              {policy.include_history && (
                <label className="mt-3 block text-sm">
                  Only content changed after this date (optional)
                  <input
                    type="date"
                    className="mt-1 block rounded-md border border-border bg-background px-3 py-2"
                    value={policy.history_start_date ?? ""}
                    onChange={(event) =>
                      setPolicy({ ...policy, history_start_date: event.target.value || null })
                    }
                  />
                </label>
              )}
            </div>
            <label className="block text-sm">
              Update frequency
              <select
                className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
                value={policy.sync_frequency}
                onChange={(event) =>
                  setPolicy({
                    ...policy,
                    sync_frequency: event.target.value as ConnectionPolicy["sync_frequency"],
                  })
                }
              >
                <option value="realtime">Near real-time + safety checks</option>
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
              </select>
            </label>
          </div>
        ) : step === 2 ? (
          <div className="space-y-3">
            <h3 className="font-medium">Who can search this information?</h3>
            {[
              ["respect_source_permissions", "Respect source permissions", "Recommended — users only see content they can access at the source."],
              ["organization", "Everyone in the organization", "All Loom users in this organization can search it."],
              ["selected", "Selected people or departments", "Restrict search to an explicit audience."],
            ].map(([value, title, description]) => (
              <label key={value} className="flex gap-3 rounded-md border border-border p-3">
                <input
                  type="radio"
                  name="access"
                  checked={policy.access_mode === value}
                  onChange={() =>
                    setPolicy({ ...policy, access_mode: value as ConnectionPolicy["access_mode"] })
                  }
                />
                <span>
                  <span className="block text-sm font-medium">{title}</span>
                  <span className="block text-xs text-muted-foreground">{description}</span>
                </span>
              </label>
            ))}
            {policy.access_mode === "selected" && (
              <div className="space-y-3 rounded-md border border-border p-3">
                <label className="block text-sm">
                  Allowed departments
                  <input
                    className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
                    placeholder="Engineering, Operations"
                    value={policy.allowed_departments.join(", ")}
                    onChange={(event) =>
                      setPolicy({
                        ...policy,
                        allowed_departments: event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                      })
                    }
                  />
                </label>
                <label className="block text-sm">
                  Allowed employee IDs
                  <input
                    className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
                    placeholder="user-id-1, user-id-2"
                    value={policy.allowed_user_ids.join(", ")}
                    onChange={(event) =>
                      setPolicy({
                        ...policy,
                        allowed_user_ids: event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                      })
                    }
                  />
                </label>
                <p className="text-xs text-muted-foreground">
                  These identifiers are checked by the backend policy. A directory picker can replace
                  this text entry when role management is added.
                </p>
              </div>
            )}
          </div>
        ) : (
          <Preview preview={preview} policy={policy} busy={busy} />
        )}

        {error && (
          <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!loading && (
          <div className="flex justify-between border-t border-border pt-4">
            {step > 0 ? (
              <SecondaryButton onClick={() => setStep(step - 1)}>
                <ChevronLeft className="size-4" /> Back
              </SecondaryButton>
            ) : <span />}
            <PrimaryButton onClick={() => void continueFlow()} disabled={busy || (step === 3 && !preview)}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {step === 3 ? "Connect and start initial import" : "Continue"}
            </PrimaryButton>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ResourceRow({
  resource,
  children,
  selected,
  onToggle,
}: {
  resource: ConnectionResource;
  children: ConnectionResource[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="rounded-md border border-border">
      <ResourceCheck resource={resource} selected={selected.has(resource.id)} onToggle={onToggle} />
      {children.length > 0 && (
        <div className="border-t border-border bg-muted/30 pl-6">
          {children.map((child) => (
            <ResourceCheck
              key={child.id}
              resource={child}
              selected={selected.has(child.id)}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ResourceCheck({
  resource,
  selected,
  onToggle,
}: {
  resource: ConnectionResource;
  selected: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <label className="flex items-start gap-3 px-3 py-3">
      <input type="checkbox" checked={selected} onChange={() => onToggle(resource.id)} />
      <span className="min-w-0">
        <span className="block text-sm font-medium">{resource.name}</span>
        <span className="text-xs capitalize text-muted-foreground">{resource.kind}</span>
        {resource.warning && (
          <span className="mt-1 flex items-center gap-1 text-xs text-amber-700">
            <AlertTriangle className="size-3" /> {resource.warning}
          </span>
        )}
      </span>
    </label>
  );
}

function Preview({
  preview,
  policy,
  busy,
}: {
  preview: ConnectionPreview | null;
  policy: ConnectionPolicy;
  busy: boolean;
}) {
  if (busy || !preview) return <div className="flex justify-center py-10"><Loader2 className="animate-spin" /></div>;
  const rows = [
    ["Selected locations", preview.selected_resources.toLocaleString()],
    [preview.count_is_exact ? "Items found" : "Estimated items", `${preview.count_is_exact ? "" : "About "}${preview.estimated_items.toLocaleString()}`],
    ["Data size", `${preview.count_is_exact ? "" : "About "}${(preview.estimated_size_bytes / 1_000_000).toFixed(1)} MB`],
    ["Historical import", policy.include_history ? (policy.history_start_date ? `Since ${policy.history_start_date}` : "All available") : "New content only"],
    ["Search access", policy.access_mode.replaceAll("_", " ")],
    ["Unsupported items", preview.unsupported_items.toLocaleString()],
  ];
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-medium">Review before connecting</h3>
        <p className="text-sm text-muted-foreground">
          {preview.count_is_exact ? "The selected content was scanned; no import has started yet." : "The provider limited the preview, so larger totals are estimated; no import has started yet."}
        </p>
      </div>
      <dl className="divide-y divide-border rounded-md border border-border">
        {rows.map(([name, value]) => (
          <div key={name} className="flex justify-between gap-4 px-4 py-3 text-sm">
            <dt className="text-muted-foreground">{name}</dt><dd className="text-right font-medium capitalize">{value}</dd>
          </div>
        ))}
      </dl>
      {preview.permission_warnings.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-green-700"><Check className="size-4" /> No access problems detected.</p>
      ) : preview.permission_warnings.map((warning) => (
        <p key={warning} className="flex gap-2 text-sm text-amber-700"><AlertTriangle className="size-4" /> {warning}</p>
      ))}
    </div>
  );
}
