import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  answerExpertRequestWithMedia,
  listExpertInbox,
  type KnowledgeReview,
} from "@/services/reviews";

export default function ExpertInbox({ onCountChange }: { onCountChange?: (count: number) => void }) {
  const [requests, setRequests] = useState<KnowledgeReview[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await listExpertInbox();
      setRequests(rows);
      onCountChange?.(rows.filter((row) => row.status === "open").length);
    } finally {
      setLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    void load();
  }, [load]);

  async function publish(request: KnowledgeReview) {
    const answer = (answers[request.review_id] || "").trim();
    if (!answer && !files[request.review_id]) return;
    setBusy(request.review_id);
    setMessage(null);
    try {
      await answerExpertRequestWithMedia(
        request.review_id,
        answer,
        files[request.review_id],
      );
      setAnswers((current) => ({ ...current, [request.review_id]: "" }));
      setFiles((current) => ({ ...current, [request.review_id]: null }));
      setMessage("A proposed Skill File was created. Review and approve it in Skill Files before it becomes searchable.");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not publish the answer.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <div className="flex justify-center py-16"><Loader2 className="animate-spin" /></div>;

  const open = requests.filter((request) => request.status === "open");
  const answered = requests.filter((request) => ["answered", "drafted"].includes(request.status));
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Expert inbox</h2>
        <p className="mt-1 text-muted-foreground">
          Questions Company Brain could not answer and routed to you.
        </p>
      </div>
      {message && <p className="rounded-md border border-border bg-muted p-3 text-sm">{message}</p>}
      {open.length === 0 ? (
        <Card><CardContent className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><CheckCircle2 className="size-5" />No unanswered expert requests.</CardContent></Card>
      ) : open.map((request) => (
        <Card key={request.review_id}>
          <CardHeader>
            <CardTitle className="text-lg">{request.title.replace("Expert question: ", "")}</CardTitle>
            <p className="text-sm text-muted-foreground">{request.description}</p>
          </CardHeader>
          <CardContent className="space-y-3">
            {request.deliveries && Object.keys(request.deliveries).length > 0 && (
              <div className="flex flex-wrap gap-2 text-xs">
                {Object.entries(request.deliveries).map(([channel, delivery]) => (
                  <span
                    key={channel}
                    className={`rounded-full px-2 py-1 ${delivery.status === "delivered" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"}`}
                    title={delivery.error || undefined}
                  >
                    {channel}: {delivery.status}
                  </span>
                ))}
              </div>
            )}
            <textarea
              className="min-h-32 w-full rounded-md border border-border bg-background p-3 text-sm"
              placeholder="Write the company answer. It becomes searchable as soon as you publish it."
              value={answers[request.review_id] || ""}
              onChange={(event) => setAnswers((current) => ({
                ...current, [request.review_id]: event.target.value,
              }))}
            />
            <label className="block text-sm">
              Add a screenshot, voice note, text file, or short recording (optional)
              <input
                type="file"
                accept="image/*,audio/*,video/*,text/plain"
                className="mt-1 block w-full rounded-md border border-border p-2"
                onChange={(event) => setFiles((current) => ({
                  ...current,
                  [request.review_id]: event.target.files?.[0] || null,
                }))}
              />
            </label>
            <PrimaryButton
              disabled={
                busy === request.review_id ||
                (!(answers[request.review_id] || "").trim() &&
                  !files[request.review_id])
              }
              onClick={() => void publish(request)}
            >
              {busy === request.review_id && <Loader2 className="size-4 animate-spin" />}
              Create proposed Skill File
            </PrimaryButton>
          </CardContent>
        </Card>
      ))}
      {answered.length > 0 && (
        <section>
          <h3 className="mb-3 font-medium">Recently answered or drafted</h3>
          <div className="space-y-2">
            {answered.map((request) => (
              <div key={request.review_id} className="rounded-md border border-border p-3">
                <p className="text-sm font-medium">{request.title.replace("Expert question: ", "")}</p>
                <p className="mt-1 text-sm text-muted-foreground">{request.proposed_content}</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
