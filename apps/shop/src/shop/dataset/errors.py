"""Errores de la preparación del dataset de la tienda."""

from __future__ import annotations


class DatasetError(Exception):
    """Base de los errores al bajar o preparar el dataset."""


class HttpStatusError(DatasetError):
    """El servidor respondió con un estado distinto de 200."""

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"{url} respondió {status}")
        self.status = status


class SplitError(DatasetError):
    """La partición de las interacciones no es la que se espera."""


class CoreError(DatasetError):
    """Un usuario o un producto tiene menos interacciones que las del k-core."""


class PlacesFormatError(DatasetError):
    """El CSV de lugares no tiene el formato que escribe `demos.descargar_lugares`."""
