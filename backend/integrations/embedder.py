"""Local embedding model (BAAI/bge-small-en-v1.5 via sentence-transformers).

Ingestion and query time MUST use the same model and normalisation, otherwise retrieval
silently degrades. Both therefore go through this class:

* vectors are L2-normalised (matches the cosine collection),
* queries get the model's retrieval instruction prefix, documents never do,
* the model is loaded once per process (``get_embedder().load()`` at app startup).
"""

import logging
import threading
from functools import lru_cache
from typing import Any, Optional, Sequence

from config import Settings, get_settings

logger = logging.getLogger(__name__)

# Recommended by the BGE authors for short query -> passage retrieval.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbedderError(RuntimeError):
    pass


class Embedder:
    def __init__(
        self,
        model_name: str,
        dim: int,
        *,
        model: Any = None,
        batch_size: int = 32,
        query_instruction: str = QUERY_INSTRUCTION,
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self._model = model
        self._lock = threading.Lock()
        self._vocabulary: Optional[frozenset[str]] = None
        if model is not None:
            self._check_dim(model)

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "Embedder":
        settings = settings or get_settings()
        return cls(settings.embedding_model, settings.embedding_dim)

    # ── model lifecycle ──────────────────────────────────────────────────────
    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load the model if it is not loaded yet. Idempotent and thread-safe."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbedderError(
                    "sentence-transformers is not installed (pip install -r requirements.txt)"
                ) from exc
            logger.info("loading embedding model", extra={"model": self.model_name})
            try:
                model = SentenceTransformer(self.model_name)
            except Exception as exc:
                raise EmbedderError(f"could not load embedding model {self.model_name!r}: {exc}") from exc
            self._check_dim(model)
            self._model = model
            logger.info("embedding model ready", extra={"model": self.model_name, "dim": self.dim})

    def _check_dim(self, model: Any) -> None:
        # Renamed in sentence-transformers 5; keep working on older versions.
        get_dim = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
        actual = get_dim()
        if actual != self.dim:
            raise EmbedderError(
                f"{self.model_name} produces {actual}-dim vectors but EMBEDDING_DIM={self.dim}; "
                "the vector collection must match the model"
            )

    def _loaded_model(self) -> Any:
        self.load()
        return self._model

    # ── embedding ────────────────────────────────────────────────────────────
    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._loaded_model().encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in v] for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a user question (with the retrieval instruction prefix)."""
        if not text or not text.strip():
            raise ValueError("cannot embed an empty query")
        return self._encode([self.query_instruction + text.strip()])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for indexing (no instruction prefix)."""
        if not texts:
            return []
        if any(not t or not t.strip() for t in texts):
            raise ValueError("cannot embed an empty document")
        return self._encode([t.strip() for t in texts])

    # ── tokenizer helpers (ingestion sizes chunks with the model's own tokenizer) ──
    @property
    def max_seq_length(self) -> int:
        return int(self._loaded_model().max_seq_length)

    def knows_word(self, word: str) -> bool:
        """Whether ``word`` is a whole word of the model's own vocabulary, i.e. ordinary English ("capital",
        "chicken") rather than something it has to spell out in pieces ("chiken", "frige")."""
        if self._vocabulary is None:
            self._vocabulary = frozenset(self._loaded_model().tokenizer.get_vocab())
        return word.lower() in self._vocabulary

    def count_tokens(self, text: str) -> int:
        """Token count under this model's tokenizer, excluding special tokens."""
        tokenizer = self._loaded_model().tokenizer
        return len(tokenizer.encode(text, add_special_tokens=False))


@lru_cache
def get_embedder() -> Embedder:
    """Process-wide embedder (model loaded lazily; call ``.load()`` at startup)."""
    return Embedder.from_settings()
