"""Batch-embed chunks with the backend's embedder (BAAI/bge-small-en-v1.5, normalised, no query prefix).

The backend ``Embedder`` is reused rather than re-implemented: ingestion and query time must share the
model, normalisation and tokenizer, or retrieval silently degrades.
"""

import logging
from typing import Sequence

from ingestion.chunker import RawChunk, embed_input

from integrations.embedder import Embedder, get_embedder  # noqa: E402  (backend on sys.path)

logger = logging.getLogger("ingestion.embed")


def token_counter(embedder: Embedder):
    """Token count under the embedding model's tokenizer (not tiktoken)."""
    return embedder.count_tokens


def over_limit(chunks: Sequence[RawChunk], embedder: Embedder, embed_section: bool = True) -> list[tuple[int, int]]:
    """(index, tokens) of every chunk whose embedded input exceeds the model's input limit.

    The model silently truncates such input, so the tail would never be searchable.
    """
    limit = embedder.max_seq_length
    found = []
    for i, chunk in enumerate(chunks):
        n = embedder.count_tokens(embed_input(chunk, embed_section)) + 2  # [CLS] and [SEP]
        if n > limit:
            found.append((i, n))
    return found


def embed_chunks(chunks: Sequence[RawChunk], embedder: Embedder, embed_section: bool = True) -> list[list[float]]:
    texts = [embed_input(c, embed_section) for c in chunks]
    logger.info("embedding %d chunks", len(texts))
    return embedder.embed_documents(texts)
