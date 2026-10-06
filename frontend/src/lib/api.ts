import { getClientId } from "@/lib/clientId";
import type {
  ChatRequest,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  ErrorEnvelope,
  HealthResponse,
  UpdateConversationRequest,
} from "@/types/api";

// Must be referenced statically so Next.js can inline it at build time.
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

export type { HealthResponse };

/** A failed API call. `status` is 0 when the server could not be reached at all. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      signal,
      headers: {
        // Scopes every conversation to this browser; see lib/clientId.ts.
        "X-Client-Id": getClientId(),
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(
      0,
      "network_error",
      "Could not reach the assistant. Check your connection and try again.",
    );
  }
  if (!response.ok) {
    let envelope: Partial<ErrorEnvelope> = {};
    try {
      envelope = await response.json();
    } catch {
      // Not an envelope (e.g. a proxy error page): fall back to the status.
    }
    throw new ApiError(
      response.status,
      envelope.error ?? "http_error",
      envelope.message ?? `The request failed with status ${response.status}.`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request("/api/health", {}, signal);
}

export function listConversations(
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  return request("/api/conversations", {}, signal);
}

export function getConversation(
  id: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  return request(`/api/conversations/${id}`, {}, signal);
}

export function sendChat(body: ChatRequest): Promise<ChatResponse> {
  return request("/api/chat", { method: "POST", body: JSON.stringify(body) });
}

export function renameConversation(
  id: string,
  title: string,
): Promise<ConversationSummary> {
  const body: UpdateConversationRequest = { title };
  return request(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteConversation(id: string): Promise<void> {
  return request(`/api/conversations/${id}`, { method: "DELETE" });
}
