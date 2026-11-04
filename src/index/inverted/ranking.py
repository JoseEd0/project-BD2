"""El ranking de los documentos que contienen algún término de la consulta: TF-IDF con
similitud del coseno (SMART lnc.ltc) y BM25, sobre el mismo índice.

Los dos se evalúan término a término: cada lista de postings se recorre una vez y suma la
contribución de su término al acumulador de cada documento. La lista de un término es la
de todos los segmentos del índice, una tras otra, así que el ranking no sabe cuántos hay.
Ver `README.md` para las fórmulas y las decisiones (términos repetidos, empates, puntajes
nulos, borrados).
"""

from __future__ import annotations

import heapq
import math
from array import array
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import NamedTuple

from config import EngineConfig, bm25_parameters_error
from storage.record_id import RecordId
from text import Analyzer

from .format import InvertedIndexError
from .index import InvertedIndex, TermEntry
from .postings import PostingList
from .weights import log_frequency_weight

# La corrección de Robertson y Spärck Jones: medio documento a cada lado del cociente, para
# que el idf no se indefina cuando el término está en ninguno o en todos los documentos.
BM25_DOCUMENT_FREQUENCY_SMOOTHING = 0.5

Accumulators = dict[int, float]
# Puntaje negado y `doc_id`: lo que ordena el montículo. A igual puntaje, el orden de
# salida es el de la dirección de la fila, que se resuelve solo entre los empatados
# (`_in_row_order`): buscarla para cada candidato costaba un 8 % de la consulta.
RankingKey = tuple[float, int]


@unique
class RankingMethod(Enum):
    TF_IDF = "TF_IDF"
    BM25 = "BM25"


class RankingParameterError(InvertedIndexError):
    """Una búsqueda rankeada pedida con parámetros que no sirven: `k` negativo, `k1` o `b`
    fuera de rango, o parámetros de BM25 para TF-IDF."""


@dataclass(frozen=True, slots=True)
class Bm25Parameters:
    """`k1` (saturación de la frecuencia) y `b` (normalización por longitud) de BM25.

    Raises:
        RankingParameterError: si `k1` es negativo o no es finito, o `b` no está en [0, 1].
    """

    k1: float
    b: float

    def __post_init__(self) -> None:
        error = bm25_parameters_error(self.k1, self.b)
        if error is not None:
            raise RankingParameterError(error)

    @classmethod
    def from_config(cls, config: EngineConfig) -> Bm25Parameters:
        return cls(config.bm25_k1, config.bm25_b)


class ScoredDocument(NamedTuple):
    """Un documento que contiene algún término de la consulta, con su puntaje."""

    doc_id: int
    record_id: RecordId
    score: float


@dataclass(slots=True)
class RankingStats:
    """El trabajo de una búsqueda, para el plan de ejecución.

    Attributes:
        document_frequencies: cada término de la consulta ya analizado, en el orden en
            que aparece, con su df; 0 si no está en el índice.
        postings_read: postings recorridos entre todas las listas.
        documents_scored: documentos no borrados que recibieron un puntaje.
    """

    document_frequencies: dict[str, int] = field(default_factory=dict)
    postings_read: int = 0
    documents_scored: int = 0


class _QueryTerm(NamedTuple):
    entry: TermEntry
    query_frequency: int


