"""El embedder ONNX contra la implementación de referencia (transformers y PyTorch).

`fixtures/e5_reference.json` tiene los tokens y los vectores que da la receta de la tarjeta
del modelo con la misma revisión; `fixtures/e5_reference.py` dice cómo se generaron.
"""

from __future__ import annotations

import numpy as np
import pytest
from rag_models_support import reference_items

from rag.embeddings import (
    EMBEDDING_DIMENSIONS,
    MAX_TOKENS,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    E5Embedder,
    l2_normalized,
    mean_pooled,
)
from rag.models import ModelVariant

# fp32 repite el cálculo de PyTorch salvo por el orden de las sumas en coma flotante.
FP32_TOLERANCE = 1e-5
# La cuantización dinámica a int8 mueve cada vector un poco: con los textos de la referencia,
# el coseno más bajo contra fp32 es 0,972.
INT8_MIN_COSINE = 0.96
# La cuantización dinámica fija la escala de las activaciones con todo el lote, así que en
# int8 el vector de un texto cambia un poco según con quién comparte el lote (coseno 0,999
# con estos textos); en fp32, no.
INT8_MIN_BATCH_COSINE = 0.995
LONG_TEXT = 4


def reference_vectors() -> np.ndarray:
    return np.array([item["vector"] for item in reference_items()], dtype=np.float32)


def reference_texts() -> list[str]:
    return [str(item["text"]) for item in reference_items()]


def without_prefix(text: str, prefix: str) -> str:
    assert text.startswith(prefix)
    return text.removeprefix(prefix)


def test_padding_does_not_change_the_mean():
    hidden = np.array([[[1.0, 2.0], [3.0, 4.0], [100.0, 100.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]], dtype=np.int64)
    assert mean_pooled(hidden, mask).tolist() == [[2.0, 3.0]]


def test_normalized_rows_have_unit_norm():
    vectors = l2_normalized(np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float32))
    assert np.allclose(vectors, [[0.6, 0.8], [0.0, 1.0]])


def test_a_zero_vector_stays_finite():
    assert np.isfinite(l2_normalized(np.zeros((1, 3), dtype=np.float32))).all()


def test_tokens_match_the_reference_tokenizer(embedder: E5Embedder):
    for item in reference_items():
        assert embedder.tokens(str(item["text"])) == item["input_ids"]


def test_a_long_passage_is_truncated_to_the_model_limit(embedder: E5Embedder):
    long_text = reference_texts()[LONG_TEXT]
    assert len(embedder.tokens(long_text)) == MAX_TOKENS
    passages = [without_prefix(text, PASSAGE_PREFIX) for text in reference_texts()[2:]]
    assert embedder.embed_passages(passages).truncated == 1


def test_vectors_match_the_reference(embedder: E5Embedder):
    texts = reference_texts()
    queries = [embedder.embed_query(without_prefix(text, QUERY_PREFIX)) for text in texts[:2]]
    passages = embedder.embed_passages([without_prefix(text, PASSAGE_PREFIX) for text in texts[2:]])
    vectors = np.vstack([*queries, passages.vectors])
    assert vectors.shape == (len(texts), EMBEDDING_DIMENSIONS)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-6)
    if embedder.model_id.endswith(ModelVariant.FP32):
        assert np.abs(vectors - reference_vectors()).max() < FP32_TOLERANCE
    else:
        assert (vectors * reference_vectors()).sum(axis=1).min() > INT8_MIN_COSINE


def test_batches_keep_the_order_of_the_passages(embedder: E5Embedder):
    passages = ["short", "a somewhat longer passage about indexes", "mid length text", "x"]
    together = embedder.embed_passages(passages).vectors
    alone = np.vstack([embedder.embed_passages([passage]).vectors for passage in passages])
    if embedder.model_id.endswith(ModelVariant.FP32):
        assert np.allclose(together, alone, atol=FP32_TOLERANCE)
    else:
        assert (together * alone).sum(axis=1).min() > INT8_MIN_BATCH_COSINE


@pytest.mark.parametrize(
    ("question", "answer", "unrelated"),
    [
        (
            "¿Cómo se calcula la relevancia de un documento con BM25?",
            "BM25 ranks documents by term frequency saturated with k1 and normalized by "
            "document length with b.",
            "The R-tree groups nearby points into minimum bounding rectangles.",
        ),
        (
            "¿Qué estructura agrupa puntos cercanos en rectángulos?",
            "The R-tree groups nearby points into minimum bounding rectangles.",
            "BM25 ranks documents by term frequency saturated with k1 and normalized by "
            "document length with b.",
        ),
    ],
)
def test_a_spanish_question_is_closest_to_its_english_answer(
    embedder: E5Embedder, question: str, answer: str, unrelated: str
):
    query = embedder.embed_query(question)
    passages = embedder.embed_passages([answer, unrelated]).vectors
    assert passages[0] @ query > passages[1] @ query
