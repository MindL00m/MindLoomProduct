import { useEffect, useMemo, useState } from "react";
import { Bot, FileStack, Loader2, Plus, Trash2, Wand2 } from "lucide-react";
import {
  fillTemplate,
  getTemplate,
  listFiles,
  makeTemplate,
  setTemplate,
  type FillTemplateResult,
  type LoomFileMeta,
  type TemplateInfo,
} from "@/services/files";
import { cn } from "@/lib/utils";

const TEMPLATE_KINDS = ["docx", "xlsx", "pptx", "csv", "txt", "md"];

function label(field: string): string {
  return field.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * Templates in Files: mark a file with {{placeholders}} as a template, fill one
 * (type values, or let LoomBot read source files such as a bill of lading), or
 * turn a sample document into a template.
 */
export function TemplateControls({
  file,
  onChanged,
  onCreated,
}: {
  file: LoomFileMeta;
  onChanged: (file: LoomFileMeta) => void;
  onCreated: (file: LoomFileMeta) => void;
}) {
  const [busy, setBusy] = useState<"" | "flag" | "make">("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  if (!TEMPLATE_KINDS.includes(file.kind)) return null;

  async function toggle() {
    setBusy("flag");
    setError("");
    try {
      const info = await setTemplate(file.file_id, !file.is_template);
      onChanged({ ...file, is_template: !file.is_template });
      if (!file.is_template && info.fields.length === 0 && Object.keys(info.lists).length === 0) {
        setError("No {{placeholders}} found. Add some, or use Make template on a sample.");
      }
      setOpen(!file.is_template);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the setting.");
    } finally {
      setBusy("");
    }
  }

  async function make() {
    setBusy("make");
    setError("");
    try {
      const made = await makeTemplate(file.file_id);
      onCreated(made.file);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not make a template.");
    } finally {
      setBusy("");
    }
  }

  const button =
    "flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50";
  return (
    <>
      {file.is_template ? (
        <button type="button" className={cn(button, "border-primary/40 text-brand-700")} onClick={() => setOpen((v) => !v)}>
          <FileStack className="size-3.5" /> Fill template
        </button>
      ) : file.kind !== "pptx" ? (
        <button type="button" className={button} disabled={busy !== ""} onClick={() => void make()} title="Copy this sample with its deal-specific values turned into fields">
          {busy === "make" ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
          Make template
        </button>
      ) : null}
      <button type="button" className={button} disabled={busy !== ""} onClick={() => void toggle()}>
        {busy === "flag" ? <Loader2 className="size-3.5 animate-spin" /> : null}
        {file.is_template ? "Not a template" : "Use as template"}
      </button>
      {error && <span className="w-full text-xs text-destructive">{error}</span>}
      {open && file.is_template && <FillTemplatePanel file={file} onCreated={onCreated} />}
    </>
  );
}

function FillTemplatePanel({
  file,
  onCreated,
}: {
  file: LoomFileMeta;
  onCreated: (file: LoomFileMeta) => void;
}) {
  const [info, setInfo] = useState<TemplateInfo | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [items, setItems] = useState<Record<string, Record<string, string>[]>>({});
  const [instructions, setInstructions] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [myFiles, setMyFiles] = useState<LoomFileMeta[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<FillTemplateResult | null>(null);

  useEffect(() => {
    void getTemplate(file.file_id)
      .then((t) => {
        setInfo(t);
        setItems(Object.fromEntries(Object.keys(t.lists).map((name) => [name, []])));
      })
      .catch((err: Error) => setError(err.message));
    void listFiles()
      .then((all) => setMyFiles(all.filter((f) => f.file_id !== file.file_id)))
      .catch(() => undefined);
  }, [file.file_id]);

  const sourceChoices = useMemo(() => myFiles.slice(0, 200), [myFiles]);

  async function fill() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const typed: Record<string, unknown> = Object.fromEntries(
        Object.entries(values).filter(([, v]) => v.trim()),
      );
      for (const [name, rows] of Object.entries(items)) {
        const kept = rows.filter((row) => Object.values(row).some((v) => v.trim()));
        if (kept.length) typed[name] = kept;
      }
      const done = await fillTemplate(file.file_id, {
        values: typed,
        instructions: instructions.trim() || undefined,
        source_file_ids: sources,
      });
      setResult(done);
      onCreated(done.file);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not fill the template.");
    } finally {
      setBusy(false);
    }
  }

  if (!info) {
    return (
      <div className="w-full rounded-md border border-border p-3 text-xs text-muted-foreground">
        {error || (
          <span className="flex items-center gap-2">
            <Loader2 className="size-3.5 animate-spin" /> Reading the template…
          </span>
        )}
      </div>
    );
  }

  const input = "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm";
  return (
    <div className="w-full space-y-3 rounded-md border border-primary/30 bg-brand-50/40 p-3 text-sm">
      <p className="text-xs text-muted-foreground">
        Type the values you know. LoomBot fills the rest from the files you pick and from company
        knowledge, and never guesses: anything it can't find is left for you. A new file is saved;
        the template doesn't change.
      </p>
      <label className="block text-xs font-medium">
        Ask LoomBot
        <textarea
          rows={2}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="e.g. Invoice for the Gulf Agro SOP shipment, using the BL and the sales contract"
          className={cn(input, "mt-1")}
        />
      </label>
      <div className="text-xs font-medium">
        Read values from
        <div className="mt-1 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
          {sourceChoices.map((f) => (
            <button
              key={f.file_id}
              type="button"
              onClick={() =>
                setSources((cur) => (cur.includes(f.file_id) ? cur.filter((id) => id !== f.file_id) : [...cur, f.file_id]))
              }
              className={cn(
                "rounded-full border px-2 py-0.5 font-normal",
                sources.includes(f.file_id) ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card",
              )}
            >
              {f.name}
            </button>
          ))}
          {sourceChoices.length === 0 && <span className="font-normal text-muted-foreground">No other files yet.</span>}
        </div>
      </div>
      {info.fields.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {info.fields.map((field) => (
            <label key={field} className="block text-xs font-medium">
              {label(field)}
              <input
                value={values[field] ?? ""}
                onChange={(e) => setValues((cur) => ({ ...cur, [field]: e.target.value }))}
                placeholder="LoomBot fills this if empty"
                className={cn(input, "mt-1")}
              />
            </label>
          ))}
        </div>
      )}
      {Object.entries(info.lists).map(([name, fields]) => (
        <div key={name} className="space-y-1">
          <p className="text-xs font-medium">{label(name)} (one row per line item)</p>
          {(items[name] ?? []).map((row, index) => (
            <div key={index} className="flex gap-1">
              {fields.map((f) => (
                <input
                  key={f}
                  value={row[f] ?? ""}
                  placeholder={label(f)}
                  onChange={(e) =>
                    setItems((cur) => {
                      const rows = [...(cur[name] ?? [])];
                      rows[index] = { ...rows[index], [f]: e.target.value };
                      return { ...cur, [name]: rows };
                    })
                  }
                  className={cn(input, "min-w-0 flex-1 text-xs")}
                />
              ))}
              <button
                type="button"
                aria-label="Remove row"
                onClick={() => setItems((cur) => ({ ...cur, [name]: (cur[name] ?? []).filter((_, i) => i !== index) }))}
                className="rounded p-1 text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setItems((cur) => ({ ...cur, [name]: [...(cur[name] ?? []), {}] }))}
            className="flex items-center gap-1 text-xs font-medium text-primary"
          >
            <Plus className="size-3" /> Add a row (or leave empty for LoomBot)
          </button>
        </div>
      ))}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {result && (
        <p className="text-xs">
          Saved <span className="font-medium">{result.file.name}</span>.
          {result.notes ? ` ${result.notes}` : ""}
          {result.unfilled.length
            ? ` Still empty: ${result.unfilled.map(label).join(", ")}.`
            : " Every field was filled."}
        </p>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => void fill()}
        className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
      >
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Bot className="size-3.5" />}
        Create document
      </button>
    </div>
  );
}
