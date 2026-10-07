"use client";

import { Fragment, useEffect, useRef } from "react";
import type { Chat } from "@/hooks/useChats";
import { dayLabel, sameDay, useNow } from "@/lib/format";
import { followUpsFor } from "@/lib/suggestions";
import AssistantBubble from "./AssistantBubble";
import FollowUps from "./FollowUps";
import { BowlIcon } from "./icons";
import RefusalBanner from "./RefusalBanner";
import UserBubble from "./UserBubble";

interface Props {
  chat: Chat;
  onRetry: (messageId: string) => void;
  onCite: (messageId: string, chunkId: string | null) => void;
  /** Sends a question, e.g. a suggested follow-up the user picked. */
  onSend: (question: string) => void;
  children?: React.ReactNode;
}

/** The scrolling conversation. A polite live region, so a screen reader announces each new answer. */
export default function MessageList({ chat, onRetry, onCite, onSend, children }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const now = useNow();

  // Suggestions sit under the latest reply only, and only once nothing is pending.
  const latest = chat.messages[chat.messages.length - 1];
  const followUps =
    latest && latest.role === "assistant" && !chat.pending
      ? followUpsFor(latest, chat.messages.filter((m) => m.role === "user").map((m) => m.content))
      : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [chat.key, chat.messages.length, chat.pending, chat.loaded]);

  return (
    <div className="messages" role="log" aria-live="polite" aria-label="Conversation">
      {children}
      {chat.messages.map((message, i) => {
        const previous = chat.messages[i - 1];
        const selectedChunk =
          chat.selection && chat.selection.messageId === message.id ? chat.selection.chunkId : null;
        return (
          <Fragment key={message.id}>
            {(!previous || !sameDay(previous.created_at, message.created_at)) && (
              <div className="day-divider">
                <span>{dayLabel(message.created_at, now)}</span>
              </div>
            )}
            {message.role === "user" ? (
              <UserBubble message={message} onRetry={onRetry} />
            ) : message.status === "answered" ? (
              <AssistantBubble message={message} selectedChunkId={selectedChunk} onCite={onCite} />
            ) : (
              <RefusalBanner message={message} />
            )}
          </Fragment>
        );
      })}
      {followUps && <FollowUps {...followUps} onPick={onSend} />}
      {chat.pending && (
        <div className="row assistant-row">
          <span className="avatar" aria-hidden="true">
            <BowlIcon />
          </span>
          <div className="bubble assistant-bubble thinking" role="status">
            <span className="spinner" aria-hidden="true" /> Checking the sources…
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
