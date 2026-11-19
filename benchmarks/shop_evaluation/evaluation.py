"""Medir listas recomendadas contra las compras que se dejaron fuera: Hit@k y NDCG@k por
usuario, sus medias con intervalo, la cobertura del catálogo y la popularidad media."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .cases import EvaluationSplit
from .metrics import (
    Interval,
    bootstrap_mean,
    coverage,
    hit_at,
    mean_popularity,
    ndcg_at,
    paired_difference,
    rank_of,
)
from .signals import UserSignals

DIGITS = 4
Recommend = Callable[[UserSignals, frozenset[str]], list[str]]


class Target(StrEnum):
    """Qué compra se deja fuera: la penúltima (para ajustar) o la última (para medir)."""

    VALIDATION = "validación"
    TEST = "prueba"


@dataclass(frozen=True, slots=True)
class Settings:
    """El corte `k` y el bootstrap: remuestreos, confianza y semilla."""

    k: int
    resamples: int
    confidence: float
    seed: int


@dataclass(frozen=True, slots=True)
class Scores:
    """Hit y NDCG de cada usuario de una selección, en el mismo orden."""

    hits: list[float]
    ndcgs: list[float]

    def mean_ndcg(self) -> float:
        return sum(self.ndcgs) / len(self.ndcgs)

    def mean_hit(self) -> float:
        return sum(self.hits) / len(self.hits)


class Evaluator:
    """Las listas y medidas de un conjunto fijo de usuarios.

    Una lista se pide para todos los usuarios a la vez (`lists`) y se mide sobre todos o
    sobre una selección de ellos por posición (el arranque en frío mide grupos).
    """

    def __init__(
        self, users: Sequence[UserSignals], split: EvaluationSplit, settings: Settings
    ) -> None:
        self.users = list(users)
        self.split = split
        self.settings = settings
        self._excluded = [frozenset(user.history) for user in self.users]
        self._targets = {
            Target.VALIDATION: [split.held_out[user.user].validation for user in self.users],
            Target.TEST: [split.held_out[user.user].test for user in self.users],
        }

    def lists(self, recommend: Recommend) -> list[list[str]]:
        """La lista de cada usuario, sin lo que ya compró en el entrenamiento."""
        return [
            recommend(user, excluded)
            for user, excluded in zip(self.users, self._excluded, strict=True)
        ]

    def scores(
        self, lists: Sequence[Sequence[str]], target: Target, selection: Sequence[int]
    ) -> Scores:
        """Hit@k y NDCG@k de los usuarios en las posiciones `selection`."""
        k = self.settings.k
        targets = self._targets[target]
        ranks = [rank_of(targets[index], lists[index][:k]) for index in selection]
        return Scores([hit_at(rank, k) for rank in ranks], [ndcg_at(rank, k) for rank in ranks])

    def everyone(self) -> list[int]:
        return list(range(len(self.users)))

    def target_of(self, index: int, target: Target) -> str:
        return self._targets[target][index]

    def summary(
        self, lists: Sequence[Sequence[str]], target: Target, selection: Sequence[int]
    ) -> dict[str, Any]:
        """Las medidas de una lista para guardar: medias con intervalo, cobertura del
        catálogo, popularidad media y cuántos usuarios entraron."""
        scores = self.scores(lists, target, selection)
        chosen = [lists[index][: self.settings.k] for index in selection]
        return {
            **_interval_fields("hit", self._interval(scores.hits)),
            **_interval_fields("ndcg", self._interval(scores.ndcgs)),
            "cobertura": round(coverage(chosen, len(self.split.catalog)), DIGITS),
            "popularidad_media": round(mean_popularity(chosen, self.split.popularity), DIGITS),
            "usuarios": len(selection),
        }

    def difference(
        self,
        first: Sequence[Sequence[str]],
        second: Sequence[Sequence[str]],
        target: Target,
    ) -> dict[str, Any]:
        """Lo que `first` saca a `second` sobre los mismos usuarios, con intervalo pareado."""
        everyone = self.everyone()
        one, other = self.scores(first, target, everyone), self.scores(second, target, everyone)
        settings = self.settings
        hit = paired_difference(
            one.hits, other.hits, settings.resamples, settings.confidence, settings.seed
        )
        ndcg = paired_difference(
            one.ndcgs, other.ndcgs, settings.resamples, settings.confidence, settings.seed
        )
        return {**_interval_fields("hit", hit), **_interval_fields("ndcg", ndcg)}

    def _interval(self, values: Sequence[float]) -> Interval:
        settings = self.settings
        return bootstrap_mean(values, settings.resamples, settings.confidence, settings.seed)


def _interval_fields(name: str, interval: Interval) -> dict[str, float]:
    return {
        name: round(interval.mean, DIGITS),
        f"{name}_bajo": round(interval.low, DIGITS),
        f"{name}_alto": round(interval.high, DIGITS),
    }
