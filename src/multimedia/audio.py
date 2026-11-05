"""Descriptores MFCC de un audio, con libsndfile (lectura) y librosa (remuestreo y MFCC).

Cada llamada a esas bibliotecas pasa por una función de este módulo con tipos de NumPy,
para que el resto del paquete no dependa de su API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, TypeAlias

import librosa
import numpy as np
import numpy.typing as npt
import soundfile

from config import EngineConfig

from .descriptors import DESCRIPTOR_DTYPE, Descriptors, no_descriptors
from .errors import UnreadableMediaError

# Velocidad y aceleración de cada coeficiente: el descriptor clásico de 39 dimensiones del
# reconocimiento de voz, que distingue un sonido que sube de uno que baja con el mismo
# timbre medio.
MFCC_DELTA_ORDERS = (1, 2)
# El modo por omisión de librosa («interp») falla con menos ventanas que el ancho de la
# derivada, es decir, con un audio de menos de 90 ms; «nearest» repite el borde.
MFCC_DELTA_EDGE_MODE: Final = "nearest"

Signal: TypeAlias = npt.NDArray[np.float32]


def extract_mfcc(path: Path, config: EngineConfig) -> Descriptors:
    """Los descriptores MFCC del audio de `path`: una fila por ventana, `(n, 3c)` en
    `float32`, con los `c = config.mfcc_coefficients` coeficientes seguidos de sus
    primeras y sus segundas derivadas.

    Se describe el segmento que empieza en `config.audio_offset_seconds` y dura
    `config.audio_duration_seconds`, mezclado a mono y remuestreado a
    `config.audio_sample_rate`. Un segmento vacío (un archivo sin muestras o un inicio
    después del final) da `(0, 3c)`.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es un audio.
    """
    signal = _read_segment(path, config)
    if not signal.size:
        return no_descriptors(mfcc_dimension(config))
    coefficients = _mfcc(signal, config)
    derivatives = [
        librosa.feature.delta(
            coefficients, width=config.mfcc_delta_width, order=order, mode=MFCC_DELTA_EDGE_MODE
        )
        for order in MFCC_DELTA_ORDERS
    ]
    frames: Descriptors = np.ascontiguousarray(
        np.vstack([coefficients, *derivatives]).T, dtype=DESCRIPTOR_DTYPE
    )
    return frames


def mfcc_dimension(config: EngineConfig) -> int:
    """Columnas de los descriptores de audio con `config`."""
    return config.mfcc_coefficients * (1 + len(MFCC_DELTA_ORDERS))


def _read_segment(path: Path, config: EngineConfig) -> Signal:
    """Se abre con libsndfile antes de dárselo a librosa para que un archivo dañado dé su
    error en vez de pasar al lector de respaldo de librosa, distinto entre versiones."""
    try:
        with soundfile.SoundFile(path) as sound:
            if config.audio_offset_seconds * sound.samplerate >= sound.frames:
                return np.empty(0, dtype=DESCRIPTOR_DTYPE)
            signal: Signal
            signal, _ = librosa.load(
                sound,
                sr=config.audio_sample_rate,
                mono=True,
                offset=config.audio_offset_seconds,
                duration=config.audio_duration_seconds,
                dtype=DESCRIPTOR_DTYPE,
            )
    except (soundfile.SoundFileError, OSError) as error:
        raise UnreadableMediaError(f"no se puede leer el audio '{path.name}': {error}") from error
    return signal


def _mfcc(signal: Signal, config: EngineConfig) -> npt.NDArray[np.float32]:
    coefficients: npt.NDArray[np.float32] = librosa.feature.mfcc(
        y=signal,
        sr=config.audio_sample_rate,
        n_mfcc=config.mfcc_coefficients,
        n_fft=config.mfcc_fft_length,
        win_length=config.mfcc_window_length,
        hop_length=config.mfcc_hop_length,
    )
    return coefficients
