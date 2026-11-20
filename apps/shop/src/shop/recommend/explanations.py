"""Por qué se recomienda un producto, en español y con lo que dijo cada señal que aportó."""

from __future__ import annotations

from enum import Enum

from .candidates import CopurchaseEvidence, Signal, StockEvidence
from .fusion import Contribution, FusedProduct

METERS_PER_KILOMETER = 1000.0
METERS_ROUNDING = 10
REASON_SEPARATOR = " · "


class Scope(Enum):
    """De qué parte la recomendación: un producto («este producto») o las compras de un
    usuario («tus compras»)."""

    PRODUCT = "producto"
    USER = "usuario"


class MissingEvidenceError(ValueError):
    """Una señal que se explica con su evidencia llegó sin ella."""


def explanation(fused: FusedProduct, scope: Scope) -> str:
    """Una frase por señal que aportó, de la que más aportó a la que menos."""
    return REASON_SEPARATOR.join(reason(part, scope) for part in contributed(fused))


def contributed(fused: FusedProduct) -> tuple[Contribution, ...]:
    """Los aportes que sumaron algo al puntaje. Una señal con peso 0 también propone
    candidatos, pero decir «se ve parecido» de un producto que la imagen no movió engañaría."""
    return tuple(part for part in fused.contributions if part.weighted > 0)


def reason(contribution: Contribution, scope: Scope) -> str:
    """La frase de una señal: «comprado junto con este producto por 12 clientes», «se ve
    parecido», «descripción parecida» o «en stock a 1,2 km en Centro».

    Raises:
        MissingEvidenceError: si las compras conjuntas o la cercanía llegan sin evidencia.
    """
    evidence = contribution.evidence
    if contribution.signal is Signal.COPURCHASE:
        if not isinstance(evidence, CopurchaseEvidence):
            raise MissingEvidenceError("las compras conjuntas llegaron sin sus clientes")
        return _bought_together(evidence, scope)
    if contribution.signal is Signal.NEARBY:
        if not isinstance(evidence, StockEvidence):
            raise MissingEvidenceError("la cercanía llegó sin su tienda")
        return f"en stock a {format_distance(evidence.meters)} en {evidence.store}"
    if contribution.signal is Signal.VISUAL:
        return "se ve parecido" if scope is Scope.PRODUCT else "se ve parecido a tus compras"
    if scope is Scope.PRODUCT:
        return "descripción parecida"
    return "descripción parecida a tus compras"


def format_distance(meters: float) -> str:
    """`850 m` por debajo de un kilómetro (a la decena) y `1,2 km` desde ahí, con coma
    decimal."""
    rounded = round(meters / METERS_ROUNDING) * METERS_ROUNDING
    if rounded < METERS_PER_KILOMETER:
        return f"{max(rounded, METERS_ROUNDING)} m"
    kilometers = f"{meters / METERS_PER_KILOMETER:.1f}".replace(".", ",")
    return f"{kilometers} km"


def _bought_together(evidence: CopurchaseEvidence, scope: Scope) -> str:
    customers = "1 cliente" if evidence.customers == 1 else f"{evidence.customers} clientes"
    if scope is Scope.PRODUCT:
        return f"comprado junto con este producto por {customers}"
    purchases = "una" if evidence.purchases == 1 else str(evidence.purchases)
    return f"comprado junto con {purchases} de tus compras por {customers}"
