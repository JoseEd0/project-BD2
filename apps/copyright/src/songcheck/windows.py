"""Las ventanas en que se corta una canción, iguales en el catálogo y en la consulta.

El catálogo guarda cada ventana de cada pista como una fila `pista.mp3#t=inicio,fin` (W3C
Media Fragments) y la detección (#76) consulta una canción con `POST /media/segments` con
las mismas `WINDOWING`: si las dos usaran otras, los vecinos de una ventana de la consulta
serían tramos de otra duración y el alineamiento por desfases no encajaría. Por eso viven
aquí, como constantes, y no en la configuración de cada proceso. La regla de corte es la del
gestor (`src/multimedia/segments.py`): la ventana `i` va de `i · salto` a `i · salto +
duración`, entran solo las que caben enteras y un audio más corto que una ventana es una
sola, del inicio al final. La justificación de 5 s y 2,5 s está en el README del servicio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SEGMENT_WINDOW_SECONDS = 5.0
SEGMENT_HOP_SECONDS = 2.5
# El audio más corto que el gestor acepta describir por segmentos (`min_segment_seconds`):
# un segundo da unas cien ventanas MFCC; con menos el histograma de palabras queda vacío.
MIN_SEGMENT_SECONDS = 1.0
FRAGMENT_SEPARATOR = "#"
TEMPORAL_PREFIX = "t="
# Como escribe el gestor un tramo: sin ceros de relleno y con las cifras de un `float`.
SECONDS_FORMAT = ".15g"


class WindowingError(ValueError):
    """La ventana o el salto no sirven para cortar un audio."""


@dataclass(frozen=True, slots=True)
class Windowing:
    """Duración de cada ventana y segundos entre el inicio de una y el de la siguiente.

    Raises:
        WindowingError: si la ventana es más corta que `MIN_SEGMENT_SECONDS` o el salto no
            es positivo.
    """

    window_seconds: float
    hop_seconds: float

    def __post_init__(self) -> None:
        if self.window_seconds < MIN_SEGMENT_SECONDS or self.hop_seconds <= 0:
            raise WindowingError(
                f"la ventana tiene que durar al menos {MIN_SEGMENT_SECONDS:g} s y el salto ser "
                f"positivo; llegaron {self.window_seconds:g} s y {self.hop_seconds:g} s"
            )


WINDOWING = Windowing(SEGMENT_WINDOW_SECONDS, SEGMENT_HOP_SECONDS)


@dataclass(frozen=True, slots=True)
class SegmentWindow:
    """Un tramo de una pista, en segundos desde su inicio."""

    start: float
    end: float

    def fragment(self) -> str:
        """El fragmento temporal sin el `#`: `t=2.5,7.5`."""
        return f"{TEMPORAL_PREFIX}{_seconds(self.start)},{_seconds(self.end)}"

    def of(self, path: str) -> str:
        """La ruta del tramo: `pista.mp3#t=2.5,7.5`."""
        return f"{path}{FRAGMENT_SEPARATOR}{self.fragment()}"


def segment_windows(duration: float, windowing: Windowing = WINDOWING) -> tuple[SegmentWindow, ...]:
    """Las ventanas de un audio de `duration` segundos; ninguna si dura menos que
    `MIN_SEGMENT_SECONDS`, porque el gestor no lo puede describir."""
    if duration < MIN_SEGMENT_SECONDS:
        return ()
    window, hop = windowing.window_seconds, windowing.hop_seconds
    if duration < window:
        return (SegmentWindow(0.0, duration),)
    count = math.floor((duration - window) / hop) + 1
    return tuple(SegmentWindow(index * hop, index * hop + window) for index in range(count))


def _seconds(value: float) -> str:
    return format(value, SECONDS_FORMAT)
