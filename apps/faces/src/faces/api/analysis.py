"""Las redes de la réplica, cada una usada por un solo hilo a la vez.

`FacePipeline` no se comparte entre hilos (OpenCV guarda el tamaño de la última entrada), y
analizar una foto cuesta de 20 a 100 ms de CPU: se hace en un hilo para no detener el bucle
de eventos, con una cola de pipelines que limita cuántas fotos se analizan a la vez.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import TypeVar

import anyio.to_thread

from appkit import timed
from faces.api.errors import (
    TOO_MANY_PIXELS,
    UNREADABLE_IMAGE,
    TooManyPixelsError,
    UnreadableImageError,
)
from faces.errors import ImageTooLargeError, InvalidImageError
from faces.pipeline import FacePipeline

Result = TypeVar("Result")
ANALYSIS_STAGE = "analisis"


class AnalysisPool:
    """Presta un pipeline libre para un trabajo en un hilo y lo recupera al terminar."""

    def __init__(self, pipelines: Sequence[FacePipeline]) -> None:
        self._idle: asyncio.Queue[FacePipeline] = asyncio.Queue()
        for pipeline in pipelines:
            self._idle.put_nowait(pipeline)

    async def run(self, work: Callable[[FacePipeline], Result]) -> Result:
        """El resultado de `work` con un pipeline que nadie más usa; espera si están todos
        ocupados. El hilo no se abandona si se cancela la petición: el pipeline vuelve a la
        cola solo cuando el trabajo terminó de verdad."""
        pipeline = await self._idle.get()
        try:
            return await anyio.to_thread.run_sync(work, pipeline)
        finally:
            self._idle.put_nowait(pipeline)


async def analyzed(pool: AnalysisPool, work: Callable[[FacePipeline], Result]) -> Result:
    """`pool.run(work)` con los errores de la foto convertidos en respuestas del API.

    Raises:
        TooManyPixelsError: si la foto declara más píxeles de los permitidos.
        UnreadableImageError: si no es una imagen de un formato aceptado o está dañada.
    """
    try:
        with timed(ANALYSIS_STAGE):
            return await pool.run(work)
    except ImageTooLargeError as error:
        raise TooManyPixelsError(TOO_MANY_PIXELS) from error
    except InvalidImageError as error:
        raise UnreadableImageError(UNREADABLE_IMAGE) from error
