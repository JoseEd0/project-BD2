"""Errores del servicio de copyright fuera de una petición: la descarga, el catálogo y la
carga en el gestor. Los de una petición son los `ServiceError` de `appkit`."""

from __future__ import annotations


class CopyrightError(Exception):
    """Base de los errores del servicio de copyright."""


class CatalogError(CopyrightError):
    """`catalog.csv` no tiene la forma que escribe la descarga."""


class TracksError(CopyrightError):
    """`tracks.csv` o el `.zip` de audio no tienen la forma que publica el FMA."""


class SeedError(CopyrightError):
    """El gestor no quedó con el catálogo: un lote rechazado o tablas con otras filas."""
