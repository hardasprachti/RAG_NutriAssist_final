"""Run the real API against the local corpus, for developing and testing the frontend (Phase 6).

    python -m scripts.serve_local [--port 8000] [--database-url sqlite:///...]

Real embeddings and a real Groq call (GROQ_API_KEY), but no hosted services: the vector store is a copy of the
embedded Qdrant that ingestion wrote to ingestion/data/qdrant (a copy, so a running ingestion keeps its lock),
and conversations go to a local SQLite file (backend/.cli/chat.db) unless DATABASE_URL points elsewhere.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--qdrant-path", default=str(REPO_ROOT / "ingestion" / "data" / "qdrant"))
    parser.add_argument("--database-url", help="default: DATABASE_URL, else a local SQLite file")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(BACKEND_DIR)  # prompts/ and .env resolution are relative to the backend
    from scripts.ask import _ensure_tables, _local_store, _prepare_environment

    if args.database_url is None and not os.getenv("DATABASE_URL"):
        (BACKEND_DIR / ".cli").mkdir(exist_ok=True)
        args.database_url = f"sqlite:///{(BACKEND_DIR / '.cli' / 'chat.db').as_posix()}"
    _prepare_environment(args.database_url)
    os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3100")

    import uvicorn
    from config import get_settings

    settings = get_settings()
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set (put it in .env)", file=sys.stderr)
        return 2
    if not Path(args.qdrant_path).exists():
        print(f"no local corpus at {args.qdrant_path}; run `python -m ingestion.run_ingestion` first", file=sys.stderr)
        return 2

    from core.rag_pipeline import build_pipeline
    from core.chat_service import ChatService
    from main import app
    from routers.deps import get_chat_service

    _ensure_tables()
    scratch = Path(tempfile.mkdtemp(prefix="nutrition_qdrant_")) / "qdrant"
    shutil.copytree(args.qdrant_path, scratch)
    pipeline = build_pipeline(settings, store=_local_store(str(scratch)))
    pipeline.retriever._embedder.load()
    service = ChatService(pipeline, settings)
    app.dependency_overrides[get_chat_service] = lambda: service

    print(f"serving http://127.0.0.1:{args.port}  (database: {os.environ['DATABASE_URL'].split('///')[0]}///…)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
