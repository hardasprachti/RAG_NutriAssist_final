"use client";

import type { Chat } from "@/hooks/useChats";
import { BowlIcon } from "./icons";
import MessageInputBar from "./MessageInputBar";
import MessageList from "./MessageList";

export const EXAMPLE_QUESTIONS = [
  "How long can cooked chicken stay in the fridge?",
  "What does WHO recommend about free sugars?",
  "How much iron do adults need each day?",
  "What is the recommended limit for salt intake?",
];

interface Props {
  chat: Chat;
  onSend: (question?: string) => void;
  onInput: (value: string) => void;
  onRetry: (messageId: string) => void;
  onRetryHistory: () => void;
  onCite: (messageId: string, chunkId: string | null) => void;
}

export default function ChatWindow({ chat, onSend, onInput, onRetry, onRetryHistory, onCite }: Props) {
  const empty = chat.loaded && chat.messages.length === 0;

  return (
    <div className="chat-inner">
      <MessageList chat={chat} onRetry={onRetry} onCite={onCite} onSend={onSend}>
        {!chat.loaded && !chat.loadError && (
          <p className="muted center" role="status">
            Loading chat…
          </p>
        )}
        {chat.loadError && (
          <p className="send-error center" role="alert">
            {chat.loadError}{" "}
            <button type="button" className="link" onClick={onRetryHistory}>
              Try again
            </button>
          </p>
        )}
        {empty && (
          <div className="empty">
            <span className="empty-mark">
              <BowlIcon />
            </span>
            <h2>What would you like to know?</h2>
            <p className="muted">
              Ask about nutrition or food safety. Every answer comes only from the source documents, with
              citations you can check.
            </p>
            <div className="examples">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button key={q} type="button" disabled={chat.pending} onClick={() => onSend(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
      </MessageList>

      <MessageInputBar
        value={chat.input}
        disabled={!chat.loaded}
        loading={chat.pending}
        focusKey={chat.key}
        onChange={onInput}
        onSend={() => onSend()}
      />
    </div>
  );
}
