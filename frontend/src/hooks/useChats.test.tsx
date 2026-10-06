import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import {
  answeredResponse,
  deferred,
  detail,
  message,
  refusalResponse,
  summary,
} from "@/test-utils";
import type { ChatResponse, ConversationSummary } from "@/types/api";
import { chatPhase, useChats, type Chat } from "./useChats";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  sendChat: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}));

// A tiny in-memory "server": the list the API returns follows what has been answered or deleted.
let server: ConversationSummary[];

function answer(conversationId: string, text: string): ChatResponse {
  server = [summary(conversationId, text), ...server.filter((s) => s.id !== conversationId)];
  return answeredResponse(conversationId, text);
}

beforeEach(() => {
  server = [];
  vi.mocked(api.listConversations).mockImplementation(async () => [...server]);
  vi.mocked(api.deleteConversation).mockImplementation(async (id) => {
    server = server.filter((s) => s.id !== id);
  });
  vi.mocked(api.sendChat).mockReset();
  vi.mocked(api.getConversation).mockReset();
});

async function mount() {
  const hook = renderHook(() => useChats());
  await waitFor(() => expect(hook.result.current.listStatus).toBe("ready"));
  return hook;
}

const contents = (chat: Chat) => chat.messages.map((m) => m.content);

describe("independent chats", () => {
  it("lets a second chat be asked and answered while the first is still waiting", async () => {
    const { result } = await mount();
    const a = deferred<ChatResponse>();
    const b = deferred<ChatResponse>();
    vi.mocked(api.sendChat).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);

    act(() => result.current.setInput("question A"));
    act(() => result.current.send());
    expect(result.current.active.pending).toBe(true);

    act(() => result.current.newChat());
    expect(result.current.active.messages).toEqual([]);
    expect(result.current.active.pending).toBe(false);

    act(() => result.current.setInput("question B"));
    act(() => result.current.send());
    await act(async () => b.resolve(answer("conv-b", "answer B")));

    expect(contents(result.current.active)).toEqual(["question B", "answer B"]);
    expect(result.current.active.conversationId).toBe("conv-b");
    expect(result.current.sidebar.filter((i) => i.pending)).toHaveLength(1); // A, still on its way

    await act(async () => a.resolve(answer("conv-a", "answer A")));
    act(() => result.current.open("conv-a"));
    expect(contents(result.current.active)).toEqual(["question A", "answer A"]);
    act(() => result.current.open("conv-b"));
    expect(contents(result.current.active)).toEqual(["question B", "answer B"]);

    // Both turns started as new chats: neither was sent into the other's conversation.
    expect(vi.mocked(api.sendChat).mock.calls.map(([body]) => body.conversation_id)).toEqual([null, null]);
  });

  it("continues a chat in its own conversation, not in another", async () => {
    server = [summary("conv-a", "A"), summary("conv-b", "B")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockResolvedValue(detail("conv-b", "B", []));
    vi.mocked(api.sendChat).mockImplementation(async (body) => answer(body.conversation_id!, "ok"));

    act(() => result.current.open("conv-b"));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));
    act(() => result.current.setInput("follow-up"));
    act(() => result.current.send());

    await waitFor(() => expect(api.sendChat).toHaveBeenCalledWith({ conversation_id: "conv-b", question: "follow-up" }));
  });

  it("keeps each chat's unsent text", async () => {
    const { result } = await mount();
    vi.mocked(api.sendChat).mockReturnValue(deferred<ChatResponse>().promise);

    act(() => result.current.send("first question"));
    act(() => result.current.setInput("typed while waiting"));
    const firstChat = result.current.active.key;

    act(() => result.current.newChat());
    expect(result.current.active.input).toBe("");
    act(() => result.current.setInput("typed in the new chat"));

    act(() => result.current.open(firstChat));
    expect(result.current.active.input).toBe("typed while waiting");
    act(() => result.current.newChat());
    expect(result.current.active.input).toBe("typed in the new chat");
  });

  it("re-keys a new chat to its conversation id and follows it", async () => {
    const { result } = await mount();
    vi.mocked(api.sendChat).mockImplementation(async () => answer("conv-1", "hello"));
    act(() => result.current.setInput("hi"));
    act(() => result.current.send());
    await waitFor(() => expect(result.current.active.conversationId).toBe("conv-1"));
    expect(result.current.active.key).toBe("conv-1");
    expect(result.current.sidebar.map((i) => i.key)).toEqual(["conv-1"]);
  });
});

