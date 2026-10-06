"""Harness for the API tests: the real app, a real migrated database, the real safety validator and failure
logging, with a scripted LLM and a tiny in-memory corpus (``tests.support``) standing in for Groq and Qdrant."""

import threading
import time
import uuid
from typing import Any, Optional

from fastapi.testclient import TestClient
from sqlalchemy import select

from core.chat_service import ChatService
from core.rag_pipeline import PipelineConfig, RAGPipeline, Retriever
from core.safety_validator import SafetyValidator
from integrations.db_client import session_scope
from models.db_models import Conversation, FailureLog, Message, RetrievedChunk
from tests.support import CHICKEN, GOOD_CHICKEN, FakeEmbedder, FakeLLM, answered, make_store

CHICKEN_Q = "How long can chicken stay in the fridge?"
SUGAR_Q = "What does WHO say about sugar?"
RESTRICTED_Q = "How many calories should I eat to lose weight?"
OFF_TOPIC_Q = "What is the capital of France?"


def good_chicken():
    return answered(CHICKEN, GOOD_CHICKEN)


class SlowLLM(FakeLLM):
    """Sleeps before answering, and tracks how many calls overlap."""

    def __init__(self, delay: float, *script: Any) -> None:
        super().__init__(*script)
        self.delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def generate_structured(self, messages):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            with self._lock:  # FakeLLM's script list is shared between threads
                return super().generate_structured(messages)
        finally:
            with self._lock:
                self.active -= 1


class ApiHarness:
    def __init__(self, app: Any, llm: Optional[FakeLLM] = None, *, max_retries: int = 1, concurrency: int = 4,
                 client_id: Optional[str] = None) -> None:
        from config import get_settings
        from routers.deps import get_chat_service

        self.app = app
        self.llm = llm or FakeLLM()
        config = PipelineConfig(top_k=5, min_score=0.5, max_retries=max_retries)
        self.pipeline = RAGPipeline(
            safety=SafetyValidator(),
            retriever=Retriever(FakeEmbedder(), make_store(), config),
            llm=self.llm,
            config=config,  # failure logging: the real default, into the test database
        )
        import dataclasses

        self.service = ChatService(self.pipeline, dataclasses.replace(get_settings(), chat_max_concurrency=concurrency))
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.client_id = client_id or str(uuid.uuid4())  # the same browser keeps its id across a server restart
        self.client = TestClient(app, headers={"X-Client-Id": self.client_id})
        self._extra: list[TestClient] = []

    def another_visitor(self) -> TestClient:
        """A second browser: same server, its own anonymous client id."""
        other = TestClient(self.app, headers={"X-Client-Id": str(uuid.uuid4())})
        self._extra.append(other)
        return other

    def close(self) -> None:
        from routers.deps import get_chat_service

        self.client.close()
        for other in self._extra:
            other.close()
        self.app.dependency_overrides.pop(get_chat_service, None)

    def script(self, *items: Any) -> "ApiHarness":
        self.llm.script.extend(items)
        return self

    def chat(self, question: str = CHICKEN_Q, conversation_id: Optional[str] = None):
        return self.client.post("/api/chat", json={"conversation_id": conversation_id, "question": question})

    # ── database inspection ──────────────────────────────────────────────────
    @staticmethod
    def rows(model: Any) -> list[Any]:
        with session_scope() as session:
            return list(session.scalars(select(model)))

    def conversations(self) -> list[Conversation]:
        return self.rows(Conversation)

    def messages(self) -> list[Message]:
        return sorted(self.rows(Message), key=lambda m: m.created_at)

    def failure_logs(self) -> list[FailureLog]:
        return self.rows(FailureLog)

    def stored_chunks(self) -> list[RetrievedChunk]:
        return self.rows(RetrievedChunk)
