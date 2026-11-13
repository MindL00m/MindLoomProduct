import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { SecondaryButton } from "@/components/SecondaryButton";
import {
  createErpImport,
  deleteErpImport,
  listErpChanges,
  listErpImports,
  runErpImport,
  type ErpChange,
  type ErpImport,
  type ErpProvider,
} from "@/services/erp";

const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

const HELP: Record<ErpProvider, { label: string; placeholder: string; hint: string }> = {
  netsuite: {
    label: "SuiteQL query",
    placeholder: "SELECT id, companyname, email FROM customer",
    hint: "One SELECT. Choose only the columns worth searching.",
  },
  sap: {
    label: "Record set",
    placeholder: "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner",
    hint: "An OData record set (for Business One, a name such as BusinessPartners).",
  },
};

/**
 * Knowledge imports for one connected system: named reads whose rows MindLoom keeps in
 * knowledge, refreshed daily, so Ask can find and cite them. Nothing is imported unless
 * the person adds one here.
 */
export function ErpImports({ provider }: { provider: ErpProvider }) {
  const [imports, setImports] = useState<ErpImport[]>([]);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [select, setSelect] = useState("");
  const [maxRows, setMaxRows] = useState(500);
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const help = HELP[provider];

  const load = useCallback(async () => {
    setImports((await listErpImports()).filter((item) => item.provider === provider));
  }, [provider]);

  useEffect(() => {
    void load().catch((err) => setError(err instanceof Error ? err.message : "Could not load imports."));
  }, [load]);

  const act = async (key: string, work: () => Promise<unknown>, done = "") => {
    setBusy(key);
    setError("");
    setNote("");
    try {
      await work();
      await load();
      setNote(done);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy("");
    }
  };

  const add = () =>
    act(
      "add",
      async () => {
        await createErpImport({
          provider,
          name: name.trim(),
          query: query.trim(),
          options: provider === "sap" && select.trim() ? { $select: select.trim() } : undefined,
          max_rows: maxRows,
          visibility: shared ? "org" : "me",
        });
        setName("");
        setQuery("");
        setSelect("");
      },
      "Saved. The first import is running; it can take a few minutes to show up in Ask.",
    );

  return (
    <div className="space-y-3 border-t border-border pt-3">
      <div>
        <p className="font-medium">Add to knowledge</p>
        <p className="text-xs text-muted-foreground">
          Choose what Ask should know from here. Each import is refreshed once a day.
        </p>
      </div>
      {error && <p className="text-destructive">{error}</p>}
      {note && <p className="text-muted-foreground">{note}</p>}

      {imports.map((item) => (
        <div key={item.import_id} className="rounded-md border border-border p-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">{item.name}</p>
            <div className="flex gap-2">
              <SecondaryButton
                disabled={busy !== ""}
                onClick={() => void act(item.import_id, () => runErpImport(item.import_id), "Refresh started.")}
              >
                {busy === item.import_id ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                Refresh
              </SecondaryButton>
              <SecondaryButton
                disabled={busy !== ""}
                onClick={() => void act(item.import_id, () => deleteErpImport(item.import_id))}
              >
                <Trash2 className="size-4" />
                Delete
              </SecondaryButton>
            </div>
          </div>
          <p className="mt-1 break-all text-xs text-muted-foreground">{item.query}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {item.visibility === "org" ? "Everyone in the organization" : "Only you"} · up to {item.max_rows} rows ·{" "}
            {item.last_status === "ok"
              ? `${item.last_rows ?? 0} rows, ${new Date(item.last_run_at ?? "").toLocaleString()}`
              : item.last_status === "error"
                ? "last run failed"
                : "not run yet"}
          </p>
          {item.last_status === "error" && item.last_error && (
            <p className="mt-1 text-xs text-destructive">{item.last_error}</p>
          )}
        </div>
      ))}

      <label className="block">
        <span className="font-medium">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Customers" className={inputClass} />
      </label>
      <label className="block">
        <span className="font-medium">{help.label}</span>
        <textarea
          rows={2}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={help.placeholder}
          className={inputClass}
        />
        <span className="mt-1 block text-xs text-muted-foreground">{help.hint}</span>
      </label>
      {provider === "sap" && (
        <label className="block">
          <span className="font-medium">Fields (optional)</span>
          <input
            value={select}
            onChange={(e) => setSelect(e.target.value)}
            placeholder="BusinessPartner,BusinessPartnerName"
            className={inputClass}
          />
        </label>
      )}
      <div className="flex flex-wrap items-end gap-4">
        <label className="block">
          <span className="font-medium">Row limit</span>
          <input
            type="number"
            min={1}
            max={5000}
            value={maxRows}
            onChange={(e) => setMaxRows(Math.max(1, Math.min(5000, Number(e.target.value) || 1)))}
            className={`${inputClass} w-28`}
          />
        </label>
        <label className="flex items-center gap-2 pb-2">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          Everyone in the organization can find it
        </label>
      </div>
      <SecondaryButton disabled={busy !== "" || !name.trim() || !query.trim()} onClick={() => void add()}>
        {busy === "add" && <Loader2 className="size-4 animate-spin" />}
        Add import
      </SecondaryButton>
    </div>
  );
}

/**
 * What MindLoom changed in this system for the person, newest first: from an automation or
 * from a card approved in Ask, whether it worked. Field names only; the values stay in the ERP.
 */
export function ErpChanges({ provider }: { provider: ErpProvider }) {
  const [changes, setChanges] = useState<ErpChange[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    void listErpChanges(50)
      .then((all) => setChanges(all.filter((change) => change.system === provider)))
      .catch(() => setChanges([]));
  }, [open, provider]);

  return (
    <div className="border-t border-border pt-3">
      <button type="button" className="font-medium underline" onClick={() => setOpen((cur) => !cur)}>
        {open ? "Hide recent changes" : "Show recent changes"}
      </button>
      {open && changes === null && <p className="mt-2 text-xs text-muted-foreground">Loading…</p>}
      {open && changes?.length === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">MindLoom has not changed anything here for you yet.</p>
      )}
      {open &&
        changes?.map((change) => (
          <p key={change.change_id} className="mt-2 break-all text-xs text-muted-foreground">
            <span className={change.ok ? "font-medium text-foreground" : "font-medium text-destructive"}>
              {change.ok ? (change.action === "create" ? "Created" : "Changed") : "Failed"}
            </span>{" "}
            {change.target}
            {change.record_id ? ` (${change.record_id})` : ""} · {change.field_names || "no fields"} ·{" "}
            {change.source === "ask" ? "approved in Ask" : "automation"} · {new Date(change.created_at).toLocaleString()}
            {!change.ok && change.detail ? ` · ${change.detail}` : ""}
          </p>
        ))}
    </div>
  );
}
