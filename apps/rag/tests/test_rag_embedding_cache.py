"""La caché de embeddings: por huella y modelo, en disco, y sana tras un proceso muerto."""

from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pytest

from rag.embedding_cache import KEYS_FILENAME, VECTOR_BYTES, VECTORS_FILENAME, EmbeddingCache
from rag.embeddings import EMBEDDING_DIMENSIONS
from rag.errors import EmbeddingCacheError

MODEL = "multilingual-e5-small@614241f622f5-int8"
OTHER_MODEL = "multilingual-e5-small@614241f622f5-fp32"


def vectors(*seeds: int) -> np.ndarray:
    return np.vstack([np.full(EMBEDDING_DIMENSIONS, seed, dtype=np.float32) for seed in seeds])


def model_directory(root: Path) -> Path:
    (directory,) = root.iterdir()
    return directory


def test_an_empty_cache_misses_everything(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    assert len(cache) == 0
    assert cache.missing(["a", "b", "a"]) == ["a", "b"]


def test_vectors_come_back_by_key_in_the_order_asked(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    cache.add(["a", "b", "c"], vectors(1, 2, 3))
    assert cache.vectors(["c", "a"]).tolist() == vectors(3, 1).tolist()
    assert cache.missing(["a", "d"]) == ["d"]


def test_one_vector(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    cache.add(["a"], vectors(7))
    assert "a" in cache
    assert cache.vectors(["a"]).tolist() == vectors(7).tolist()


def test_the_cache_survives_reopening(tmp_path: Path):
    EmbeddingCache(tmp_path, MODEL).add(["a", "b"], vectors(1, 2))
    reopened = EmbeddingCache(tmp_path, MODEL)
    assert len(reopened) == 2
    assert reopened.vectors(["b"]).tolist() == vectors(2).tolist()


def test_each_model_has_its_own_vectors(tmp_path: Path):
    EmbeddingCache(tmp_path, MODEL).add(["a"], vectors(1))
    assert EmbeddingCache(tmp_path, OTHER_MODEL).missing(["a"]) == ["a"]


def test_a_known_or_repeated_key_is_stored_once(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    cache.add(["a"], vectors(1))
    cache.add(["a", "b", "b"], vectors(9, 2, 3))
    assert len(cache) == 2
    assert cache.vectors(["a", "b"]).tolist() == vectors(1, 2).tolist()
    reopened = EmbeddingCache(tmp_path, MODEL)
    assert reopened.vectors(["a", "b"]).tolist() == vectors(1, 2).tolist()


def test_vectors_without_their_key_are_dropped_on_open(tmp_path: Path):
    EmbeddingCache(tmp_path, MODEL).add(["a"], vectors(1))
    directory = model_directory(tmp_path)
    with (directory / VECTORS_FILENAME).open("ab") as stored:
        stored.write(vectors(2).tobytes()[: VECTOR_BYTES // 2])
    reopened = EmbeddingCache(tmp_path, MODEL)
    assert len(reopened) == 1
    assert (directory / VECTORS_FILENAME).stat().st_size == VECTOR_BYTES
    reopened.add(["b"], vectors(2))
    assert EmbeddingCache(tmp_path, MODEL).vectors(["a", "b"]).tolist() == vectors(1, 2).tolist()


def test_a_key_cut_in_half_is_dropped_on_open(tmp_path: Path):
    EmbeddingCache(tmp_path, MODEL).add(["aa"], vectors(1))
    directory = model_directory(tmp_path)
    with (directory / KEYS_FILENAME).open("a", encoding="ascii") as keys:
        keys.write("b")
    reopened = EmbeddingCache(tmp_path, MODEL)
    reopened.add(["c"], vectors(3))
    assert EmbeddingCache(tmp_path, MODEL).vectors(["aa", "c"]).tolist() == vectors(1, 3).tolist()


def test_a_matrix_of_another_shape_is_refused(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    with pytest.raises(EmbeddingCacheError):
        cache.add(["a", "b"], vectors(1))


def test_an_unknown_key_cannot_be_read(tmp_path: Path):
    cache = EmbeddingCache(tmp_path, MODEL)
    cache.add(["a"], vectors(1))
    with pytest.raises(KeyError):
        cache.vectors(["z"])


def test_a_complete_cache_is_read_from_a_read_only_directory(tmp_path: Path):
    EmbeddingCache(tmp_path, MODEL).add(["a"], vectors(1))
    directory = model_directory(tmp_path)
    for path in (*directory.iterdir(), directory, tmp_path):
        path.chmod(stat.S_IREAD | stat.S_IEXEC if path.is_dir() else stat.S_IREAD)
    try:
        assert EmbeddingCache(tmp_path, MODEL).vectors(["a"]).tolist() == vectors(1).tolist()
    finally:
        for path in (tmp_path, directory, *directory.iterdir()):
            path.chmod(stat.S_IRWXU)
