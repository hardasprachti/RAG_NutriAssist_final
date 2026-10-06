"""Run the whole ingestion: download -> extract -> chunk -> QA -> embed -> upsert -> report.

    python -m ingestion.run_ingestion                    # everything, into the configured Qdrant + DB
    python -m ingestion.run_ingestion --dry-run          # no embedding/upload; chunks + report only
    python -m ingestion.run_ingestion --only who_healthy_diet fda_storage_chart
    python -m ingestion.run_ingestion --qdrant-path ingestion/data/qdrant --no-db   # fully local

Run from the repository root. Safe to re-run: chunk ids are deterministic and a document's old
points are replaced, so the vector store never holds duplicates or stale chunks.
"""

import argparse
import collections
import dataclasses
import json
import logging
import os
import sys
from datetime import date
from typing import Optional

from ingestion import report
from ingestion.chunker import RawChunk, build_chunks, embed_input, to_records
from ingestion.common import CHUNKS_DIR, MANIFEST_PATH, REPORT_PATH, Document, load_manifest, load_registry
from ingestion.download_documents import download
from ingestion.embedder import embed_chunks, over_limit
from ingestion.extract_text import extract
from ingestion import qa
from ingestion.upload_to_vectorstore import make_store, register_document, upload_document

logger = logging.getLogger("ingestion")


def _write_chunks(doc: Document, chunks: list[RawChunk], records, tokens: list[int]) -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    with doc.chunks_path.open("w", encoding="utf-8") as fh:
        for chunk, record, n in zip(chunks, records, tokens):
            row = dataclasses.asdict(record) | {"page": chunk.page, "kind": chunk.kind, "tokens": n}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def process(doc: Document, embedder, manifest: dict, refresh: bool, embed_section: bool = True) -> dict:
    extraction = extract(doc, use_cache=not refresh)
    chunks = build_chunks(extraction, embedder.count_tokens, embed_section=embed_section)
    retrieval_date = date.fromisoformat(manifest[doc.id]["retrieval_date"])
    records = to_records(doc, chunks, retrieval_date)
    tokens = [embedder.count_tokens(embed_input(c, embed_section)) + 2 for c in chunks]
    _write_chunks(doc, chunks, records, tokens)

    section_counts = collections.Counter(c.section for c in chunks)
    evidence = qa.year_evidence(doc)
    warnings = list(extraction.warnings)
    fused = qa.suspicious_table_cells(extraction) if doc.extractor == "docling" else []  # the failure mode seen there
    warnings += [f"{doc.id}: possible fused table cell: {f}" for f in fused]
    long = over_limit(chunks, embedder, embed_section)
    if long:
        warnings.append(f"{doc.id}: {len(long)} chunk(s) exceed the embedding model's input limit: {long[:5]}")
    if not chunks:
        warnings.append(f"{doc.id}: produced no chunks")
    return {
        "doc": doc, "chunks": chunks, "records": records,
        "result": {
            "name": doc.document_name, "extractor": extraction.extractor, "pages": extraction.pages,
            "year": doc.year, "year_evidence": evidence, "year_ok": qa.year_matches(doc, evidence),
            "empty_pages": extraction.empty_pages, "garbled_pages": extraction.garbled_pages,
            "excluded_pages": extraction.excluded_pages,
            "stats": qa.chunk_stats(chunks, tokens), "over_limit": len(long),
            "fidelity": qa.numeric_fidelity(doc, extraction), "fused_cells": len(fused),
            "section_counts": section_counts.most_common(), "warnings": warnings,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="document ids (default: all six)")
    parser.add_argument("--skip-download", action="store_true", help="use files already in ingestion/data/raw")
    parser.add_argument("--refresh", action="store_true", help="re-run extraction instead of using the cache")
    parser.add_argument("--dry-run", action="store_true", help="chunk and report only; no embedding or upload")
    parser.add_argument("--no-db", action="store_true", help="do not write document_metadata rows")
    parser.add_argument("--qdrant-path", help="use an embedded local Qdrant at this path instead of QDRANT_URL")
    parser.add_argument("--database-url", help="override DATABASE_URL (e.g. sqlite:///ingestion/data/local.db)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url  # must precede the first get_settings()

    from config import get_settings
    from integrations.embedder import get_embedder

    docs = load_registry(args.only)
    if not args.skip_download:
        failures = download(docs)
        if failures:
            for doc_id, message in failures.items():
                logger.error("download failed for %s: %s", doc_id, message)
            return 1
    manifest = load_manifest()
    missing = [d.id for d in docs if d.id not in manifest or not d.raw_path.exists()]
    if missing:
        logger.error("not downloaded: %s (run without --skip-download)", missing)
        return 1

    embedder = get_embedder()
    embedder.load()
    processed = [process(d, embedder, manifest, args.refresh) for d in docs]
    results = [p["result"] for p in processed]

    vector_total: Optional[int] = None
    if not args.dry_run:
        store = make_store(args.qdrant_path)
        for p in processed:
            doc, chunks, records = p["doc"], p["chunks"], p["records"]
            if p["result"]["over_limit"]:
                logger.error("refusing to upload %s: chunks exceed the model's input limit", doc.id)
                return 1
            vectors = embed_chunks(chunks, embedder)
            upload_document(store, records, vectors)
            if not args.no_db:
                register_document(doc, len(records), date.fromisoformat(manifest[doc.id]["retrieval_date"]),
                                  get_settings().embedding_model)
        vector_total = store.count()
        logger.info("vector store now holds %d points", vector_total)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        report.render(results, get_settings().embedding_model, not args.dry_run, vector_total), encoding="utf-8"
    )
    logger.info("report written to %s", REPORT_PATH)
    for r in results:
        s = r["stats"]
        logger.info("%-52s %4d chunks (avg %5.1f tok, max %3d)", r["name"], s["chunks"], s["tokens_avg"], s["tokens_max"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
