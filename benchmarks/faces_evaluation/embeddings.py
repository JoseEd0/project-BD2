"""El embedding de cada foto de LFW, del rostro más cercano al centro, y lo que tardó cada
etapa del pipeline con ella.

En el 7,8 % de las fotos de LFW YuNet encuentra más de un rostro (sección 7 de
`benchmarks/README.md`): son fotos de prensa, recortadas alrededor de la persona que
nombran, así que esa persona es el rostro del centro y no necesariamente el más grande.

Cada proceso de `workers` crea su propio `FacePipeline`, que no se comparte entre hilos.
El resultado se puede guardar en un `.npz` y reutilizar: calcularlo para las 13 233 fotos
tarda unos cuatro minutos con un proceso.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from benchmarks.faces_benchmark import timed
from faces import FacePipeline, PipelineConfig, locate_models
from faces.detection import center_face
from faces.lfw import ImageReference, LfwImage
from faces.recognition import EMBEDDING_DIMENSION

STAGES = ("decodificar", "detectar", "alinear", "embeber")
# Las fotos se reparten a los procesos en tandas: una por foto pagaría más en mensajes que
# lo que tarda el pipeline.
PHOTOS_PER_TASK = 64

_worker_pipeline: FacePipeline | None = None


@dataclass(frozen=True, slots=True)
class PhotoResult:
    """El embedding del rostro elegido (o `None` sin rostro), cuántos rostros había y los
    milisegundos de cada etapa de `STAGES`; alinear y embeber son NaN sin rostro."""

    embedding: npt.NDArray[np.float32] | None
    faces: int
    milliseconds: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class LfwEmbeddings:
    """Una fila por foto de LFW, en el orden de `faces.lfw.lfw_images`.

    `vectors` tiene NaN en las filas de fotos sin rostro, que `found` marca en falso.
    """

    names: tuple[str, ...]
    people: tuple[str, ...]
    vectors: npt.NDArray[np.float32]
    found: npt.NDArray[np.bool_]
    faces: npt.NDArray[np.int_]
    milliseconds: npt.NDArray[np.float64]
    rows: Mapping[str, int]

    def row_of(self, reference: ImageReference) -> int:
        """La fila de la foto que nombra un archivo de pares.

        Raises:
            KeyError: si esa foto no está.
        """
        return self.rows[_name_of(reference.path_in(Path()))]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            names=np.array(self.names),
            vectors=self.vectors,
            faces=self.faces,
            milliseconds=self.milliseconds,
        )


def analyze_photo(pipeline: FacePipeline, data: bytes) -> PhotoResult:
    image, decode = timed(lambda: pipeline.decode(data))
    faces, detect = timed(lambda: pipeline.detect(image))
    if not faces:
        return PhotoResult(None, 0, (decode, detect, math.nan, math.nan))
    height, width = image.shape[:2]
    chosen = center_face(faces, width, height)
    aligned, align = timed(lambda: pipeline.align(image, chosen))
    embedding, describe = timed(lambda: pipeline.describe(aligned))
    return PhotoResult(embedding, len(faces), (decode, detect, align, describe))


def compute_embeddings(
    images: Sequence[LfwImage], models: Path, workers: int, threads: int
) -> LfwEmbeddings:
    """Analiza cada foto de `images` en `workers` procesos con `threads` hilos de OpenCV."""
    paths = [image.path for image in images]
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_start_worker, initargs=(models, threads)
    ) as pool:
        results = list(pool.map(_analyze_path, paths, chunksize=PHOTOS_PER_TASK))
    vectors = np.full((len(results), EMBEDDING_DIMENSION), np.nan, dtype=np.float32)
    for row, result in enumerate(results):
        if result.embedding is not None:
            vectors[row] = result.embedding
    return _assemble(
        tuple(_name_of(path.relative_to(path.parents[1])) for path in paths),
        vectors,
        np.array([result.faces for result in results]),
        np.array([result.milliseconds for result in results], dtype=np.float64),
    )


def load_embeddings(path: Path) -> LfwEmbeddings:
    with np.load(path) as stored:
        return _assemble(
            tuple(str(name) for name in stored["names"]),
            stored["vectors"].astype(np.float32),
            stored["faces"],
            stored["milliseconds"],
        )


def _assemble(
    names: tuple[str, ...],
    vectors: npt.NDArray[np.float32],
    faces: npt.NDArray[np.int_],
    milliseconds: npt.NDArray[np.float64],
) -> LfwEmbeddings:
    return LfwEmbeddings(
        names=names,
        people=tuple(name.split("/", 1)[0] for name in names),
        vectors=vectors,
        found=~np.isnan(vectors).any(axis=1),
        faces=faces,
        milliseconds=milliseconds,
        rows={name: row for row, name in enumerate(names)},
    )


def _name_of(relative: Path) -> str:
    return relative.as_posix()


def _start_worker(models: Path, threads: int) -> None:
    global _worker_pipeline
    cv2.setNumThreads(threads)
    _worker_pipeline = FacePipeline(locate_models(models), PipelineConfig())


def _analyze_path(path: Path) -> PhotoResult:
    if _worker_pipeline is None:
        raise RuntimeError("el proceso no se inició con _start_worker")
    return analyze_photo(_worker_pipeline, path.read_bytes())
