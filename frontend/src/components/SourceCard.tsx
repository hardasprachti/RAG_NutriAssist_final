"use client";

import { forwardRef } from "react";
import type { RetrievedSource } from "@/types/api";
import { ExternalIcon } from "./icons";

interface Props {
  source: RetrievedSource;
  /** The answer cites this passage (as opposed to it only having been retrieved). */
  cited: boolean;
  /** A citation badge pointed here. */
  selected: boolean;
}

/** One retrieved passage: where it comes from, the supporting excerpt, and a link to the document. */
const SourceCard = forwardRef<HTMLLIElement, Props>(function SourceCard({ source, cited, selected }, ref) {
  return (
    <li ref={ref} className={`source-card${selected ? " selected" : ""}`} data-testid="source-card">
      <div className="source-top">
        <span className="source-num">{source.rank + 1}</span>
        <span className="chip">{source.publisher}</span>
        <span className="chip chip-year">{source.year}</span>
        {cited && <span className="chip chip-cited">Cited</span>}
      </div>
      <h3>{source.document_name}</h3>
      {source.section && <p className="source-section">{source.section}</p>}
      <details open={selected}>
        <summary>Supporting excerpt</summary>
        <blockquote>{source.text}</blockquote>
      </details>
      <div className="source-foot">
        <a href={source.url} target="_blank" rel="noopener noreferrer">
          View source <ExternalIcon />
        </a>
      </div>
    </li>
  );
});

export default SourceCard;
