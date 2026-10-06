"""Smoke-test the ingested corpus: does a question retrieve a chunk from the document that answers it?

This checks that ingestion produced searchable chunks (right document, right kind of unit). It is not the
Phase 7 retrieval evaluation, which uses its own, larger question set.

    python -m ingestion.sanity_check [--qdrant-path ingestion/data/qdrant] [-k 5]
"""

import argparse
import sys
from typing import Optional

from ingestion.upload_to_vectorstore import make_store

# (question, document_name that should appear in the top k, text that should appear in a retrieved chunk)
CASES = [
    ("How long can raw chicken be kept in the refrigerator?", "Refrigerator & Freezer Storage Chart", "Chicken or turkey, whole"),
    ("How long can hard-boiled eggs be stored?", "Refrigerator & Freezer Storage Chart", "Hard cooked"),
    ("What is the recommended limit on free sugars as a share of energy?", "Healthy Diet Fact Sheet", "free sugars"),
    ("How much total fat should adults eat as a share of energy?", "Healthy Diet Fact Sheet", "30%"),
    ("What are the Population Reference Intakes for calcium?", "Summary of Dietary Reference Values", "Calcium"),
    ("What is the average requirement for energy at physical activity level 1.6?", "Summary of Dietary Reference Values", "PAL=1.6"),
    ("What are the five food groups in the Eatwell Guide?", "The Eatwell Guide", "Eatwell"),
    ("How much salt should adults have each day?", "The Eatwell Guide", "6g"),
    ("What does the Indian dietary guideline say about edible oils and ghee?", "Dietary Guidelines for Indians", "oil"),
    ("What are the Dietary Guidelines for Americans' recommendations on added sugars?", "Dietary Guidelines for Americans, 2020-2025", "added sugars"),
]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qdrant-path", help="embedded local Qdrant (default: QDRANT_URL)")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args(argv)

    from integrations.embedder import get_embedder

    embedder = get_embedder()
    store = make_store(args.qdrant_path)
    print(f"{store.count()} chunks in '{store.collection_name}'\n")
    failures = 0
    for question, document, snippet in CASES:
        hits = store.search(embedder.embed_query(question), k=args.k)
        in_doc = [h for h in hits if h.chunk.document_name == document]
        ok = bool(in_doc) and any(snippet.lower() in h.chunk.text.lower() for h in in_doc)
        failures += not ok
        top = hits[0]
        print(f"{'PASS' if ok else 'FAIL'}  {question}")
        print(f"      top: {top.chunk.document_name} | {top.chunk.section[:60]} | score {top.score:.3f}")
        if not ok:
            print(f"      expected {document!r} containing {snippet!r}; got {[h.chunk.document_name for h in hits]}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} questions retrieved the expected document and passage in the top {args.k}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
