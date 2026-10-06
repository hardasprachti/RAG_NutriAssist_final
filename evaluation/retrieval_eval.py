"""Retrieval evaluation (Phase 7.1): does the right chunk come back? No LLM involved.

    backend\\.venv\\Scripts\\python -m evaluation.retrieval_eval [--split dev|heldout|all] [--k 5] [--no-db]

For each question in ``questions/retrieval_questions.json`` (expected document and section known in advance) the
production retriever runs exactly as in the chat pipeline: embedding, document routing, per-document search for
comparisons, the similarity gate. A question is a **hit** when every expected (document, section) pair is among
the chunks the model would be shown. Two diagnostics sit beside it:

* ``raw``: plain unfiltered top-k with no gate, to show what routing and the gate change;
* ``evidence``: the stricter check that the actual facts (figures, phrases) are in the retrieved text.

A miss is classified ``gate`` / ``wrong_document`` / ``wrong_section``, and results are reported apart from any
generation result: this script never calls the model, so a miss here is a retrieval failure by construction.
"""

import argparse
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation import scoring
from evaluation.common import (LOCAL_QDRANT, configure_console, load_questions, now_utc, prepare_database,
                               save_evaluation_rows, save_result)


def _hit_dict(rank: int, hit: Any) -> dict[str, Any]:
    c = hit.chunk
    return {"rank": rank, "chunk_id": c.chunk_id, "document_name": c.document_name, "section": c.section,
            "score": round(float(hit.score), 4), "text": c.text}


def evaluate(questions: list[dict[str, Any]], retriever: Any, embedder: Any, store: Any, k: int) -> list[dict[str, Any]]:
    min_score = retriever.config.min_score
    rows = []
    for q in questions:
        result = retriever.retrieve(q["question"])
        hits = [_hit_dict(i, h) for i, h in enumerate(result.hits, start=1)]
        raw = [_hit_dict(i, h) for i, h in enumerate(store.search(embedder.embed_query(q["question"]), k=k), start=1)]
        targets = scoring.targets_of(q)
        ranks = [scoring.first_rank(t, hits) for t in targets]
        hit = all(r is not None for r in ranks)
        matched = [hits[r - 1]["score"] for r in ranks if r]
        rows.append({
            "id": q["id"], "split": q["split"], "difficulty": q.get("difficulty", "standard"),
            "topic": q.get("topic", ""), "question": q["question"],
            "expected_document": q["expected_document"], "expected_section": q["expected_section"],
            "targets": [{"document": t.document, "section_match": list(t.section_match), "rank": r}
                        for t, r in zip(targets, ranks)],
            "hit": hit,
            "hit_at_1": all(r == 1 for r in ranks),
            "hit_at_3": all(r is not None and r <= 3 for r in ranks),
            "reciprocal_rank": (1.0 / max(r for r in ranks if r)) if hit else 0.0,
            "document_hit": all(any(h["document_name"] == t.document for h in hits) for t in targets),
            "evidence_hit": scoring.evidence_found(q, hits),
            "raw_hit": all(scoring.first_rank(t, raw) is not None for t in targets),
            "miss_reason": scoring.classify_retrieval_miss(q, hits, result.top_score, min_score),
            "strategy": result.strategy, "searched": [d.document_name for d in result.searched],
            "top_score": round(result.top_score, 4) if result.top_score is not None else None,
            "matched_score": round(min(matched), 4) if matched else None,
            "corrected_query": result.corrected_query,
            "retrieved": hits, "raw_retrieved": raw,
        })
    return rows


def _rate(rows: list[dict[str, Any]], key: str = "hit") -> dict[str, Any]:
    scored = [r for r in rows if r.get(key) is not None]
    n = len(scored)
    hits = sum(bool(r[key]) for r in scored)
    return {"n": n, "hits": hits, "rate": round(hits / n, 4) if n else None}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def grouped(field: str) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[r[field]].append(r)
        return {name: _rate(g) for name, g in sorted(groups.items())}

    return {
        "hit_rate": _rate(rows), "hit_at_1": _rate(rows, "hit_at_1"), "hit_at_3": _rate(rows, "hit_at_3"),
        "document_hit": _rate(rows, "document_hit"), "evidence_hit": _rate(rows, "evidence_hit"),
        "raw_unrouted_ungated_hit": _rate(rows, "raw_hit"),
        "mrr": round(sum(r["reciprocal_rank"] for r in rows) / len(rows), 4) if rows else None,
        "by_split": grouped("split"), "by_difficulty": grouped("difficulty"), "by_document": grouped("expected_document"), "by_topic": grouped("topic"),
        "by_strategy": grouped("strategy"),
        "misses": {reason: sum(1 for r in rows if r["miss_reason"] == reason)
                   for reason in sorted({r["miss_reason"] for r in rows if r["miss_reason"]})},
    }


