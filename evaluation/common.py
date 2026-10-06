"""Shared plumbing for the Phase 7 evaluation scripts.

Run everything from the repository root with the backend's virtualenv, e.g.
``backend\\.venv\\Scripts\\python -m evaluation.retrieval_eval``.

Two ways of reaching the system, on purpose:

* **Retrieval** is measured in-process (real embedder, real vector store, no LLM): it isolates retrieval from
  generation, which the problem statement requires ("identify retrieval failures separately").
* **Everything else** goes through the real ``POST /api/chat`` of a running backend (``--base-url``, or
  ``--spawn-server`` for a local one), so the safety layer, validator, persistence and error envelope are
  exercised exactly as a user would hit them. The same scripts run against the production URL in Phase 9.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
EVAL_DIR = REPO_ROOT / "evaluation"
QUESTIONS_DIR = EVAL_DIR / "questions"
RESULTS_DIR = EVAL_DIR / "results"
CHUNKS_DIR = REPO_ROOT / "ingestion" / "data" / "chunks"
RAW_DIR = REPO_ROOT / "ingestion" / "data" / "raw"
LOCAL_QDRANT = REPO_ROOT / "ingestion" / "data" / "qdrant"
REGISTRY_PATH = REPO_ROOT / "ingestion" / "document_registry.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PACE_SECONDS = 4.0  # between chat calls: stays under the 20/min per-IP limit and Groq's token-per-minute limit


def configure_console() -> None:
    """Windows consoles default to cp1252; model answers contain en dashes and Greek letters."""
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now_utc().strftime("%Y%m%d-%H%M%S")


# ── question files and results ───────────────────────────────────────────────
def load_questions(name: str) -> Any:
    return json.loads((QUESTIONS_DIR / name).read_text(encoding="utf-8"))


def save_result(kind: str, payload: dict[str, Any]) -> Path:
    """Write ``results/<kind>_<timestamp>.json`` and refresh ``results/<kind>_latest.json``."""
    RESULTS_DIR.mkdir(exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    path = RESULTS_DIR / f"{kind}_{stamp()}.json"
    path.write_text(text, encoding="utf-8")
    (RESULTS_DIR / f"{kind}_latest.json").write_text(text, encoding="utf-8")
    return path


def load_latest(kind: str) -> Optional[dict[str, Any]]:
    path = RESULTS_DIR / f"{kind}_latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ── environment and database ─────────────────────────────────────────────────
def default_database_url() -> str:
    """The database ``scripts.serve_local`` uses, so failure logs written by the server can be read back here."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    url = os.getenv("DATABASE_URL", "").strip()
    return url or f"sqlite:///{(BACKEND_DIR / '.cli' / 'chat.db').as_posix()}"


def prepare_database(url: Optional[str] = None) -> str:
    """Point the backend's db_client at ``url`` and make sure the tables exist (SQLite scratch DBs only).

    Must run before anything imports ``config`` or ``integrations.db_client``: both cache what they read."""
    url = url or default_database_url()
    os.environ["DATABASE_URL"] = url
    (BACKEND_DIR / ".cli").mkdir(exist_ok=True)
    from scripts.ask import _ensure_tables

    _ensure_tables()
    return url


def save_evaluation_rows(rows: list[dict[str, Any]]) -> int:
    """Store rows in ``evaluation_results`` (Architecture §9). Returns how many were written."""
    from integrations.db_client import save_evaluation_result, session_scope

    with session_scope() as session:
        for row in rows:
            save_evaluation_result(session, **row)
    return len(rows)


def failure_counts_since(since: datetime) -> dict[str, int]:
    """``failure_logs`` rows created at or after ``since``, by category (the log is written by the server)."""
    from sqlalchemy import func, select

    from integrations.db_client import session_scope
    from models.db_models import FailureLog

    since = since.astimezone(timezone.utc)
    with session_scope() as session:
        rows = session.execute(
            select(FailureLog.failure_category, func.count()).where(FailureLog.created_at >= _naive_utc(session, since))
            .group_by(FailureLog.failure_category)
        ).all()
    return {category: int(n) for category, n in rows}


def _naive_utc(session: Any, moment: datetime) -> datetime:
    """SQLite stores naive timestamps; PostgreSQL keeps the zone. Compare in the form the database holds."""
    return moment.replace(tzinfo=None) if session.get_bind().dialect.name == "sqlite" else moment


def log_failure(category: str, question: str, description: str, *, model_response: Optional[str] = None,
                model_info: Optional[str] = None) -> bool:
    """Record an evaluation finding in ``failure_logs`` (e.g. ``inconsistent_numerical_claim``)."""
    from core.failure_logger import FailureCategory, FailureLogger

    return FailureLogger().log(FailureCategory(category), question, description,
                               model_response=model_response, model_info=model_info)


# ── the HTTP side ────────────────────────────────────────────────────────────
class ChatError(RuntimeError):
    pass


