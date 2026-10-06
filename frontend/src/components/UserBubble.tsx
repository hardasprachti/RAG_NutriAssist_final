"use client";

import type { UiMessage } from "@/hooks/useChats";
import { clockTime } from "@/lib/format";

interface Props {
  message: UiMessage;
  onRetry: (messageId: string) => void;
}

export default function UserBubble({ message, onRetry }: Props) {
  const failed = message.local === "failed";
  return (
    <div className="row user">
      <div className="bubble user-bubble">
        <p>{message.content}</p>
        <div className="bubble-meta">{clockTime(message.created_at)}</div>
      </div>
      {failed && (
        <p className="send-error" role="alert">
          {message.error}{" "}
          <button type="button" className="link" onClick={() => onRetry(message.id)}>
            Try again
          </button>
        </p>
      )}
    </div>
  );
}
