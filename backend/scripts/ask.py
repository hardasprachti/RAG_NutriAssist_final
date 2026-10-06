"""Ask the RAG pipeline from the command line (Phase 4 exit gate; not the Phase 7 evaluation).

    python -m scripts.ask "How long can raw chicken stay in the fridge?"
    python -m scripts.ask --batch scripts/phase4_questions.json

Uses the real pipeline: real embeddings, the real vector store and a real Groq call (GROQ_API_KEY). Without
QDRANT_URL it falls back to the embedded store ingestion wrote to ingestion/data/qdrant, and without
DATABASE_URL it records failures in a local SQLite file (backend/.cli/failures.db).

A batch file is a JSON list of {"question", "expect_status" (string or list), "expect_documents" (optional,
document names that must be cited), "expect_min_documents" (optional), "history" (optional prior turns),
"note"}. Exit code 1 if any expectation fails.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _prepare_environment(database_url: Optional[str]) -> None:
    """Must run before config/db_client are imported: both cache what they read."""
    if database_url:
        os.environ["DATABASE_URL"] = database_url
        return
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    if not os.getenv("DATABASE_URL", "").strip():
        cli_dir = BACKEND_DIR / ".cli"
        cli_dir.mkdir(exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{(cli_dir / 'failures.db').as_posix()}"


def _ensure_tables() -> None:
    from integrations.db_client import get_engine
    from models.db_models import Base

    engine = get_engine()
    if engine.dialect.name == "sqlite":  # local scratch DB only; real databases use Alembic migrations
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)


def _add_missing_columns(engine: Any) -> None:
    """``create_all`` never alters a table that exists, so a scratch database made before a column was added would
    fail on it. SQLite only: real databases are upgraded by Alembic."""
    from sqlalchemy import inspect, text

    if "owner_id" not in {c["name"] for c in inspect(engine).get_columns("conversations")}:
        with engine.begin() as conn:  # conversations from before owners existed belong to nobody (NULL)
            conn.execute(text("ALTER TABLE conversations ADD COLUMN owner_id CHAR(32)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated "
                              "ON conversations (owner_id, updated_at)"))


def _local_store(path: str) -> Any:
    from qdrant_client import QdrantClient

    from config import get_settings
    from integrations.vector_store import VectorStore

    settings = get_settings()
    return VectorStore(QdrantClient(path=path), settings.qdrant_collection_name, settings.embedding_dim)


def _failure_count() -> int:
    from sqlalchemy import func, select

    from integrations.db_client import session_scope
    from models.db_models import FailureLog

    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(FailureLog)) or 0


def _turns(raw: list[dict]) -> list:
    from core.rag_pipeline import ChatTurn

    return [ChatTurn(t["role"], t["content"]) for t in raw]


def run_one(pipeline: Any, case: dict, verbose: bool = True) -> tuple[bool, str]:
    result = pipeline.answer(case["question"], _turns(case.get("history", [])))
    response = result.response
    expected = case.get("expect_status")
    expected = [expected] if isinstance(expected, str) else expected
    cited = {c.source.document_name for c in response.claims}
    problems = []
    if expected and response.status.value not in expected:
        problems.append(f"status {response.status.value!r}, expected {expected}")
    for doc in case.get("expect_documents", []):
        if doc not in cited:
            problems.append(f"does not cite {doc!r}")
    if len(cited) < case.get("expect_min_documents", 0):
        problems.append(f"cites {len(cited)} document(s), expected at least {case['expect_min_documents']}")
    if response.status.value == "answered" and not response.claims:
        problems.append("answered without claims")

    if verbose:
        print(f"\n{'PASS' if not problems else 'FAIL'}  {case['question']}")
        retrieval = result.retrieval
        shown = ", ".join(f"{h.chunk.chunk_id.split('_chunk_')[0]}:{h.score:.2f}" for h in result.hits) or "none"
        print(f"  status={response.status.value} stage={result.stage} attempts={result.attempts} "
              f"{result.latency_ms}ms strategy={retrieval.strategy if retrieval else '-'} chunks=[{shown}]")
        print(f"  answer: {response.answer}")
        for c in response.claims:
            print(f"   - {c.claim_text}\n       [{c.source.chunk_id} | {c.source.document_name} | {c.source.section[:50]}]")
        if response.refusal_reason:
            print(f"  reason: {response.refusal_reason}")
        for v in result.failures:
            print(f"  logged {'(blocking)' if v.blocking else '(advisory)'}: {v}")
        for p in problems:
            print(f"  EXPECTATION: {p}")
    return not problems, _markdown(case, result, problems)


def _markdown(case: dict, result: Any, problems: list[str]) -> str:
    r = result.response
    retrieval = result.retrieval
    lines = [
        f"### {'PASS' if not problems else 'FAIL'} - {case['question']}",
        "",
        f"- Expected: {case.get('expect_status', '-')}" + (f" ({case['note']})" if case.get("note") else ""),
        f"- Got: **{r.status.value}** (decided at: {result.stage}; attempts: {result.attempts}; "
        f"retrieval: {retrieval.strategy if retrieval else 'n/a'}; chunks shown: {len(result.hits)})",
        f"- Answer: {r.answer}",
    ]
    for c in r.claims:
        lines.append(f"  - {c.claim_text} _[{c.source.document_name}, {c.source.year}; {c.source.section[:60]}; `{c.source.chunk_id}`]_")
    if r.refusal_reason:
        lines.append(f"- Reason: {r.refusal_reason}")
    for v in result.failures:
        lines.append(f"- Logged ({'blocking' if v.blocking else 'advisory'}): {v}")
    lines += [f"- **Expectation not met:** {p}" for p in problems]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="a single question")
    parser.add_argument("--batch", help="JSON file of questions with expectations")
    parser.add_argument("--qdrant-path", help="embedded Qdrant directory (default: QDRANT_URL, else ingestion/data/qdrant)")
    parser.add_argument("--database-url", help="where failure logs go (default: DATABASE_URL, else local SQLite)")
    parser.add_argument("--report", help="write a Markdown record of the run to this path")
    args = parser.parse_args(argv)
    if not args.question and not args.batch:
        parser.error("give a question or --batch FILE")

    _prepare_environment(args.database_url)
    sys.path.insert(0, str(BACKEND_DIR))
    from config import get_settings
    from core.rag_pipeline import build_pipeline
    from logging_config import configure_logging

    settings = get_settings()
    configure_logging("WARNING")  # keep the report readable; failures are printed explicitly
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set (put it in .env)", file=sys.stderr)
        return 2
    _ensure_tables()
    local = args.qdrant_path or (None if settings.qdrant_url else str(REPO_ROOT / "ingestion" / "data" / "qdrant"))
    store = _local_store(local) if local else None
    if local:
        print(f"vector store: embedded Qdrant at {local}")
    pipeline = build_pipeline(settings, store=store)
    pipeline.retriever._embedder.load()

    cases = json.loads(Path(args.batch).read_text(encoding="utf-8")) if args.batch else [{"question": args.question}]
    before = _failure_count()
    outcomes = [run_one(pipeline, case) for case in cases]
    passed = sum(ok for ok, _ in outcomes)
    logged = _failure_count() - before
    print(f"\n{passed}/{len(cases)} cases met their expectations; {logged} failure(s) logged")
    if args.report:
        header = (
            "# Phase 4 exit-gate run\n\n"
            f"_{passed}/{len(cases)} cases met their expectations; {logged} failure-log row(s) written. Generated by "
            "`python -m scripts.ask --batch scripts/phase4_questions.json --report ...` against the local corpus "
            f"(real embeddings, real Groq `{settings.groq_model}`). A smoke run, not the Phase 7 evaluation._\n\n"
        )
        Path(args.report).write_text(header + "\n".join(md for _, md in outcomes), encoding="utf-8")
        print(f"report written to {args.report}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
