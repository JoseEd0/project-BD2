"""La caché de embeddings y el cálculo por lotes, con una torre de mentira."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from apps.shop.tests.shop_support import image_bytes

from shop.clip import (
    EMBEDDING_DIMENSION,
    BatchSettings,
    EmbeddingCache,
    EmbeddingCacheError,
    PixelEncoder,
    fill_cache,
    image_digest,
)
from shop.clip.preprocessing import PixelValues

MODEL_SHA256 = "ab" * 32
SETTINGS = BatchSettings(batch_size=2, preprocess_workers=2, progress_every=2)


class CountingEncoder:
    """Un vector por foto que depende de sus píxeles, y cuántas fotos pasaron por la torre."""

    def __init__(self) -> None:
        self.embedded = 0

    def embed_pixels(self, batch: PixelValues) -> np.ndarray:
        self.embedded += len(batch)
        means = batch.reshape(len(batch), -1).mean(axis=1)
        vectors = np.zeros((len(batch), EMBEDDING_DIMENSION), dtype=np.float32)
        vectors[:, 0] = means
        return vectors


def vector(value: float) -> np.ndarray:
    filled = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
    filled[0] = value
    return filled


def photos(directory: Path, colors: list[tuple[int, int, int]]) -> list[Path]:
    paths = []
    for position, color in enumerate(colors):
        path = directory / f"{position}.png"
        path.write_bytes(image_bytes((40, 30), color))
        paths.append(path)
    return paths


def test_an_empty_cache_has_nothing(tmp_path: Path):
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        assert len(cache) == 0
        assert image_digest(b"x") not in cache


def test_vectors_survive_reopening(tmp_path: Path):
    digest = image_digest(b"una foto")
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        cache.put_all([(digest, vector(0.5))])
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        assert len(cache) == 1
        np.testing.assert_array_equal(cache.get(digest), vector(0.5))


def test_each_model_has_its_own_file(tmp_path: Path):
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        cache.put_all([(image_digest(b"foto"), vector(1.0))])
    with EmbeddingCache(tmp_path, "cd" * 32) as other:
        assert len(other) == 0


def test_a_record_cut_by_a_crash_is_dropped(tmp_path: Path):
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        cache.put_all([(image_digest(b"a"), vector(1.0)), (image_digest(b"b"), vector(2.0))])
        path = cache.path
    path.write_bytes(path.read_bytes()[:-10])
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        assert len(cache) == 1
        np.testing.assert_array_equal(cache.get(image_digest(b"a")), vector(1.0))
        cache.put_all([(image_digest(b"b"), vector(2.0))])
    with EmbeddingCache(tmp_path, MODEL_SHA256) as cache:
        np.testing.assert_array_equal(cache.get(image_digest(b"b")), vector(2.0))


def test_a_cache_used_outside_with_fails(tmp_path: Path):
    with pytest.raises(EmbeddingCacheError):
        EmbeddingCache(tmp_path, MODEL_SHA256).get(image_digest(b"a"))


def test_fill_cache_embeds_each_distinct_photo_once(tmp_path: Path):
    paths = photos(tmp_path, [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 255, 0), (9, 9, 9)])
    encoder = CountingEncoder()
    with EmbeddingCache(tmp_path / "cache", MODEL_SHA256) as cache:
        run = fill_cache([*paths, paths[1]], encoder, cache, SETTINGS)
        assert (run.images, run.computed, run.cached) == (5, 4, 1)
        assert encoder.embedded == 4
        assert run.digests[paths[0]] == run.digests[paths[2]]
        assert cache.get(run.digests[paths[0]])[0] != cache.get(run.digests[paths[1]])[0]


def test_a_second_fill_only_reads_the_cache(tmp_path: Path):
    paths = photos(tmp_path, [(255, 0, 0), (0, 0, 255), (0, 255, 0)])
    with EmbeddingCache(tmp_path / "cache", MODEL_SHA256) as cache:
        fill_cache(paths[:2], CountingEncoder(), cache, SETTINGS)
    encoder: PixelEncoder = CountingEncoder()
    with EmbeddingCache(tmp_path / "cache", MODEL_SHA256) as cache:
        run = fill_cache(paths, encoder, cache, SETTINGS)
    assert (run.computed, run.cached) == (1, 2)
