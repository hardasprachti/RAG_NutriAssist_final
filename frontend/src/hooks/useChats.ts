"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import {
  ApiError,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  sendChat,
} from "@/lib/api";
import type {
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  MessageOut,
} from "@/types/api";

/**
 * The per-chat state machine from Architecture §4. `submitting` is the instant between the click and the
 * request leaving; with a single synchronous dispatch it is folded into `loading`.
 */
export type ChatPhase = "idle" | "loading" | "answered" | "not_in_corpus" | "out_of_scope" | "error";

export function chatPhase(chat: Chat): ChatPhase {
  if (chat.pending) return "loading";
  const last = chat.messages[chat.messages.length - 1];
  if (!last) return "idle";
  if (last.role === "user") return last.local === "failed" ? "error" : "idle";
  return last.status ?? "error";
}

/**
 * Many independent chats, ChatGPT-style. Every chat owns its own messages, in-flight request and source
 * selection, so a slow answer in one chat never blocks, leaks into or reorders another. The backend keeps
 * no cross-chat state either: history is read per `conversation_id`.
 *
 * A chat that has not sent its first question yet is a *draft* (`conversationId === null`). It is keyed
 * locally and re-keyed to the server's conversation id when the first answer arrives.
 */

/** A stored message, or a question that has not been answered (`local`). */
export interface UiMessage extends MessageOut {
  local?: "failed";
  error?: string;
}

export interface Selection {
  messageId: string;
  /** The cited chunk to highlight, or null to just show the message's sources. */
  chunkId: string | null;
}

export interface Chat {
  key: string;
  conversationId: string | null;
  title: string | null;
  messages: UiMessage[];
  /** History fetched (always true for drafts). */
  loaded: boolean;
  loadError: string | null;
  /** A question is in flight. */
  pending: boolean;
  selection: Selection | null;
  /** Unsent text in this chat's input box; each chat keeps its own. */
  input: string;
}

export interface SidebarItem {
  key: string;
  /** Null for a new chat still waiting for its first answer (it cannot be renamed or deleted yet). */
  conversationId: string | null;
  title: string;
  /** Last activity (ISO), or null while a new chat awaits its first answer. */
  updatedAt: string | null;
  active: boolean;
  pending: boolean;
}

interface State {
  chats: Record<string, Chat>;
  activeKey: string;
  summaries: ConversationSummary[];
  listStatus: "loading" | "ready" | "error";
  /** The URL has been read; until then nothing may be written back to it. */
  hydrated: boolean;
  notice: string | null;
}

type Action =
  | { type: "hydrate"; id: string | null }
  | { type: "open"; id: string }
  | { type: "activate"; key: string }
  | { type: "new"; draftKey: string }
  | { type: "list_loaded"; summaries: ConversationSummary[] }
  | { type: "list_failed" }
  | { type: "history_loaded"; detail: ConversationDetail }
  | { type: "history_failed"; id: string; message: string }
  | { type: "history_retry"; id: string }
  | { type: "history_refreshed"; detail: ConversationDetail }
  | { type: "send_started"; key: string; message: UiMessage }
  | { type: "send_retried"; key: string; messageId: string }
  | { type: "send_succeeded"; key: string; messageId: string; response: ChatResponse; now: string }
  | { type: "send_failed"; key: string; messageId: string; error: string }
  | { type: "select"; key: string; selection: Selection | null }
  | { type: "input"; key: string; value: string }
  | { type: "renamed"; id: string; title: string }
  | { type: "removed"; id: string; draftKey: string }
  | { type: "gone"; id: string; draftKey: string }
  | { type: "notice"; message: string | null };

const TITLE_MAX_CHARS = 60;
export const UNTITLED = "New chat";

function draft(key: string): Chat {
  return {
    key, conversationId: null, title: null, messages: [], loaded: true, loadError: null, pending: false,
    selection: null, input: "",
  };
}

function stored(id: string, title: string | null = null): Chat {
  return {
    key: id, conversationId: id, title, messages: [], loaded: false, loadError: null, pending: false,
    selection: null, input: "",
  };
}

/** Mirrors the backend's `conversation_title` closely enough for the instant before the list is refreshed. */
export function titleFromQuestion(question: string): string {
  const text = question.replace(/\s+/g, " ").trim();
  if (text.length <= TITLE_MAX_CHARS) return text;
  let cut = text.slice(0, TITLE_MAX_CHARS - 1);
  if (text[cut.length] !== " " && cut.includes(" ")) cut = cut.slice(0, cut.lastIndexOf(" "));
  return cut.replace(/[ ,;:.-]+$/, "") + "…";
}

