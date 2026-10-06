"use client";

import { useState } from "react";
import type { UiMessage } from "@/hooks/useChats";
import CitationBadge from "./CitationBadge";
import { BowlIcon, CheckCircleIcon, CopyIcon } from "./icons";

interface Props {
  message: UiMessage;
  /** The chunk currently highlighted in the sources panel for this message, if any. */
  selectedChunkId: string | null;
  onCite: (messageId: string, chunkId: string | null) => void;
}

/** The text of an answer with its claims, each followed by a badge for the passage that supports it. */
export function answerAsText(message: UiMessage): string {
  const claims = message.claims.map((c) => `- ${c.claim_text} (${c.source.publisher}, ${c.source.year})`);
  return [message.content, ...claims].join("\n");
}

export default function AssistantBubble({ message, selectedChunkId, onCite }: Props) {
  const [copied, setCopied] = useState(false);

  // Badge numbers match the numbering in the sources panel.
  const ranks = new Map(message.retrieved_sources.map((s) => [s.chunk_id, s.rank + 1]));
  const publishers = [...new Set(message.claims.map((c) => c.source.publisher))];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answerAsText(message));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access can be denied (insecure context, permissions): nothing to recover.
    }
  };

  return (
    <div className="row assistant-row">
      <span className="avatar" aria-hidden="true">
        <BowlIcon />
      </span>
      <div className="bubble assistant-bubble">
        <div className="bubble-head">
          <span className="bubble-source">
            <CheckCircleIcon /> Answered from {publishers.join(" and ")}
          </span>
        </div>

        <p className="answer">{message.content}</p>

        <ul className="claims">
          {message.claims.map((claim, i) => {
            const { chunk_id: chunkId, document_name: doc, publisher, year } = claim.source;
            const number = ranks.get(chunkId) ?? "?";
            return (
              <li key={i}>
                <span>{claim.claim_text}</span>
                <CitationBadge
                  number={number}
                  label={`Source ${number}: ${doc}, ${publisher} ${year}`}
                  active={chunkId === selectedChunkId}
                  onClick={() => onCite(message.id, chunkId)}
                />
              </li>
            );
          })}
        </ul>

        <div className="bubble-foot">
          <span>Based only on the retrieved official documents · Not individual medical advice</span>
          <span className="bubble-actions">
            {message.retrieved_sources.length > 0 && (
              <button type="button" onClick={() => onCite(message.id, null)}>
                All {message.retrieved_sources.length} sources
              </button>
            )}
            <button type="button" onClick={copy} aria-label="Copy answer">
              <CopyIcon /> {copied ? "Copied" : "Copy"}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
