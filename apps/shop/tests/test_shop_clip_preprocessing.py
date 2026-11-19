"""El preprocesado de CLIP contra el de Hugging Face.

Los valores de referencia salen de `CLIPImageProcessor` (transformers 4.57.1, Pillow 13.0.0)
con la configuración del modelo fijado, sobre las mismas imágenes de `pattern_image`: la
media de cada canal y cinco píxeles `(canal, fila, columna)` de la salida.
"""

from __future__ import annotations

import numpy as np
import pytest
from apps.shop.tests.shop_support import pattern_image

from shop.clip import IMAGE_SIDE, pixel_values

SAMPLED_PIXELS = ((0, 0, 0), (0, 111, 112), (1, 223, 223), (2, 50, 200), (1, 7, 190))
TOLERANCE = 1e-5
REFERENCES = {
    (320, 200): (
        (0.061738, 0.153956, 0.333385),
        (-1.106137, 0.061738, 2.074884, 1.392234, -1.647043),
    ),
    (200, 320): (
        (0.061803, 0.153889, 0.332011),
        (-1.792263, 0.076336, 1.354511, -0.783437, -0.971693),
    ),
    (100, 60): (
        (0.05685, 0.15409, 0.335562),
        (-1.07694, 0.061738, 2.074884, 0.922971, -1.66205),
    ),
    (224, 224): (
        (0.061803, 0.153956, 0.332113),
        (-1.792263, 0.076336, 2.074884, -1.45178, -1.632035),
    ),
}


@pytest.mark.parametrize("size", list(REFERENCES), ids=lambda size: f"{size[0]}x{size[1]}")
def test_pixels_match_the_hugging_face_processor(size: tuple[int, int]):
    channel_means, pixels = REFERENCES[size]
    values = pixel_values(pattern_image(*size))
    assert values.shape == (3, IMAGE_SIDE, IMAGE_SIDE)
    assert values.dtype == np.float32
    np.testing.assert_allclose(values.mean(axis=(1, 2)), channel_means, atol=TOLERANCE)
    np.testing.assert_allclose([values[at] for at in SAMPLED_PIXELS], pixels, atol=TOLERANCE)


def test_grayscale_and_transparency_become_rgb():
    gray = pattern_image(320, 200).convert("L")
    transparent = pattern_image(320, 200).convert("RGBA")
    assert pixel_values(gray).shape == (3, IMAGE_SIDE, IMAGE_SIDE)
    np.testing.assert_array_equal(
        pixel_values(transparent), pixel_values(pattern_image(320, 200))
    )
