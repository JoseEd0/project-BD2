"""Un audio cortado en ventanas para consultarlo por segmentos.

La ventana `i` va de `i · salto` a `i · salto + duración` y solo entran las que caben
enteras: con 10 s y un salto de 5 s, una pista de 3 min da 35 ventanas, de 0-10 a 170-180.
Un audio más corto que una ventana (y no más corto que `min_segment_seconds`) es una sola
ventana, del inicio al final. El audio se decodifica una vez y cada ventana se recorta de
esas muestras, con el mismo cálculo que un `#t=inicio,fin` en la ruta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig

from .audio import DecodedAudio, audio_duration, decode_audio, describe_fragment
from .descriptors import Descriptors
from .errors import MediaError, UnsupportedMediaTypeError
from .fragments import TemporalFragment
from .media import MediaKind, media_kind_of


class SegmentLimitError(MediaError):
    """La consulta por segmentos pasa de un límite: la duración del audio, la ventana más
    corta o el número de ventanas."""


@dataclass(frozen=True, slots=True)
class SegmentedAudio:
    """Un audio decodificado y las ventanas en que se corta."""

    audio: DecodedAudio
    windows: tuple[TemporalFragment, ...]


def segment_windows(
    duration: float, window: float, hop: float, config: EngineConfig
) -> tuple[TemporalFragment, ...]:
    """Las ventanas de `window` segundos, cada `hop`, de un audio de `duration` segundos.

    Raises:
        SegmentLimitError: si la ventana o el salto no llegan a `min_segment_seconds`, si el
            audio es más corto que eso o más largo que `max_segment_audio_seconds`, o si
            salen más de `max_segments` ventanas.
    """
    shortest = config.min_segment_seconds
    if not (window >= shortest and hop > 0):
        raise SegmentLimitError(
            f"la ventana tiene que durar al menos {shortest:g} s y el salto ser positivo; "
            f"llegaron {window:g} s y {hop:g} s"
        )
    if duration > config.max_segment_audio_seconds:
        raise SegmentLimitError(
            f"el audio dura {duration:.1f} s y la consulta por segmentos admite hasta "
            f"{config.max_segment_audio_seconds:g} s"
        )
    if duration < shortest:
        raise SegmentLimitError(
            f"el audio dura {duration:.3f} s, menos que la ventana más corta ({shortest:g} s)"
        )
    if duration < window:
        return (TemporalFragment(0.0, duration),)
    count = math.floor((duration - window) / hop) + 1
    if count > config.max_segments:
        raise SegmentLimitError(
            f"{count} ventanas de {window:g} s cada {hop:g} s pasan del máximo de "
            f"{config.max_segments}: usa un salto mayor"
        )
    return tuple(TemporalFragment(i * hop, i * hop + window) for i in range(count))


def decode_segments(
    path: Path, window: float, hop: float, config: EngineConfig
) -> SegmentedAudio:
    """Decodifica una vez el audio de `path` y lo corta en ventanas.

    Los límites se comprueban con la duración de la cabecera, antes de decodificar; las
    ventanas salen de las muestras decodificadas.

    Raises:
        SegmentLimitError: si pasa de algún límite (ver `segment_windows`).
        UnsupportedMediaTypeError: si no es un audio.
        UnreadableMediaError: si el archivo no se puede leer o decodificar.
    """
    if media_kind_of(path) is not MediaKind.AUDIO:
        raise UnsupportedMediaTypeError(
            f"'{path.name}' no es un audio: solo un audio se consulta por segmentos"
        )
    segment_windows(audio_duration(path), window, hop, config)
    audio = decode_audio(path)
    return SegmentedAudio(audio, segment_windows(audio.duration, window, hop, config))


def describe_segments(segmented: SegmentedAudio, config: EngineConfig) -> list[Descriptors]:
    """Los descriptores MFCC de cada ventana, en orden."""
    return [describe_fragment(segmented.audio, window, config) for window in segmented.windows]
