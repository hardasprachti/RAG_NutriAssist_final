"""Build ``docs/evaluation_report.md`` from the latest evaluation results.

    backend\\.venv\\Scripts\\python -m evaluation.report

Inputs (all in ``evaluation/results/``): ``retrieval_latest.json``, ``benchmark_latest.json``,
``consistency_latest.json``, ``safety_latest.json`` (written by the scripts), ``manual_review.json`` and
``citation_review.json`` (written by a person), and ``findings.md`` (the written analysis, included verbatim).
Every number in the report is computed here from those files: nothing is typed in by hand except the analysis in
``findings.md``, so the report cannot drift from the runs it describes.
"""

import json
import sys
from pathlib import Path
from typing import Any, Optional

from evaluation.common import RESULTS_DIR, REPO_ROOT, configure_console, load_latest

REPORT_PATH = REPO_ROOT / "docs" / "evaluation_report.md"


def esc(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "n/a"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def load_json(name: str) -> dict[str, Any]:
    path = RESULTS_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rate(d: dict[str, Any]) -> str:
    return pct(d["hits"], d["n"])


# ── sections ─────────────────────────────────────────────────────────────────
def retrieval_section(data: dict[str, Any]) -> str:
    if not data:
        return "## 1. Retrieval evaluation\n\n_Not run yet._\n"
    s, rows = data["summary"], data["questions"]
    out = [
        "## 1. Retrieval evaluation (no LLM involved)", "",
        f"Run {data['run_at'][:19]} UTC against {data['vector_store']} ({data['points_in_store']} chunks), "
        f"embedding model `{data['embedding_model']}`, k = {data['k']}, similarity gate {data['min_score']}. "
        "Each question names its expected document and section in advance; a **hit** means every expected "
        "(document, section) pair is among the chunks the model would be shown, produced by the same retriever the chat "
        "pipeline uses (document routing, per-document search for comparisons, the similarity gate).", "",
        f"**Retrieval hit rate: {rate(s['hit_rate'])}.**  hit@1 {rate(s['hit_at_1'])}; hit@3 {rate(s['hit_at_3'])}; MRR {s['mrr']}. "
        f"The expected facts themselves (figures/phrases) were present in the retrieved text for {rate(s['evidence_hit'])} "
        f"of the questions that list evidence. For comparison, plain unrouted, ungated top-{data['k']} search hit "
        f"{rate(s['raw_unrouted_ungated_hit'])}.", "",
        "### By split and difficulty", "",
        table(["Group", "Hits"], [[f"split: {k}", rate(v)] for k, v in s["by_split"].items()]
              + [[f"difficulty: {k}", rate(v)] for k, v in s.get("by_difficulty", {}).items()]), "",
        "`dev` questions may inform tuning in Phase 8; `heldout` questions are never used to tune. The `hard` tier "
        "(colloquial, keyword-free, indirect questions) was added after the first 30 `standard` questions all hit, "
        "because a set the system passes completely cannot distinguish good retrieval from easy questions.", "",
        "### By expected document", "",
        table(["Document", "Hits"], [[k, rate(v)] for k, v in s["by_document"].items()]), "",
        "### By retrieval strategy", "",
        table(["Strategy", "Hits"], [[k, rate(v)] for k, v in s["by_strategy"].items()]), "",
    ]
    misses = [r for r in rows if not r["hit"]]
    out += ["### Retrieval failures (reported apart from generation failures)", ""]
    if misses:
        out.append(table(
            ["Id", "Question", "Expected", "Reason", "Retrieved instead (top 3)"],
            [[r["id"], r["question"], f"{r['expected_document']} > {r['expected_section']}", r["miss_reason"],
              "; ".join(f"{h['document_name'].split(',')[0]} > {h['section'][-45:]} ({h['score']})" for h in r["retrieved"][:3]) or "nothing cleared the gate"]
             for r in misses]))
    else:
        out.append("None.")
    late = [r for r in rows if r["hit"] and any(t["rank"] and t["rank"] > 3 for t in r["targets"])]
    if late:
        out += ["", "Hits found only at rank 4-5 (a smaller k would have lost them): "
                + "; ".join(f"{r['id']} (rank {[t['rank'] for t in r['targets']]})" for r in late) + "."]
    out += ["", "<details><summary>Every question</summary>", "",
            table(["Id", "Split", "Question", "Rank of expected chunk(s)", "Top score", "Strategy"],
                  [[r["id"], r["split"], r["question"], [t["rank"] for t in r["targets"]], r["top_score"], r["strategy"]] for r in rows]),
            "", "</details>", ""]
    return "\n".join(out)


def benchmark_section(data: dict[str, Any], manual: dict[str, Any]) -> str:
    if not data:
        return "## 2. Benchmark\n\n_Not run yet._\n"
    s, rows = data["summary"], data["questions"]
    reviews = manual.get("benchmark", {})
    out = [
        "## 2. Benchmark through the real API", "",
        f"Run {data['run_at'][:19]} UTC against `{data['base_url']}` (health: {data.get('health', {})}). "
        f"{s['questions']} questions in fresh conversations, each audited against the chunk files ingestion wrote and "
        "against facts recorded from the source documents in advance (see `evaluation/scoring.py`).", "",
        f"**{s['passed']}/{s['questions']} responses passed every automated check.** Statuses returned: {s['status_counts']}. "
        f"Claims in answered responses: {s['answered_claims']}; with every number present in the cited chunk: "
        f"{s['answered_claims_with_all_numbers_in_cited_chunk']}. Median latency {s['median_latency_ms']} ms.", "",
        "### Failures grouped by category (automated)", "",
    ]
    if s["failures_by_category"]:
        out.append(table(["Category", "Occurrences"], [[k, v] for k, v in s["failures_by_category"].items()]))
    else:
        out.append("No automated failures.")
    if s["flags_by_category"]:
        out += ["", "Flags raised for manual review (not counted as failures): "
                + ", ".join(f"{k} x{v}" for k, v in s["flags_by_category"].items()) + "."]
    out += ["", "### By question category", "",
            table(["Category", "Passed"], [[k, pct(v["passed"], v["n"])] for k, v in s["by_question_category"].items()]), "",
            "### Per-response scoring sheet", "",
            table(["Id", "Category", "Status (expected)", "Automated result", "Manual review"],
                  [[r["id"], r["category"], f"{r['status']} ({'/'.join(r['expected_status'])})",
                    "pass" if r["passed"] else "; ".join(f"{f['category']}: {f['detail']}" for f in r["findings"] if f["fatal"]),
                    _manual_cell(reviews.get(r["id"]))] for r in rows]), ""]
    if reviews:
        out += ["### Manual review totals", "", _manual_totals(reviews), ""]
    return "\n".join(out)


def _manual_cell(review: Optional[dict[str, Any]]) -> str:
    if not review:
        return "not reviewed"
    issues = [k.replace("_", " ") for k in ("unsupported_claims", "changed_numbers", "fabricated_citations",
                                            "incorrect_or_missing_refusal", "incorrect_retrieval", "unhelpful_uncertainty") if review.get(k)]
    return ("; ".join(issues) if issues else "no issues") + (f" - {review['note']}" if review.get("note") else "")


def _manual_totals(reviews: dict[str, Any]) -> str:
    crit = ["unsupported_claims", "changed_numbers", "fabricated_citations", "incorrect_or_missing_refusal",
            "incorrect_retrieval", "unhelpful_uncertainty"]
    rows = [[c.replace("_", " "), sum(1 for r in reviews.values() if r.get(c))] for c in crit]
    return table(["Criterion (responses affected)", f"of {len(reviews)} reviewed"], rows)


def consistency_section(data: dict[str, Any], manual: dict[str, Any]) -> str:
    if not data:
        return "## 3. Consistency\n\n_Not run yet._\n"
    s, rows = data["summary"], data["questions"]
    classes = manual.get("consistency", {})
    n = s["questions"]
    yes = lambda b: "yes" if b else "NO"  # noqa: E731
    out = [
        "## 3. Consistency (same question, three runs)", "",
        f"Run {data['run_at'][:19]} UTC. {n} questions x {s['runs_per_question']} runs, each in a fresh conversation. "
        "Wording is ignored; the comparison is on figures, cited documents/sections, evidence and status.", "",
        table(["Dimension", "Consistent on"], [
            ["Numerical (same figures in all runs)", pct(s["numerical_consistency"], n)],
            ["Citation: same documents", pct(s["citation_consistency_documents"], n)],
            ["Citation: same sections", pct(s["citation_consistency_sections"], n)],
            ["Evidence agreement (claim figures in cited chunk, every run)", pct(s["evidence_agreement"], n)],
            ["Recommendation stability (status same and claim wording overlap >= 0.5)", pct(s["recommendation_stable"], n)],
            ["Refusal (same status in every run)", pct(s["refusal_consistency"], n)]]), "",
        table(["Id", "Question", "Status", "Numbers", "Documents", "Sections", "Evidence", "Stability", "Figures per run", "Manual classification"],
              [[r["id"], r["question"], r["comparison"]["status"], yes(r["comparison"]["numerical_consistency"]),
                yes(r["comparison"]["citation_consistency_documents"]), yes(r["comparison"]["citation_consistency_sections"]),
                yes(r["comparison"]["evidence_agreement"]), f"{r['comparison']['recommendation_stability']} ({r['comparison']['min_pairwise_wording_similarity']})",
                " / ".join(",".join(x["numbers"]) or "-" for x in r["runs"]),
                classes.get(r["id"], {}).get("classification", "not classified" if not r["comparison"]["numerical_consistency"] else "")]
               for r in rows]), "",
        f"`inconsistent_numerical_claim` rows written to `failure_logs`: {s['inconsistent_numerical_claim_logged']}. "
        "A differing figure is recorded as a potential failure and the answers are not altered.", ""]
    for r in rows:
        note = classes.get(r["id"], {}).get("note")
        if note:
            out.append(f"- **{r['id']}**: {note}")
    return "\n".join(out) + "\n"


def citation_section(review: dict[str, Any], benchmark: dict[str, Any]) -> str:
    reviews = review.get("reviews", {})
    out = ["## 4. Citation spot-check (manual)", ""]
    if not reviews:
        return "\n".join(out + ["_Not done yet._", ""])
    flat = [(qid, claim, v) for qid, claims in reviews.items() for claim, v in claims.items()]
    verdicts = {}
    for _, _, v in flat:
        verdicts[v["verdict"]] = verdicts.get(v["verdict"], 0) + 1
    answers = len(reviews)
    out += [
        f"{answers} answered responses ({len(flat)} claims) were checked by opening the cited document at the cited "
        "section and comparing claim and numbers. " + review.get("method", ""), "",
        f"Reviewer: {review.get('reviewer', 'unrecorded')}. " + review.get("reviewer_note", ""), "",
        table(["Verdict", "Claims"], [[k, v] for k, v in sorted(verdicts.items())]), "",
        table(["Response", "Claim", "Verdict", "Source page / section checked", "Note"],
              [[qid, claim, v["verdict"], v.get("where", ""), v.get("note", "")] for qid, claim, v in flat]), ""]
    bad = [(q, c, v) for q, c, v in flat if v["verdict"] != "supported"]
    out += ["**Incorrect or unsupported citations:** " + ("; ".join(f"{q} claim {c} ({v['verdict']}): {v.get('note', '')}" for q, c, v in bad) if bad else "none found."), ""]
    return "\n".join(out)


def safety_section(data: dict[str, Any]) -> str:
    if not data:
        return "## 5. Adversarial safety run\n\n_Not run yet._\n"
    s = data["summary"]
    out = [
        "## 5. Adversarial safety run (end to end through the API)", "",
        f"Run {data['run_at'][:19]} UTC against `{data['base_url']}`. Prompts are in `evaluation/questions/safety_questions.json`; "
        "they are deliberately different from the wordings the backend unit tests were written against.", "",
        f"**Restricted prompts refused: {pct(s['restricted_refused'], s['restricted_turns'])}. "
        f"Legitimate questions not blocked: {pct(s['legitimate_not_blocked'], s['legitimate_turns'])}.**", "",
        table(["Attack type", "Scenarios passed"], [[k, pct(v["passed"], v["scenarios"])] for k, v in s["by_type"].items()]), ""]
    out.append("### Failures")
    out.append("")
    out.append(table(["Scenario", "Type", "Prompt", "Expected", "Got", "Why"],
                     [[f["scenario"], f["type"], f["turn"], f["expect"], f["status"], f["why"]] for f in s["failures"]])
               if s["failures"] else "None.")
    if s["borderline_recorded"]:
        out += ["", "### Borderline prompts (recorded, not scored)", "",
                table(["Scenario", "Prompt", "Status"], [[b["scenario"], b["turn"], b["status"]] for b in s["borderline_recorded"]])]
    return "\n".join(out) + "\n"


def failure_log_section(*runs: tuple[str, dict[str, Any]]) -> str:
    out = ["## 6. Failure log populated by the runs", "",
           "Rows the backend wrote to `failure_logs` while each suite ran (rejected or repaired model outputs, provider errors) "
           "and rows written by the evaluation itself:", ""]
    rows = [[name, ", ".join(f"{k} x{v}" for k, v in sorted(d.get("failure_logs_written_during_run", {}).items())) or "none"]
            for name, d in runs if d]
    return "\n".join(out + [table(["Suite", "failure_logs rows by category"], rows), ""])


def build() -> str:
    retrieval, benchmark = load_latest("retrieval") or {}, load_latest("benchmark") or {}
    consistency, safety = load_latest("consistency") or {}, load_latest("safety") or {}
    manual, citation = load_json("manual_review.json"), load_json("citation_review.json")
    findings = RESULTS_DIR / "findings.md"
    head = [
        "# Evaluation report", "",
        "> Phase 7 of the [implementation plan](./implementation_plan.md). Generated by `python -m evaluation.report` from "
        "`evaluation/results/`; the analysis in section 7 is written by hand. Results are recorded as measured: no answer, "
        "question or threshold was altered to improve a number.", "",
    ]
    parts = [retrieval_section(retrieval), benchmark_section(benchmark, manual), consistency_section(consistency, manual),
             citation_section(citation, benchmark), safety_section(safety),
             failure_log_section(("Benchmark", benchmark), ("Consistency", consistency), ("Safety", safety)),
             "## 7. Findings and limitations\n\n" + (findings.read_text(encoding="utf-8") if findings.exists() else "_Not written yet._"),
             "## 8. Reproducing this report\n\n```\n"
             "backend\\.venv\\Scripts\\python -m evaluation.retrieval_eval\n"
             "backend\\.venv\\Scripts\\python -m evaluation.benchmark_questions --spawn-server\n"
             "backend\\.venv\\Scripts\\python -m evaluation.consistency_test --spawn-server\n"
             "backend\\.venv\\Scripts\\python -m evaluation.safety_eval --spawn-server\n"
             "backend\\.venv\\Scripts\\python -m evaluation.citation_checker      # worksheet for the manual spot-check\n"
             "backend\\.venv\\Scripts\\python -m evaluation.report\n"
             "backend\\.venv\\Scripts\\python -m pytest evaluation/tests          # the evaluation's own tests\n```\n"
             "Add `--base-url https://<deployment>` instead of `--spawn-server` to run the same suites against production (Phase 9)."]
    return "\n".join(head) + "\n" + "\n".join(parts)


def main() -> int:
    configure_console()
    REPORT_PATH.write_text(build(), encoding="utf-8")
    print(f"report written to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
