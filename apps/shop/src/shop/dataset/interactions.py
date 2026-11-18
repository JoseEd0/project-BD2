"""Las compras del 5-core oficial y la partición dejando fuera la última de cada usuario.

Cada fila del 5-core es una reseña verificada: un usuario que compró un producto, con su
nota y su fecha en milisegundos. Antes de escribir nada se comprueba que el conjunto siga
siendo un 5-core y que la partición sea la que se usa para evaluar: la última compra de
cada usuario en la prueba, la penúltima en la validación y el resto en el entrenamiento.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shop.dataset.errors import CoreError, SplitError
from shop.dataset.sources import Split

USER_COLUMN = "user_id"
PRODUCT_COLUMN = "parent_asin"
RATING_COLUMN = "rating"
TIMESTAMP_COLUMN = "timestamp"
MILLISECONDS_PER_SECOND = 1000
HELD_OUT_SPLITS = (Split.VALID, Split.TEST)


@dataclass(frozen=True, slots=True)
class Interaction:
    """Una compra con reseña: quién, qué, con qué nota, cuándo y en qué partición."""

    user_id: str
    product_id: str
    rating: float
    timestamp_ms: int
    split: Split

    @property
    def purchased_at(self) -> str:
        """La fecha en ISO 8601, en UTC, con milisegundos."""
        moment = datetime.fromtimestamp(self.timestamp_ms / MILLISECONDS_PER_SECOND, tz=UTC)
        return moment.isoformat(timespec="milliseconds")


def read_split(path: Path, split: Split) -> Iterator[Interaction]:
    """Las filas de un CSV de partición del benchmark (`user_id, parent_asin, rating,
    timestamp`)."""
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield Interaction(
                user_id=row[USER_COLUMN],
                product_id=row[PRODUCT_COLUMN],
                rating=float(row[RATING_COLUMN]),
                timestamp_ms=int(row[TIMESTAMP_COLUMN]),
                split=split,
            )


def verify_core(interactions: Iterable[Interaction], minimum: int) -> None:
    """Comprueba que cada usuario y cada producto tenga al menos `minimum` interacciones.

    Raises:
        CoreError: con el primer usuario o producto que no llega, si alguno no llega.
    """
    items = list(interactions)
    for label, counts in (
        ("usuario", Counter(item.user_id for item in items)),
        ("producto", Counter(item.product_id for item in items)),
    ):
        short = sorted(key for key, count in counts.items() if count < minimum)
        if short:
            raise CoreError(
                f"{len(short)} de {len(counts)} con menos de {minimum} interacciones; "
                f"por ejemplo, el {label} {short[0]}"
            )


def verify_leave_last_out(interactions: Iterable[Interaction]) -> None:
    """Comprueba que cada usuario tenga una sola compra en validación y una en prueba, y
    que ninguna compra anterior quede después de ellas (empates de fecha permitidos).

    Raises:
        SplitError: con el primer usuario que no cumple.
    """
    by_user: defaultdict[str, list[Interaction]] = defaultdict(list)
    for item in interactions:
        by_user[item.user_id].append(item)
    for user, items in sorted(by_user.items()):
        problem = _leave_last_out_problem(items)
        if problem:
            raise SplitError(f"el usuario {user} {problem}")


def chronological(interactions: Iterable[Interaction]) -> list[Interaction]:
    """Las interacciones por fecha; los empates, por usuario y producto."""
    return sorted(
        interactions, key=lambda item: (item.timestamp_ms, item.user_id, item.product_id)
    )


def counts_by_user(interactions: Iterable[Interaction]) -> dict[str, int]:
    """Cuántas interacciones tiene cada usuario, ordenados por id."""
    counts = Counter(item.user_id for item in interactions)
    return dict(sorted(counts.items()))


def train_popularity(interactions: Iterable[Interaction]) -> Counter[str]:
    """Compras de cada producto en el entrenamiento: lo que puede usar quien no debe ver
    la validación ni la prueba."""
    return Counter(item.product_id for item in interactions if item.split is Split.TRAIN)


def _leave_last_out_problem(items: list[Interaction]) -> str:
    held_out: dict[Split, list[Interaction]] = {split: [] for split in HELD_OUT_SPLITS}
    train: list[Interaction] = []
    for item in items:
        held_out.get(item.split, train).append(item)
    for split, chosen in held_out.items():
        if len(chosen) != 1:
            return f"tiene {len(chosen)} compras en {split}, no 1"
    valid, test = held_out[Split.VALID][0], held_out[Split.TEST][0]
    if valid.timestamp_ms > test.timestamp_ms:
        return "tiene la validación después de la prueba"
    if any(item.timestamp_ms > valid.timestamp_ms for item in train):
        return "tiene una compra de entrenamiento después de la validación"
    return ""
