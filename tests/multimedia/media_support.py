"""Imágenes y audios generados en el test: ningún test depende de un dataset."""

from pathlib import Path

import cv2
import numpy as np
import soundfile

SEED = 7
TEXTURE_SIDE = 160
# Desenfocar el ruido deja manchas de unos pocos píxeles: esquinas y bordes a varias
# escalas, que es lo que SIFT detecta.
TEXTURE_BLUR_SIGMA = 2.0
FLAT_GRAY = 128
SAMPLE_RATE = 16_000
TONE_HZ = 440.0
TONE_AMPLITUDE = 0.5
CHIRP_START_HZ = 200.0
CHIRP_END_HZ = 4_000.0


def textured_pixels(height: int, width: int, seed: int = SEED) -> np.ndarray:
    noise = np.random.default_rng(seed).integers(0, 256, (height, width)).astype(np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), TEXTURE_BLUR_SIGMA)


def write_image(path: Path, pixels: np.ndarray) -> Path:
    assert cv2.imwrite(str(path), pixels)
    return path


def tone(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    times = np.arange(round(seconds * sample_rate)) / sample_rate
    return (TONE_AMPLITUDE * np.sin(2 * np.pi * TONE_HZ * times)).astype(np.float32)


def chirp(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Un barrido de frecuencia: cada ventana suena distinta, así que un segmento mal
    recortado da otros coeficientes."""
    times = np.arange(round(seconds * sample_rate)) / sample_rate
    rate = (CHIRP_END_HZ - CHIRP_START_HZ) / seconds
    phase = 2 * np.pi * (CHIRP_START_HZ * times + rate * times**2 / 2)
    return (TONE_AMPLITUDE * np.sin(phase)).astype(np.float32)


def write_audio(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    soundfile.write(path, samples, sample_rate)
    return path
