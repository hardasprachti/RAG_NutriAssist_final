import type { UiMessage } from "@/hooks/useChats";

/**
 * Follow-up questions offered under the latest reply. They are fixed lists, not model output: every one is a
 * question the assistant is known to answer from its sources (taken from the project's retrieval and benchmark
 * question sets), so a suggestion is never a dead end and costs no extra model call.
 */
export const FOLLOW_UPS_BY_DOCUMENT: Record<string, readonly string[]> = {
  "Healthy Diet Fact Sheet": [
    "What are the four principles that make up a healthy diet?",
    "What share of daily calories should come from total fat for adults, according to WHO?",
    "Which kinds of food does most of our salt come from?",
  ],
  "Dietary Guidelines for Americans, 2020-2025": [
    "What are the recommended limits for added sugars, saturated fat and sodium in the US dietary guidelines?",
    "What four steps does the US guidance give for keeping food safe at home?",
    "Can babies have honey?",
  ],
  "Refrigerator & Freezer Storage Chart": [
    "How long does bacon last in the fridge and in the freezer?",
    "How long are hard-boiled eggs good for in the fridge?",
    "How long can I keep raw shrimp in the freezer?",
  ],
  "The Eatwell Guide": [
    "How many portions of fish should be eaten each week, and how many of those should be oily?",
    "What is the recommended limit on free sugars for children aged 7 to 10 years?",
    "How much fruit juice or smoothie can count towards daily fluid intake in the UK guidance?",
  ],
  "Summary of Dietary Reference Values": [
    "What is the adequate intake of dietary fibre for adults in the EU dietary reference values?",
    "What are the EFSA reference intakes for calcium for adults?",
    "How much iron do women need in Europe?",
  ],
  "Dietary Guidelines for Indians": [
    "What do the Indian guidelines recommend about the use of ghee, butter and cooking oil?",
    "How does boiling affect vitamins according to the Indian guidelines on cooking?",
    "How long should drinking water of doubtful safety be boiled to purify it?",
  ],
  "Calories and Macronutrients of Common Foods": [
    "How much protein is in 100g of cod?",
    "How many calories are in an average portion of avocado?",
    "Which meat has the highest protein per average portion?",
  ],
  "Nutrition for Healthspan": [
    "What does Nutrition for Healthspan say about fasting?",
    "Why is mindful eating recommended?",
    "What does Nutrition for Healthspan say about understanding food labels?",
  ],
};

/** One question from each source in turn, so a "try something else" list shows the range of what is covered. */
export const GENERAL_QUESTIONS: readonly string[] = [
  "How long can cooked chicken be stored in a refrigerator?",
  "What is the maximum daily salt intake for adults recommended by WHO?",
  "How much protein is in 100g of cod?",
  "What does Nutrition for Healthspan say about fasting?",
  "How many portions of fruit and vegetables does the Eatwell Guide recommend each day?",
  "What is the adequate intake of dietary fibre for adults according to EFSA?",
  "What do the Dietary Guidelines for Americans say about the limit on added sugars?",
  "Why do the Indian dietary guidelines advise against using excess water when cooking rice?",
];

/** Offered after a decline: general questions about published guidance, none of them personal advice. */
export const AFTER_DECLINE_QUESTIONS: readonly string[] = [
  "What are the four principles that make up a healthy diet?",
  "How many portions of fruit and vegetables does the Eatwell Guide recommend each day?",
  "What is the maximum daily salt intake for adults recommended by WHO?",
  "What do the Dietary Guidelines for Americans say about the limit on added sugars?",
];

export interface FollowUps {
  heading: string;
  questions: string[];
}

const normalise = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/** Takes the first `limit` candidates that were not already asked in this chat, without repeats. */
function pick(candidates: readonly string[], asked: Set<string>, limit: number, chosen: string[] = []): string[] {
  for (const question of candidates) {
    if (chosen.length >= limit) break;
    const key = normalise(question);
    if (asked.has(key) || chosen.some((c) => normalise(c) === key)) continue;
    chosen.push(question);
  }
  return chosen;
}

/** Suggestions for the reply `message`, skipping anything the user has already asked. Empty for an error. */
export function followUpsFor(message: UiMessage, askedQuestions: readonly string[], limit = 3): FollowUps | null {
  const asked = new Set(askedQuestions.map(normalise));

  if (message.status === "answered") {
    // Related questions from the documents the answer cited (one from each in turn), then a mixed list.
    const cited = [...new Set(message.claims.map((c) => c.source.document_name))];
    const pools = cited.map((name) => FOLLOW_UPS_BY_DOCUMENT[name] ?? []);
    const interleaved = Array.from({ length: Math.max(0, ...pools.map((p) => p.length)) }, (_, i) =>
      pools.map((p) => p[i]).filter((q): q is string => Boolean(q)),
    ).flat();
    const questions = pick(GENERAL_QUESTIONS, asked, limit, pick(interleaved, asked, limit));
    return questions.length ? { heading: "Related questions", questions } : null;
  }
  if (message.status === "out_of_scope") {
    const questions = pick(GENERAL_QUESTIONS, asked, limit, pick(AFTER_DECLINE_QUESTIONS, asked, limit));
    return questions.length ? { heading: "General questions I can answer", questions } : null;
  }
  if (message.status === "not_in_corpus") {
    const questions = pick(GENERAL_QUESTIONS, asked, limit);
    return questions.length ? { heading: "Try one of these instead", questions } : null;
  }
  return null;
}
