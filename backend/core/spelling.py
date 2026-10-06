"""Spelling repair for retrieval, using the corpus's own words.

A typo'd but legitimate question ("how lng can chiken stay in the frige") embeds far from its meaning: it scored
0.551 against the 0.58 gate, so the user would be told the sources do not cover it. When nothing clears the gate
the retriever asks this class for a corrected search text and tries once more.

The vocabulary is the words of the ingested chunks, so a "correction" is always a word the documents really use.
Only words that are *not* in the vocabulary are touched; each becomes the most frequent corpus word within one edit
(two for long words), and the retry only replaces the first search if it clears the gate. The corrected text is
used for searching only: the question the model sees, and the safety check, always use what the user typed.
"""

import re
from collections import Counter
from typing import Callable, Iterable, Optional

_WORD = re.compile(r"[a-z]{3,}")
_CANDIDATE = re.compile(r"\b[A-Za-z]{5,}\b")
MIN_LENGTH = 5  # shorter words are too ambiguous to repair ("lng" could be many things)


def edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insert, delete, substitute, or swap two neighbours."""
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            cost = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, rows[-2][j - 2] + 1)
            row.append(cost)
        rows.append(row)
    return rows[-1][-1]


class Speller:
    def __init__(self, texts: Iterable[str], is_known_word: Optional[Callable[[str], bool]] = None) -> None:
        """``is_known_word`` says whether a word is ordinary English that must never be "repaired" even though the
        corpus never uses it (the embedding model's own vocabulary: "capital" is known, "chiken" is not)."""
        self._is_known_word = is_known_word
        counts: Counter[str] = Counter()
        for text in texts:
            counts.update(_WORD.findall(text.lower()))
        self._counts = counts
        self._by_length: dict[int, list[str]] = {}
        for word in counts:
            if len(word) >= MIN_LENGTH - 1:
                self._by_length.setdefault(len(word), []).append(word)

    def __len__(self) -> int:
        return len(self._counts)

    def _fix(self, word: str) -> str:
        if word in self._counts:
            return word
        limit = 2 if len(word) >= 9 else 1
        best: Optional[str] = None
        for length in range(len(word) - limit, len(word) + limit + 1):
            for candidate in self._by_length.get(length, ()):
                if abs(len(candidate) - len(word)) <= limit and edit_distance(word, candidate) <= limit:
                    if best is None or self._counts[candidate] > self._counts[best]:
                        best = candidate
        return best or word

    def correct(self, text: str) -> Optional[str]:
        """``text`` with unknown words replaced by their nearest corpus word, or None when nothing changed.

        All-capitals words (WHO, EFSA, shouted text) and words with capitals inside are left alone."""
        def replace(match: re.Match[str]) -> str:
            word = match.group()
            if word.isupper() or (word != word.lower() and word[1:] != word[1:].lower()):
                return word
            if self._is_known_word and self._is_known_word(word):
                return word
            lower = word.lower()
            fixed = lower if lower in self._counts else self._fix(lower)
            return word if fixed == lower else fixed  # a word with no better spelling keeps the user's casing

        fixed = _CANDIDATE.sub(replace, text)
        return fixed if fixed != text else None
