"""Create (or verify) the Qdrant collection and its payload indexes.

Safe to re-run. Fails loudly if an existing collection has a different dimension or
distance than the configured embedding model expects.

    python -m scripts.init_vector_store
"""

import logging

from config import get_settings
from integrations.vector_store import VectorStore
from logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    store = VectorStore.from_settings(settings)
    store.ensure_collection()
    logger.info(
        "vector store ready",
        extra={
            "collection": store.collection_name,
            "dim": store.dim,
            "points": store.count(),
        },
    )


if __name__ == "__main__":
    main()
