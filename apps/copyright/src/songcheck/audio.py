"""La duración de una pista, decodificándola entera con libsndfile como lo hace el gestor.

La cabecera no basta: `fma_small` trae pistas cuya cabecera se lee pero cuyo audio se corta a
mitad (098565, 098567 y 098569, entre otras), y el gestor fallaría al construir el índice con
ellas. Decodificar una pista de 30 s cuesta unos 10 ms y se hace una vez, al escribir el
catálogo.
"""

from __future__ import annotations

from pathlib import Path

import soundfile

from songcheck.errors import CopyrightError

DECODE_BLOCK_FRAMES = 1 << 16
DECODE_TYPE = "int16"


class UnreadableAudioError(CopyrightError):
    """El archivo no es un audio que libsndfile pueda decodificar entero."""


def decoded_duration(path: Path) -> float:
    """Segundos de audio que salen de decodificar `path` entero.

    Raises:
        UnreadableAudioError: si no se puede abrir o decodificar hasta el final.
    """
    try:
        with soundfile.SoundFile(path) as sound:
            frames = sum(
                len(block)
                for block in sound.blocks(blocksize=DECODE_BLOCK_FRAMES, dtype=DECODE_TYPE)
            )
            return float(frames / sound.samplerate)
    except (soundfile.SoundFileError, RuntimeError, OSError) as error:
        raise UnreadableAudioError(f"no se puede decodificar el audio {path}: {error}") from error
