"""De los bytes de una foto a sus rostros con su embedding: decodificar, detectar, alinear
y describir."""

from __future__ import annotations

from dataclasses import dataclass

from .config import PipelineConfig
from .detection import DetectedFace, FaceDetector, only_face
from .image import BgrImage, decode_image
from .models import ModelPaths
from .recognition import Embedding, FaceEmbedder


@dataclass(frozen=True, slots=True)
class FaceAnalysis:
    """Un rostro de la foto y su embedding."""

    face: DetectedFace
    embedding: Embedding


@dataclass(frozen=True, slots=True)
class ImageAnalysis:
    """Lo que se encontró en una foto: su tamaño, ya derecha, y sus rostros del de mayor
    área al de menor."""

    width: int
    height: int
    faces: tuple[FaceAnalysis, ...]

    def only_face(self) -> FaceAnalysis:
        """El único rostro de la foto.

        Raises:
            NoFaceError: si no tiene ninguno.
            MultipleFacesError: si tiene más de uno.
        """
        chosen = only_face([analysis.face for analysis in self.faces])
        return next(analysis for analysis in self.faces if analysis.face is chosen)


class FacePipeline:
    """El detector y el embedder con la misma configuración.

    Carga las dos redes al crearse (unas decenas de milisegundos) y no se comparte entre
    hilos: cada hilo o proceso crea el suyo.
    """

    def __init__(self, models: ModelPaths, config: PipelineConfig) -> None:
        self._config = config
        self._detector = FaceDetector(models.detector, config)
        self._embedder = FaceEmbedder(models.recognizer)

    def decode(self, data: bytes) -> BgrImage:
        """La imagen de `data`; ver `decode_image`.

        Raises:
            InvalidImageError: si no es una imagen aceptada o pasa del máximo de píxeles.
        """
        return decode_image(data, self._config.max_image_pixels)

    def detect(self, image: BgrImage) -> list[DetectedFace]:
        return self._detector.detect(image)

    def align(self, image: BgrImage, face: DetectedFace) -> BgrImage:
        return self._embedder.align(image, face)

    def describe(self, aligned: BgrImage) -> Embedding:
        return self._embedder.describe(aligned)

    def embed(self, image: BgrImage, face: DetectedFace) -> Embedding:
        return self._embedder.embed(image, face)

    def analyze(self, data: bytes) -> ImageAnalysis:
        """Todos los rostros de la foto de `data`, cada uno con su embedding; ninguno si
        no hay rostros, que no es un error: para exigir uno está `ImageAnalysis.only_face`.

        Raises:
            InvalidImageError: si `data` no es una imagen aceptada.
        """
        image = self.decode(data)
        faces = tuple(FaceAnalysis(face, self.embed(image, face)) for face in self.detect(image))
        height, width = image.shape[:2]
        return ImageAnalysis(width=width, height=height, faces=faces)
