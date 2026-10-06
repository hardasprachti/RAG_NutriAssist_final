"use client";

import { useEffect, useRef } from "react";
import type { UiMessage } from "@/hooks/useChats";
import { BookIcon, CloseIcon } from "./icons";
import SourceCard from "./SourceCard";

interface Props {
  /** The assistant message whose evidence is shown, or null when nothing has been answered yet. */
  message: UiMessage | null;
  /** The chunk to highlight (the one a citation badge pointed at). */
  chunkId: string | null;
  onClose: () => void;
}

export default function SourcesPanel({ message, chunkId, onClose }: Props) {
  const highlighted = useRef<HTMLLIElement | null>(null);
  const sources = message?.retrieved_sources ?? [];
  const cited = new Set(message?.claims.map((c) => c.source.chunk_id));

  useEffect(() => {
    highlighted.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [chunkId, message?.id]);

  return (
    <div className="sources-inner">
      <div className="sources-head">
        <h2>
          <BookIcon /> Evidence Sources{sources.length > 0 ? ` (${sources.length})` : ""}
        </h2>
        <button type="button" className="icon-btn" aria-label="Close sources" onClick={onClose}>
          <CloseIcon />
        </button>
      </div>

      <div className="sources-body">
        {sources.length === 0 ? (
          <p className="muted">
            The passages an answer is based on appear here, so you can check each claim against the original document.
          </p>
        ) : (
          <>
            <p className="muted">Passages retrieved for the selected answer. The ones it cites are marked.</p>
            <ol className="source-list">
              {sources.map((source) => {
                const selected = source.chunk_id === chunkId;
                return (
                  <SourceCard
                    key={source.chunk_id}
                    ref={selected ? highlighted : undefined}
                    source={source}
                    cited={cited.has(source.chunk_id)}
                    selected={selected}
                  />
                );
              })}
            </ol>
          </>
        )}
      </div>
    </div>
  );
}