class Ranker:
    """Busca y ordena los documentos de un índice invertido con TF-IDF o con BM25.

    La consulta pasa por el mismo analizador que los documentos, en el idioma con el que
    se construyó el índice. Un documento entra al resultado si contiene algún término de
    la consulta y no está borrado; con TF-IDF puede hacerlo con puntaje 0. A igual
    puntaje, primero la fila de menor dirección: el orden depende solo de las filas
    vivas, no de en qué orden llegaron al índice.

    Con `P` postings en las listas de la consulta y `C` documentos que las contienen,
    evaluar cuesta `O(P)`; la longitud y la norma de cada documento salen de
    `InvertedIndex.document_columns`, que se carga una vez por índice.
    """

    def __init__(self, index: InvertedIndex, config: EngineConfig) -> None:
        self._index = index
        self._analyzer = Analyzer(config, index.language)
        self._default_bm25 = Bm25Parameters.from_config(config)

    def top_k(
        self,
        query: str,
        method: RankingMethod,
        k: int,
        parameters: Bm25Parameters | None = None,
        stats: RankingStats | None = None,
    ) -> list[ScoredDocument]:
        """Los `k` documentos de mayor puntaje, de mayor a menor; a igual puntaje, el de
        menor dirección de fila primero. Con `k = 0` no hay ninguno, como `LIMIT 0`.

        `parameters` es solo para BM25; sin él se usan los de la configuración.
        Complejidad: `O(P + C log k)`.

        Raises:
            RankingParameterError: si `k` es negativo o se dan parámetros para TF-IDF.
        """
        if k < 0:
            raise RankingParameterError(f"k tiene que ser al menos 0, no {k}")
        keys = _ranking_keys(self._scores(query, method, parameters, stats))
        if k == 0:
            return []
        # Uno de más para saber si el último puntaje que entra empata con alguno que queda
        # fuera: entonces decide la dirección de la fila entre todos los empatados.
        best = heapq.nsmallest(k + 1, keys)
        if len(best) > k and best[k][0] == best[k - 1][0]:
            boundary = best[k - 1][0]
            best = [key for key in keys if key[0] <= boundary]
        return [self._scored(key) for key in self._in_row_order(best)[:k]]

    def ranked(
        self,
        query: str,
        method: RankingMethod,
        parameters: Bm25Parameters | None = None,
        stats: RankingStats | None = None,
    ) -> Iterator[ScoredDocument]:
        """Todos los documentos que contienen algún término, en el orden de `top_k`, para
        una consulta con filtros que no se sabe cuántos documentos va a consumir.

        Puntúa al llamarse, `O(P + C)`, y forma un montículo con los `C` documentos; cada
        documento que se pide después cuesta `O(log C)`.

        Raises:
            RankingParameterError: si se dan parámetros para TF-IDF.
        """
        keys = _ranking_keys(self._scores(query, method, parameters, stats))
        heapq.heapify(keys)
        return self._pop_in_order(keys)

    def analyzed(self, query: str) -> dict[str, int]:
        """Los términos de la consulta tal como se buscan —plegados, sin stopwords y
        reducidos a su raíz—, en el orden en que aparecen, con su df; 0 si no están en el
        índice. No lee ninguna lista de postings."""
        stats = RankingStats()
        self._query_terms(query, stats)
        return stats.document_frequencies

    def _scores(
        self,
        query: str,
        method: RankingMethod,
        parameters: Bm25Parameters | None,
        stats: RankingStats | None,
    ) -> Accumulators:
        stats = RankingStats() if stats is None else stats
        require_fitting_parameters(method, parameters)
        query_terms = self._query_terms(query, stats)
        if self._index.total_length == 0:
            # Ningún documento vivo tiene términos: lo que haya en las listas es de
            # documentos borrados, y N o avgdl serían 0 en las fórmulas.
            return {}
        if method is RankingMethod.TF_IDF:
            accumulators = self._tf_idf(query_terms, stats)
        else:
            bm25 = self._default_bm25 if parameters is None else parameters
            accumulators = self._bm25(query_terms, bm25, stats)
        deleted = self._index.document_columns().deleted
        if deleted:
            accumulators = {doc: score for doc, score in accumulators.items() if doc not in deleted}
        stats.documents_scored = len(accumulators)
        return accumulators

    def _query_terms(self, query: str, stats: RankingStats) -> list[_QueryTerm]:
        """Los términos de la consulta que están en el índice. Los que no están no aportan
        a ningún documento y, con TF-IDF, tampoco a la norma de la consulta."""
        query_terms = []
        for term, frequency in self._analyzer.analyze(query).frequencies.items():
            entry = self._index.lookup(term)
            stats.document_frequencies[term] = 0 if entry is None else entry.document_frequency
            if entry is not None:
                query_terms.append(_QueryTerm(entry, frequency))
        return query_terms

    def _tf_idf(self, query_terms: list[_QueryTerm], stats: RankingStats) -> Accumulators:
        """lnc.ltc: `Σ_t q_t · (1 + log₁₀ tf_t,d) / ‖d‖`, con `q_t` el peso ltc de la consulta
        ya normalizado. La norma del documento es común a todos sus términos, así que se
        divide una sola vez, al final."""
        document_count = self._index.document_count
        weights = [
            log_frequency_weight(term.query_frequency)
            * math.log10(document_count / self._document_frequency(term.entry))
            for term in query_terms
        ]
        query_norm = math.sqrt(math.fsum(weight * weight for weight in weights))
        accumulators: Accumulators = {}
        for term, weight in zip(query_terms, weights, strict=True):
            # Si todos los términos están en todos los documentos, el idf los anula y la
            # consulta es el vector nulo: su coseno con cualquier documento se toma como 0.
            query_weight = weight / query_norm if query_norm > 0 else 0.0
            postings = self._read(term.entry, stats)
            _add_tf_idf(accumulators, postings, query_weight)
        norms = self._index.document_columns().norms
        return {doc_id: score / norms[doc_id] for doc_id, score in accumulators.items()}

    def _bm25(
        self, query_terms: list[_QueryTerm], parameters: Bm25Parameters, stats: RankingStats
    ) -> Accumulators:
        """`Σ_t idf_t · tf · (k1 + 1) / (tf + k1 · (1 - b + b · dl / avgdl))` sobre los
        términos distintos de la consulta."""
        if not query_terms:
            return {}
        document_count = self._index.document_count
        k1, b = parameters.k1, parameters.b
        # k1 · (1 - b + b · dl / avgdl) = intercept + slope · dl; `_scores` ya descartó
        # avgdl = 0.
        intercept = k1 * (1 - b)
        slope = k1 * b / self._index.average_length
        lengths = self._index.document_columns().lengths
        accumulators: Accumulators = {}
        for term in query_terms:
            idf = _bm25_idf(document_count, self._document_frequency(term.entry))
            postings = self._read(term.entry, stats)
            _add_bm25(accumulators, postings, idf * (k1 + 1), intercept, slope, lengths)
        return accumulators

    def _document_frequency(self, entry: TermEntry) -> int:
        """El df con que pesa el término. Hasta la próxima fusión cuenta documentos
        borrados, y N no: se acota a N para que el idf de TF-IDF no se vuelva negativo.
        Tras una fusión el df nunca pasa de N y la cota no cambia nada."""
        return min(entry.document_frequency, self._index.document_count)

    def _read(self, entry: TermEntry, stats: RankingStats) -> PostingList:
        postings = self._index.postings_of(entry)
        stats.postings_read += len(postings)
        return postings

    def _in_row_order(self, keys: list[RankingKey]) -> list[RankingKey]:
        """Por puntaje y, a igual puntaje, por dirección de la fila."""
        record_keys = self._index.document_columns().record_keys
        return sorted(keys, key=lambda key: (key[0], record_keys[key[1]]))

    def _scored(self, key: RankingKey) -> ScoredDocument:
        negated_score, doc_id = key
        record_id = self._index.document_columns().record_id(doc_id)
        return ScoredDocument(doc_id, record_id, -negated_score)

    def _pop_in_order(self, keys: list[RankingKey]) -> Iterator[ScoredDocument]:
        """Saca del montículo cada grupo de puntajes iguales y lo entrega en el orden de
        sus filas."""
        while keys:
            tied = [heapq.heappop(keys)]
            while keys and keys[0][0] == tied[0][0]:
                tied.append(heapq.heappop(keys))
            for key in self._in_row_order(tied):
                yield self._scored(key)


