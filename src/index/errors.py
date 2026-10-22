"""Errores de nodo que comparten los árboles del motor, el B+ y el R-Tree."""

from __future__ import annotations

from storage.types import StorageError


class NodeCapacityError(StorageError):
    """La página es demasiado pequeña para que el árbol funcione."""


class CorruptNodeError(StorageError):
    """Los bytes de la página no describen un nodo válido."""
