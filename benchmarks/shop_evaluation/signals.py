"""Los candidatos de cada señal para cada usuario, pedidos una vez al recomendador de verdad
(`shop.recommend.Recommender`, por HTTP contra el gestor) y guardados en JSON Lines.

Pedirlos es lo caro (unos 0,45 s por usuario); fusionarlos con unos pesos es Python puro.
Así la búsqueda de pesos, las señales solas y el arranque en frío se calculan sobre las
mismas listas, y una ejecución cortada sigue desde el último usuario guardado.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minigestor_client import AsyncClient
from shop.recommend.candidates import CLIP_METHOD, SIFT_METHOD, Candidate, Signal
from shop.recommend.fusion import FusionWeights
from shop.recommend.recommender import (
    Nearby,
    RecommendationRequest,
    Recommendations,
    Recommender,
    user_history,
)

Found = dict[Signal, tuple[Candidate, ...]]

USER_FIELD = "usuario"
HISTORY_FIELD = "historial"
CLIP_FIELD = "clip"
SIFT_FIELD = "sift"
ENCODING = "utf-8"
PROGRESS_USERS = 250


@dataclass(frozen=True, slots=True)
class UserSignals:
    """Lo que el recomendador propuso para un usuario a partir de sus compras de
    entrenamiento.

    Attributes:
        history: sus compras de entrenamiento, que el recomendador excluye.
        clip: candidatos de las cuatro señales con CLIP como parecido visual.
        sift: candidatos de compras, SIFT y texto (la misma petición con `visual=sift` y sin
            cercanía).
    """

    user: str
    history: tuple[str, ...]
    clip: Found
    sift: Found

    def to_json(self) -> dict[str, Any]:
        return {
            USER_FIELD: self.user,
            HISTORY_FIELD: list(self.history),
            CLIP_FIELD: _found_to_json(self.clip),
            SIFT_FIELD: _found_to_json(self.sift),
        }

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> UserSignals:
        return cls(
            str(document[USER_FIELD]),
            tuple(document[HISTORY_FIELD]),
            _found_from_json(document[CLIP_FIELD]),
            _found_from_json(document[SIFT_FIELD]),
        )


@dataclass(frozen=True, slots=True)
class Collection:
    """Cómo se le pide al recomendador: la partición que lo alimenta, cuántas recomendaciones
    y desde dónde (la cercanía necesita una ubicación y los usuarios del dataset no la
    tienen)."""

    split: str
    limit: int
    weights: FusionWeights
    nearby: Nearby


def read_signals(path: Path) -> dict[str, UserSignals]:
    """Los usuarios ya guardados en `path`, o ninguno si no existe."""
    if not path.exists():
        return {}
    with path.open(encoding=ENCODING) as handle:
        found = (UserSignals.from_json(json.loads(line)) for line in handle if line.strip())
        return {signals.user: signals for signals in found}


async def collect(
    engine: AsyncClient,
    recommender: Recommender,
    users: Iterable[str],
    collection: Collection,
    path: Path,
) -> None:
    """Pide al recomendador los candidatos de cada usuario de `users` y los añade a `path`,
    uno por línea, a medida que llegan."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=ENCODING) as handle:
        for done, user in enumerate(users, start=1):
            signals = await _user_signals(engine, recommender, user, collection)
            handle.write(json.dumps(signals.to_json(), ensure_ascii=False) + "\n")
            if done % PROGRESS_USERS == 0:
                handle.flush()
                logging.info("candidatos de %d usuarios", done)


async def _user_signals(
    engine: AsyncClient, recommender: Recommender, user: str, collection: Collection
) -> UserSignals:
    history = await user_history(engine, user, collection.split)
    with_clip = await recommender.for_user(
        user,
        RecommendationRequest(
            collection.limit, CLIP_METHOD, collection.weights, collection.nearby
        ),
    )
    with_sift = await recommender.for_user(
        user, RecommendationRequest(collection.limit, SIFT_METHOD, collection.weights)
    )
    return UserSignals(user, tuple(history), _found(with_clip), _found(with_sift))


def _found(recommendations: Recommendations) -> Found:
    return {run.signal: run.candidates for run in recommendations.runs}


def _found_to_json(found: Found) -> dict[str, list[list[Any]]]:
    return {
        signal.value: [[candidate.product, candidate.score] for candidate in candidates]
        for signal, candidates in found.items()
    }


def _found_from_json(document: Mapping[str, Iterable[Iterable[Any]]]) -> Found:
    return {
        Signal(signal): tuple(Candidate(str(product), float(score)) for product, score in pairs)
        for signal, pairs in document.items()
    }


def pending(users: Iterable[str], done: Mapping[str, UserSignals]) -> Iterator[str]:
    """Los usuarios de `users` que todavía no están guardados."""
    return (user for user in users if user not in done)
