"""Embeddings de multilingual-e5-small con onnxruntime y el tokenizador de `tokenizers`.

La receta es la de la tarjeta del modelo: cada texto lleva su prefijo (`query: ` para una
pregunta, `passage: ` para un fragmento), se trunca a 512 tokens, la última capa se promedia
sobre los tokens reales (máscara de atención) y el promedio se normaliza a norma 1. Así el
producto punto es el coseno, que es la métrica del HNSW de `rag_chunks`.

Una pregunta en español y el fragmento en inglés que la responde quedan cerca: el modelo se
entrenó con pares en más de cien idiomas.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

import numpy as np
import numpy.typing as npt
import onnxruntime
from tokenizers import Encoding, Tokenizer

from .models import ModelFiles

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
MAX_TOKENS = 512
EMBEDDING_DIMENSIONS = 384
PAD_TOKEN = "<pad>"
# Con 32 secuencias de 512 tokens, la activación más grande (la atención: 32 por 12 cabezas
# por 512 al cuadrado) ocupa 400 MB en float32: acota la memoria de un lote.
DEFAULT_BATCH_SIZE = 32
# Una división por la norma de un vector nulo daría NaN; ninguna salida del modelo la tiene.
MIN_NORM = 1e-12
ONNX_PROVIDER = "CPUExecutionProvider"

Embeddings: TypeAlias = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class EmbedderSettings:
    """Cuántos hilos usa onnxruntime y cuántas secuencias van por llamada al modelo."""

    threads: int = os.cpu_count() or 1
    batch_size: int = DEFAULT_BATCH_SIZE


@dataclass(frozen=True, slots=True)
class PassageEmbeddings:
    """Los vectores de unos fragmentos, en su orden, y cuántos se truncaron a `MAX_TOKENS`."""

    vectors: Embeddings
    truncated: int


class Embedder(Protocol):
    """Lo que la ingesta y la búsqueda piden a un modelo de embeddings."""

    @property
    def model_id(self) -> str: ...

    def embed_query(self, question: str) -> Embeddings: ...

    def embed_passages(self, passages: Sequence[str]) -> PassageEmbeddings: ...


def mean_pooled(hidden: npt.NDArray[np.float32], mask: npt.NDArray[np.int64]) -> Embeddings:
    """El promedio de `hidden` (lote, tokens, dimensión) sobre los tokens con máscara 1."""
    weights = mask[..., np.newaxis].astype(np.float32)
    summed = (hidden * weights).sum(axis=1)
    return (summed / weights.sum(axis=1)).astype(np.float32)


def l2_normalized(vectors: Embeddings) -> Embeddings:
    """Cada fila dividida por su norma euclidiana."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, MIN_NORM)).astype(np.float32)


class E5Embedder:
    """multilingual-e5-small cargado en una sesión de onnxruntime.

    Es seguro llamarlo desde varios hilos: `InferenceSession.run` lo es, y el tokenizador
    se configura una sola vez al crearlo.
    """

    def __init__(self, files: ModelFiles, settings: EmbedderSettings) -> None:
        self._model_id = files.model_id
        self._batch_size = settings.batch_size
        self._tokenizer = _configured_tokenizer(files)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = settings.threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = onnxruntime.InferenceSession(
            str(files.model), sess_options=options, providers=[ONNX_PROVIDER]
        )
        self._input_names = {model_input.name for model_input in self._session.get_inputs()}

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed_query(self, question: str) -> Embeddings:
        """El vector (de `EMBEDDING_DIMENSIONS`, norma 1) de una pregunta."""
        vectors, _ = self._embed([QUERY_PREFIX + question])
        return vectors[0]

    def embed_passages(self, passages: Sequence[str]) -> PassageEmbeddings:
        """Los vectores de `passages`, en su orden, una fila por fragmento.

        Ordena los textos por largo antes de armar los lotes: un lote junta secuencias
        parecidas y casi no gasta cómputo en relleno. La memoria la acota quien llama con
        el largo de `passages`.
        """
        vectors, truncated = self._embed([PASSAGE_PREFIX + passage for passage in passages])
        return PassageEmbeddings(vectors, truncated)

    def tokens(self, text: str) -> list[int]:
        """Los identificadores de los tokens de `text` tal como entran al modelo."""
        return list(self._tokenizer.encode(text).ids)

    def _embed(self, texts: Sequence[str]) -> tuple[Embeddings, int]:
        encodings = self._tokenizer.encode_batch(list(texts))
        truncated = sum(1 for encoding in encodings if encoding.overflowing)
        by_length = sorted(range(len(texts)), key=lambda position: len(encodings[position]))
        vectors = np.empty((len(texts), EMBEDDING_DIMENSIONS), dtype=np.float32)
        for start in range(0, len(by_length), self._batch_size):
            positions = by_length[start : start + self._batch_size]
            vectors[positions] = self._run([encodings[position] for position in positions])
        return vectors, truncated

    def _run(self, batch: Sequence[Encoding]) -> Embeddings:
        width = max(len(encoding) for encoding in batch)
        pad_id = self._tokenizer.token_to_id(PAD_TOKEN)
        input_ids = np.full((len(batch), width), pad_id, dtype=np.int64)
        attention_mask = np.zeros((len(batch), width), dtype=np.int64)
        for row, encoding in enumerate(batch):
            input_ids[row, : len(encoding)] = encoding.ids
            attention_mask[row, : len(encoding)] = 1
        feed = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.zeros_like(input_ids),
        }
        (hidden,) = self._session.run(
            None, {name: value for name, value in feed.items() if name in self._input_names}
        )
        return l2_normalized(mean_pooled(hidden, attention_mask))


def _configured_tokenizer(files: ModelFiles) -> Tokenizer:
    tokenizer = Tokenizer.from_file(str(files.tokenizer))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    tokenizer.no_padding()
    return tokenizer
