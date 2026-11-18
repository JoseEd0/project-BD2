"""Los dos modelos ONNX de OpenCV Zoo (Apache 2.0): dónde se bajan y dónde se buscan.

    python -m faces models --salida data/models/faces

- **YuNet** (`face_detection_yunet_2023mar.onnx`, 227 KiB): detector que da la caja del
  rostro y cinco puntos (ojos, nariz y comisuras), con `cv2.FaceDetectorYN`.
- **SFace** (`face_recognition_sface_2021dec.onnx`, 37 MiB): embedding de 128 dimensiones,
  entrenado con una pérdida de margen angular de tipo ArcFace, con `cv2.FaceRecognizerSF`.

Las URL apuntan a un commit fijo del repositorio de OpenCV Zoo, y cada archivo se acepta
solo si su SHA-256 es el de su puntero de Git LFS en ese commit. La carpeta sale de
`--salida` o de la variable `FACES_MODELS_DIR`, la misma que usan el servicio y los tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from appkit.download import RemoteFile, fetch_verified

from .errors import ModelNotFoundError

MODELS_DIRECTORY_VARIABLE = "FACES_MODELS_DIR"
OPENCV_ZOO_COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
OPENCV_ZOO_FILES = f"https://github.com/opencv/opencv_zoo/raw/{OPENCV_ZOO_COMMIT}/models"
DETECTOR_FILENAME = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILENAME = "face_recognition_sface_2021dec.onnx"

DETECTOR = RemoteFile(
    url=f"{OPENCV_ZOO_FILES}/face_detection_yunet/{DETECTOR_FILENAME}",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size=232_589,
)
RECOGNIZER = RemoteFile(
    url=f"{OPENCV_ZOO_FILES}/face_recognition_sface/{RECOGNIZER_FILENAME}",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    size=38_696_353,
)


@dataclass(frozen=True, slots=True)
class ModelPaths:
    """Dónde están los dos modelos en disco."""

    detector: Path
    recognizer: Path

    @classmethod
    def inside(cls, directory: Path) -> ModelPaths:
        return cls(directory / DETECTOR_FILENAME, directory / RECOGNIZER_FILENAME)


def download_models(directory: Path) -> ModelPaths:
    """Baja los dos modelos a `directory`, comprobando su SHA-256; los que ya están
    verificados no se vuelven a bajar.

    Raises:
        appkit.download.DownloadError: si un modelo no llega completo o con su suma.
    """
    paths = ModelPaths.inside(directory)
    fetch_verified(DETECTOR, paths.detector)
    fetch_verified(RECOGNIZER, paths.recognizer)
    return paths


def locate_models(directory: Path) -> ModelPaths:
    """Los modelos de `directory`, sin volver a calcular su suma: se comprobó al bajarlos.

    Raises:
        ModelNotFoundError: si falta alguno.
    """
    paths = ModelPaths.inside(directory)
    missing = [path.name for path in (paths.detector, paths.recognizer) if not path.is_file()]
    if missing:
        raise ModelNotFoundError(
            f"faltan {', '.join(missing)} en {directory}: bájalos con "
            f"`python -m faces models --salida {directory}`"
        )
    return paths


def models_directory_from_environment() -> Path:
    """La carpeta de `FACES_MODELS_DIR`.

    Raises:
        ModelNotFoundError: si la variable no está definida.
    """
    configured = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not configured:
        raise ModelNotFoundError(f"falta {MODELS_DIRECTORY_VARIABLE}: la carpeta de los modelos")
    return Path(configured)