@dataclass
class ChatResult:
    ok: bool
    http_status: int
    body: dict[str, Any]
    latency_ms: int
    attempts: int = 1

    @property
    def status(self) -> str:
        return str(self.body.get("status", f"http_{self.http_status}"))

    @property
    def claims(self) -> list[dict[str, Any]]:
        return list(self.body.get("claims") or [])

    @property
    def sources(self) -> list[dict[str, Any]]:
        return list(self.body.get("retrieved_sources") or [])


@dataclass
class ChatClient:
    """Calls the real API with one anonymous client id per run (conversations belong to it)."""

    base_url: str = DEFAULT_BASE_URL
    pace: float = PACE_SECONDS
    timeout: float = 120.0
    client_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _last_call: float = 0.0
    calls: int = 0

    def _http(self) -> Any:
        import httpx

        return httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout,
                            headers={"X-Client-Id": self.client_id})

    def health(self) -> dict[str, Any]:
        with self._http() as http:
            response = http.get("/api/health")
        return {"http_status": response.status_code, **response.json()}

    def ask(self, question: str, conversation_id: Optional[str] = None, retries: int = 4) -> ChatResult:
        """One chat turn. Waits out 429s and retries transport errors; every other response is returned as is."""
        payload: dict[str, Any] = {"question": question}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        attempts = 0
        while True:
            attempts += 1
            self._pace()
            started = time.monotonic()
            try:
                with self._http() as http:
                    response = http.post("/api/chat", json=payload)
            except Exception as exc:  # connection reset, read timeout...
                if attempts > retries:
                    raise ChatError(f"{type(exc).__name__}: {exc}") from exc
                time.sleep(2.0 * attempts)
                continue
            self.calls += 1
            latency = int((time.monotonic() - started) * 1000)
            if response.status_code == 429 and attempts <= retries:
                time.sleep(min(float(response.headers.get("Retry-After", "10")) + 1.0, 70.0))
                continue
            if response.status_code in (503, 504) and attempts <= retries:
                time.sleep(5.0 * attempts)
                continue
            try:
                body = response.json()
            except ValueError:
                body = {"error": "non_json_response", "message": response.text[:300]}
            return ChatResult(response.status_code == 200, response.status_code, body, latency, attempts)

    def _pace(self) -> None:
        wait = self._last_call + self.pace - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()


def add_server_arguments(parser: Any) -> None:
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"backend to test (default {DEFAULT_BASE_URL})")
    parser.add_argument("--spawn-server", action="store_true",
                        help="start a local backend (scripts.serve_local) for the run and stop it afterwards")
    parser.add_argument("--port", type=int, default=8765, help="port for --spawn-server")
    parser.add_argument("--database-url", help="database the backend writes failure logs to (default: serve_local's)")
    parser.add_argument("--pace", type=float, default=PACE_SECONDS, help="minimum seconds between chat calls")


class spawned_server:
    """Context manager: a local backend with the rate limits raised, so an evaluation run is not throttled
    (the limits themselves are tested in the backend suite). Yields the base URL."""

    def __init__(self, port: int, database_url: str) -> None:
        self.port = port
        self.database_url = database_url
        self.process: Optional[subprocess.Popen] = None
        self.log_path = RESULTS_DIR / "server.log"

    def __enter__(self) -> str:
        RESULTS_DIR.mkdir(exist_ok=True)
        env = {**os.environ, "RATE_LIMIT_CHAT_PER_MINUTE": "600", "RATE_LIMIT_API_PER_MINUTE": "6000",
               "PYTHONIOENCODING": "utf-8"}
        log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "scripts.serve_local", "--port", str(self.port),
             "--database-url", self.database_url],
            cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 240  # loading the embedding model dominates
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ChatError(f"the local backend exited early (see {self.log_path})")
            with suppress(Exception):
                if ChatClient(base_url=base, pace=0).health().get("http_status") in (200, 503):
                    return base
            time.sleep(2.0)
        self.__exit__(None, None, None)
        raise ChatError(f"the local backend did not become healthy in time (see {self.log_path})")

    def __exit__(self, *exc: Any) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            with suppress(Exception):
                self.process.wait(timeout=20)
            if self.process.poll() is None:
                self.process.kill()


class Target:
    """What a script talks to: a base URL, plus the server it started (if any)."""

    def __init__(self, args: Any) -> None:
        self.args = args
        self.database_url = prepare_database(args.database_url)
        self._server: Optional[spawned_server] = None
        self.base_url = args.base_url

    def __enter__(self) -> "Target":
        if self.args.spawn_server:
            self._server = spawned_server(self.args.port, self.database_url)
            self.base_url = self._server.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server:
            self._server.__exit__(*exc)

    def client(self) -> ChatClient:
        return ChatClient(base_url=self.base_url, pace=self.args.pace)
