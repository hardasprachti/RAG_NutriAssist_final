"use client";

import type { FollowUps as FollowUpsData } from "@/lib/suggestions";

interface Props extends FollowUpsData {
  onPick: (question: string) => void;
}

/** Clickable next questions under the latest reply; picking one sends it as if it had been typed. */
export default function FollowUps({ heading, questions, onPick }: Props) {
  return (
    <div className="follow-ups" role="group" aria-label={heading} data-testid="follow-ups">
      <p className="muted follow-ups-heading">{heading}</p>
      <div className="examples">
        {questions.map((question) => (
          <button key={question} type="button" onClick={() => onPick(question)}>
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
