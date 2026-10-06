"""Show a few stored chunk embeddings: metadata, text preview and the actual vector.

    python -m scripts.show_embeddings                          # 3 chunks, first 8 vector values each
    python -m scripts.show_embeddings -n 5 --values 16
    python -m scripts.show_embeddings --document "The Eatwell Guide"
    python -m scripts.show_embeddings --chunk-id who_healthy_diet_chunk_007 --full
    python -m scripts.show_embeddings --verify                 # re-embed each text, compare with the stored vector

The vectors live in Qdrant (QDRANT_URL; if unset, the embedded store ingestion wrote to
ingestion/data/qdrant). Each chunk was embedded as "<section>\\n<text>" with BAAI/bge-small-en-v1.5 and
L2-normalised, so every stored vector has length 1 and cosine similarity is a plain dot product.
"""

import argparse
import math
import sys
import textwrap
from pathlib import Path
from typing import Optional, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _open_store(qdrant_path: Optional[str]):
    from config import get_settings
    from integrations.vector_store import VectorStore

    settings = get_settings()
    if qdrant_path or not settings.qdrant_url:
        from qdrant_client import QdrantClient

        path = qdrant_path or str(REPO_ROOT / "ingestion" / "data" / "qdrant")
        if not Path(path).exists():
            raise SystemExit(f"no local vector store at {path}; run python -m ingestion.run_ingestion first")
        print(f"vector store: embedded Qdrant at {path}")
        return VectorStore(QdrantClient(path=path), settings.qdrant_collection_name, settings.embedding_dim)
    print(f"vector store: {settings.qdrant_url}")
    return VectorStore.from_settings(settings)


def _show(chunk, vector: Sequence[float], values: int, full: bool, verified: Optional[float]) -> None:
    print("=" * 100)
    print(f"chunk_id   : {chunk.chunk_id}  (chunk {chunk.chunk_index + 1} of {chunk.total_chunks})")
    print(f"document   : {chunk.document_name} | {chunk.publisher} | {chunk.year}")
    print(f"section    : {chunk.section}")
    print(f"source_url : {chunk.source_url}   (retrieved {chunk.retrieval_date})")
    preview = chunk.text if full else textwrap.shorten(chunk.text.replace("\n", " "), width=300, placeholder=" ...")
    print("text       :")
    print(textwrap.indent(textwrap.fill(preview, 94) if not full else chunk.text, "    "))
    shown = list(vector) if full else list(vector[:values])
    body = ", ".join(f"{x:+.4f}" for x in shown)
    print(f"embedding  : {len(vector)} dimensions, L2 norm {_norm(vector):.6f}, "
          f"min {min(vector):+.4f}, max {max(vector):+.4f}")
    print(f"  {'all values' if full else f'first {len(shown)} values'}: [{body}{'' if full else ', ...'}]")
    if verified is not None:
        verdict = "matches" if verified > 0.999 else "DIFFERS FROM"
        print(f"  re-embedded text {verdict} the stored vector (cosine similarity {verified:.6f})")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", "--limit", type=int, default=3, help="how many chunks to show (default 3)")
    parser.add_argument("--values", type=int, default=8, help="vector values to print per chunk (default 8)")
    parser.add_argument("--full", action="store_true", help="print the whole text and all vector values")
    parser.add_argument("--document", help="only chunks of this document_name")
    parser.add_argument("--chunk-id", action="append", default=[], help="show this exact chunk (repeatable)")
    parser.add_argument("--qdrant-path", help="embedded Qdrant directory (default: QDRANT_URL, else ingestion/data/qdrant)")
    parser.add_argument("--verify", action="store_true",
                        help="re-embed each chunk and compare with its stored vector (loads the embedding model)")
    args = parser.parse_args(argv)

    store = _open_store(args.qdrant_path)
    print(f"collection '{store.collection_name}' holds {store.count()} chunks, {store.dim}-dimensional, cosine distance")
    rows = store.sample(limit=args.limit, document_filter=args.document, chunk_ids=args.chunk_id)
    if not rows:
        print("no matching chunks")
        return 1

    embedder = None
    if args.verify:
        from integrations.embedder import get_embedder

        embedder = get_embedder()
        embedder.load()

    for chunk, vector in rows:
        similarity = None
        if embedder is not None:
            # Ingestion embedded "<section>\n<text>" (documents get no query prefix).
            fresh = embedder.embed_documents([f"{chunk.section}\n{chunk.text}"])[0]
            similarity = _dot(fresh, vector)
        _show(chunk, vector, args.values, args.full, similarity)

    if len(rows) > 1:
        print("=" * 100)
        print("cosine similarity between the shown chunks (stored vectors are unit length, so this is a dot product):")
        for i, (a, va) in enumerate(rows):
            for j in range(i + 1, len(rows)):
                b, vb = rows[j]
                print(f"  {a.chunk_id}  vs  {b.chunk_id}: {_dot(va, vb):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
