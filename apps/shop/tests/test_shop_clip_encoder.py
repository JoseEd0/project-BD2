"""Las dos torres de CLIP en onnxruntime: forma, norma, lotes y que texto e imagen caigan en
el mismo espacio."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from apps.shop.tests.shop_support import disc, pattern_image

from shop.clip import (
    EMBEDDING_DIMENSION,
    ClipImageEncoder,
    ClipTextEncoder,
    UnreadableImageError,
    load_image,
)

COLORS = ("red", "blue", "green")
BATCH_TOLERANCE = 1e-4


def test_image_embeddings_have_unit_norm(image_encoder: ClipImageEncoder):
    embeddings = image_encoder.embed([pattern_image(320, 200), pattern_image(100, 60)])
    assert embeddings.shape == (2, EMBEDDING_DIMENSION)
    assert embeddings.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(embeddings, axis=1), 1.0, rtol=1e-5)


def test_an_image_gets_the_same_embedding_alone_or_in_a_batch(image_encoder: ClipImageEncoder):
    alone = image_encoder.embed([disc("red")])
    batched = image_encoder.embed([disc("blue"), disc("red")])
    np.testing.assert_allclose(batched[1], alone[0], atol=BATCH_TOLERANCE)


def test_padding_does_not_change_a_text_embedding(text_encoder: ClipTextEncoder):
    alone = text_encoder.embed(["a red circle"])
    padded = text_encoder.embed(["a red circle", "a long description of a big blue circle"])
    np.testing.assert_allclose(padded[0], alone[0], atol=BATCH_TOLERANCE)


def test_each_disc_is_closest_to_the_text_of_its_color(
    image_encoder: ClipImageEncoder, text_encoder: ClipTextEncoder
):
    images = image_encoder.embed([disc(color) for color in COLORS])
    texts = text_encoder.embed([f"a {color} circle" for color in COLORS])
    similarity = images @ texts.T
    assert similarity.argmax(axis=1).tolist() == list(range(len(COLORS)))
    assert similarity.argmax(axis=0).tolist() == list(range(len(COLORS)))


def test_a_file_that_is_not_an_image_is_rejected(tmp_path: Path):
    broken = tmp_path / "foto.jpg"
    broken.write_bytes(b"no es un jpeg")
    with pytest.raises(UnreadableImageError, match=r"foto\.jpg"):
        load_image(broken)
