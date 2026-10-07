import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CLAIM, OTHER_SOURCE, SOURCE, message } from "@/test-utils";
import AssistantBubble, { answerAsText } from "./AssistantBubble";
import MessageInputBar from "./MessageInputBar";
import RefusalBanner, { CORPUS_DOCUMENTS } from "./RefusalBanner";
import SourcesPanel from "./SourcesPanel";

const answered = message({
  id: "a1", role: "assistant", content: "The FDA says 1 to 2 days.", status: "answered", claims: [CLAIM],
  retrieved_sources: [SOURCE, OTHER_SOURCE],
});

describe("AssistantBubble", () => {
  it("numbers each claim's badge like the sources panel and reports the click", async () => {
    const onCite = vi.fn();
    render(<AssistantBubble message={answered} selectedChunkId={null} onCite={onCite} />);

    expect(screen.getByText(CLAIM.claim_text)).toBeInTheDocument();
    const badge = screen.getByRole("button", { name: /Source 1: Refrigerator & Freezer Storage Chart/ });
    expect(badge).toHaveTextContent("1");
    await userEvent.click(badge);
    expect(onCite).toHaveBeenCalledWith("a1", SOURCE.chunk_id);

    await userEvent.click(screen.getByRole("button", { name: "All 2 sources" }));
    expect(onCite).toHaveBeenLastCalledWith("a1", null);
  });

  it("marks the selected badge as pressed", () => {
    render(<AssistantBubble message={answered} selectedChunkId={SOURCE.chunk_id} onCite={() => {}} />);
    expect(screen.getByRole("button", { name: /Source 1/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("names the publishers the answer rests on", () => {
    render(<AssistantBubble message={answered} selectedChunkId={null} onCite={() => {}} />);
    expect(screen.getByText(/Answered from U\.S\. Food and Drug Administration/)).toBeInTheDocument();
  });

  it("copies the answer with its claims", () => {
    expect(answerAsText(answered)).toContain("The FDA says 1 to 2 days.");
    expect(answerAsText(answered)).toContain(`- ${CLAIM.claim_text} (${SOURCE.publisher}, 2023)`);
  });
});

describe("RefusalBanner", () => {
  const refusal = (status: "out_of_scope" | "not_in_corpus" | "error") =>
    message({ id: "r", role: "assistant", content: "I can't help with that.", status, refusal_reason: "Reason." });

  it("shows only the decline message for out_of_scope: no reason, no hint, no source details", () => {
    const decline =
      "I can't help with calorie targets, weight goals, or medical advice. " +
      "For personalised guidance, please consult a registered dietitian or your doctor.";
    render(<RefusalBanner message={message({ id: "d", role: "assistant", content: decline, status: "out_of_scope", refusal_reason: "Reason." })} />);
    const banner = screen.getByTestId("refusal-out_of_scope");
    expect(within(banner).getByText("Out of scope")).toBeInTheDocument();
    expect(banner).toHaveTextContent(decline);
    expect(banner).not.toHaveTextContent("Reason.");
    expect(banner).not.toHaveTextContent(/fits you/);
    expect(banner).not.toHaveTextContent(CORPUS_DOCUMENTS[0]);
    expect(banner.querySelectorAll("p")).toHaveLength(1);
  });

  it("shows only the message for not_in_corpus: no title, tag, reason, hint or document list", () => {
    const content =
      "I don’t have enough information to answer that reliably. " +
      "Please consult a qualified nutritionist or healthcare professional for personalized advice.";
    render(<RefusalBanner message={message({ id: "n", role: "assistant", content, status: "not_in_corpus", refusal_reason: "Reason." })} />);
    const banner = screen.getByTestId("refusal-not_in_corpus");
    expect(banner).toHaveTextContent(content);
    expect(banner.textContent).toBe(content);
    expect(banner.querySelectorAll("p")).toHaveLength(1);
    expect(banner.querySelector("details")).toBeNull();
    expect(CORPUS_DOCUMENTS).toHaveLength(8);
  });

  it("treats a verification failure as its own kind, never as an answer", () => {
    render(<RefusalBanner message={refusal("error")} />);
    expect(screen.getByTestId("refusal-error")).toHaveTextContent("I couldn’t give a verified answer");
  });
});

describe("SourcesPanel", () => {
  it("shows publisher, year, section, document, the excerpt and a link for each retrieved passage", () => {
    render(<SourcesPanel message={answered} chunkId={null} onClose={() => {}} />);
    const cards = screen.getAllByTestId("source-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText(SOURCE.publisher)).toBeInTheDocument();
    expect(within(cards[0]).getByText("2023")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Fresh Poultry")).toBeInTheDocument();
    expect(within(cards[0]).getByText(SOURCE.document_name)).toBeInTheDocument();
    expect(within(cards[0]).getByText(SOURCE.text)).toBeInTheDocument();
    expect(within(cards[0]).getByRole("link", { name: /View source/ })).toHaveAttribute("href", SOURCE.url);
    expect(within(cards[0]).getByRole("link", { name: /View source/ })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("marks cited passages and highlights the selected one", () => {
    render(<SourcesPanel message={answered} chunkId={SOURCE.chunk_id} onClose={() => {}} />);
    const [first, second] = screen.getAllByTestId("source-card");
    expect(first).toHaveClass("selected");
    expect(within(first).getByText("Cited")).toBeInTheDocument();
    expect(second).not.toHaveClass("selected");
    expect(within(second).queryByText("Cited")).not.toBeInTheDocument();
  });

  it("explains itself when there is nothing to show", () => {
    render(<SourcesPanel message={null} chunkId={null} onClose={() => {}} />);
    expect(screen.queryByTestId("source-card")).not.toBeInTheDocument();
    expect(screen.getByText(/appear here/)).toBeInTheDocument();
  });
});

describe("MessageInputBar", () => {
  const setup = (props: Partial<React.ComponentProps<typeof MessageInputBar>> = {}) => {
    const onSend = vi.fn();
    const onChange = vi.fn();
    render(
      <MessageInputBar value="hello" disabled={false} loading={false} focusKey="k" onChange={onChange} onSend={onSend} {...props} />,
    );
    return { onSend, onChange };
  };

  it("sends on Enter, adds a line on Shift+Enter", async () => {
    const { onSend } = setup();
    await userEvent.type(screen.getByLabelText("Your question"), "{Shift>}{Enter}{/Shift}");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Your question"), "{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("is blocked while loading, but typing stays possible", async () => {
    const { onSend, onChange } = setup({ loading: true });
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your question"), "x{Enter}");
    expect(onChange).toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send blank input", async () => {
    const { onSend } = setup({ value: "   " });
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your question"), "{Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("limits the question to 1000 characters and counts down near the limit", () => {
    setup({ value: "x".repeat(900) });
    expect(screen.getByLabelText("Your question")).toHaveAttribute("maxlength", "1000");
    expect(screen.getByText("100 characters left")).toBeInTheDocument();
  });
});