function patchChat(state: State, key: string, patch: (chat: Chat) => Chat): State {
  const chat = state.chats[key];
  return chat ? { ...state, chats: { ...state.chats, [key]: patch(chat) } } : state;
}

function patchMessage(chat: Chat, messageId: string, patch: Partial<UiMessage>): Chat {
  return { ...chat, messages: chat.messages.map((m) => (m.id === messageId ? { ...m, ...patch } : m)) };
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const rest = { ...record };
  delete rest[key];
  return rest;
}

/** Open `id`, creating its (unloaded) chat on first use. */
function openStored(state: State, id: string): State {
  const known = state.summaries.find((s) => s.id === id)?.title ?? null;
  return {
    ...state,
    chats: state.chats[id] ? state.chats : { ...state.chats, [id]: stored(id, known) },
    activeKey: id,
  };
}

/** Switch to the first chat that is not `removedKey`, or to a fresh draft. */
function afterRemoval(state: State, removedKey: string, draftKey: string): State {
  if (state.activeKey !== removedKey) return state;
  const next = state.summaries.find((s) => s.id !== removedKey);
  if (next) return openStored(state, next.id);
  return { ...state, chats: { ...state.chats, [draftKey]: draft(draftKey) }, activeKey: draftKey };
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "hydrate": {
      const base = { ...state, hydrated: true };
      return action.id ? openStored(base, action.id) : base;
    }
    case "open":
      return openStored(state, action.id);
    case "activate":
      return state.chats[action.key] ? { ...state, activeKey: action.key } : state;
    case "new": {
      // Re-use an untouched draft instead of piling up empty ones.
      const empty = Object.values(state.chats).find((c) => !c.conversationId && c.messages.length === 0);
      if (empty) return { ...state, activeKey: empty.key };
      return { ...state, chats: { ...state.chats, [action.draftKey]: draft(action.draftKey) }, activeKey: action.draftKey };
    }
    case "list_loaded":
      return { ...state, summaries: action.summaries, listStatus: "ready" };
    case "list_failed":
      return state.listStatus === "ready" ? state : { ...state, listStatus: "error" };
    case "history_loaded": {
      const { detail } = action;
      return patchChat(state, detail.id, (chat) => ({
        ...chat,
        title: detail.title,
        loaded: true,
        loadError: null,
        // Anything sent from this tab while history was loading comes after what is stored.
        messages: [...detail.messages, ...chat.messages.filter((m) => !detail.messages.some((s) => s.id === m.id))],
      }));
    }
    case "history_failed":
      return patchChat(state, action.id, (chat) => ({ ...chat, loadError: action.message }));
    case "history_refreshed":
      // Another tab may have added turns. The server is the truth for a chat that is at rest; a chat with a
      // question in flight or a failed one still on screen keeps what this tab has.
      return patchChat(state, action.detail.id, (chat) =>
        chat.pending || chat.messages.some((m) => m.local)
          ? chat
          : {
              ...chat,
              title: action.detail.title,
              messages: action.detail.messages,
              selection: action.detail.messages.some((m) => m.id === chat.selection?.messageId) ? chat.selection : null,
            },
      );
    case "history_retry":
      return patchChat(state, action.id, (chat) => ({ ...chat, loadError: null }));
    case "send_started":
      return patchChat(state, action.key, (chat) => ({
        ...chat,
        pending: true,
        title: chat.title ?? titleFromQuestion(action.message.content),
        messages: [...chat.messages, action.message],
        selection: null,
        input: "",
      }));
    case "send_retried":
      return patchChat(state, action.key, (chat) => ({
        ...patchMessage(chat, action.messageId, { local: undefined, error: undefined }),
        pending: true,
      }));
    case "send_failed":
      return patchChat(state, action.key, (chat) => ({
        ...patchMessage(chat, action.messageId, { local: "failed", error: action.error }),
        pending: false,
      }));
    case "send_succeeded": {
      const chat = state.chats[action.key];
      if (!chat) return state; // the chat was deleted while the answer was on its way
      const { response } = action;
      const answer: UiMessage = {
        id: response.message_id,
        role: "assistant",
        content: response.answer,
        status: response.status,
        claims: response.claims,
        refusal_reason: response.refusal_reason,
        retrieved_sources: response.retrieved_sources,
        created_at: action.now,
      };
      const done: Chat = {
        ...chat,
        key: response.conversation_id,
        conversationId: response.conversation_id,
        messages: [...chat.messages, answer],
        pending: false,
        selection: null,
      };
      // A draft becomes a real conversation: re-key it, and follow it if the user is looking at it.
      const chats = { ...without(state.chats, action.key), [done.key]: done };
      const known = state.summaries.find((s) => s.id === done.key);
      const summaries: ConversationSummary[] = [
        {
          id: done.key,
          title: known?.title ?? done.title,
          created_at: known?.created_at ?? action.now,
          updated_at: action.now,
        },
        ...state.summaries.filter((s) => s.id !== done.key),
      ];
      return { ...state, chats, summaries, activeKey: state.activeKey === action.key ? done.key : state.activeKey };
    }
    case "select":
      return patchChat(state, action.key, (chat) => ({ ...chat, selection: action.selection }));
    case "input":
      return patchChat(state, action.key, (chat) => ({ ...chat, input: action.value }));
    case "renamed":
      return {
        ...patchChat(state, action.id, (chat) => ({ ...chat, title: action.title })),
        summaries: state.summaries.map((s) => (s.id === action.id ? { ...s, title: action.title } : s)),
      };
    case "removed": {
      const next = {
        ...state,
        chats: without(state.chats, action.id),
        summaries: state.summaries.filter((s) => s.id !== action.id),
      };
      return afterRemoval(next, action.id, action.draftKey);
    }
    case "gone": {
      // Deleted from elsewhere. Silent when this tab already forgot it (the user deleted it themselves).
      if (!state.chats[action.id]) return state;
      return { ...reducer(state, { type: "removed", id: action.id, draftKey: action.draftKey }), notice: "That chat no longer exists." };
    }
    case "notice":
      return { ...state, notice: action.message };
  }
}

