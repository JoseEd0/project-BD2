"""Las frases de cada señal, en español."""

from __future__ import annotations

import pytest

from shop.recommend.candidates import CopurchaseEvidence, Signal, StockEvidence
from shop.recommend.explanations import (
    MissingEvidenceError,
    Scope,
    contributed,
    explanation,
    format_distance,
    reason,
)
from shop.recommend.fusion import Contribution, FusedProduct


def part(signal: Signal, evidence: object = None, weighted: float = 0.1) -> Contribution:
    return Contribution(signal, 1.0, weighted, evidence)  # type: ignore[arg-type]


def test_bought_together_names_the_customers():
    together = part(Signal.COPURCHASE, CopurchaseEvidence(12, 1))
    assert reason(together, Scope.PRODUCT) == "comprado junto con este producto por 12 clientes"
    alone = part(Signal.COPURCHASE, CopurchaseEvidence(1, 1))
    assert reason(alone, Scope.PRODUCT) == "comprado junto con este producto por 1 cliente"


def test_bought_together_for_a_user_counts_their_purchases():
    one = part(Signal.COPURCHASE, CopurchaseEvidence(5, 1))
    several = part(Signal.COPURCHASE, CopurchaseEvidence(7, 3))
    assert reason(one, Scope.USER) == "comprado junto con una de tus compras por 5 clientes"
    assert reason(several, Scope.USER) == "comprado junto con 3 de tus compras por 7 clientes"


def test_looks_and_reads_alike():
    assert reason(part(Signal.VISUAL), Scope.PRODUCT) == "se ve parecido"
    assert reason(part(Signal.TEXT), Scope.PRODUCT) == "descripción parecida"
    assert reason(part(Signal.VISUAL), Scope.USER) == "se ve parecido a tus compras"
    assert reason(part(Signal.TEXT), Scope.USER) == "descripción parecida a tus compras"


def test_stock_nearby_names_store_and_distance():
    stock = part(Signal.NEARBY, StockEvidence("Centro", 1234.0))
    assert reason(stock, Scope.PRODUCT) == "en stock a 1,2 km en Centro"


@pytest.mark.parametrize(
    ("meters", "text"),
    [(3.0, "10 m"), (847.0, "850 m"), (996.0, "1,0 km"), (1250.0, "1,2 km"), (15000.0, "15,0 km")],
)
def test_distances_are_written_in_meters_or_kilometers(meters: float, text: str):
    assert format_distance(meters) == text


def test_the_explanation_follows_the_contributions_in_order():
    fused = FusedProduct(
        "a",
        0.5,
        (part(Signal.VISUAL, weighted=0.3), part(Signal.TEXT, weighted=0.2)),
    )
    assert explanation(fused, Scope.PRODUCT) == "se ve parecido · descripción parecida"


def test_a_signal_with_weight_zero_proposes_but_is_not_explained():
    text = part(Signal.TEXT, weighted=0.2)
    fused = FusedProduct("a", 0.2, (text, part(Signal.VISUAL, weighted=0.0)))
    assert contributed(fused) == (text,)
    assert explanation(fused, Scope.PRODUCT) == "descripción parecida"


def test_a_signal_without_its_evidence_is_an_error():
    with pytest.raises(MissingEvidenceError):
        reason(part(Signal.COPURCHASE), Scope.PRODUCT)
    with pytest.raises(MissingEvidenceError):
        reason(part(Signal.NEARBY), Scope.PRODUCT)
