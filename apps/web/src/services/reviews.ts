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

export interface ExpertThread {
  review_id: string;
  status: string;
  title: string;
  description: string;
  created_by: string;
  owner_user_id: string;
  requester_name?: string | null;
  requester_email?: string | null;
  expert_name?: string | null;
  expert_email?: string | null;
  last_message?: string | null;
  last_message_at?: string | null;
  unread_count: number;
}

export interface ExpertMessage {
  message_id: string;
  sender_user_id: string;
  sender_name: string;
  body: string;
  message_type: string;
  attachment_name?: string | null;
  created_at: string;
  read_at?: string | null;
}

export interface MessageContact {
  user_id: string;
  name: string;
  email: string;
  role: string;
}

export async function listExpertThreads(): Promise<ExpertThread[]> {
  const response = await apiFetch("/knowledge/reviews/messages");
  if (!response.ok) throw new Error("Could not load expert conversations.");
  return response.json();
}

export async function listMessageContacts(): Promise<MessageContact[]> {
  const response = await apiFetch("/knowledge/reviews/messages/contacts");
  if (!response.ok) throw new Error("Could not load company contacts.");
  return response.json();
}

export async function listThreadMessages(reviewId: string): Promise<ExpertMessage[]> {
  const response = await apiFetch(`/knowledge/reviews/messages/${reviewId}`);
  if (!response.ok) throw new Error("Could not load messages.");
  return response.json();
}

export async function startExpertThread(
  expertUserId: string,
  message: string,
): Promise<{ review_id: string }> {
  const response = await apiFetch("/knowledge/reviews/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expert_user_id: expertUserId, message }),
  });
  if (!response.ok) throw new Error("Could not start the conversation.");
  return response.json();
}

export async function sendProposedExpertMessage(
  recipientUserId: string,
  message: string,
): Promise<{ review_id: string; status: string }> {
  const response = await apiFetch("/knowledge/reviews/messages/send-proposed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient_user_id: recipientUserId,
      message,
    }),
  });
  if (!response.ok) {
    let detail = "Could not send the proposed message.";
    try {
      const body = await response.json();
      detail = (body?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function sendProposedEmail(
  recipientEmail: string,
  subject: string,
  body: string,
): Promise<{ status: string; provider_message_id?: string }> {
  const response = await apiFetch("/knowledge/reviews/messages/send-proposed-email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient_email: recipientEmail,
      subject,
      body,
    }),
  });
  if (!response.ok) {
    let detail = "Could not send the proposed email.";
    try {
      const parsed = await response.json();
      detail = (parsed?.detail as string) || detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function sendExpertMessage(
  reviewId: string,
  message: string,
): Promise<void> {
  const response = await apiFetch(`/knowledge/reviews/messages/${reviewId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) throw new Error("Could not send the message.");
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
