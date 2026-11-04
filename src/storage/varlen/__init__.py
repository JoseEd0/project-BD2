"""Valores de bytes de longitud variable, con fragmentos encadenados para los que no caben
en una página."""

from .varlen_file import ValueNotFoundError, VariableLengthFile

__all__ = ["ValueNotFoundError", "VariableLengthFile"]
