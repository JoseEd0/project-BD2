"""De dónde salen los datos: Amazon Reviews 2023 (McAuley Lab), categoría Musical_Instruments.

Las URL apuntan a una revisión fija del repositorio del Hugging Face Hub, y el SHA-256 y el
tamaño de cada archivo son los que el Hub publica para esa revisión (el `oid` de LFS), así
que una descarga distinta de la publicada se detecta y se rechaza.

Licencia de los datos: los publica el McAuley Lab (UCSD) para investigación
(amazon-reviews-2023.github.io).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from appkit.download import RemoteFile

CATEGORY = "Musical_Instruments"
HUB_REPOSITORY = "McAuley-Lab/Amazon-Reviews-2023"
HUB_REVISION = "2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e"
HUB_BASE_URL = f"https://huggingface.co/datasets/{HUB_REPOSITORY}/resolve/{HUB_REVISION}"
# `last_out` deja la última interacción de cada usuario como prueba y la penúltima como
# validación. La variante `timestamp` del mismo 5-core corta en dos fechas globales, así
# que la mayoría de los usuarios no tiene ninguna interacción en la prueba.
SPLIT_SCHEME = "last_out"
CORE_DIRECTORY = f"benchmark/5core/{SPLIT_SCHEME}"
CORE_MINIMUM = 5


class Split(StrEnum):
    TRAIN = "train"
    VALID = "valid"
    TEST = "test"


def file_name_of(remote: RemoteFile) -> str:
    """El nombre con el que se guarda un archivo publicado: el último tramo de su URL."""
    return PurePosixPath(urlsplit(remote.url).path).name


METADATA = RemoteFile(
    url=f"{HUB_BASE_URL}/raw/meta_categories/meta_{CATEGORY}.jsonl",
    sha256="96f40b5440918f07e3e08e84b7a6874a06a5e32a9988998ca9520cfd1b51a12c",
    size=631_877_970,
)

SPLITS: dict[Split, RemoteFile] = {
    Split.TRAIN: RemoteFile(
        url=f"{HUB_BASE_URL}/{CORE_DIRECTORY}/{CATEGORY}.train.csv",
        sha256="57011953cb95406af0656fc273b948e03f72301ff987d75774bcf813b7ebc76c",
        size=23_024_713,
    ),
    Split.VALID: RemoteFile(
        url=f"{HUB_BASE_URL}/{CORE_DIRECTORY}/{CATEGORY}.valid.csv",
        sha256="0bc030c196db45d02c2f08b3b8561de6d2b7af10720f5a648a29b80c2ba8041e",
        size=3_331_575,
    ),
    Split.TEST: RemoteFile(
        url=f"{HUB_BASE_URL}/{CORE_DIRECTORY}/{CATEGORY}.test.csv",
        sha256="f6a47056a50746e30c48ecb4f12dc8569f5857dd3edffb6bf3ea4c8e8395ef3e",
        size=3_331_575,
    ),
}
