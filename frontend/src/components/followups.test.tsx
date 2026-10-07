import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { Chat } from "@/hooks/useChats";
import { CLAIM, OTHER_SOURCE, SOURCE, message } from "@/test-utils";
import MessageList from "./MessageList";

beforeAll(() => {
  // jsdom has no layout, so no scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
});

const user = (id: string, content: string) => message({ id, role: "user", content });
const answered = message({
  id: "a1", role: "assistant", content: "The FDA says 1 to 2 days.", status: "answered", claims: [CLAIM],
  retrieved_sources: [SOURCE, OTHER_SOURCE],
});
const declined = message({
  id: "d1", role: "assistant", content: "I can't help with that.", status: "out_of_scope", refusal_reason: "Medical.",
});

function chat(messages: Chat["messages"], overrides: Partial<Chat> = {}): Chat {
  return {
    key: "c1", conversationId: "c1", title: "t", messages, loaded: true, loadError: null, pending: false,
    selection: null, input: "", ...overrides,
  };
}

const renderList = (c: Chat, onSend = vi.fn()) => {
  render(<MessageList chat={c} onRetry={() => {}} onCite={() => {}} onSend={onSend} />);
  return onSend;
};

describe("follow-up suggestions in the conversation", () => {
  it("shows related questions under an answer and sends the one the user picks", async () => {
    const onSend = renderList(chat([user("u1", "How long can chicken stay in the fridge?"), answered]));
    const group = screen.getByRole("group", { name: "Related questions" });
    const buttons = within(group).getAllByRole("button");
    expect(buttons).toHaveLength(3);

    await userEvent.click(buttons[0]);
    expect(onSend).toHaveBeenCalledWith(buttons[0].textContent);
  });

  it("shows general questions after a decline", () => {
    renderList(chat([user("u1", "How many calories should I eat?"), declined]));
    const group = screen.getByRole("group", { name: "General questions I can answer" });
    expect(within(group).getAllByRole("button")).toHaveLength(3);
  });

  it("appears under the latest reply only", () => {
    renderList(chat([user("u1", "q1"), answered, user("u2", "q2"), declined]));
    expect(screen.getAllByTestId("follow-ups")).toHaveLength(1);
    expect(screen.getByRole("group", { name: "General questions I can answer" })).toBeInTheDocument();
  });

  it("is hidden while a question is pending, after an error, and when the user spoke last", () => {
    const { unmount } = render(
      <MessageList chat={chat([user("u1", "q"), answered], { pending: true })} onRetry={() => {}} onCite={() => {}} onSend={() => {}} />,
    );
    expect(screen.queryByTestId("follow-ups")).toBeNull();
    unmount();

    const error = message({ id: "e1", role: "assistant", content: "x", status: "error", refusal_reason: "r" });
    const second = render(
      <MessageList chat={chat([user("u1", "q"), error])} onRetry={() => {}} onCite={() => {}} onSend={() => {}} />,
    );
    expect(screen.queryByTestId("follow-ups")).toBeNull();
    second.unmount();

    render(<MessageList chat={chat([answered, user("u2", "q2")])} onRetry={() => {}} onCite={() => {}} onSend={() => {}} />);
    expect(screen.queryByTestId("follow-ups")).toBeNull();
  });

  it("does not suggest a question the user has already asked", () => {
    const asked = "How long does bacon last in the fridge and in the freezer?";
    renderList(chat([user("u1", asked), answered]));
    expect(screen.queryByRole("button", { name: asked })).toBeNull();
  });
});
