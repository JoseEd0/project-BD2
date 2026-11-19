"""El nivel de cada ventana de un audio subido, para que las ventanas en silencio no voten.

Un tramo en silencio se parece a cualquier otro tramo en silencio del catálogo, y como todas
las ventanas en silencio de la subida reciben los mismos vecinos, sus votos se encadenan en
rachas falsas. Content ID tampoco reclama silencio. El audio se decodifica por bloques de
`LEVEL_BLOCK_SECONDS`, sin tenerlo entero en memoria: de cada bloque queda su potencia media,
y el nivel de una ventana es la de sus bloques, en dB respecto de la escala completa (dBFS).
Decodificarlo entero también revela un archivo con buena cabecera y cuerpo dañado.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import soundfile

from songcheck.upload import CorruptAudioError

# Diez bloques por segundo: las ventanas empiezan en múltiplos de 2,5 s y caen en el borde
# de un bloque, y una pista de 10 min son 6 000 potencias.
LEVEL_BLOCK_SECONDS = 0.1
DECODE_TYPE = "float32"
DECIBELS_PER_POWER_DECADE = 10.0
# El nivel de un bloque sin señal: más bajo que cualquier umbral de silencio razonable, y
# finito para poder promediarlo y serializarlo.
DIGITAL_SILENCE_DBFS = -120.0
# Los bordes de las ventanas son múltiplos del bloque que en coma flotante pueden quedar una
# millonésima por encima o por debajo.
EDGE_TOLERANCE_BLOCKS = 1e-6


@dataclass(frozen=True, slots=True)
class Loudness:
    """La potencia media de cada bloque de `block_seconds` de un audio, mezclado a mono por
    potencia (la media de los canales)."""

    block_seconds: float
    block_powers: tuple[float, ...]

    @classmethod
    def measure(cls, content: bytes, block_seconds: float = LEVEL_BLOCK_SECONDS) -> Loudness:
        """Decodifica `content` entero, por bloques.

        Raises:
            CorruptAudioError: si libsndfile no lo puede decodificar hasta el final.
        """
        try:
            with soundfile.SoundFile(io.BytesIO(content)) as sound:
                frames = max(1, round(block_seconds * sound.samplerate))
                powers = tuple(
                    float(np.mean(np.square(block)))
                    for block in sound.blocks(blocksize=frames, dtype=DECODE_TYPE, always_2d=True)
                )
        except (soundfile.SoundFileError, RuntimeError) as error:
            raise CorruptAudioError(f"el audio no se puede decodificar: {error}") from error
        return cls(block_seconds, powers)

    def window_dbfs(self, start: float, end: float) -> float:
        """El nivel de `[start, end)` en dBFS; `DIGITAL_SILENCE_DBFS` si no tiene señal."""
        first = math.floor(start / self.block_seconds + EDGE_TOLERANCE_BLOCKS)
        last = math.ceil(end / self.block_seconds - EDGE_TOLERANCE_BLOCKS)
        powers = self.block_powers[first : max(first + 1, last)]
        return power_dbfs(sum(powers) / len(powers)) if powers else DIGITAL_SILENCE_DBFS

    def silent_windows(
        self, bounds: Sequence[tuple[float, float]], threshold_dbfs: float
    ) -> list[bool]:
        """Cuáles de las ventanas `(inicio, fin)` quedan por debajo de `threshold_dbfs`."""
        return [self.window_dbfs(start, end) < threshold_dbfs for start, end in bounds]


def power_dbfs(power: float) -> float:
    if power <= 0:
        return DIGITAL_SILENCE_DBFS
    return max(DIGITAL_SILENCE_DBFS, DECIBELS_PER_POWER_DECADE * math.log10(power))
