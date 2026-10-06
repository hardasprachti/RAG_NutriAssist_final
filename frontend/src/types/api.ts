// API contract v1 (frozen at the end of Phase 3; PATCH/DELETE /api/conversations/{id} added for multi-chat).
//
// Mirrors backend/models/schemas.py. UUIDs and timestamps are strings on the wire
// (UUID v4 / ISO-8601). backend/tests/test_schemas.py fails if the field names here
// drift from the Pydantic models, so change both together.

export type ResponseStatus =
  | "answered"
  | "not_in_corpus"
  | "out_of_scope"
  | "error";

export interface SourceReference {
  document_name: string;
  publisher: string;
  year: number;
  /** May be an empty string when a document has no detectable heading. */
  section: string;
  url: string;
  chunk_id: string;
}

export interface Claim {
  claim_text: string;
  source: SourceReference;
}

/**
 * Invariants guaranteed by the backend:
 * - "answered": `claims` is non-empty and `refusal_reason` is null.
 * - any other status: `claims` is empty and `refusal_reason` is a non-empty string.
 */
export interface NutritionResponse {
  answer: string;
  claims: Claim[];
  status: ResponseStatus;
  refusal_reason: string | null;
}

/** A retrieved chunk with its supporting excerpt, for the sources panel. */
export interface RetrievedSource {
  rank: number;
  chunk_id: string;
  document_name: string;
  publisher: string;
  year: number;
  section: string;
  url: string;
  /** The chunk text (the evidence excerpt). */
  text: string;
  similarity_score: number | null;
}

/** POST /api/chat request body. `question` is 1-1000 characters after trimming. */
export interface ChatRequest {
  conversation_id: string | null;
  question: string;
}

/** POST /api/chat response. Refusals and errors carry an empty `retrieved_sources`. */
export interface ChatResponse extends NutritionResponse {
  conversation_id: string;
  message_id: string;
  retrieved_sources: RetrievedSource[];
}

/** POST /api/conversations request body. `title` is at most 255 characters; blank means untitled. */
export interface CreateConversationRequest {
  title?: string | null;
}

/** PATCH /api/conversations/{id} request body. `title` is 1-255 characters after trimming. */
export interface UpdateConversationRequest {
  title: string;
}

/** GET /api/conversations item; POST /api/conversations returns one of these. */
export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * A stored message. User messages have `status: null` and no claims; assistant messages
 * carry the structured response so the sources panel can be restored on reload.
 */
export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: ResponseStatus | null;
  claims: Claim[];
  refusal_reason: string | null;
  retrieved_sources: RetrievedSource[];
  created_at: string;
}

/** GET /api/conversations/{id}. */
export interface ConversationDetail extends ConversationSummary {
  messages: MessageOut[];
}

/**
 * GET /api/health. `status` is "ok" or "degraded"; each dependency is "ok", "unavailable"
 * or (llm only) "not_configured". HTTP 503 means a hard dependency (vector store or database)
 * is down; an unavailable LLM alone still returns 200 with status "degraded".
 */
export interface HealthResponse {
  status: string;
  vector_store?: string | null;
  database?: string | null;
  llm?: string | null;
}

/** Body of every non-2xx response. */
export interface ErrorEnvelope {
  /** Machine-readable code, e.g. "invalid_request". */
  error: string;
  message: string;
  detail?: string | null;
}
