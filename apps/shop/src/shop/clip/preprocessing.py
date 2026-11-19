"""De una imagen a la entrada de la torre de imagen de CLIP: `float32` de 3 x 224 x 224.

Repite paso a paso el `CLIPImageProcessor` de Hugging Face con la configuración que
publica el modelo (`preprocessor_config.json`), que a su vez reproduce el preprocesado con
que OpenAI entrenó CLIP:

1. RGB;
2. el lado menor a 224 px con interpolación bicúbica, y el mayor en proporción, truncado;
3. el recorte central de 224 x 224;
4. de 0..255 a 0..1 y normalizado con la media y la desviación de cada canal.

Una entrada distinta de la del entrenamiento no falla: da embeddings peores sin avisar. Por
eso los tests comparan con valores de referencia calculados con el procesador de Hugging
Face.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from PIL import Image

PixelValues: TypeAlias = npt.NDArray[np.float32]

IMAGE_SIDE = 224
RGB = "RGB"
MAX_8_BIT = 255.0
# Media y desviación de cada canal RGB en los datos de entrenamiento de CLIP (OpenAI).
CHANNEL_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CHANNEL_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def pixel_values(image: Image.Image) -> PixelValues:
    """La imagen como la ve CLIP: un arreglo `(3, 224, 224)` en `float32`, canales primero."""
    resized = image.convert(RGB).resize(_resized_size(image.size), Image.Resampling.BICUBIC)
    pixels = np.asarray(_center_crop(resized), dtype=np.float32) / np.float32(MAX_8_BIT)
    normalized = (pixels - CHANNEL_MEAN) / CHANNEL_STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)


def _resized_size(size: tuple[int, int]) -> tuple[int, int]:
    """El lado menor a `IMAGE_SIDE` y el mayor en proporción, truncado como en Hugging Face."""
    width, height = size
    if width <= height:
        return IMAGE_SIDE, int(IMAGE_SIDE * height / width)
    return int(IMAGE_SIDE * width / height), IMAGE_SIDE


def _center_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    left = (width - IMAGE_SIDE) // 2
    top = (height - IMAGE_SIDE) // 2
    return image.crop((left, top, left + IMAGE_SIDE, top + IMAGE_SIDE))
