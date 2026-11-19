"""Errores del catálogo: datos del dataset que no se pueden cargar tal cual."""

from __future__ import annotations


class CatalogError(Exception):
    """Un archivo del dataset no tiene la forma que espera la carga."""