describe("failures", () => {
  it("keeps the question after a failed send and retries it without duplicating", async () => {
    const { result } = await mount();
    vi.mocked(api.sendChat)
      .mockRejectedValueOnce(new api.ApiError(503, "service_unavailable", "The assistant is unavailable."))
      .mockImplementationOnce(async () => answer("conv-1", "answer"));

    act(() => result.current.setInput("will fail"));
    act(() => result.current.send());
    await waitFor(() => expect(result.current.active.pending).toBe(false));

    const failed = result.current.active.messages[0];
    expect(failed).toMatchObject({ content: "will fail", local: "failed", error: "The assistant is unavailable." });
    expect(chatPhase(result.current.active)).toBe("error");

    act(() => result.current.retry(failed.id));
    await waitFor(() => expect(contents(result.current.active)).toEqual(["will fail", "answer"]));
    expect(vi.mocked(api.sendChat).mock.calls.map(([b]) => b.question)).toEqual(["will fail", "will fail"]);
  });

  it("explains a network failure in words", async () => {
    const { result } = await mount();
    vi.mocked(api.sendChat).mockRejectedValueOnce(new api.ApiError(0, "network_error", "Could not reach the assistant."));
    act(() => result.current.setInput("q"));
    act(() => result.current.send());
    await waitFor(() => expect(result.current.active.messages[0]?.error).toBe("Could not reach the assistant."));
  });

  it("does not send blank input or send twice while an answer is pending", async () => {
    const { result } = await mount();
    const pending = deferred<ChatResponse>();
    vi.mocked(api.sendChat).mockReturnValue(pending.promise);

    act(() => result.current.send("   "));
    expect(api.sendChat).not.toHaveBeenCalled();

    act(() => result.current.send("first"));
    act(() => result.current.send("second"));
    expect(api.sendChat).toHaveBeenCalledTimes(1);
  });

  it("shows a refusal as the chat's phase", async () => {
    const { result } = await mount();
    vi.mocked(api.sendChat).mockResolvedValue(refusalResponse("conv-1", "out_of_scope"));
    act(() => result.current.send("how many calories should I eat?"));
    await waitFor(() => expect(chatPhase(result.current.active)).toBe("out_of_scope"));
  });
});

