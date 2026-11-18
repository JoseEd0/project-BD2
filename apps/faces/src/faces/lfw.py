"""Labeled Faces in the Wild (LFW): la galería pública y sus pares oficiales de verificación.

    python -m faces lfw --salida data/datasets/lfw

LFW (Huang et al., UMass Amherst, 2007) son 13 233 fotos de 250 x 250 px de 5 749 personas
públicas, sacadas de noticias, y `pairs.txt`, el protocolo de verificación: 10 pliegues de
300 pares de la misma persona y 300 de personas distintas. `pairsDevTrain.txt` y
`pairsDevTest.txt` son la «vista 1», para elegir parámetros sin tocar esos 10 pliegues.

El sitio de UMass se cae a menudo, así que se baja del espejo en figshare que usa
scikit-learn (`fetch_lfw_pairs`), con las mismas sumas SHA-256 que fija scikit-learn. Es la
versión original, sin alinear: la alineación la hace el pipeline con los puntos de YuNet.

En `--salida` quedan `lfw/<Persona>/<Persona>_0001.jpg` y los tres archivos de pares. El
`.tgz` (172 MiB) se borra al extraerlo; si se corta, la descarga sigue donde quedó, y si
`lfw/` ya está, no se vuelve a bajar.
"""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from appkit.download import RemoteFile, fetch_verified

from .errors import DatasetError

FIGSHARE_FILES = "https://ndownloader.figshare.com/files"
ARCHIVE = RemoteFile(
    url=f"{FIGSHARE_FILES}/5976018",
    sha256="055f7d9c632d7370e6fb4afc7468d40f970c34a80d4c6f50ffec63f5a8d536c0",
    size=180_566_744,
)
PAIRS = RemoteFile(
    url=f"{FIGSHARE_FILES}/5976006",
    sha256="ea42330c62c92989f9d7c03237ed5d591365e89b3e649747777b70e692dc1592",
    size=155_335,
)
DEVELOPMENT_TRAIN_PAIRS = RemoteFile(
    url=f"{FIGSHARE_FILES}/5976012",
    sha256="1d454dada7dfeca0e7eab6f65dc4e97a6312d44cf142207be28d688be92aabfa",
    size=56_579,
)
DEVELOPMENT_TEST_PAIRS = RemoteFile(
    url=f"{FIGSHARE_FILES}/5976009",
    sha256="7cb06600ea8b2814ac26e946201cdb304296262aad67d046a16a7ec85d0ff87c",
    size=26_002,
)
PAIR_FILES = {
    "pairs.txt": PAIRS,
    "pairsDevTrain.txt": DEVELOPMENT_TRAIN_PAIRS,
    "pairsDevTest.txt": DEVELOPMENT_TEST_PAIRS,
}
ARCHIVE_FILENAME = "lfw.tgz"
IMAGES_DIRECTORY = "lfw"
EXTRACTION_DIRECTORY = "lfw.extrayendo"
IMAGE_SUFFIX = ".jpg"
IMAGE_NUMBER_DIGITS = 4
LFW_IMAGES = 13_233
LFW_PEOPLE = 5_749
# Una línea de un par de la misma persona trae el nombre y dos números de foto; la de un
# par de personas distintas, nombre y número de cada una.
MATCHED_FIELDS = 3
MISMATCHED_FIELDS = 4
# `10 300` en `pairs.txt`: pliegues y pares de cada clase por pliegue.
FOLDED_HEADER_FIELDS = 2


@dataclass(frozen=True, slots=True)
class LfwImage:
    """Una foto de LFW: de quién es y dónde está."""

    person: str
    path: Path


@dataclass(frozen=True, slots=True)
class ImageReference:
    """Una foto nombrada como en los archivos de pares: la persona y su número (desde 1)."""

    person: str
    number: int

    def path_in(self, images_directory: Path) -> Path:
        filename = f"{self.person}_{self.number:0{IMAGE_NUMBER_DIGITS}d}{IMAGE_SUFFIX}"
        return images_directory / self.person / filename


@dataclass(frozen=True, slots=True)
class Pair:
    first: ImageReference
    second: ImageReference
    same_person: bool


@dataclass(frozen=True, slots=True)
class Fold:
    """Un pliegue del protocolo: sus pares de la misma persona y de personas distintas."""

    matched: tuple[Pair, ...]
    mismatched: tuple[Pair, ...]


