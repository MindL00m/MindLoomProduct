import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { QueryResponse } from "@/services/ask";

export interface Turn {
  id: string;
  question: string;
  status: "pending" | "done" | "error";
  response?: QueryResponse;
  error?: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  turns: Turn[];
}

interface ChatState {
  conversations: Conversation[];
  activeId: string | null;

  /** Create a new empty conversation and make it active. Returns its id. */
  newConversation: () => string;
  /** Ensure there is an active conversation, creating one if needed. */
  ensureActive: () => string;
  setActive: (id: string) => void;
  deleteConversation: (id: string) => void;

  addTurn: (conversationId: string, turn: Turn) => void;
  updateTurn: (conversationId: string, turnId: string, patch: Partial<Turn>) => void;
}

function makeConversation(): Conversation {
  const now = Date.now();
  return {
    id: crypto.randomUUID(),
    title: "New conversation",
    createdAt: now,
    updatedAt: now,
    turns: [],
  };
}

function titleFrom(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed;
}

export const useChat = create<ChatState>()(
  persist(
    (set, get) => ({
      conversations: [],
      activeId: null,

      newConversation: () => {
        const conversation = makeConversation();
        set((state) => ({
          conversations: [conversation, ...state.conversations],
          activeId: conversation.id,
        }));
        return conversation.id;
      },

      ensureActive: () => {
        const { activeId, conversations } = get();
        if (activeId && conversations.some((c) => c.id === activeId)) {
          return activeId;
        }
        if (conversations.length > 0) {
          set({ activeId: conversations[0].id });
          return conversations[0].id;
        }
        return get().newConversation();
      },

      setActive: (id) => set({ activeId: id }),

      deleteConversation: (id) =>
        set((state) => {
          const conversations = state.conversations.filter((c) => c.id !== id);
          const activeId =
            state.activeId === id
              ? (conversations[0]?.id ?? null)
              : state.activeId;
          return { conversations, activeId };
        }),

      addTurn: (conversationId, turn) =>
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === conversationId
              ? {
                  ...c,
                  turns: [...c.turns, turn],
                  updatedAt: Date.now(),
                  title:
                    c.turns.length === 0 ? titleFrom(turn.question) : c.title,
                }
              : c,
          ),
        })),

      updateTurn: (conversationId, turnId, patch) =>
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === conversationId
              ? {
                  ...c,
                  updatedAt: Date.now(),
                  turns: c.turns.map((t) =>
                    t.id === turnId ? { ...t, ...patch } : t,
                  ),
                }
              : c,
          ),
        })),
    }),
    {
      name: "company-brain-chat",
      // Convert any turns left "pending" by a reload/crash into an error so the
      // UI never shows a permanently-spinning message.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        state.conversations = state.conversations.map((c) => ({
          ...c,
          turns: c.turns.map((t) =>
            t.status === "pending"
              ? { ...t, status: "error" as const, error: "Interrupted." }
              : t,
          ),
        }));
      },
    },
  ),
);
