"""Reconocimiento facial de la Parte 5: detección con YuNet, alineación con cinco puntos y
embedding de 128 dimensiones con SFace, sobre OpenCV.

El servicio no importa el gestor: los embeddings llegan a su índice HNSW por el API REST.
"""

from .config import PipelineConfig
from .detection import (
    BoundingBox,
    DetectedFace,
    FaceDetector,
    FaceLandmarks,
    Point,
    detection_scale,
    only_face,
)
from .errors import (
    DatasetError,
    FaceError,
    ImageTooLargeError,
    InvalidImageError,
    ModelNotFoundError,
    MultipleFacesError,
    NoFaceError,
)
from .image import BgrImage, decode_image, resized_by
from .models import ModelPaths, download_models, locate_models, models_directory_from_environment
from .pipeline import FaceAnalysis, FacePipeline, ImageAnalysis
from .recognition import (
    EMBEDDING_DIMENSION,
    Embedding,
    FaceEmbedder,
    cosine_similarity,
    l2_normalized,
)

__all__ = [
    "EMBEDDING_DIMENSION",
    "BgrImage",
    "BoundingBox",
    "DatasetError",
    "DetectedFace",
    "Embedding",
    "FaceAnalysis",
    "FaceDetector",
    "FaceEmbedder",
    "FaceError",
    "FaceLandmarks",
    "FacePipeline",
    "ImageAnalysis",
    "ImageTooLargeError",
    "InvalidImageError",
    "ModelNotFoundError",
    "ModelPaths",
    "MultipleFacesError",
    "NoFaceError",
    "PipelineConfig",
    "Point",
    "cosine_similarity",
    "decode_image",
    "detection_scale",
    "download_models",
    "l2_normalized",
    "locate_models",
    "models_directory_from_environment",
    "only_face",
    "resized_by",
]