def main(argv: Any = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=["all", "dev", "heldout"], default="all")
    parser.add_argument("--k", type=int, help="chunks retrieved (default: the value in the question file, 5)")
    parser.add_argument("--qdrant-path", default=str(LOCAL_QDRANT), help="embedded Qdrant directory (ignored with QDRANT_URL)")
    parser.add_argument("--database-url", help="where evaluation_results rows go (default: the local scratch SQLite)")
    parser.add_argument("--no-db", action="store_true", help="do not write evaluation_results rows")
    parser.add_argument("--label", default="", help="free-text label stored in the result file (e.g. 'baseline')")
    args = parser.parse_args(argv)

    prepare_database(args.database_url)
    from config import get_settings
    from core.rag_pipeline import PipelineConfig, Retriever
    from integrations.embedder import get_embedder

    spec = load_questions("retrieval_questions.json")
    k = args.k or spec.get("k", 5)
    questions = [q for q in spec["questions"] if args.split in ("all", q["split"])]
    settings = get_settings()

    scratch = None
    if settings.qdrant_url:
        from integrations.vector_store import VectorStore

        store = VectorStore.from_settings(settings)
        source = settings.qdrant_url
    else:
        from scripts.ask import _local_store

        if not Path(args.qdrant_path).exists():
            print(f"no local corpus at {args.qdrant_path}; run `python -m ingestion.run_ingestion` first", file=sys.stderr)
            return 2
        scratch = Path(tempfile.mkdtemp(prefix="nutrition_eval_qdrant_")) / "qdrant"
        shutil.copytree(args.qdrant_path, scratch)  # a copy: the embedded store is single-process
        store = _local_store(str(scratch))
        source = f"embedded Qdrant ({args.qdrant_path})"
    try:
        embedder = get_embedder()
        embedder.load()
        config = PipelineConfig.from_settings(settings)
        config.top_k = k
        retriever = Retriever(embedder, store, config)
        started = now_utc()
        rows = evaluate(questions, retriever, embedder, store, k)
        points = store.count()
    finally:
        if scratch:
            shutil.rmtree(scratch.parent, ignore_errors=True)

    summary = summarise(rows)
    payload = {
        "kind": "retrieval", "label": args.label, "run_at": started.isoformat(), "vector_store": source,
        "points_in_store": points, "k": k, "min_score": config.min_score, "embedding_model": settings.embedding_model,
        "split": args.split, "summary": summary, "questions": rows,
    }
    path = save_result("retrieval", payload)
    if not args.no_db:
        save_evaluation_rows([{
            "question": r["question"], "expected_document": r["expected_document"],
            "expected_section": r["expected_section"][:255], "hit_at_k": r["hit"], "k_value": k,
            "retrieval_score": r["matched_score"] if r["matched_score"] is not None else r["top_score"],
        } for r in rows])

    for r in rows:
        mark = "HIT " if r["hit"] else "MISS"
        extra = "" if r["hit"] else f"  [{r['miss_reason']}] got: " + "; ".join(
            f"{h['document_name'].split(',')[0][:22]} > {h['section'][-40:]}" for h in r["retrieved"][:3])
        print(f"{mark} {r['id']} ({r['split']:7}) rank={[t['rank'] for t in r['targets']]} {r['question'][:70]}{extra}")
    h = summary["hit_rate"]
    print(f"\nretrieval hit rate @k={k}: {h['hits']}/{h['n']} = {h['rate']:.1%}   "
          f"(hit@1 {summary['hit_at_1']['rate']:.1%}, hit@3 {summary['hit_at_3']['rate']:.1%}, MRR {summary['mrr']})")
    for group, names in (("by_split", ("dev", "heldout")), ("by_difficulty", ("standard", "hard"))):
        for name in names:
            if name in summary[group]:
                s = summary[group][name]
                print(f"  {name:8} {s['hits']}/{s['n']} = {s['rate']:.1%}")
    print(f"  evidence present in retrieved text: {summary['evidence_hit']['hits']}/{summary['evidence_hit']['n']}; "
          f"raw unrouted/ungated top-{k}: {summary['raw_unrouted_ungated_hit']['hits']}/{summary['raw_unrouted_ungated_hit']['n']}")
    print(f"results: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
