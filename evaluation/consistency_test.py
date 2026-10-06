"""Consistency test (Phase 7.3): the same question three times, compared on substance not wording.

    backend\\.venv\\Scripts\\python -m evaluation.consistency_test --spawn-server [--runs 3]

Each run is a fresh conversation (no shared history). The five dimensions of Problem Statement §10 are computed
per question: numerical, citation (documents, sections), evidence agreement, recommendation stability and refusal.
A number that differs between runs is logged to ``failure_logs`` as ``inconsistent_numerical_claim``; the answers
are never altered or averaged to look consistent. A differing number can be a different *omission* (one run states
5% sugar as well, another does not) or a real contradiction; the script cannot tell them apart, so every case is
printed with each run's figures for a human to classify in ``results/manual_review.json``.
"""

import argparse
import json
import sys
from typing import Any

from evaluation import scoring
from evaluation.benchmark_questions import load_corpus
from evaluation.common import (ChatError, Target, add_server_arguments, configure_console, load_questions, log_failure,
                               now_utc, save_result)


def run_question(client: Any, case: dict[str, Any], runs: int, corpus: scoring.Corpus) -> dict[str, Any]:
    responses, digests = [], []
    for _ in range(runs):
        try:
            result = client.ask(case["question"])
            body = result.body
            if not result.ok:
                body = {"status": f"http_{result.http_status}", "answer": str(result.body), "claims": []}
        except ChatError as exc:
            body = {"status": "unreachable", "answer": str(exc), "claims": []}
        responses.append(body)
        digests.append(scoring.digest(body, corpus))
    comparison = scoring.compare_runs(digests)
    return {
        "id": case["id"], "kind": case["kind"], "question": case["question"], "comparison": comparison,
        "runs": [{
            "status": d.status, "numbers": sorted(d.numbers, key=float), "documents": sorted(d.documents),
            "sections": sorted(d.sections), "chunk_ids": sorted(d.chunk_ids), "answer": d.answer,
            "claims": [c["claim_text"] for c in (r.get("claims") or [])],
        } for d, r in zip(digests, responses)],
    }


def main(argv: Any = None) -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_server_arguments(parser)
    parser.add_argument("--questions", default="consistency_questions.json")
    parser.add_argument("--runs", type=int, help="repeats per question (default: the file's, 3)")
    parser.add_argument("--ids", nargs="*", help="only these question ids")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    from config import get_settings

    spec = load_questions(args.questions)
    runs = args.runs or spec.get("runs", 3)
    cases = [c for c in spec["questions"] if not args.ids or c["id"] in args.ids]
    corpus = load_corpus()

    with Target(args) as target:
        client = target.client()
        health = client.health()
        print(f"backend {target.base_url}: {health}")
        started = now_utc()
        rows = []
        for case in cases:
            row = run_question(client, case, runs, corpus)
            rows.append(row)
            c = row["comparison"]
            print(f"\n{row['id']} [{row['kind']}] {row['question']}")
            print(f"   status={c['status']}  numbers {'CONSISTENT' if c['numerical_consistency'] else 'DIFFER ' + str(c['numbers_differing'])}  "
                  f"documents {'same' if c['citation_consistency_documents'] else 'DIFFER'}  "
                  f"sections {'same' if c['citation_consistency_sections'] else 'DIFFER'}  "
                  f"evidence {'ok' if c['evidence_agreement'] else 'NOT SUPPORTED'}  "
                  f"stability={c['recommendation_stability']} (wording overlap {c['min_pairwise_wording_similarity']})")
            for i, r in enumerate(row["runs"], start=1):
                print(f"   run {i}: {r['status']:13} numbers={r['numbers']} docs={[d.split(',')[0][:24] for d in r['documents']]}")
            if row["comparison"]["status"] == "answered" and not c["numerical_consistency"]:
                row["logged_inconsistent_numerical_claim"] = log_failure(
                    "inconsistent_numerical_claim", row["question"],
                    f"figures differ across {runs} runs: {c['numbers_differing']}; per run: "
                    + "; ".join(f"run {i}: {r['numbers']}" for i, r in enumerate(row["runs"], start=1)),
                    model_response=json.dumps([r["claims"] for r in row["runs"]], ensure_ascii=False),
                    model_info=f"groq:{get_settings().groq_model}")

    n = len(rows)
    dims = ("refusal_consistency", "numerical_consistency", "citation_consistency_documents",
            "citation_consistency_sections", "evidence_agreement")
    summary = {"questions": n, "runs_per_question": runs,
               **{d: sum(r["comparison"][d] for r in rows) for d in dims},
               "recommendation_stable": sum(r["comparison"]["recommendation_stability"] == "stable" for r in rows),
               "inconsistent_numerical_claim_logged": sum(bool(r.get("logged_inconsistent_numerical_claim")) for r in rows)}
    path = save_result("consistency", {"kind": "consistency", "label": args.label, "run_at": started.isoformat(),
                                       "base_url": target.base_url, "summary": summary, "questions": rows})
    print(f"\nconsistent on {n} questions x {runs} runs: " + ", ".join(f"{d} {summary[d]}/{n}" for d in dims)
          + f", recommendation stable {summary['recommendation_stable']}/{n}")
    print(f"results: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
