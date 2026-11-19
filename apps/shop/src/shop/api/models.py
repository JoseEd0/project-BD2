"""Lo que responde el API de la tienda."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shop.catalog.schema import PRODUCT_ID_LENGTH


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


class SignalQuery(BaseModel):
    """Una señal tal como corrió: el SQL exacto, sus parámetros (un vector, por su dimensión),
    cuántos candidatos dio y los milisegundos que tardó."""

    senal: str
    sql: str
    parametros: list[Any]
    candidatos: int
    milisegundos: float


class Reason(BaseModel):
    """Lo que aportó una señal: su puntaje normalizado, por su peso, y su frase."""

    senal: str
    aporte: float
    texto: str


class Recommendation(BaseModel):
    producto: ProductSummary
    puntaje: float
    motivos: list[Reason]
    explicacion: str


class Recommendations(BaseModel):
    """Las recomendaciones, con los pesos efectivos, si fue arranque en frío y las consultas
    de cada señal."""

    origen: str
    arranque_en_frio: bool
    pesos: dict[str, float]
    version_pesos: str
    senales: list[SignalQuery]
    recomendaciones: list[Recommendation]


class PurchaseRequest(BaseModel):
    producto: str = Field(min_length=1, max_length=PRODUCT_ID_LENGTH)


class PurchaseRecorded(BaseModel):
    id: int
    usuario: str
    producto: str
    instante: int
    particion: str
    pares_sumados: int
    pares_nuevos: int
