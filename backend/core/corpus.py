"""The six-document corpus as the backend knows it: the citation whitelist and document detection.

``ingestion/document_registry.json`` is the source of truth for what gets indexed; this module mirrors the
fields the query side needs (name, publisher, year, citation URL) and ``tests/test_corpus.py`` fails if the
two drift apart. Keeping a copy here means the deployed backend does not depend on the ingestion package.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CorpusDocument:
    id: str
    document_name: str
    publisher: str
    year: int
    url: str
    # Names a user might use for this document. Matched case-sensitively unless the pattern carries (?i):
    # "WHO" the organisation must not match "who" the pronoun.
    alias_patterns: tuple[str, ...]
    # Explicit organisation / title mentions only (no weak cues like "UK" or "European"): used to catch a claim
    # that names a different document than the one it cites.
    name_patterns: tuple[str, ...]
    # Words that only name the document ("the guide", "fact sheet"): dropped from the search text, with the
    # aliases, when the question is routed to this document.
    routing_extras: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.document_name} ({self.publisher}, {self.year})"

    def mentioned_in(self, text: str) -> bool:
        return any(re.search(p, text) for p in self.alias_patterns)

    def named_in(self, text: str) -> bool:
        return any(re.search(p, text) for p in self.name_patterns)


CORPUS: tuple[CorpusDocument, ...] = (
    CorpusDocument(
        "who_healthy_diet", "Healthy Diet Fact Sheet", "World Health Organization (WHO)", 2020,
        "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
        alias_patterns=(r"\bWHO\b", r"(?i)\bworld health organi[sz]ation\b"),
        name_patterns=(r"\bWHO\b", r"(?i)\bworld health organi[sz]ation\b"),
        routing_extras=(r"(?i)\bfact ?sheet\b",),
    ),
    CorpusDocument(
        "usda_dietary_guidelines", "Dietary Guidelines for Americans, 2020-2025",
        "U.S. Department of Agriculture (USDA) / HHS", 2020,
        "https://www.dietaryguidelines.gov/sites/default/files/2020-12/Dietary_Guidelines_for_Americans_2020-2025.pdf",
        alias_patterns=(
            r"\bUSDA\b", r"\bHHS\b", r"\bDGAs?\b", r"(?i)\bdietary guidelines for americans\b",
            r"(?i)\bu\.?s\.? department of agriculture\b", r"(?i)\bamerican (?:dietary )?guidelines\b",
        ),
        name_patterns=(r"\bUSDA\b", r"(?i)\bdietary guidelines for americans\b", r"(?i)\bdepartment of agriculture\b"),
    ),
    CorpusDocument(
        "fda_storage_chart", "Refrigerator & Freezer Storage Chart", "U.S. Food and Drug Administration (FDA)", 2023,
        "https://www.fda.gov/media/74435/download",
        alias_patterns=(r"\bFDA\b", r"(?i)\bfood and drug administration\b", r"(?i)\bstorage chart\b"),
        name_patterns=(r"\bFDA\b", r"(?i)\bfood and drug administration\b"),
        routing_extras=(r"(?i)\bchart\b",),
    ),
    CorpusDocument(
        "uk_eatwell_guide", "The Eatwell Guide", "UK Food Standards Agency", 2018,
        "https://assets.publishing.service.gov.uk/media/69b3e02e9d8b52961a62b3bb/eatwell-guide-master-digital_Final.pdf",
        alias_patterns=(
            r"(?i)\beatwell\b", r"\bFSA\b", r"(?i)\bfood standards agency\b", r"\bUK\b", r"(?i)\bbritish\b",
            r"(?i)\bunited kingdom\b",
        ),
        name_patterns=(r"(?i)\beatwell\b", r"(?i)\bfood standards agency\b", r"\bFSA\b"),
        routing_extras=(r"(?i)\bguide\b",),
    ),
    CorpusDocument(
        "efsa_drv_summary", "Summary of Dietary Reference Values", "European Food Safety Authority (EFSA)", 2017,
        "https://www.efsa.europa.eu/sites/default/files/assets/DRV_Summary_tables_jan_17.pdf",
        alias_patterns=(
            r"\bEFSA\b", r"(?i)\beuropean food safety\b", r"\bEU\b", r"(?i)\beuropean\b", r"\bDRVs?\b",
            r"(?i)\bdietary reference values?\b",
        ),
        name_patterns=(r"\bEFSA\b", r"(?i)\beuropean food safety\b"),
    ),
    CorpusDocument(
        "icmr_nin_guidelines", "Dietary Guidelines for Indians",
        "Indian Council of Medical Research (ICMR) / National Institute of Nutrition (NIN)", 2011,
        "https://www.nin.res.in/downloads/DietaryGuidelinesforNINwebsite.pdf",
        alias_patterns=(
            r"\bICMR\b", r"\bNIN\b", r"(?i)\bnational institute of nutrition\b", r"(?i)\bindian council of medical\b",
            r"(?i)\bindians?\b", r"(?i)\bindia\b",
        ),
        name_patterns=(
            r"\bICMR\b", r"\bNIN\b", r"(?i)\bnational institute of nutrition\b", r"(?i)\bindian council of medical\b",
        ),
    ),
)

URL_WHITELIST: frozenset[str] = frozenset(d.url for d in CORPUS)
BY_NAME: dict[str, CorpusDocument] = {d.document_name: d for d in CORPUS}
BY_URL: dict[str, CorpusDocument] = {d.url: d for d in CORPUS}

# A question that asks to set sources side by side needs evidence from several documents, not the
# five nearest chunks (which one document can crowd out).
_COMPARISON = re.compile(
    r"(?i)\b(?:compare[sd]?|comparison|differ(?:s|ent|ence|ences|ently)?|versus|vs\.?|both|each of|"
    r"agree|disagree|conflict(?:s|ing)?|contradict\w*|across|"
    r"all (?:the )?(?:documents|guidelines|sources|guidance)|other (?:guidelines|sources|documents)|"
    r"(?:various|several|multiple) (?:guidelines|sources|documents|countries))\b"
)


def detect_documents(text: str) -> list[CorpusDocument]:
    """Documents the text names ("according to WHO", "the FDA chart"), in corpus order."""
    return [d for d in CORPUS if d.mentioned_in(text)]


_FILLER = re.compile(r"(?i)" + chr(92) + "b(?:according to|as per|between|in their)" + chr(92) + "b")
# Words that carry no topic; a question reduced to these has nothing left to search with.
_QUESTION_WORDS = frozenset(
    "what does how the are say says tell about and for can you your their they them with from this that "
    "which who why when where is was were has have had any some more most much many also than then".split()
)


def routing_free(text: str, docs: list[CorpusDocument]) -> str:
    """The question without the words that only route it ("according to WHO", "the Eatwell Guide", "differ").

    When a question is already scoped to documents by a filter, those words add nothing to the search but pull
    the embedding toward each document's boilerplate: "How do WHO and the Eatwell Guide differ on salt?"
    retrieves WHO's "resolutions" chunks, while "advice on salt" retrieves the salt sections. Falls back to the
    original text if no topic word is left to search with.
    """
    cleaned = text
    for d in docs:
        for pattern in (*d.alias_patterns, *d.routing_extras):
            cleaned = re.sub(pattern, " ", cleaned)
    cleaned = _FILLER.sub(" ", _COMPARISON.sub(" ", cleaned))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?.,;:") 
    topic_words = [w for w in re.findall(r"[a-z]{3,}", cleaned.lower()) if w not in _QUESTION_WORDS]
    return f"{cleaned}?" if topic_words else text


def wants_comparison(text: str) -> bool:
    return bool(_COMPARISON.search(text))


def find_document(name: Optional[str]) -> Optional[CorpusDocument]:
    return BY_NAME.get(name or "")
