import type { UiMessage } from "@/hooks/useChats";
import { AlertIcon, HeartIcon } from "./icons";

/** The documents the assistant answers from. */
export const CORPUS_DOCUMENTS = [
  "WHO — Healthy Diet Fact Sheet (2020)",
  "USDA / HHS — Dietary Guidelines for Americans, 2020-2025",
  "FDA — Refrigerator & Freezer Storage Chart (2023)",
  "UK Food Standards Agency — The Eatwell Guide (2018)",
  "EFSA — Summary of Dietary Reference Values (2017)",
  "ICMR / NIN — Dietary Guidelines for Indians (2011)",
  "Fit for Films — Calories and Macronutrients of Common Foods (2018)",
  "Global Wellness Institute — Nutrition for Healthspan (2023)",
];

const COPY = {
  out_of_scope: {
    title: "Health Boundary Notice",
    tag: "Out of scope",
    Icon: HeartIcon,
    hint: null, // the decline message already names who to ask; nothing more is shown with it
  },
  error: {
    title: "I couldn’t give a verified answer",
    tag: "Not verified",
    Icon: AlertIcon,
    hint: "Nothing unverified is shown. Rephrasing the question or trying again may help.",
  },
} as const;

interface Props {
  message: UiMessage;
}

/** A refusal or verification failure, each visually distinct from an answer and from one another. */
export default function RefusalBanner({ message }: Props) {
  const kind = message.status === "out_of_scope" || message.status === "not_in_corpus" ? message.status : "error";
  // When the documents don't contain the answer, only the fixed message is shown: no banner chrome, no document
  // list, no explanation.
  if (kind === "not_in_corpus") {
    return (
      <div className="row">
        <div className="banner not_in_corpus" data-testid="refusal-not_in_corpus" role="note">
          <div className="banner-body">
            <p>{message.content}</p>
          </div>
        </div>
      </div>
    );
  }

  const { title, tag, Icon, hint } = COPY[kind];
  const text = message.content;
  // A decline shows only its message: the internal reason is not repeated underneath it.
  const reason =
    kind !== "out_of_scope" && message.refusal_reason && message.refusal_reason !== message.content
      ? message.refusal_reason
      : null;

  return (
    <div className="row">
      <div className={`banner ${kind}`} data-testid={`refusal-${kind}`} role="note">
        <span className="banner-icon">
          <Icon />
        </span>
        <div className="banner-body">
          <div className="banner-head">
            <strong>{title}</strong>
            <span className="banner-tag">{tag}</span>
          </div>
          <p>{text}</p>
          {reason && <p className="muted">{reason}</p>}
          {hint && <p className="muted">{hint}</p>}
        </div>
      </div>
    </div>
  );
}
