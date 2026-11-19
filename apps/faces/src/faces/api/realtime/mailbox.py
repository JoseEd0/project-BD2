"""La contrapresión del reconocimiento en vivo: un solo cuadro en espera, el más nuevo.

Si el servidor está ocupado con un cuadro, el que llega reemplaza al que esperaba, que se
descarta y se cuenta. Nunca se encola: una cola haría que cada respuesta llegue más tarde que
la anterior mientras el celular mande más rápido de lo que se procesa, y lo que se dibuja
sobre la cámara sería cada vez más viejo. Con un solo lugar, la latencia de un cuadro es a lo
sumo el que está en curso más el suyo.
"""

from __future__ import annotations

import asyncio

from faces.api.realtime.protocol import Frame


class LatestFrame:
    """El cuadro más nuevo que todavía no se procesa."""

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._arrived = asyncio.Event()
        self.dropped = 0

    def put(self, frame: Frame) -> None:
        """Deja `frame` en espera; el que esperaba, si había, se descarta."""
        if self._frame is not None:
            self.dropped += 1
        self._frame = frame
        self._arrived.set()

    async def wait(self) -> None:
        """Espera hasta que haya un cuadro."""
        await self._arrived.wait()

    def take(self) -> Frame:
        """El cuadro en espera, que deja de estarlo.

        Raises:
            LookupError: si no hay ninguno (llamar después de `wait`).
        """
        frame = self._frame
        if frame is None:
            raise LookupError("no hay ningún cuadro en espera")
        self._frame = None
        self._arrived.clear()
        return frame
