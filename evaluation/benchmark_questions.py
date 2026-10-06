"""Benchmark run (Phase 7.2): the question bank through the real ``POST /api/chat``.

    backend\\.venv\\Scripts\\python -m evaluation.benchmark_questions --spawn-server
    backend\\.venv\\Scripts\\python -m evaluation.benchmark_questions --base-url https://<production>   # Phase 9

Every question is asked in a fresh conversation. Each response is audited by ``evaluation/scoring.py`` against the
chunk files ingestion wrote and the facts the question author recorded: status, retrieved documents, citations,
figures, attribution across documents. Failures are grouped by category with counts (Problem Statement §9). What
no script can settle (is the claim *really* supported by the page?) is left to the manual review in
``results/manual_review.json``; claims flagged as weak are listed for it.
"""

import argparse
import sys
from collections import Counter, defaultdict
from typing import Any

from evaluation import scoring
from evaluation.common import (CHUNKS_DIR, REGISTRY_PATH, ChatError, Target, add_server_arguments, configure_console,
                               failure_counts_since, load_questions, now_utc, save_evaluation_rows, save_result)


def load_corpus() -> scoring.Corpus:
    if not CHUNKS_DIR.exists():
        print(f"note: {CHUNKS_DIR} not found; citations are checked against the excerpts the API returned", file=sys.stderr)
    return scoring.Corpus.load(CHUNKS_DIR, REGISTRY_PATH) if CHUNKS_DIR.exists() else scoring.Corpus({}, _registry_only())


def _registry_only() -> dict[str, Any]:
    import json

    return {d["document_name"]: d for d in json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["documents"]}


def run_case(client: Any, case: dict[str, Any], corpus: scoring.Corpus) -> dict[str, Any]:
    try:
        result = client.ask(case["question"])
    except ChatError as exc:
        return {"id": case["id"], "category": case["category"], "question": case["question"],
                "expected_status": case["expected_status"], "http_status": 0, "status": "unreachable",
                "latency_ms": 0, "findings": [scoring.Finding("error_status", f"request failed: {exc}").as_dict()],
                "passed": False, "response": {}}
    if not result.ok:
        finding = scoring.Finding("error_status", f"HTTP {result.http_status}: {result.body.get('message', result.body)}")
        return {"id": case["id"], "category": case["category"], "question": case["question"],
                "expected_status": case["expected_status"], "http_status": result.http_status,
                "status": f"http_{result.http_status}", "latency_ms": result.latency_ms,
                "findings": [finding.as_dict()], "passed": False, "response": result.body}
    scored = scoring.score_benchmark(case, result.body, corpus)
    return {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "expected_status": case["expected_status"], "notes": case.get("notes", ""),
        "http_status": result.http_status, "status": result.status, "latency_ms": result.latency_ms,
        "attempts": result.attempts, "metrics": scored.metrics,
        "findings": [f.as_dict() for f in scored.findings], "passed": scored.passed, "response": result.body,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failures = Counter(f["category"] for r in rows for f in r["findings"] if f["fatal"])
    flags = Counter(f["category"] for r in rows for f in r["findings"] if not f["fatal"])
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "passed": 0})
    for r in rows:
        by_category[r["category"]]["n"] += 1
        by_category[r["category"]]["passed"] += int(r["passed"])
    answered = [r for r in rows if r["status"] == "answered"]
    claims = sum(r["metrics"].get("claims", 0) for r in answered)
    supported = sum(r["metrics"].get("claims_number_supported", 0) for r in answered)
    return {
        "questions": len(rows), "passed": sum(r["passed"] for r in rows),
        "failures_by_category": dict(sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))),
        "flags_by_category": dict(sorted(flags.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_question_category": dict(by_category),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "answered_claims": claims, "answered_claims_with_all_numbers_in_cited_chunk": supported,
        "median_latency_ms": sorted(r["latency_ms"] for r in rows)[len(rows) // 2] if rows else None,
    }


def main(argv: Any = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_server_arguments(parser)
    parser.add_argument("--questions", default="benchmark_questions.json")
    parser.add_argument("--ids", nargs="*", help="only these question ids")
    parser.add_argument("--no-db", action="store_true", help="do not write evaluation_results rows")
    parser.add_argument("--label", default="", help="free-text label stored in the result file")
    args = parser.parse_args(argv)

    cases = load_questions(args.questions)["questions"]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    corpus = load_corpus()

    with Target(args) as target:
        client = target.client()
        health = client.health()
        print(f"backend {target.base_url}: {health}")
        started = now_utc()
        rows = []
        for case in cases:
            row = run_case(client, case, corpus)
            rows.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            print(f"{mark} {row['id']} [{row['category']}] -> {row['status']} ({row['latency_ms']} ms) {row['question'][:60]}")
            for f in row["findings"]:
                print(f"       {'!' if f['fatal'] else '?'} {f['category']}: {f['detail']}")
        logged = failure_counts_since(started)

    summary = summarise(rows)
    payload = {"kind": "benchmark", "label": args.label, "run_at": started.isoformat(), "base_url": target.base_url,
               "health": health, "summary": summary, "failure_logs_written_during_run": logged, "questions": rows}
    path = save_result("benchmark", payload)
    if not args.no_db:
        save_evaluation_rows([_db_row(r) for r in rows])

    print(f"\n{summary['passed']}/{summary['questions']} responses passed the automated checks")
    print("failures by category:", summary["failures_by_category"] or "none")
    print("flags for manual review:", summary["flags_by_category"] or "none")
    print("failure_logs rows written by the server during the run:", logged or "none")
    print(f"results: {path}")
    return 0 if summary["passed"] == summary["questions"] else 1


def _db_row(row: dict[str, Any]) -> dict[str, Any]:
    sources = (row.get("response") or {}).get("retrieved_sources") or []
    citation_findings = {"fabricated_source", "invalid_citation", "unsupported_claim", "missing_citation"}
    citation_valid = None
    if row["status"] == "answered":
        citation_valid = not any(f["fatal"] and f["category"] in citation_findings for f in row["findings"])
    return {
        "question": row["question"], "expected_document": None, "expected_section": None,
        "hit_at_k": None if not sources else not any(f["category"] == "incorrect_retrieval" for f in row["findings"]),
        "k_value": len(sources) or None,
        "retrieval_score": max((s.get("similarity_score") or 0.0 for s in sources), default=None),
        "citation_valid": citation_valid,
    }


if __name__ == "__main__":
    sys.exit(main())