describe("history, rename and delete", () => {
  const ID_A = "11111111-1111-4111-8111-111111111111";
  const ID_B = "22222222-2222-4222-8222-222222222222";

  it("loads a stored chat once, with its sources, when it is opened", async () => {
    server = [summary(ID_A, "Chicken")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockResolvedValue(
      detail(ID_A, "Chicken", [
        message({ id: "u1", role: "user", content: "How long?" }),
        message({ id: "a1", role: "assistant", content: "1-2 days", status: "answered", claims: answeredResponse(ID_A, "x").claims, retrieved_sources: answeredResponse(ID_A, "x").retrieved_sources }),
      ]),
    );

    act(() => result.current.open(ID_A));
    expect(result.current.active.loaded).toBe(false);
    await waitFor(() => expect(result.current.active.loaded).toBe(true));
    expect(result.current.active.messages[1].retrieved_sources).toHaveLength(2);

    act(() => result.current.newChat());
    act(() => result.current.open(ID_A));
    expect(api.getConversation).toHaveBeenCalledTimes(1);
  });

  it("keeps the open chat in the URL and follows the back button", async () => {
    server = [summary(ID_A, "A"), summary(ID_B, "B")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockImplementation(async (id) => detail(id, id === ID_A ? "A" : "B", []));

    act(() => result.current.open(ID_A));
    await waitFor(() => expect(window.location.hash).toBe(`#${ID_A}`));

    act(() => {
      window.location.hash = ID_B;
    });
    await waitFor(() => expect(result.current.active.conversationId).toBe(ID_B));

    act(() => result.current.newChat());
    await waitFor(() => expect(window.location.hash).toBe(""));
  });

  it("opens the chat named in the URL on load", async () => {
    server = [summary(ID_A, "A")];
    window.location.hash = ID_A;
    vi.mocked(api.getConversation).mockResolvedValue(detail(ID_A, "A", [message({ id: "u1", role: "user", content: "hi" })]));
    const { result } = await mount();
    await waitFor(() => expect(result.current.active.conversationId).toBe(ID_A));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));
    expect(contents(result.current.active)).toEqual(["hi"]);
  });

  it("forgets a chat that no longer exists instead of showing an error page", async () => {
    window.location.hash = ID_A;
    vi.mocked(api.getConversation).mockRejectedValue(new api.ApiError(404, "conversation_not_found", "That conversation does not exist."));
    const { result } = await mount();
    await waitFor(() => expect(result.current.notice).toBe("That chat no longer exists."));
    expect(result.current.active.conversationId).toBeNull();
  });

  it("deleting the open chat moves to another one, and deleting the last one gives a fresh chat", async () => {
    server = [summary(ID_A, "A"), summary(ID_B, "B")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockImplementation(async (id) => detail(id, id, []));
    act(() => result.current.open(ID_A));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));

    await act(async () => result.current.remove(ID_A));
    expect(result.current.sidebar.map((i) => i.key)).toEqual([ID_B]);
    expect(result.current.active.conversationId).toBe(ID_B);

    await act(async () => result.current.remove(ID_B));
    expect(result.current.sidebar).toEqual([]);
    expect(result.current.active.conversationId).toBeNull();
    expect(result.current.active.messages).toEqual([]);
  });

  it("a delete that fails leaves the chat and says so", async () => {
    server = [summary(ID_A, "A")];
    const { result } = await mount();
    vi.mocked(api.deleteConversation).mockRejectedValueOnce(new api.ApiError(503, "service_unavailable", "Try later."));
    await act(async () => result.current.remove(ID_A));
    expect(result.current.sidebar.map((i) => i.key)).toEqual([ID_A]);
    expect(result.current.notice).toContain("Could not delete");
  });

  it("an answer that arrives for a chat the user deleted meanwhile is dropped quietly", async () => {
    server = [summary(ID_A, "A")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockResolvedValue(detail(ID_A, "A", []));
    act(() => result.current.open(ID_A));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));

    const late = deferred<ChatResponse>();
    vi.mocked(api.sendChat).mockReturnValue(late.promise);
    act(() => result.current.send("follow-up"));
    await act(async () => result.current.remove(ID_A));
    expect(result.current.sidebar).toEqual([]);

    // The server now reports the conversation as gone; this tab already knows, so no notice.
    await act(async () => late.reject(new api.ApiError(404, "conversation_not_found", "gone")));
    expect(result.current.notice).toBeNull();
    expect(result.current.sidebar).toEqual([]);
  });

  it("tells the user when a chat was deleted from somewhere else", async () => {
    server = [summary(ID_A, "A")];
    const { result } = await mount();
    vi.mocked(api.getConversation).mockResolvedValue(detail(ID_A, "A", []));
    act(() => result.current.open(ID_A));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));

    vi.mocked(api.sendChat).mockRejectedValue(new api.ApiError(404, "conversation_not_found", "gone"));
    act(() => result.current.send("follow-up"));
    await waitFor(() => expect(result.current.notice).toBe("That chat no longer exists."));
    expect(result.current.active.conversationId).toBeNull();
  });

  it("renames in the sidebar and in the header title", async () => {
    server = [summary(ID_A, "Old")];
    const { result } = await mount();
    vi.mocked(api.renameConversation).mockResolvedValue(summary(ID_A, "New"));
    vi.mocked(api.getConversation).mockResolvedValue(detail(ID_A, "Old", []));
    act(() => result.current.open(ID_A));
    await waitFor(() => expect(result.current.active.loaded).toBe(true));
    await act(async () => result.current.rename(ID_A, "  New  "));
    expect(api.renameConversation).toHaveBeenCalledWith(ID_A, "New");
    expect(result.current.sidebar[0].title).toBe("New");
    expect(result.current.active.title).toBe("New");
  });
});