def download_lfw(root: Path) -> Path:
    """Baja LFW a `root` y devuelve la carpeta de las fotos, `root/lfw`.

    Raises:
        appkit.download.DownloadError: si un archivo no llega completo o con su suma.
        DatasetError: si lo extraído no son las 13 233 fotos o un par nombra una que falta.
    """
    for filename, remote in PAIR_FILES.items():
        fetch_verified(remote, root / filename)
    images_directory = root / IMAGES_DIRECTORY
    if not images_directory.is_dir():
        archive = fetch_verified(ARCHIVE, root / ARCHIVE_FILENAME)
        _extract(archive, root)
        archive.unlink()
    check_complete(root)
    return images_directory


def check_complete(root: Path) -> None:
    """Comprueba que `root` tiene las 13 233 fotos de 5 749 personas y todas las que
    nombran los archivos de pares.

    Raises:
        DatasetError: si no.
    """
    images = lfw_images(root / IMAGES_DIRECTORY)
    people = {image.person for image in images}
    if (len(images), len(people)) != (LFW_IMAGES, LFW_PEOPLE):
        raise DatasetError(
            f"{root} tiene {len(images)} fotos de {len(people)} personas y LFW tiene "
            f"{LFW_IMAGES} de {LFW_PEOPLE}: bórralo y vuelve a bajarlo"
        )
    for filename in PAIR_FILES:
        _check_pairs_exist(read_pairs(root / filename), root / IMAGES_DIRECTORY)


def lfw_images(images_directory: Path) -> list[LfwImage]:
    """Las fotos de `images_directory`, ordenadas por persona y número."""
    return [
        LfwImage(person=path.parent.name, path=path)
        for path in sorted(images_directory.glob(f"*/*{IMAGE_SUFFIX}"))
    ]


def read_pairs(path: Path) -> tuple[Fold, ...]:
    """Los pliegues de un archivo de pares de LFW.

    `pairs.txt` empieza con `10 300`: diez pliegues de 300 pares de la misma persona
    seguidos de 300 de personas distintas. Los de la vista 1 empiezan con un solo número,
    los pares de cada clase de su único pliegue.

    Raises:
        DatasetError: si una línea no es un par o faltan líneas.
    """
    lines = iter(path.read_text(encoding="utf-8").splitlines())
    header = [int(field) for field in next(lines).split()]
    folds, pairs_per_class = header if len(header) == FOLDED_HEADER_FIELDS else (1, header[0])
    return tuple(_read_fold(lines, pairs_per_class, path) for _ in range(folds))


def _read_fold(lines: Iterator[str], pairs_per_class: int, path: Path) -> Fold:
    matched = tuple(_parse_pair(_next_line(lines, path)) for _ in range(pairs_per_class))
    mismatched = tuple(_parse_pair(_next_line(lines, path)) for _ in range(pairs_per_class))
    if not all(pair.same_person for pair in matched) or any(
        pair.same_person for pair in mismatched
    ):
        raise DatasetError(f"{path}: un pliegue no tiene sus pares en el orden del protocolo")
    return Fold(matched, mismatched)


def _next_line(lines: Iterator[str], path: Path) -> str:
    line = next(lines, None)
    if line is None:
        raise DatasetError(f"{path} termina antes de lo que anuncia su cabecera")
    return line


def _parse_pair(line: str) -> Pair:
    fields = line.split()
    if len(fields) == MATCHED_FIELDS:
        person, first, second = fields
        return Pair(ImageReference(person, int(first)), ImageReference(person, int(second)), True)
    if len(fields) == MISMATCHED_FIELDS:
        first_person, first, second_person, second = fields
        return Pair(
            ImageReference(first_person, int(first)),
            ImageReference(second_person, int(second)),
            False,
        )
    raise DatasetError(f"la línea {line!r} no es un par de LFW")


def _check_pairs_exist(folds: Sequence[Fold], images_directory: Path) -> None:
    for fold in folds:
        for pair in (*fold.matched, *fold.mismatched):
            for reference in (pair.first, pair.second):
                if not reference.path_in(images_directory).is_file():
                    raise DatasetError(f"falta {reference.path_in(images_directory)}")


def _extract(archive: Path, root: Path) -> None:
    """Extrae en una carpeta aparte y la mueve al final: un corte a mitad no deja una
    `lfw/` incompleta que la siguiente ejecución daría por buena. El filtro `data` rechaza
    rutas absolutas, enlaces y `..` que salgan de la carpeta."""
    staging = root / EXTRACTION_DIRECTORY
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(staging, filter="data")
    (staging / IMAGES_DIRECTORY).replace(root / IMAGES_DIRECTORY)
    shutil.rmtree(staging)
