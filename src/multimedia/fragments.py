"""El tramo de un audio escrito en su ruta, con la sintaxis temporal de W3C Media Fragments.

`pista.mp3#t=10,15` es el tramo de la pista que va del segundo 10 al 15 (Media Fragments URI
1.0, §4.2.1). Como en una URL, el `#` separa la ruta del fragmento. Se admite el formato NPT
(segundos desde el inicio), el único que tiene sentido en un archivo:

| Fragmento | Tramo |
|---|---|
| `#t=10,15` | del segundo 10 al 15 |
| `#t=10` | del segundo 10 al final |
| `#t=,15` | del inicio al segundo 15 |
| `#t=npt:10,15` | igual que `#t=10,15`, con el formato nombrado |
| `#t=1:30,2:00.5` | minutos y segundos (`mm:ss`), o `h:mm:ss`, con decimales |

Se rechazan, cada uno con su error: los formatos SMPTE y `clock:` (fotogramas y fechas de
reloj), las otras dimensiones (`xywh`, `track`, `id`), varias juntas con `&` y un tramo
vacío, que empieza donde termina o después.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import EmptyFragmentError, MalformedFragmentError

FRAGMENT_SEPARATOR = "#"
TEMPORAL_PREFIX = "t="
NPT_FORMAT_PREFIX = "npt:"
RANGE_SEPARATOR = ","
DIMENSION_SEPARATOR = "&"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
# npt-sec = 1*DIGIT ["." *DIGIT]; npt-mmss = mm ":" ss; npt-hhmmss = 1*DIGIT ":" mm ":" ss,
# con minutos y segundos de dos cifras entre 00 y 59 y decimales opcionales en los segundos.
NPT_SECONDS = re.compile(r"(\d+(?:\.\d*)?)")
NPT_CLOCK = re.compile(r"(?:(\d+):)?([0-5]\d):([0-5]\d(?:\.\d*)?)")
# Sin ceros de relleno («10» y no «10.0») y con todas las cifras que un `float` conserva.
SECONDS_FORMAT = ".15g"


@dataclass(frozen=True, slots=True)
class TemporalFragment:
    """Un tramo en segundos desde el inicio del archivo; `end = None` llega hasta el final."""

    start: float = 0.0
    end: float | None = None

    def __str__(self) -> str:
        end = "" if self.end is None else f"{RANGE_SEPARATOR}{_seconds_text(self.end)}"
        return f"{TEMPORAL_PREFIX}{_seconds_text(self.start)}{end}"


@dataclass(frozen=True, slots=True)
class MediaReference:
    """Un archivo multimedia y, si se pidió, el tramo de él que se describe."""

    path: Path
    fragment: TemporalFragment | None = None


def split_fragment(text: str) -> tuple[str, TemporalFragment | None]:
    """La ruta sin el fragmento y el tramo que pide; `None` si la ruta no lleva `#`.

    Raises:
        MalformedFragmentError: si lo que sigue al `#` no es un `t=` que se pueda leer.
        EmptyFragmentError: si el tramo empieza donde termina o después.
    """
    path, separator, fragment = text.partition(FRAGMENT_SEPARATOR)
    if not separator:
        return text, None
    return path, parse_fragment(fragment)


def parse_fragment(fragment: str) -> TemporalFragment:
    """El tramo de un fragmento temporal escrito sin el `#`, como `t=10,15`.

    Raises:
        MalformedFragmentError: si no es un `t=` del formato NPT que se pueda leer.
        EmptyFragmentError: si el tramo empieza donde termina o después.
    """
    if DIMENSION_SEPARATOR in fragment or not fragment.startswith(TEMPORAL_PREFIX):
        raise MalformedFragmentError(
            f"'#{fragment}' no es un fragmento temporal: se admite uno solo, como "
            "'#t=10,15' (del segundo 10 al 15), '#t=10' o '#t=,15'"
        )
    times = fragment.removeprefix(TEMPORAL_PREFIX).removeprefix(NPT_FORMAT_PREFIX)
    start_text, separator, end_text = times.partition(RANGE_SEPARATOR)
    if not start_text and not end_text:
        raise MalformedFragmentError(f"'#{fragment}' no dice dónde empieza ni dónde termina")
    start = _npt_seconds(start_text, fragment) if start_text else 0.0
    end = _npt_seconds(end_text, fragment) if separator else None
    if end is not None and start >= end:
        raise EmptyFragmentError(
            f"el fragmento '#{fragment}' está vacío: empieza en {_seconds_text(start)} s y "
            f"termina en {_seconds_text(end)} s"
        )
    return TemporalFragment(start, end)


def _npt_seconds(text: str, fragment: str) -> float:
    if seconds := NPT_SECONDS.fullmatch(text):
        return float(seconds.group(1))
    if clock := NPT_CLOCK.fullmatch(text):
        hours, minutes, seconds_text = clock.groups()
        return (
            int(hours or 0) * SECONDS_PER_HOUR
            + int(minutes) * SECONDS_PER_MINUTE
            + float(seconds_text)
        )
    raise MalformedFragmentError(
        f"'{text}' en '#{fragment}' no es un tiempo NPT: se escribe en segundos ('10.5'), "
        "como mm:ss ('01:30') o como h:mm:ss ('1:02:03'); SMPTE y clock no se admiten"
    )


def _seconds_text(seconds: float) -> str:
    return format(seconds, SECONDS_FORMAT)