describe("coming back to a tab", () => {
  const ID = "33333333-3333-4333-8333-333333333333";
  const focus = () => act(() => void window.dispatchEvent(new Event("focus")));

  async function openChat(initial = [message({ id: "u1", role: "user", content: "first" })]) {
    server = [summary(ID, "Chat")];
    vi.mocked(api.getConversation).mockResolvedValue(detail(ID, "Chat", initial));
    const hook = await mount();
    act(() => hook.result.current.open(ID));
    await waitFor(() => expect(hook.result.current.active.loaded).toBe(true));
    return hook;
  }

  it("picks up turns another tab added to the open chat", async () => {
    const { result } = await openChat();
    expect(contents(result.current.active)).toEqual(["first"]);

    vi.mocked(api.getConversation).mockResolvedValue(
      detail(ID, "Renamed elsewhere", [
        message({ id: "u1", role: "user", content: "first" }),
        message({ id: "a1", role: "assistant", content: "reply from the other tab", status: "answered" }),
      ]),
    );
    focus();
    await waitFor(() => expect(contents(result.current.active)).toEqual(["first", "reply from the other tab"]));
    expect(result.current.active.title).toBe("Renamed elsewhere");
  });

  it("refreshes the chat list too", async () => {
    const { result } = await openChat();
    server = [summary("44444444-4444-4444-8444-444444444444", "Made on another device"), ...server];
    focus();
    await waitFor(() => expect(result.current.sidebar.map((i) => i.title)).toContain("Made on another device"));
  });

  it("does not touch a chat that has a question in flight", async () => {
    const { result } = await openChat();
    vi.mocked(api.sendChat).mockReturnValue(deferred<ChatResponse>().promise);
    act(() => result.current.send("pending question"));
    vi.mocked(api.getConversation).mockClear();
    focus();
    await new Promise((r) => setTimeout(r, 50));
    expect(api.getConversation).not.toHaveBeenCalled();
    expect(contents(result.current.active)).toEqual(["first", "pending question"]);
  });

  it("does not wipe a failed question that is waiting for Try again", async () => {
    const { result } = await openChat();
    vi.mocked(api.sendChat).mockRejectedValue(new api.ApiError(503, "service_unavailable", "Try later."));
    act(() => result.current.send("will fail"));
    await waitFor(() => expect(result.current.active.messages.at(-1)?.local).toBe("failed"));
    vi.mocked(api.getConversation).mockClear();
    focus();
    await new Promise((r) => setTimeout(r, 50));
    expect(api.getConversation).not.toHaveBeenCalled();
    expect(result.current.active.messages.at(-1)).toMatchObject({ content: "will fail", local: "failed" });
  });

  it("asks only once when focus and visibility change arrive together", async () => {
    const { result } = await openChat();
    vi.mocked(api.getConversation).mockClear();
    act(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(api.getConversation).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(api.getConversation).toHaveBeenCalledTimes(1);
    expect(result.current.active.loaded).toBe(true);
  });

  it("keeps what is on screen when the refresh fails", async () => {
    const { result } = await openChat();
    vi.mocked(api.getConversation).mockRejectedValue(new api.ApiError(0, "network_error", "offline"));
    focus();
    await new Promise((r) => setTimeout(r, 50));
    expect(contents(result.current.active)).toEqual(["first"]);
    expect(result.current.notice).toBeNull();
  });

  it("says so when the chat was deleted from another device", async () => {
    const { result } = await openChat();
    vi.mocked(api.getConversation).mockRejectedValue(new api.ApiError(404, "conversation_not_found", "gone"));
    focus();
    await waitFor(() => expect(result.current.notice).toBe("That chat no longer exists."));
    expect(result.current.active.conversationId).toBeNull();
  });
});

describe("chatPhase", () => {
  const base: Chat = {
    key: "k", conversationId: null, title: null, messages: [], loaded: true, loadError: null, pending: false,
    selection: null, input: "",
  };
  const user = message({ id: "u", role: "user", content: "q" });

  it("follows the Architecture §4 state machine", () => {
    expect(chatPhase(base)).toBe("idle");
    expect(chatPhase({ ...base, pending: true, messages: [user] })).toBe("loading");
    expect(chatPhase({ ...base, messages: [user, message({ id: "a", role: "assistant", content: "x", status: "answered" })] })).toBe("answered");
    for (const status of ["not_in_corpus", "out_of_scope", "error"] as const) {
      expect(chatPhase({ ...base, messages: [user, message({ id: "a", role: "assistant", content: "x", status })] })).toBe(status);
    }
    expect(chatPhase({ ...base, messages: [{ ...user, local: "failed" }] })).toBe("error");
  });
});
