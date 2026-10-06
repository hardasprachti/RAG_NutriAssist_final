import math
import os

import numpy as np
import pytest

from integrations.embedder import QUERY_INSTRUCTION, Embedder, EmbedderError


class FakeTokenizer:
    def encode(self, text, add_special_tokens=True):
        ids = text.split()
        return ids if not add_special_tokens else ["[CLS]", *ids, "[SEP]"]


class FakeModel:
    """Deterministic stand-in for SentenceTransformer: records what it was asked to encode."""

    max_seq_length = 512
    tokenizer = FakeTokenizer()

    def __init__(self, dim=4):
        self.dim = dim
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, batch_size, normalize_embeddings, convert_to_numpy, show_progress_bar):
        self.calls.append(
            {"texts": list(texts), "batch_size": batch_size, "normalize": normalize_embeddings}
        )
        vectors = np.array([[len(t), sum(map(ord, t)) % 97, 1.0, 2.0][: self.dim] for t in texts], dtype=float)
        if normalize_embeddings:
            vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors


def make(model=None, dim=4):
    return Embedder("fake/model", dim, model=model or FakeModel(dim))


def test_query_gets_instruction_prefix_but_documents_do_not():
    model = FakeModel()
    embedder = make(model)
    embedder.embed_query("  how much salt?  ")
    embedder.embed_documents(["Adults should eat less than 5 g of salt."])
    assert model.calls[0]["texts"] == [QUERY_INSTRUCTION + "how much salt?"]
    assert model.calls[1]["texts"] == ["Adults should eat less than 5 g of salt."]


def test_vectors_are_normalised_floats_of_configured_dim():
    embedder = make()
    vector = embedder.embed_query("salt")
    assert len(vector) == 4
    assert all(type(x) is float for x in vector)
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)
    assert embedder._model.calls[0]["normalize"] is True


def test_embed_documents_batches_in_order_and_handles_empty():
    model = FakeModel()
    embedder = Embedder("fake/model", 4, model=model, batch_size=8)
    vectors = embedder.embed_documents(["a", "bb", "ccc"])
    assert len(vectors) == 3
    assert model.calls[0]["batch_size"] == 8
    assert vectors[1] == embedder.embed_documents(["bb"])[0]
    assert embedder.embed_documents([]) == []


@pytest.mark.parametrize("bad", ["", "   ", "\n"])
def test_empty_query_rejected(bad):
    with pytest.raises(ValueError):
        make().embed_query(bad)


def test_empty_document_rejected():
    with pytest.raises(ValueError):
        make().embed_documents(["fine", " "])


def test_dimension_mismatch_fails_loudly():
    with pytest.raises(EmbedderError, match="384"):
        Embedder("fake/model", 384, model=FakeModel(dim=4))


def test_count_tokens_excludes_special_tokens_and_max_seq_length():
    embedder = make()
    assert embedder.count_tokens("one two three") == 3
    assert embedder.max_seq_length == 512


def test_load_is_lazy_and_missing_dependency_is_reported(monkeypatch):
    embedder = Embedder("fake/model", 4)
    assert not embedder.is_loaded
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)  # force ImportError
    with pytest.raises(EmbedderError, match="sentence-transformers"):
        embedder.embed_query("hello")
    assert not embedder.is_loaded


def test_same_text_same_vector():
    embedder = make()
    assert embedder.embed_query("fibre") == embedder.embed_query("fibre")


# ── real model (opt-in: downloads ~130 MB and needs torch) ───────────────────

@pytest.mark.skipif(not os.getenv("RUN_MODEL_TESTS"), reason="set RUN_MODEL_TESTS=1 to load the real model")
def test_real_bge_small_model():
    embedder = Embedder("BAAI/bge-small-en-v1.5", 384)
    embedder.load()
    query = embedder.embed_query("How much saturated fat should I limit?")
    relevant, unrelated = embedder.embed_documents(
        [
            "Saturated fats should be less than 10% of total energy intake.",
            "Store raw chicken in the refrigerator at 40 degrees F or below.",
        ]
    )
    assert len(query) == 384
    assert math.isclose(sum(x * x for x in query), 1.0, rel_tol=1e-4)
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    assert dot(query, relevant) > dot(query, unrelated)
    assert embedder.max_seq_length == 512
