import type { UiMessage } from "@/hooks/useChats";
import { AlertIcon, HeartIcon, InfoIcon } from "./icons";

/** The documents the assistant answers from, named when a question is not covered by them. */
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
  not_in_corpus: {
    title: "Not covered by my sources",
    tag: "Not in sources",
    Icon: InfoIcon,
    hint: "I looked in these documents and found nothing that answers it:",
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
  const { title, tag, Icon, hint } = COPY[kind];
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
          <p>{message.content}</p>
          {reason && <p className="muted">{reason}</p>}
          {hint && <p className="muted">{hint}</p>}
          {kind === "not_in_corpus" && (
            <ul className="corpus-list">
              {CORPUS_DOCUMENTS.map((doc) => (
                <li key={doc}>{doc}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
