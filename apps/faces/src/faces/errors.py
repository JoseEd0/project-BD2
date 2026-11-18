"""Errores del reconocimiento facial: lo que la foto o los modelos no permiten hacer."""

from __future__ import annotations


class FaceError(Exception):
    """Base de los errores del servicio de rostros."""


class InvalidImageError(FaceError):
    """Los bytes no son una imagen de un formato aceptado, o la imagen está dañada."""


class ImageTooLargeError(InvalidImageError):
    """La imagen declara más píxeles de los permitidos; no se llega a decodificar."""


class NoFaceError(FaceError):
    """En la imagen no se encontró ningún rostro."""


class MultipleFacesError(FaceError):
    """Se pedía un solo rostro y la imagen tiene varios."""


class ModelNotFoundError(FaceError):
    """Falta un modelo, o no se dijo en qué carpeta buscarlos."""


class DatasetError(FaceError):
    """El dataset descargado no tiene lo que debería."""
