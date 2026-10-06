import type {
  ChatResponse,
  Claim,
  ConversationDetail,
  ConversationSummary,
  MessageOut,
  RetrievedSource,
} from "@/types/api";

export const SOURCE: RetrievedSource = {
  rank: 0,
  chunk_id: "fda_chart_chunk_003",
  document_name: "Refrigerator & Freezer Storage Chart",
  publisher: "U.S. Food and Drug Administration (FDA)",
  year: 2023,
  section: "Fresh Poultry",
  url: "https://www.fda.gov/media/74421/download",
  text: "Chicken or turkey, parts | 1 - 2 days | 9 months",
  similarity_score: 0.82,
};

export const OTHER_SOURCE: RetrievedSource = { ...SOURCE, rank: 1, chunk_id: "fda_chart_chunk_004", section: "Eggs" };

export const CLAIM: Claim = {
  claim_text: "Chicken parts can be stored in the refrigerator for 1 - 2 days.",
  source: {
    document_name: SOURCE.document_name,
    publisher: SOURCE.publisher,
    year: SOURCE.year,
    section: SOURCE.section,
    url: SOURCE.url,
    chunk_id: SOURCE.chunk_id,
  },
};

export function answeredResponse(conversationId: string, answer: string): ChatResponse {
  return {
    conversation_id: conversationId,
    message_id: `msg-${conversationId}-${answer}`,
    answer,
    claims: [CLAIM],
    status: "answered",
    refusal_reason: null,
    retrieved_sources: [SOURCE, OTHER_SOURCE],
  };
}

export function refusalResponse(conversationId: string, status: "out_of_scope" | "not_in_corpus" | "error"): ChatResponse {
  return {
    conversation_id: conversationId,
    message_id: `msg-${conversationId}-${status}`,
    answer: "Sorry, I can't answer that.",
    claims: [],
    status,
    refusal_reason: "Because.",
    retrieved_sources: [],
  };
}

export function summary(id: string, title: string, updated = "2026-10-06T10:00:00Z"): ConversationSummary {
  return { id, title, created_at: updated, updated_at: updated };
}

export function message(overrides: Partial<MessageOut> & Pick<MessageOut, "id" | "role" | "content">): MessageOut {
  return {
    status: null, claims: [], refusal_reason: null, retrieved_sources: [], created_at: "2026-10-06T10:00:00Z",
    ...overrides,
  };
}

export function detail(id: string, title: string, messages: MessageOut[]): ConversationDetail {
  return { ...summary(id, title), messages };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
