import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  deleteConversation,
  getHealth,
  listConversations,
  renameConversation,
  sendChat,
} from "./api";
import { getClientId, resetClientIdCache } from "./clientId";
import { clockTime, dayLabel, relativeTime, sameDay } from "./format";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

beforeEach(() => {
  window.localStorage.clear();
  resetClientIdCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getClientId", () => {
  it("creates a UUID once, saves it, and returns the same one afterwards", () => {
    const first = getClientId();
    expect(first).toMatch(UUID);
    expect(window.localStorage.getItem("nutriai.clientId")).toBe(first);
    resetClientIdCache();
    expect(getClientId()).toBe(first); // a reload reads it back
  });

  it("ignores a saved value that is not a UUID", () => {
    window.localStorage.setItem("nutriai.clientId", "not-an-id");
    expect(getClientId()).toMatch(UUID);
  });

  it("still works, for the life of the page, when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const id = getClientId();
    expect(id).toMatch(UUID);
    expect(getClientId()).toBe(id);
  });

  it("falls back to getRandomValues where crypto.randomUUID is unavailable (plain http)", () => {
    vi.stubGlobal("crypto", { getRandomValues: (a: Uint8Array) => a.fill(7) });
    expect(getClientId()).toMatch(UUID);
  });

  it("falls back to Math.random when there is no crypto at all", () => {
    vi.stubGlobal("crypto", undefined);
    expect(getClientId()).toMatch(UUID);
  });
});

function respond(body: unknown, init: ResponseInit = { status: 200 }) {
  const text = body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body);
  const fetchMock = vi.fn().mockImplementation(async () => new Response(text, init)); // a Response is read once
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api requests", () => {
  it("send the browser's client id on every call", async () => {
    const fetchMock = respond([]);
    await listConversations();
    await listConversations();
    for (const [, init] of fetchMock.mock.calls) {
      expect((init.headers as Record<string, string>)["X-Client-Id"]).toBe(getClientId());
    }
  });

  it("add a JSON content type only when there is a body", async () => {
    const fetchMock = respond({ conversation_id: "c" });
    await sendChat({ conversation_id: null, question: "hi" });
    expect((fetchMock.mock.calls[0][1].headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    respond([]);
    await listConversations();
    expect((vi.mocked(fetch).mock.calls[0][1]!.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("treat a 204 as success with no body", async () => {
    respond(undefined, { status: 204 });
    await expect(deleteConversation("id")).resolves.toBeUndefined();
  });

  it("use the server's error envelope for the message and code", async () => {
    respond({ error: "rate_limited", message: "Too many requests.", detail: "Retry in 5 seconds." }, { status: 429 });
    await expect(sendChat({ conversation_id: null, question: "q" })).rejects.toMatchObject({
      name: "ApiError", status: 429, code: "rate_limited", message: "Too many requests.",
    });
  });

  it("describe a failure that is not an envelope (a proxy error page) by its status", async () => {
    respond("<html>Bad gateway</html>", { status: 502 });
    await expect(getHealth()).rejects.toMatchObject({ status: 502, code: "http_error", message: expect.stringContaining("502") });
  });

  it("report an unreachable server as status 0 in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await listConversations().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "network_error" });
    expect(error.message).toMatch(/could not reach/i);
  });

  it("let an aborted request reject as the abort, not as a network error", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    await expect(listConversations(controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });

  it("send the title on rename", async () => {
    const fetchMock = respond({ id: "id", title: "New" });
    await renameConversation("id", "New");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/conversations\/id$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PATCH", body: JSON.stringify({ title: "New" }) });
  });
});

describe("time formatting", () => {
  const now = new Date("2026-10-06T12:00:00Z").getTime();

  it("describes recent activity in words", () => {
    expect(relativeTime("2026-10-06T11:59:40Z", now)).toBe("Just now");
    expect(relativeTime("2026-10-06T11:30:00Z", now)).toBe("30 min ago");
    expect(relativeTime("2026-10-06T09:00:00Z", now)).toBe("3h ago");
    expect(relativeTime("2026-10-05T09:00:00Z", now)).toBe("Yesterday");
    expect(relativeTime("2026-10-02T12:00:00Z", now)).toBe("4d ago");
    expect(relativeTime("2026-08-01T12:00:00Z", now)).toMatch(/Aug/);
  });

  it("does not show a negative time when a server clock is slightly ahead", () => {
    expect(relativeTime("2026-10-06T12:00:30Z", now)).toBe("Just now");
  });

  it("labels days and compares them", () => {
    expect(dayLabel("2026-10-06T10:00:00Z", now)).toMatch(/^Today, /);
    expect(dayLabel("2026-10-05T10:00:00Z", now)).toMatch(/^Yesterday, /);
    expect(sameDay("2026-10-06T10:00:00", "2026-10-06T20:00:00")).toBe(true);
    expect(sameDay("2026-10-06T10:00:00", "2026-10-07T10:00:00")).toBe(false);
    expect(clockTime("2026-10-06T10:05:00Z")).toMatch(/\d/);
  });
});
