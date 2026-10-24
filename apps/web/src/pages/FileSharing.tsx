import { useEffect, useMemo, useState } from "react";
import { Loader2, Mail, Share2, Users, X } from "lucide-react";
import { shareFile, type LoomFileMeta, type ShareResult } from "@/services/files";
import { listMessageContacts, type MessageContact } from "@/services/reviews";
import { cn } from "@/lib/utils";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** "Share" in a file's header: opens the share dialog. */
export function ShareButton({ file }: { file: LoomFileMeta }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
      >
        <Share2 className="size-3.5" /> Share
      </button>
      {open && <ShareDialog file={file} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Who shared this copy with you, shown under a shared file's name. */
export function SharedByLine({ file }: { file: LoomFileMeta }) {
  if (!file.shared_by_name) return null;
  return (
    <p className="flex items-center gap-1 px-1 text-xs text-primary">
      <Users className="size-3" />
      Shared with you by {file.shared_by_name}
      {file.shared_at ? ` on ${new Date(file.shared_at).toLocaleDateString()}` : ""}
      {file.shared_note ? <span className="text-muted-foreground">: “{file.shared_note}”</span> : null}
    </p>
  );
}

function ShareDialog({ file, onClose }: { file: LoomFileMeta; onClose: () => void }) {
  const [contacts, setContacts] = useState<MessageContact[]>([]);
  const [recipients, setRecipients] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [sendEmail, setSendEmail] = useState(true);
  const [inMindloom, setInMindloom] = useState(true);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ShareResult | null>(null);

  useEffect(() => {
    void listMessageContacts().then(setContacts).catch(() => setContacts([]));
  }, []);

  const members = useMemo(
    () => new Map(contacts.map((c) => [c.email.toLowerCase(), c])),
    [contacts],
  );
  const suggestions = useMemo(() => {
    const needle = draft.trim().toLowerCase();
    if (!needle) return [];
    return contacts
      .filter(
        (c) =>
          !recipients.includes(c.email.toLowerCase()) &&
          (c.name.toLowerCase().includes(needle) || c.email.toLowerCase().includes(needle)),
      )
      .slice(0, 5);
  }, [contacts, draft, recipients]);
  const memberCount = recipients.filter((email) => members.has(email)).length;
  const outsiders = recipients.filter((email) => !members.has(email));

  function add(value: string) {
    const parts = value
      .split(/[\s,;]+/)
      .map((part) => part.trim().toLowerCase())
      .filter(Boolean);
    const bad = parts.filter((part) => !EMAIL_RE.test(part));
    const good = parts.filter((part) => EMAIL_RE.test(part));
    if (good.length) setRecipients((rows) => [...new Set([...rows, ...good])]);
    setDraft(bad.join(" "));
    setError(bad.length ? `Not an email address: ${bad.join(", ")}` : "");
  }

  async function share() {
    const pending = draft.trim();
    const all = pending && EMAIL_RE.test(pending.toLowerCase())
      ? [...new Set([...recipients, pending.toLowerCase()])]
      : recipients;
    if (!all.length) {
      setError("Add at least one person.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setResult(
        await shareFile(file.file_id, {
          recipients: all,
          sendEmail,
          shareInMindloom: inMindloom,
          message,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not share the file.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Share ${file.name}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[90dvh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-xl bg-background p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-semibold">Share {file.name}</h3>
            <p className="text-sm text-muted-foreground">Send it by email, put a copy in colleagues’ Files, or both.</p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        {result ? (
          <div className="space-y-2 text-sm">
            {result.shared_in_mindloom.length > 0 && (
              <p>
                Added to the Files of{" "}
                {result.shared_in_mindloom.map((s) => s.name).join(", ")}. They’ll see a short
                notice that you shared it.
              </p>
            )}
            {result.emailed.length > 0 && <p>Emailed with the file attached to {result.emailed.join(", ")}.</p>}
            {result.not_in_mindloom.length > 0 && (
              <p className="text-muted-foreground">
                Not on MindLoom, so not added to Files: {result.not_in_mindloom.join(", ")}
                {result.emailed.length ? " (they got the email)." : ". Share by email to reach them."}
              </p>
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <>
            <div>
              <div className="flex flex-wrap gap-1.5 rounded-md border border-border p-2 focus-within:border-primary">
                {recipients.map((email) => {
                  const member = members.get(email);
                  return (
                    <span
                      key={email}
                      className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs"
                      title={member ? "Uses MindLoom" : "Not on MindLoom: email only"}
                    >
                      {member ? <Users className="size-3 text-primary" /> : <Mail className="size-3" />}
                      {member?.name ?? email}
                      <button
                        type="button"
                        aria-label={`Remove ${email}`}
                        onClick={() => setRecipients((rows) => rows.filter((row) => row !== email))}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  );
                })}
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.key === "Enter" || e.key === "," || e.key === "Tab") && draft.trim()) {
                      e.preventDefault();
                      add(draft);
                    }
                    if (e.key === "Backspace" && !draft && recipients.length) {
                      setRecipients((rows) => rows.slice(0, -1));
                    }
                  }}
                  onBlur={() => draft.trim() && add(draft)}
                  placeholder={recipients.length ? "" : "Name or email address"}
                  aria-label="Add people"
                  className="min-w-[8rem] flex-1 bg-transparent px-1 text-sm outline-none"
                />
              </div>
              {suggestions.length > 0 && (
                <div className="mt-1 rounded-md border border-border bg-card p-1 shadow-sm">
                  {suggestions.map((c) => (
                    <button
                      key={c.user_id}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        setRecipients((rows) => [...new Set([...rows, c.email.toLowerCase()])]);
                        setDraft("");
                      }}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
                    >
                      <Users className="size-3.5 text-primary" />
                      <span className="truncate">
                        {c.name}
                        <span className="text-muted-foreground"> — {c.email}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={inMindloom} onChange={(e) => setInMindloom(e.target.checked)} className="mt-1" />
              <span>
                <span className="font-medium">Share in MindLoom</span>
                <span className="block text-xs text-muted-foreground">
                  Puts a copy in their Files tab with a short note that you shared it.
                  {recipients.length > 0 && ` ${memberCount} of ${recipients.length} use MindLoom.`}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={sendEmail} onChange={(e) => setSendEmail(e.target.checked)} className="mt-1" />
              <span>
                <span className="font-medium">Send by email</span>
                <span className="block text-xs text-muted-foreground">
                  Emails the file as an attachment from your Gmail.
                </span>
              </span>
            </label>
            {inMindloom && !sendEmail && outsiders.length > 0 && (
              <p className="text-xs text-amber-800">
                {outsiders.join(", ")} {outsiders.length === 1 ? "is" : "are"} not on MindLoom and will
                only get the file if you also send it by email.
              </p>
            )}
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={2}
              maxLength={2000}
              placeholder="Add a message (optional)"
              aria-label="Message"
              className="rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-border px-3 py-2 text-xs font-medium"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || (!sendEmail && !inMindloom) || (!recipients.length && !draft.trim())}
                onClick={() => void share()}
                className={cn(
                  "flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50",
                )}
              >
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Share2 className="size-3.5" />}
                Share
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
