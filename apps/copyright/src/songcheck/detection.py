"""La votación por desfase: qué canciones del catálogo suenan, alineadas, dentro de una subida.

Buscar cada ventana por separado da falsos positivos: dos canciones del mismo género se
parecen en un tramo suelto. Lo que delata una copia es que **varias ventanas seguidas** de la
subida tengan por vecina a la misma canción con el **mismo desfase** `t_subida - t_original`
(la idea de Shazam y de Content ID). Para cada canción candidata:

1. cada vecino de cada ventana vota por un desfase, medido en saltos de ventana: las ventanas
   de la subida y las del catálogo empiezan en múltiplos del mismo salto, así que el desfase
   de dos ventanas es un número entero de saltos;
2. el apoyo de un desfase son las ventanas con algún voto a un salto o menos de él: una copia
   que no empieza en la rejilla del catálogo reparte sus votos entre dos desfases vecinos;
   a igual apoyo gana el pico del histograma, la mayor similitud por ese desfase exacto;
3. el desfase con más apoyo da las rachas de ventanas consecutivas que lo apoyan **y en las
   que el original avanza**: el tramo elegido para cada ventana no puede repetirse en más de
   dos ventanas seguidas ni ir hacia atrás. Una copia fuera de la rejilla repite tramo una
   vez, porque cada tramo del catálogo solapa mucho con dos ventanas de la subida; un tramo
   que se parece a todo (un «hub» del índice) aparece en muchas ventanas seguidas, su
   desfase crece un salto por ventana y, sin esta condición, la tolerancia lo encadenaría en
   una racha que no copia nada;
4. el desfase de cada racha es el que votan más de sus tramos elegidos, no el desfase con
   que se buscó: los dos difieren cuando un tramo suelto le da un voto de apoyo de más a un
   desfase vecino, y lo que se reporta son esos tramos;
5. las rachas que llegan a `min_run_windows` son coincidencias, y sus ventanas ya no votan
   por otro desfase de la misma canción; se repite con el siguiente desfase hasta que
   ninguno alcanza.

Puntaje = similitud media de la racha · cobertura, y la cobertura es la fracción de las
ventanas que las dos grabaciones podrían compartir: `ventanas de la racha / min(ventanas de
la subida, ventanas del original)`. Dividir solo por las de la subida castigaría un sample de
10 s dentro de una canción de 3 min, justo lo que se busca; dividir solo por las del original,
un fragmento corto de un original largo. Con el mínimo, una copia que cubre todo lo que
podía cubrir vale 1, sea la subida más larga o más corta que el original.

Todo es puro y determinista: el orden de las entradas no cambia la respuesta, y los empates
se rompen por similitud, desfase, id de canción y posición, en ese orden.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Un voto cuenta para su desfase y para los dos vecinos: una copia desplazada media ventana
# respecto de la rejilla del catálogo cae entre dos tramos y vota por los dos.
OFFSET_TOLERANCE_HOPS = 1


class VotingError(ValueError):
    """Los parámetros de la votación no tienen sentido."""


@dataclass(frozen=True, slots=True)
class VotingParameters:
    """El salto de las ventanas (el mismo en la subida y en el catálogo) y la racha más corta
    que cuenta como coincidencia.

    Raises:
        VotingError: si el salto no es positivo o la racha mínima es menor que una ventana.
    """

    hop_seconds: float
    min_run_windows: int

    def __post_init__(self) -> None:
        if self.hop_seconds <= 0 or self.min_run_windows < 1:
            raise VotingError(
                f"el salto tiene que ser positivo y la racha de al menos una ventana; llegaron "
                f"{self.hop_seconds:g} s y {self.min_run_windows}"
            )


@dataclass(frozen=True, slots=True)
class Neighbour:
    """Un tramo del catálogo parecido a una ventana: su canción, dónde empieza y termina en el
    original y la similitud que dio el índice."""

    song_id: int
    start: float
    end: float
    similarity: float


@dataclass(frozen=True, slots=True)
class QueryWindow:
    """Una ventana de la subida, en segundos desde su inicio, y sus vecinos del catálogo."""

    start: float
    end: float
    neighbours: tuple[Neighbour, ...]


@dataclass(frozen=True, slots=True)
class Span:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Run:
    """Ventanas consecutivas de la subida alineadas con una canción por el mismo desfase.

    Attributes:
        song_id: la canción del catálogo.
        offset_seconds: `t_subida - t_original` que votan más de sus tramos elegidos.
        first_window, last_window: posiciones de la primera y la última ventana, incluidas.
        upload: el tramo de la subida que cubren.
        original: el tramo del original que cubren sus mejores vecinos.
        similarities: la similitud del mejor vecino alineado de cada ventana, en orden.
    """

    song_id: int
    offset_seconds: float
    first_window: int
    last_window: int
    upload: Span
    original: Span
    similarities: tuple[float, ...]

    @property
    def windows(self) -> int:
        return self.last_window - self.first_window + 1

    @property
    def mean_similarity(self) -> float:
        return sum(self.similarities) / len(self.similarities)


@dataclass(frozen=True, slots=True)
class Match:
    """Una racha con su puntaje y si pasa el umbral."""

    run: Run
    coverage: float
    score: float
    above_threshold: bool


@dataclass(frozen=True, slots=True)
class _Vote:
    window: int
    offset_hops: int
    neighbour: Neighbour


def aligned_runs(windows: Sequence[QueryWindow], parameters: VotingParameters) -> list[Run]:
    """Las rachas de cada canción que vota alguna ventana, ordenadas por canción y posición.

    Una subida con menos ventanas que `min_run_windows` pide rachas de todas sus ventanas.
    """
    min_run = min(parameters.min_run_windows, len(windows))
    runs: list[Run] = []
    for song_id, votes in sorted(_votes_by_song(windows, parameters.hop_seconds).items()):
        runs.extend(_song_runs(song_id, votes, windows, parameters.hop_seconds, min_run))
    return runs


def coverage(run_windows: int, upload_windows: int, original_windows: int) -> float:
    """La fracción de las ventanas que la subida y el original podrían compartir que cubre una
    racha de `run_windows`, a lo sumo 1."""
    shareable = min(upload_windows, original_windows)
    return min(1.0, run_windows / shareable) if shareable > 0 else 0.0


def score_runs(
    runs: Iterable[Run],
    upload_windows: int,
    original_windows: Mapping[int, int],
    threshold: float,
) -> list[Match]:
    """Las rachas con su puntaje, de mayor a menor; `original_windows` da las ventanas de cada
    canción en el catálogo.

    Raises:
        KeyError: si falta la canción de alguna racha en `original_windows`.
    """
    matches = []
    for run in runs:
        covered = coverage(run.windows, upload_windows, original_windows[run.song_id])
        score = run.mean_similarity * covered
        matches.append(Match(run, covered, score, score >= threshold))
    return sorted(matches, key=_match_order)


def _votes_by_song(windows: Sequence[QueryWindow], hop: float) -> dict[int, list[_Vote]]:
    votes: defaultdict[int, list[_Vote]] = defaultdict(list)
    for position, window in enumerate(windows):
        for neighbour in window.neighbours:
            offset_hops = round((window.start - neighbour.start) / hop)
            votes[neighbour.song_id].append(_Vote(position, offset_hops, neighbour))
    return votes


def _song_runs(
    song_id: int,
    votes: list[_Vote],
    windows: Sequence[QueryWindow],
    hop: float,
    min_run: int,
) -> list[Run]:
    runs: list[Run] = []
    remaining = votes
    tried: set[int] = set()
    while (offset := _best_offset(remaining, tried, min_run)) is not None:
        tried.add(offset)
        found = [chain for chain in _advancing_chains(remaining, offset) if len(chain) >= min_run]
        runs.extend(_run_of(song_id, chain, windows, hop) for chain in found)
        consumed = {vote.window for chain in found for vote in chain}
        remaining = [vote for vote in remaining if vote.window not in consumed]
    return runs


def _best_offset(votes: list[_Vote], tried: set[int], min_run: int) -> int | None:
    """El desfase no probado con más ventanas de apoyo; `None` si ninguno llega a `min_run`.

    A igual apoyo gana el pico del histograma: la mayor similitud de los votos exactamente
    por ese desfase. Una copia alineada con la rejilla trae también a los tramos solapados de
    antes y de después, que apoyan con la tolerancia a los desfases vecinos tanto como al
    suyo, pero con menos similitud. Al final, el desfase menor.
    """
    candidates = {vote.offset_hops for vote in votes} - tried
    if not candidates:
        return None
    best = max(candidates, key=lambda offset: _offset_rank(votes, offset))
    windows, *_ = _offset_rank(votes, best)
    return best if windows >= min_run else None


def _offset_rank(votes: list[_Vote], offset: int) -> tuple[int, float, int]:
    support = _best_aligned_votes(votes, offset, OFFSET_TOLERANCE_HOPS)
    exact = _best_aligned_votes(votes, offset, tolerance_hops=0)
    return len(support), sum(vote.neighbour.similarity for vote in exact.values()), -offset


def _best_aligned_votes(
    votes: list[_Vote], offset: int, tolerance_hops: int = OFFSET_TOLERANCE_HOPS
) -> dict[int, _Vote]:
    """El mejor voto a `tolerance_hops` saltos o menos de `offset` de cada ventana que tiene
    alguno."""
    best: dict[int, _Vote] = {}
    for vote in votes:
        if abs(vote.offset_hops - offset) > tolerance_hops:
            continue
        current = best.get(vote.window)
        if current is None or _vote_rank(vote, offset) < _vote_rank(current, offset):
            best[vote.window] = vote
    return best


def _vote_rank(vote: _Vote, offset: int) -> tuple[float, int, float]:
    return (-vote.neighbour.similarity, abs(vote.offset_hops - offset), vote.neighbour.start)


def _advancing_chains(votes: list[_Vote], offset: int) -> list[list[_Vote]]:
    """Las rachas de ventanas consecutivas con un voto a un salto o menos de `offset` cuyo
    tramo hace avanzar al original (ver `_follower`). Cada ventana se queda con su mejor voto
    que lo haga; si ninguno lo hace, empieza otra racha con el mejor."""
    candidates = _aligned_by_window(votes, offset)
    chains: list[list[_Vote]] = []
    for position in sorted(candidates):
        chain = chains[-1] if chains and chains[-1][-1].window == position - 1 else None
        follower = None if chain is None else _follower(candidates[position], chain)
        if chain is not None and follower is not None:
            chain.append(follower)
        else:
            chains.append([candidates[position][0]])
    return chains


def _aligned_by_window(votes: list[_Vote], offset: int) -> dict[int, list[_Vote]]:
    """Los votos a un salto o menos de `offset` de cada ventana, del mejor al peor."""
    aligned: defaultdict[int, list[_Vote]] = defaultdict(list)
    for vote in votes:
        if abs(vote.offset_hops - offset) <= OFFSET_TOLERANCE_HOPS:
            aligned[vote.window].append(vote)
    return {
        window: sorted(found, key=lambda vote: _vote_rank(vote, offset))
        for window, found in aligned.items()
    }


def _follower(candidates: list[_Vote], chain: list[_Vote]) -> _Vote | None:
    """El mejor voto que sigue a `chain`: un tramo posterior al último, o el mismo si el
    último no lo repetía ya."""
    last = chain[-1].neighbour.start
    repeated = len(chain) > 1 and chain[-2].neighbour.start == last
    return next(
        (
            vote
            for vote in candidates
            if vote.neighbour.start > last or (vote.neighbour.start == last and not repeated)
        ),
        None,
    )


def _chain_offset(chain: list[_Vote]) -> int:
    """El desfase de una racha: el que votan más de sus tramos elegidos; a igual número, el de
    más similitud sumada y después el menor.

    No es el desfase con que se buscó la racha: ese gana por apoyo, y un tramo «hub» que vota
    junto a la racha, en una ventana que ya no la continúa, puede darle una ventana de apoyo
    más a un desfase vecino y hacerlo ganar con la misma racha. Los tramos elegidos son los
    que se reportan, así que su desfase es el que se lee en `original` frente a `upload`.
    """
    tally: defaultdict[int, list[float]] = defaultdict(list)
    for vote in chain:
        tally[vote.offset_hops].append(vote.neighbour.similarity)
    return max(tally, key=lambda offset: (len(tally[offset]), sum(tally[offset]), -offset))


def _run_of(song_id: int, chain: list[_Vote], windows: Sequence[QueryWindow], hop: float) -> Run:
    chosen = [vote.neighbour for vote in chain]
    first, last = chain[0].window, chain[-1].window
    return Run(
        song_id=song_id,
        offset_seconds=_chain_offset(chain) * hop,
        first_window=first,
        last_window=last,
        upload=Span(windows[first].start, windows[last].end),
        original=Span(min(item.start for item in chosen), max(item.end for item in chosen)),
        similarities=tuple(item.similarity for item in chosen),
    )


def _match_order(match: Match) -> tuple[float, int, int]:
    return (-match.score, match.run.song_id, match.run.first_window)
