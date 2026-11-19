"""Errores de CLIP: lo que los modelos o las imágenes no permiten hacer."""

from __future__ import annotations


class ClipError(Exception):
    """Base de los errores de CLIP."""


class ModelNotFoundError(ClipError):
    """Falta un archivo del modelo, o no se dijo en qué carpeta buscarlos."""


class UnreadableImageError(ClipError):
    """El archivo no es una imagen que Pillow pueda decodificar."""


class EmbeddingCacheError(ClipError):
    """El archivo de la caché de embeddings no tiene la forma esperada."""