def require_fitting_parameters(
    method: RankingMethod, parameters: Bm25Parameters | None
) -> None:
    """Los parámetros son de BM25: pedírselos a TF-IDF es un error, no algo que se ignora.

    Raises:
        RankingParameterError: si se dan parámetros para TF-IDF.
    """
    if method is RankingMethod.TF_IDF and parameters is not None:
        raise RankingParameterError("TF-IDF no tiene parámetros: k1 y b son de BM25")


def _bm25_idf(document_count: int, document_frequency: int) -> float:
    """`ln(1 + (N - df + 0,5) / (df + 0,5))`, el idf de BM25 que usa Lucene.

    El `1 +` lo hace positivo siempre: con el idf de Robertson y Spärck Jones sin él, un
    término en más de la mitad de los documentos restaría puntaje a quien lo contiene.
    """
    smoothing = BM25_DOCUMENT_FREQUENCY_SMOOTHING
    return math.log(
        1 + (document_count - document_frequency + smoothing) / (document_frequency + smoothing)
    )


def _add_tf_idf(accumulators: Accumulators, postings: PostingList, query_weight: float) -> None:
    # Casi todos los tf de una lista son 1, 2 o 3: el peso de cada tf distinto se calcula
    # una vez por lista y no una vez por posting.
    contribution = {
        frequency: query_weight * log_frequency_weight(frequency)
        for frequency in set(postings.term_frequencies)
    }
    current = accumulators.get
    for doc_id, frequency in zip(postings.doc_ids, postings.term_frequencies, strict=True):
        accumulators[doc_id] = current(doc_id, 0.0) + contribution[frequency]


def _add_bm25(
    accumulators: Accumulators,
    postings: PostingList,
    scaled_idf: float,
    intercept: float,
    slope: float,
    lengths: array[int],
) -> None:
    current = accumulators.get
    for doc_id, frequency in zip(postings.doc_ids, postings.term_frequencies, strict=True):
        saturation = frequency / (frequency + intercept + slope * lengths[doc_id])
        accumulators[doc_id] = current(doc_id, 0.0) + scaled_idf * saturation


def _ranking_keys(scores: Accumulators) -> list[RankingKey]:
    return [(-score, doc_id) for doc_id, score in scores.items()]
