"""Errores de la extracción de características: archivos que no se pueden describir."""

from __future__ import annotations

from errors import DomainError


class MediaError(DomainError):
    """Base de los errores de un archivo multimedia.

    Las subclases solo reciben el mensaje: así viajan intactas de un proceso de la
    extracción en paralelo al que las pidió.
    """


class UnsupportedMediaTypeError(MediaError):
    """La extensión del archivo no es la de una imagen ni la de un audio conocidos."""


class UnreadableMediaError(MediaError):
    """El archivo no existe, no se puede leer o su contenido no se puede decodificar."""
