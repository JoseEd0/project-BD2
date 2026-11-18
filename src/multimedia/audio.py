"""Descriptores MFCC de un audio, con libsndfile (lectura) y librosa (remuestreo y MFCC).

Cada llamada a esas bibliotecas pasa por una función de este módulo con tipos de NumPy,
para que el resto del paquete no dependa de su API.

Un tramo se describe de dos maneras que dan las mismas muestras: leyendo solo ese tramo del
archivo (`extract_mfcc`), o recortándolo de un audio decodificado entero una vez
(`decode_audio` y `describe_fragment`), que es lo que conviene cuando del mismo archivo
salen muchos tramos. En los dos caminos el tramo se recorta en la frecuencia original, se
mezcla a mono y solo entonces se remuestrea, con el mismo cálculo de muestras.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

import librosa
import numpy as np
import numpy.typing as npt
import soundfile

from config import EngineConfig

from .descriptors import DESCRIPTOR_DTYPE, Descriptors, no_descriptors
from .errors import FragmentOutOfRangeError, UnreadableMediaError
from .fragments import TemporalFragment

# Velocidad y aceleración de cada coeficiente: el descriptor clásico de 39 dimensiones del
# reconocimiento de voz, que distingue un sonido que sube de uno que baja con el mismo
# timbre medio.
MFCC_DELTA_ORDERS = (1, 2)
# El modo por omisión de librosa («interp») falla con menos ventanas que el ancho de la
# derivada, es decir, con un audio de menos de 90 ms; «nearest» repite el borde.
MFCC_DELTA_EDGE_MODE: Final = "nearest"
# El remuestreo por omisión de `librosa.load`, con el que se midió la Parte 4.
RESAMPLING_METHOD: Final = "soxr_hq"
WHOLE_FILE = -1

Signal: TypeAlias = npt.NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    """Un audio decodificado entero: mezclado a mono y en su frecuencia original."""

    name: str
    samples: Signal
    sample_rate: int

    @property
    def duration(self) -> float:
        """Segundos del audio."""
        return len(self.samples) / self.sample_rate


def extract_mfcc(
    path: Path, config: EngineConfig, fragment: TemporalFragment | None = None
) -> Descriptors:
    """Los descriptores MFCC del audio de `path`: una fila por ventana, `(n, 3c)` en
    `float32`, con los `c = config.mfcc_coefficients` coeficientes seguidos de sus
    primeras y sus segundas derivadas.

    Se describe el tramo `fragment` si se da; si no, el segmento que empieza en
    `config.audio_offset_seconds` y dura `config.audio_duration_seconds`. Solo se decodifica
    ese tramo, mezclado a mono y remuestreado a `config.audio_sample_rate`. Un segmento
    vacío (un archivo sin muestras o un inicio después del final) da `(0, 3c)`.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es un audio.
        FragmentOutOfRangeError: si `fragment` empieza después del final del audio.
    """
    return mfcc_of(_read_range(path, config, fragment), config)


def decode_audio(path: Path) -> DecodedAudio:
    """El audio entero de `path`, decodificado una vez, mezclado a mono y sin remuestrear.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es un audio.
    """
    try:
        with soundfile.SoundFile(path) as sound:
            frames = sound.read(frames=WHOLE_FILE, dtype=DESCRIPTOR_DTYPE, always_2d=False)
            return DecodedAudio(path.name, _mono(frames), sound.samplerate)
    except (soundfile.SoundFileError, OSError) as error:
        raise _unreadable(path, error) from error


def audio_duration(path: Path) -> float:
    """Segundos del audio de `path`, leídos de su cabecera, sin decodificarlo.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es un audio.
    """
    try:
        with soundfile.SoundFile(path) as sound:
            return float(sound.frames / sound.samplerate)
    except (soundfile.SoundFileError, OSError) as error:
        raise _unreadable(path, error) from error


def describe_fragment(
    audio: DecodedAudio, fragment: TemporalFragment | None, config: EngineConfig
) -> Descriptors:
    """Lo mismo que `extract_mfcc` sobre el tramo `fragment` (o el segmento de la
    configuración), recortado de un audio ya decodificado.

    Raises:
        FragmentOutOfRangeError: si `fragment` empieza después del final del audio.
    """
    first, count = _frame_range(
        fragment, config, audio.sample_rate, len(audio.samples), audio.name
    )
    native = audio.samples[first : first + count]
    return mfcc_of(_resampled(native, audio.sample_rate, config), config)


def mfcc_of(signal: Signal, config: EngineConfig) -> Descriptors:
    """Los descriptores MFCC de una señal mono a `config.audio_sample_rate`; `(0, 3c)` si
    no tiene muestras."""
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


def _read_range(path: Path, config: EngineConfig, fragment: TemporalFragment | None) -> Signal:
    """Se abre con libsndfile antes de dárselo a librosa para que un archivo dañado dé su
    error en vez de pasar al lector de respaldo de librosa, distinto entre versiones."""
    try:
        with soundfile.SoundFile(path) as sound:
            first, count = _frame_range(
                fragment, config, sound.samplerate, sound.frames, path.name
            )
            if not count:
                return np.empty(0, dtype=DESCRIPTOR_DTYPE)
            if first:
                sound.seek(first)
            native = _mono(sound.read(frames=count, dtype=DESCRIPTOR_DTYPE, always_2d=False))
            return _resampled(native, sound.samplerate, config)
    except (soundfile.SoundFileError, OSError) as error:
        raise _unreadable(path, error) from error


def _frame_range(
    fragment: TemporalFragment | None,
    config: EngineConfig,
    sample_rate: int,
    total: int,
    name: str,
) -> tuple[int, int]:
    """La primera muestra del tramo y cuántas tiene, en la frecuencia original.

    Sin fragmento es el segmento de la configuración, contado como `librosa.load` cuenta su
    `offset` y su `duration`. Un fragmento que termina después del final llega hasta el
    final, como pide Media Fragments; uno que empieza en el final o después es un error.
    """
    if fragment is None:
        first = int(config.audio_offset_seconds * sample_rate)
        duration = config.audio_duration_seconds
        wanted = total if duration is None else int(duration * sample_rate)
        return first, max(0, min(wanted, total - first))
    first = int(fragment.start * sample_rate)
    if first >= total:
        raise FragmentOutOfRangeError(
            f"el fragmento '#{fragment}' empieza después del final de '{name}', que dura "
            f"{total / sample_rate:.3f} s"
        )
    end = total if fragment.end is None else min(int(fragment.end * sample_rate), total)
    return first, max(0, end - first)


def _mono(frames: npt.NDArray[np.float32]) -> Signal:
    """Como `librosa.load`: un canal por fila y la media de los canales."""
    if not frames.size:
        return np.empty(0, dtype=DESCRIPTOR_DTYPE)
    mono: Signal = librosa.to_mono(frames.T)
    return mono


def _resampled(native: Signal, sample_rate: int, config: EngineConfig) -> Signal:
    if not native.size:
        return native
    resampled: Signal = librosa.resample(
        native,
        orig_sr=sample_rate,
        target_sr=config.audio_sample_rate,
        res_type=RESAMPLING_METHOD,
    )
    return resampled


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


def _unreadable(path: Path, error: Exception) -> UnreadableMediaError:
    return UnreadableMediaError(f"no se puede leer el audio '{path.name}': {error}")
