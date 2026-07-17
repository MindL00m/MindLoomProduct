import { apiFetch } from "@/lib/api";

export interface KnowledgeReview {
  review_id: string;
  review_type: "conflict" | "verification" | "proposal" | "expert_request";
  status: string;
  title: string;
  description: string;
  owner_user_id?: string | null;
  source_ids: string[];
  proposed_content?: string | null;
  due_at?: string | null;
  deliveries?: Record<string, { status: string; error?: string | null }>;
}

export async function listExpertInbox(): Promise<KnowledgeReview[]> {
  const response = await apiFetch("/knowledge/reviews/expert-inbox");
  if (!response.ok) throw new Error("Could not load expert requests.");
  return response.json();
}

export async function getExpertInboxCount(): Promise<number> {
  const response = await apiFetch("/knowledge/reviews/expert-inbox/count");
  if (!response.ok) return 0;
  return (await response.json()).count as number;
}

export async function answerExpertRequest(reviewId: string, answer: string): Promise<void> {
  const response = await apiFetch(`/knowledge/reviews/expert-inbox/${reviewId}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!response.ok) throw new Error("Could not publish the expert answer.");
}

export async function answerExpertRequestWithMedia(
  reviewId: string,
  answer: string,
  file?: File | null,
): Promise<void> {
  const form = new FormData();
  form.append("answer", answer);
  if (file) form.append("file", file);
  const response = await apiFetch(
    `/knowledge/reviews/expert-inbox/${reviewId}/answer-media`,
    { method: "POST", body: form },
  );
  if (!response.ok) throw new Error("Could not create the proposed Skill File.");
}

export async function moderateExpertAnswer(
  reviewId: string,
  action: "edit" | "remove",
  answer?: string,
): Promise<void> {
  const response = await apiFetch(`/knowledge/reviews/expert-answers/${reviewId}/moderate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, answer }),
  });
  if (!response.ok) throw new Error("Could not moderate the expert answer.");
}

export async function listKnowledgeReviews(): Promise<KnowledgeReview[]> {
  const response = await apiFetch("/knowledge/reviews");
  if (!response.ok) throw new Error("Could not load knowledge reviews.");
  return response.json();
}

export async function decideKnowledgeReview(
  reviewId: string,
  status: "approved" | "rejected" | "resolved",
  note?: string,
): Promise<void> {
  const response = await apiFetch(`/knowledge/reviews/${reviewId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note }),
  });
  if (!response.ok) throw new Error("Could not update this review.");
}

export async function proposeExpertAnswer(
  question: string,
  answer: string,
  sourceIds: string[] = [],
): Promise<void> {
  const response = await apiFetch("/knowledge/reviews/proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, answer, source_ids: sourceIds }),
  });
  if (!response.ok) throw new Error("Could not submit the proposed answer.");
}
