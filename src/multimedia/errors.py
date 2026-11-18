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


class MediaFragmentError(MediaError):
    """El tramo pedido en la ruta (`#t=...`) no se puede describir."""


class MalformedFragmentError(MediaFragmentError):
    """Lo que sigue al `#` no es un fragmento temporal que el motor sepa leer."""


class EmptyFragmentError(MediaFragmentError):
    """El tramo empieza donde termina o después."""


class FragmentOutOfRangeError(MediaFragmentError):
    """El tramo empieza después del final del audio."""


class FragmentOnImageError(MediaFragmentError):
    """Un fragmento temporal en la ruta de una imagen, que no tiene tiempo."""
