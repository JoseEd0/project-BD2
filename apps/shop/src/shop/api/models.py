"""Lo que responde el API de la tienda."""

from __future__ import annotations

from pydantic import BaseModel


class ProductSummary(BaseModel):
    """Un producto en una lista de resultados. `imagen` es la ruta de la foto en el gestor."""

    id: str
    titulo: str | None
    marca: str | None
    departamento: str | None
    categoria: str | None
    ruta_categoria: str | None
    precio: float | None
    valoracion: float | None
    valoraciones: int | None
    imagen: str | None


class Product(ProductSummary):
    """La ficha completa: viñetas y descripción."""

    descripcion: str | None


class ScoredProduct(BaseModel):
    """Un producto encontrado, con el puntaje con que se ordenó (BM25 o coseno)."""

    producto: ProductSummary
    puntaje: float


class Location(BaseModel):
    lat: float
    lon: float


class NearbyStore(BaseModel):
    id: int
    nombre: str
    tipo: str
    ubicacion: Location
    metros: float
