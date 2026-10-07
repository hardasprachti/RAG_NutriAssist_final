import { describe, expect, it } from "vitest";
import { CORPUS_DOCUMENTS } from "@/components/RefusalBanner";
import { CLAIM, message } from "@/test-utils";
import {
  AFTER_DECLINE_QUESTIONS,
  FOLLOW_UPS_BY_DOCUMENT,
  GENERAL_QUESTIONS,
  followUpsFor,
} from "./suggestions";

const reply = (status: "answered" | "out_of_scope" | "not_in_corpus" | "error", claims = status === "answered" ? [CLAIM] : []) =>
  message({ id: "a", role: "assistant", content: "x", status, claims });

const FDA = "Refrigerator & Freezer Storage Chart";

describe("suggestion lists", () => {
  it("cover every document in the corpus, and name only documents that exist", () => {
    const keys = Object.keys(FOLLOW_UPS_BY_DOCUMENT);
    expect(keys).toHaveLength(CORPUS_DOCUMENTS.length);
    for (const key of keys) expect(CORPUS_DOCUMENTS.some((label) => label.includes(key))).toBe(true);
    for (const pool of Object.values(FOLLOW_UPS_BY_DOCUMENT)) expect(pool.length).toBeGreaterThanOrEqual(3);
  });

  it("are questions with no duplicates", () => {
    const all = [...Object.values(FOLLOW_UPS_BY_DOCUMENT).flat(), ...GENERAL_QUESTIONS, ...AFTER_DECLINE_QUESTIONS];
    for (const q of all) expect(q.endsWith("?")).toBe(true);
    expect(new Set(Object.values(FOLLOW_UPS_BY_DOCUMENT).flat()).size).toBe(
      Object.values(FOLLOW_UPS_BY_DOCUMENT).flat().length,
    );
  });

  it("offer nothing personal after a decline", () => {
    for (const q of AFTER_DECLINE_QUESTIONS) expect(q).not.toMatch(/\b(I|my|me|myself|should I)\b/);
  });
});

describe("followUpsFor", () => {
  it("offers related questions from the document an answer cited", () => {
    const result = followUpsFor(reply("answered"), ["How long can chicken stay?"]);
    expect(result?.heading).toBe("Related questions");
    expect(result?.questions).toHaveLength(3);
    expect(result?.questions.slice(0, 3)).toEqual([...FOLLOW_UPS_BY_DOCUMENT[FDA]]);
  });

  it("starts with one question from each document when an answer cites several", () => {
    const eatwell = { ...CLAIM, source: { ...CLAIM.source, document_name: "The Eatwell Guide" } };
    const result = followUpsFor(reply("answered", [CLAIM, eatwell]), []);
    expect(result?.questions[0]).toBe(FOLLOW_UPS_BY_DOCUMENT[FDA][0]);
    expect(result?.questions[1]).toBe(FOLLOW_UPS_BY_DOCUMENT["The Eatwell Guide"][0]);
  });

  it("skips questions already asked, ignoring case and punctuation", () => {
    const asked = [FOLLOW_UPS_BY_DOCUMENT[FDA][0].toUpperCase().replace("?", ""), FOLLOW_UPS_BY_DOCUMENT[FDA][1]];
    const result = followUpsFor(reply("answered"), asked);
    expect(result?.questions).toHaveLength(3);
    expect(result?.questions).not.toContain(FOLLOW_UPS_BY_DOCUMENT[FDA][0]);
    expect(result?.questions).not.toContain(FOLLOW_UPS_BY_DOCUMENT[FDA][1]);
    expect(result?.questions[0]).toBe(FOLLOW_UPS_BY_DOCUMENT[FDA][2]);
  });

  it("falls back to a mixed list for a document it has no pool for, and never repeats a question", () => {
    const unknown = { ...CLAIM, source: { ...CLAIM.source, document_name: "Some Other Document" } };
    const result = followUpsFor(reply("answered", [unknown]), []);
    expect(result?.questions).toEqual(GENERAL_QUESTIONS.slice(0, 3));
    expect(new Set(result?.questions).size).toBe(3);
  });

  it("offers general, non-personal questions after a decline", () => {
    const result = followUpsFor(reply("out_of_scope"), []);
    expect(result?.heading).toBe("General questions I can answer");
    expect(result?.questions).toEqual(AFTER_DECLINE_QUESTIONS.slice(0, 3));
  });

  it("offers a spread of topics when a question is not covered", () => {
    const result = followUpsFor(reply("not_in_corpus"), []);
    expect(result?.heading).toBe("Try one of these instead");
    expect(result?.questions).toEqual(GENERAL_QUESTIONS.slice(0, 3));
  });

  it("offers nothing after an error, and nothing when everything has been asked", () => {
    expect(followUpsFor(reply("error"), [])).toBeNull();
    expect(followUpsFor(reply("not_in_corpus"), [...GENERAL_QUESTIONS])).toBeNull();
  });
});
