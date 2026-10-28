import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Ship, X } from "lucide-react";
import {
  createShipmentDocuments,
  listFiles,
  type LoomFileMeta,
  type ShipmentDocumentType,
  type ShipmentDocumentsResult,
} from "@/services/files";
import { cn } from "@/lib/utils";

const TYPES: { type: ShipmentDocumentType; label: string }[] = [
  { type: "commercial_invoice", label: "Commercial invoice" },
  { type: "packing_list", label: "Packing list" },
  { type: "certificate_of_origin", label: "Certificate of origin" },
  { type: "certificate_of_analysis", label: "Certificate of analysis" },
];

/**
 * Pick the shipment's paperwork (bill of lading, sales contract, lab report) and
 * the documents to make. LoomBot reads the facts, calculates amounts and totals,
 * and fills the company's own template for each type when one is chosen.
 */
export function ShipmentDocumentsDialog({
  onClose,
  onOpen,
}: {
  onClose: () => void;
  onOpen: (file: LoomFileMeta) => void;
}) {
  const [files, setFiles] = useState<LoomFileMeta[]>([]);
  const [types, setTypes] = useState<ShipmentDocumentType[]>(["commercial_invoice", "packing_list"]);
  const [sources, setSources] = useState<string[]>([]);
  const [templateFor, setTemplateFor] = useState<Partial<Record<ShipmentDocumentType, string>>>({});
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ShipmentDocumentsResult | null>(null);

  useEffect(() => {
    void listFiles()
      .then(setFiles)
      .catch((err: Error) => setError(err.message));
  }, []);

  const templates = files.filter((f) => f.is_template);
  const sourceChoices = files.filter((f) => !f.is_template);

  function toggle<T>(list: T[], value: T): T[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  async function create() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(
        await createShipmentDocuments({
          document_types: types,
          source_file_ids: sources,
          template_file_ids: Object.fromEntries(
            Object.entries(templateFor).filter(([type, id]) => id && types.includes(type as ShipmentDocumentType)),
          ),
          instructions: instructions.trim() || undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not make the documents.");
    } finally {
      setBusy(false);
    }
  }

  const chip = (on: boolean) =>
    cn(
      "rounded-full border px-2.5 py-1 text-xs",
      on ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card",
    );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Shipment documents"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90dvh] w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-xl bg-background p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="flex items-center gap-2 font-semibold">
              <Ship className="size-4" /> Shipment documents
            </h3>
            <p className="text-sm text-muted-foreground">
              LoomBot reads the shipment's paperwork, calculates amounts and totals exactly, and
              writes each document in your company's template or a standard layout. Nothing is
              guessed: anything it can't find is left blank and listed.
            </p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-muted">
            <X className="size-4" />
          </button>
        </div>

        <section className="space-y-2">
          <p className="text-sm font-medium">1. Documents to make</p>
          <div className="flex flex-wrap gap-1.5">
            {TYPES.map(({ type, label }) => (
              <button key={type} type="button" className={chip(types.includes(type))} onClick={() => setTypes((t) => toggle(t, type))}>
                {label}
              </button>
            ))}
          </div>
        </section>

        <section className="space-y-2">
          <p className="text-sm font-medium">2. The shipment's paperwork</p>
          <p className="text-xs text-muted-foreground">
            Bill of lading, sales contract, lab report… Upload them to Files first (scans are read with OCR).
          </p>
          <div className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
            {sourceChoices.map((f) => (
              <button key={f.file_id} type="button" className={chip(sources.includes(f.file_id))} onClick={() => setSources((s) => toggle(s, f.file_id))}>
                {f.name}
              </button>
            ))}
            {sourceChoices.length === 0 && <span className="text-xs text-muted-foreground">No files yet.</span>}
          </div>
        </section>

        <section className="space-y-2">
          <p className="text-sm font-medium">3. Your company's layout (optional)</p>
          {templates.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No templates yet. Open a sample invoice or certificate in Files and choose Make template
              to use your own layout; otherwise a standard layout is used.
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {TYPES.filter(({ type }) => types.includes(type)).map(({ type, label }) => (
                <label key={type} className="block text-xs font-medium">
                  {label}
                  <select
                    value={templateFor[type] ?? ""}
                    onChange={(e) => setTemplateFor((cur) => ({ ...cur, [type]: e.target.value }))}
                    className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                  >
                    <option value="">Standard layout</option>
                    {templates.map((t) => (
                      <option key={t.file_id} value={t.file_id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
        </section>

        <label className="block text-sm font-medium">
          Anything else (optional)
          <textarea
            rows={2}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="e.g. Invoice number INV-2026-042, dated today; payment 30 days from BL"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        {error && <p className="text-xs text-destructive">{error}</p>}

        {result && (
          <div className="space-y-2 rounded-md border border-border p-3 text-sm">
            {result.documents.map((d) => (
              <div key={d.file.file_id} className="flex flex-wrap items-center gap-2">
                <button type="button" className="font-medium text-primary hover:underline" onClick={() => onOpen(d.file)}>
                  {d.file.name}
                </button>
                <span className="text-xs text-muted-foreground">
                  {d.used_template ? "your template" : "standard layout"}
                  {d.unfilled.length ? ` · blank: ${d.unfilled.slice(0, 6).join(", ")}${d.unfilled.length > 6 ? "…" : ""}` : ""}
                </span>
              </div>
            ))}
            {typeof result.shipment.total_amount === "string" && (
              <p className="text-xs">
                Total {String(result.shipment.currency ?? "")} {result.shipment.total_amount} ·{" "}
                {String(result.shipment.amount_in_words ?? "")}
              </p>
            )}
            {result.warnings.map((w) => (
              <p key={w} className="flex items-start gap-1.5 text-xs text-amber-700">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {w}
              </p>
            ))}
            {result.missing.length > 0 && (
              <p className="text-xs text-muted-foreground">Not in the documents: {result.missing.join(", ")}.</p>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border border-border px-3 py-2 text-sm">
            Close
          </button>
          <button
            type="button"
            disabled={busy || types.length === 0 || (sources.length === 0 && !instructions.trim())}
            onClick={() => void create()}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            Make documents
          </button>
        </div>
      </div>
    </div>
  );
}
