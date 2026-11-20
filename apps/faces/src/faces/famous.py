"""Las personas públicas de LFW con que se compara «¿A qué famoso te pareces?».

Una fila por persona de LFW en `face_famosos`: el promedio de los embeddings de sus fotos
(normalizado: el centro de su nube en la esfera) y la foto que más se le parece, que es la
que se muestra. Con una fila por persona, los k vecinos del HNSW ya son k personas
distintas.

La comparación es solo contra esta tabla, nunca contra `face_embeddings`: decir a qué
persona registrada se parece alguien revelaría datos biométricos de un tercero que no
consintió ese uso (ver «Privacidad» en el README).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path, PurePosixPath

import numpy as np

from .detection import center_face
from .lfw import IMAGES_DIRECTORY, LfwImage
from .pipeline import FacePipeline
from .recognition import EMBEDDING_DIMENSION, Embedding, l2_normalized

FAMOUS_TABLE = "face_famosos"
LFW_DATASET_DIRECTORY = "lfw"
NAME_LENGTH = 64
PHOTO_PATH_LENGTH = 160
FAMOUS_SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS {FAMOUS_TABLE} (id INT PRIMARY KEY, "
    f"nombre VARCHAR({NAME_LENGTH}) NOT NULL, foto VARCHAR({PHOTO_PATH_LENGTH}) NOT NULL, "
    f"fotos INT NOT NULL, embedding VECTOR({EMBEDDING_DIMENSION}) NOT NULL)",
    f"CREATE INDEX IF NOT EXISTS face_famosos_vector ON {FAMOUS_TABLE} "
    "USING HNSW (embedding) WITH (metric = 'cosine')",
)
NEAREST_FAMOUS = (
    f"SELECT id, nombre, fotos, SIMILARITY_SCORE() AS score FROM {FAMOUS_TABLE} "
    "WHERE SIMILAR_TO(embedding, $1, k = $2)"
)
SELECT_FAMOUS_PHOTO = f"SELECT foto FROM {FAMOUS_TABLE} WHERE id = $1"
COUNT_FAMOUS = f"SELECT COUNT(*) FROM {FAMOUS_TABLE}"
# LFW nombra las carpetas con guiones bajos: `George_W_Bush`.
NAME_SEPARATOR = "_"


@dataclass(frozen=True, slots=True)
class FamousPerson:
    """Una persona de LFW lista para la tabla: su nombre legible, la foto que se muestra
    (relativa a la carpeta de fotos de LFW), cuántas fotos con rostro promedió y el
    embedding promedio."""

    name: str
    photo: PurePosixPath
    photos: int
    embedding: Embedding

    def row(self, row_id: int) -> dict[str, object]:
        return {
            "id": row_id,
            "nombre": self.name,
            "foto": str(self.photo),
            "fotos": self.photos,
            "embedding": [float(value) for value in self.embedding],
        }


@dataclass(frozen=True, slots=True)
class FamousMatch:
    """Una persona de LFW parecida al rostro buscado: su nombre, cuántas fotos se
    promediaron y su coseno."""

    famous_id: int
    name: str
    photos: int
    score: float


def lfw_images_directory(data_dir: Path) -> Path:
    """La carpeta de fotos de LFW dentro de la carpeta de datasets (la que deja
    `python -m faces lfw --salida <data_dir>/lfw`)."""
    return data_dir / LFW_DATASET_DIRECTORY / IMAGES_DIRECTORY


def display_name(folder: str) -> str:
    """`George_W_Bush` → `George W Bush`."""
    return folder.replace(NAME_SEPARATOR, " ")[:NAME_LENGTH]


def lfw_photo_embedding(pipeline: FacePipeline, data: bytes) -> Embedding | None:
    """El embedding del rostro del centro de una foto de LFW, o `None` si no tiene rostro.

    Raises:
        faces.errors.InvalidImageError: si la foto no se puede decodificar.
    """
    image = pipeline.decode(data)
    faces = pipeline.detect(image)
    if not faces:
        return None
    height, width = image.shape[:2]
    return pipeline.embed(image, center_face(faces, width, height))


def famous_people(
    images: Sequence[LfwImage],
    embeddings: Mapping[Path, Embedding],
    images_directory: Path,
) -> list[FamousPerson]:
    """Una `FamousPerson` por persona con al menos una foto en `embeddings`, en el orden de
    `images` (por persona). Las fotos sin rostro no están en `embeddings`."""
    people: list[FamousPerson] = []
    for person, photos in groupby(images, key=lambda image: image.person):
        found = [
            (image.path, embeddings[image.path]) for image in photos if image.path in embeddings
        ]
        if found:
            people.append(_famous_person(person, found, images_directory))
    return people


def famous_matches(rows: Iterable[Mapping[str, object]]) -> list[FamousMatch]:
    """Las filas de `NEAREST_FAMOUS`, de la más parecida a la menos."""
    matches = [
        FamousMatch(
            int(str(row["id"])),
            str(row["nombre"]),
            int(str(row["fotos"])),
            float(str(row["score"])),
        )
        for row in rows
    ]
    return sorted(matches, key=lambda match: (-match.score, match.famous_id))


def _famous_person(
    person: str, found: Sequence[tuple[Path, Embedding]], images_directory: Path
) -> FamousPerson:
    vectors = np.stack([embedding for _, embedding in found])
    center = l2_normalized(vectors.mean(axis=0).astype(np.float32))
    closest = int(np.argmax(vectors @ center))
    photo = PurePosixPath(found[closest][0].relative_to(images_directory).as_posix())
    return FamousPerson(display_name(person), photo, len(found), center)
