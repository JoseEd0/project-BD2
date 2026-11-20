"""«¿A qué famoso te pareces?»: el rostro de una foto contra las personas públicas de LFW.

Solo lee `face_famosos`; nunca la galería de personas registradas. La foto de consulta se
analiza en memoria y no se guarda.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from appkit import timed
from faces.api.analysis import AnalysisPool, analyzed
from faces.api.errors import FAMOUS_PHOTO_NOT_FOUND, FamousPhotoNotFoundError
from faces.api.identification import NO_FACE_MESSAGE, TOO_SMALL_MESSAGE
from faces.api.models import FamousLookalike, Lookalikes
from faces.famous import NEAREST_FAMOUS, SELECT_FAMOUS_PHOTO, FamousMatch, famous_matches
from faces.quality import QualityConfig
from faces.recognition import Embedding
from faces.shots import analyze_query
from minigestor_client import AsyncClient

LOOKALIKES_STAGE = "famosos"
# Solo el rostro más grande de la foto: es a quien se le pregunta.
QUERY_FACES = 1
PERCENT = 100
PHOTO_ROUTE = "/lookalikes/{famous_id}/photo"
NOT_LOADED_MESSAGE = (
    "Todavía no se cargaron las personas de LFW: docker compose run --rm faces-seed."
)
FOUND_MESSAGE = "Las personas públicas de LFW que más se te parecen."


class LookalikeService:
    """Busca en el HNSW de `face_famosos` y sirve las fotos de LFW de esa tabla."""

    def __init__(
        self,
        engine: AsyncClient,
        pool: AnalysisPool,
        quality: QualityConfig,
        count: int,
        photos_directory: Path | None,
    ) -> None:
        self._engine = engine
        self._pool = pool
        self._quality = quality
        self._count = count
        self._photos_directory = photos_directory

    async def find(self, photo: bytes) -> Lookalikes:
        """Las `count` personas de LFW más parecidas al rostro más grande de `photo`.

        Raises:
            UnreadableImageError, TooManyPixelsError: si la foto no se puede analizar.
        """
        work = partial(analyze_query, data=photo, config=self._quality, max_faces=QUERY_FACES)
        faces = await analyzed(self._pool, work)
        if not faces:
            return Lookalikes(people=[], message=NO_FACE_MESSAGE)
        if faces[0].embedding is None:
            return Lookalikes(people=[], message=TOO_SMALL_MESSAGE)
        matches = await self._nearest(faces[0].embedding)
        if not matches:
            return Lookalikes(people=[], message=NOT_LOADED_MESSAGE)
        return Lookalikes(people=[_lookalike(match) for match in matches], message=FOUND_MESSAGE)

    async def photo(self, famous_id: int) -> Path:
        """El archivo de la foto de LFW de la persona `famous_id`. La ruta sale de la tabla,
        nunca de la petición, y tiene que quedar dentro de la carpeta de fotos.

        Raises:
            FamousPhotoNotFoundError: si no hay esa persona, no hay carpeta de fotos o el
                archivo no está.
        """
        if self._photos_directory is None:
            raise FamousPhotoNotFoundError(FAMOUS_PHOTO_NOT_FOUND)
        relative = await self._engine.scalar(SELECT_FAMOUS_PHOTO, [famous_id])
        if relative is None:
            raise FamousPhotoNotFoundError(FAMOUS_PHOTO_NOT_FOUND)
        root = self._photos_directory.resolve()
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FamousPhotoNotFoundError(FAMOUS_PHOTO_NOT_FOUND)
        return path

    async def _nearest(self, embedding: Embedding) -> list[FamousMatch]:
        with timed(LOOKALIKES_STAGE):
            rows = await self._engine.fetch_all(NEAREST_FAMOUS, [embedding, self._count])
        return famous_matches(rows)[: self._count]


def _lookalike(match: FamousMatch) -> FamousLookalike:
    return FamousLookalike(
        id=match.famous_id,
        name=match.name,
        similarity=match.score,
        percent=round(max(match.score, 0.0) * PERCENT),
        photos=match.photos,
        photo_path=PHOTO_ROUTE.format(famous_id=match.famous_id),
    )
