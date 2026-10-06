"use client";

import { useEffect, useRef } from "react";
import { SendIcon } from "./icons";

export const MAX_QUESTION_LENGTH = 1000;

interface Props {
  value: string;
  /** Typing is blocked only while there is no history to continue (a chat still loading). */
  disabled: boolean;
  /** An answer is on its way: sending is blocked, typing is not. */
  loading: boolean;
  /** Changes when another chat is opened or an answer lands; the box takes focus again. */
  focusKey: string;
  onChange: (value: string) => void;
  onSend: () => void;
}

export default function MessageInputBar({ value, disabled, loading, focusKey, onChange, onSend }: Props) {
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const canSend = !disabled && !loading && value.trim().length > 0;

  useEffect(() => {
    if (!disabled && !loading) inputRef.current?.focus();
  }, [focusKey, disabled, loading]);

  const remaining = MAX_QUESTION_LENGTH - value.length;

  return (
    <form
      className="input-wrap"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <div className="input-bar">
        <textarea
          ref={inputRef}
          aria-label="Your question"
          placeholder="Ask about nutrition, food safety, vitamins, minerals, or dietary recommendations…"
          rows={1}
          maxLength={MAX_QUESTION_LENGTH}
          value={value}
          disabled={disabled}
          enterKeyHint="send"
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
        />
        <button type="submit" className="send" aria-label="Send question" title="Send question" disabled={!canSend}>
          <SendIcon />
        </button>
      </div>
      <div className="input-hint">
        <span>
          Press <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
        </span>
        {remaining <= 200 && <span aria-live="polite">{remaining} characters left</span>}
      </div>
    </form>
  );
}