const FIRST_DRAFT = "draft-0";
let draftCounter = 0;
let messageCounter = 0;
const nextDraftKey = () => `draft-${++draftCounter}`;
const nextMessageId = () => `local-${++messageCounter}`;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function idFromHash(): string | null {
  const id = window.location.hash.slice(1);
  return UUID.test(id) ? id.toLowerCase() : null;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export function useChats() {
  const [state, dispatch] = useReducer(reducer, undefined, (): State => ({
    chats: { [FIRST_DRAFT]: draft(FIRST_DRAFT) },
    activeKey: FIRST_DRAFT,
    summaries: [],
    listStatus: "loading",
    hydrated: false,
    notice: null,
  }));

  const active = state.chats[state.activeKey];
  const activeId = active.conversationId;

  const refreshList = useCallback(async () => {
    try {
      dispatch({ type: "list_loaded", summaries: await listConversations() });
    } catch {
      dispatch({ type: "list_failed" });
    }
  }, []);

  // Which chat is open lives in the URL (`#<conversation id>`): reloads, bookmarks, the back button and
  // a second browser tab on another chat all work.
  useEffect(() => {
    const fromUrl = (): Action => {
      const id = idFromHash();
      return id ? { type: "open", id } : { type: "new", draftKey: nextDraftKey() };
    };
    dispatch({ type: "hydrate", id: idFromHash() });
    const onHashChange = () => dispatch(fromUrl());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!state.hydrated) return;
    const wanted = activeId ?? "";
    if (window.location.hash.slice(1).toLowerCase() === wanted) return;
    const url = wanted ? `#${wanted}` : window.location.pathname + window.location.search;
    window.history.pushState(null, "", url);
  }, [state.hydrated, activeId]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // Fetch the history of the open chat once (an unloaded chat only exists when opened from the list or URL).
  const fetching = useRef(new Set<string>());
  const needsHistory = !!activeId && !active.loaded && !active.loadError;
  useEffect(() => {
    if (!needsHistory || !activeId || fetching.current.has(activeId)) return;
    const id = activeId;
    fetching.current.add(id);
    getConversation(id)
      .then((detail) => dispatch({ type: "history_loaded", detail }))
      .catch((error) => {
        const missing = error instanceof ApiError && (error.status === 404 || error.status === 422);
        dispatch(
          missing
            ? { type: "gone", id, draftKey: nextDraftKey() }
            : { type: "history_failed", id, message: errorText(error) },
        );
      })
      .finally(() => fetching.current.delete(id));
  }, [needsHistory, activeId]);

  // Coming back to this tab: pick up turns another tab or device added to the open chat, and refresh the list.
  const atRest = !!activeId && active.loaded && !active.pending && !active.messages.some((m) => m.local);
  useEffect(() => {
    if (!atRest || !activeId) return;
    const id = activeId;
    let last = 0;
    const refresh = () => {
      if (document.visibilityState !== "visible" || Date.now() - last < 2000) return; // focus and visibility both fire
      last = Date.now();
      void refreshList();
      getConversation(id)
        .then((detail) => dispatch({ type: "history_refreshed", detail }))
        .catch((error) => {
          if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
            dispatch({ type: "gone", id, draftKey: nextDraftKey() });
          } // any other failure: keep what is on screen, the next focus tries again
        });
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [atRest, activeId, refreshList]);

  const deliver = useCallback(
    async (key: string, conversationId: string | null, messageId: string, question: string) => {
      try {
        const response = await sendChat({ conversation_id: conversationId, question });
        dispatch({ type: "send_succeeded", key, messageId, response, now: new Date().toISOString() });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404 && conversationId) {
          // The chat was deleted from elsewhere; nothing of this turn was stored.
          dispatch({ type: "gone", id: key, draftKey: nextDraftKey() });
        } else {
          dispatch({ type: "send_failed", key, messageId, error: errorText(error) });
        }
      } finally {
        void refreshList();
      }
    },
    [refreshList],
  );

  const send = useCallback(
    (question: string = active.input) => {
      const text = question.trim();
      if (!text || active.pending || !active.loaded) return;
      const message: UiMessage = {
        id: nextMessageId(), role: "user", content: text, status: null, claims: [], refusal_reason: null,
        retrieved_sources: [], created_at: new Date().toISOString(),
      };
      dispatch({ type: "send_started", key: active.key, message });
      void deliver(active.key, active.conversationId, message.id, text);
    },
    [active, deliver],
  );

  /** Re-send a question whose request failed. Nothing was stored for it, so this cannot duplicate it. */
  const retry = useCallback(
    (messageId: string) => {
      const message = active.messages.find((m) => m.id === messageId && m.local === "failed");
      if (!message || active.pending) return;
      dispatch({ type: "send_retried", key: active.key, messageId });
      void deliver(active.key, active.conversationId, messageId, message.content);
    },
    [active, deliver],
  );

  const rename = useCallback(async (id: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      const updated = await renameConversation(id, trimmed);
      dispatch({ type: "renamed", id, title: updated.title ?? trimmed });
    } catch (error) {
      dispatch({ type: "notice", message: `Could not rename the chat. ${errorText(error)}` });
    }
  }, []);

  const remove = useCallback(async (id: string) => {
    try {
      await deleteConversation(id);
    } catch (error) {
      // Already gone is the outcome the user asked for.
      if (!(error instanceof ApiError && error.status === 404)) {
        dispatch({ type: "notice", message: `Could not delete the chat. ${errorText(error)}` });
        return;
      }
    }
    dispatch({ type: "removed", id, draftKey: nextDraftKey() });
  }, []);

  const sidebar: SidebarItem[] = [
    // A new chat whose first answer is still on its way is not in the server's list yet.
    ...Object.values(state.chats)
      .filter((c) => !c.conversationId && c.messages.length > 0)
      .map((c) => ({
        key: c.key, conversationId: null, title: c.title ?? UNTITLED, updatedAt: null,
        active: c.key === state.activeKey, pending: c.pending,
      })),
    ...state.summaries.map((s) => ({
      key: s.id,
      conversationId: s.id,
      title: state.chats[s.id]?.title ?? s.title ?? UNTITLED,
      updatedAt: s.updated_at,
      active: s.id === state.activeKey,
      pending: !!state.chats[s.id]?.pending,
    })),
  ];

  return {
    active,
    sidebar,
    listStatus: state.listStatus,
    notice: state.notice,
    send,
    retry,
    rename,
    remove,
    refreshList,
    newChat: () => dispatch({ type: "new", draftKey: nextDraftKey() }),
    open: (key: string) => {
      dispatch(state.chats[key]?.conversationId === null ? { type: "activate", key } : { type: "open", id: key });
    },
    retryHistory: () => activeId && dispatch({ type: "history_retry", id: activeId }),
    setInput: (value: string) => dispatch({ type: "input", key: active.key, value }),
    select: (selection: Selection | null) => dispatch({ type: "select", key: active.key, selection }),
    dismissNotice: () => dispatch({ type: "notice", message: null }),
  };
}
