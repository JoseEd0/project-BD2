"""El ranking de los documentos que contienen algún término de la consulta: TF-IDF con
similitud del coseno (SMART lnc.ltc) y BM25, sobre el mismo índice.

Los dos se evalúan término a término: cada lista de postings se recorre una vez y suma la
contribución de su término al acumulador de cada documento. La lista de un término es la
de todos los segmentos del índice, una tras otra, así que el ranking no sabe cuántos hay.
Una consulta booleana (`AND`, `OR`, `NOT`) primero mezcla las listas para saber qué
documentos la cumplen y después puntúa solo a esos, con sus términos positivos.
Ver `README.md` para las fórmulas y las decisiones (términos repetidos, empates, puntajes
nulos, borrados).
"""

from __future__ import annotations

import heapq
import math
from array import array
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from functools import partial
from typing import NamedTuple

from config import EngineConfig, bm25_parameters_error
from storage.record_id import RecordId
from text import Analyzer, BooleanExpression, is_boolean_query, parse_boolean_query

from .format import InvertedIndexError
from .index import InvertedIndex, TermEntry
from .merges import DocIds, matching_documents
from .postings import PostingList
from .weights import log_frequency_weight

# La corrección de Robertson y Spärck Jones: medio documento a cada lado del cociente, para
# que el idf no se indefina cuando el término está en ninguno o en todos los documentos.
BM25_DOCUMENT_FREQUENCY_SMOOTHING = 0.5
# Cada contribución se redondea al sumarse, y un puntaje puede pasar la suma de sus cotas
# por unos pocos ulp (del orden de 1e-16 en relativo). La poda infla las cotas en este
# factor para no descartar nunca, por redondeo, un documento que alcanza el umbral.
UPPER_BOUND_SLACK_FACTOR = 1 + 1e-9

Accumulators = dict[int, float]
# Puntaje negado y `doc_id`: lo que ordena el montículo. A igual puntaje, el orden de
# salida es el de la dirección de la fila, que se resuelve solo entre los empatados
# (`_in_row_order`): buscarla para cada candidato costaba un 8 % de la consulta.
RankingKey = tuple[float, int]
# Cómo llega al ranking la lista de cada término: entera, o solo con los documentos que
# cumplen la consulta booleana.
PostingsReader = Callable[[TermEntry], PostingList]


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
        postings_read: postings leídos entre todas las listas.
        documents_scored: documentos no borrados que recibieron su puntaje completo.
        pruned_from: con la poda MaxScore de `top_k` con BM25, el primer término cuya
            lista solo se puntuó para los documentos que aún podían quedar entre los `k`
            mejores; `None` si se puntuó todo.
    """

    document_frequencies: dict[str, int] = field(default_factory=dict)
    postings_read: int = 0
    documents_scored: int = 0
    pruned_from: str | None = None


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

    Una consulta con `AND`, `OR`, `NOT` o paréntesis es booleana (`text.boolean_query`):
    entran los documentos que cumplen la expresión, y puntúan solo sus términos
    positivos, con las mismas fórmulas. Una consulta sin operadores sigue el camino de
    siempre.

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
            BooleanQueryError: si la consulta booleana está mal formada.
        """
        if k < 0:
            raise RankingParameterError(f"k tiene que ser al menos 0, no {k}")
        scores = self._scores(query, method, parameters, stats, k)
        if k == 0:
            return []
        # El k-ésimo mejor puntaje: entran los que lo alcanzan, y si varios empatan con él,
        # decide la dirección de la fila entre todos ellos. Buscarlo entre los puntajes
        # sueltos y armar la clave solo de los que entran evita una tupla por candidato.
        best = heapq.nlargest(k, scores.values())
        boundary = best[-1] if len(best) == k else -math.inf
        chosen = [(-score, doc_id) for doc_id, score in scores.items() if score >= boundary]
        return [self._scored(key) for key in self._in_row_order(chosen)[:k]]

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
            BooleanQueryError: si la consulta booleana está mal formada.
        """
        keys = _ranking_keys(self._scores(query, method, parameters, stats, None))
        heapq.heapify(keys)
        return self._pop_in_order(keys)

    def analyzed(self, query: str) -> dict[str, int]:
        """Los términos que puntúan, tal como se buscan —plegados, sin stopwords y
        reducidos a su raíz—, en el orden en que aparecen, con su df; 0 si no están en el
        índice. De una consulta booleana, solo los positivos. No lee ninguna lista de
        postings.

        Raises:
            BooleanQueryError: si la consulta booleana está mal formada.
        """
        stats = RankingStats()
        if is_boolean_query(query):
            self._query_terms(parse_boolean_query(query, self._analyzer).positive_terms, stats)
        else:
            self._query_terms(self._analyzer.analyze(query).frequencies, stats)
        return stats.document_frequencies

    def expression(self, query: str) -> str | None:
        """La expresión booleana tal como se evalúa —términos ya analizados y cada `NOT`
        como resta de un `AND`—, vacía si todos sus operandos son stopwords; `None` si la
        consulta es texto libre.

        Raises:
            BooleanQueryError: si la consulta booleana está mal formada.
        """
        if not is_boolean_query(query):
            return None
        return parse_boolean_query(query, self._analyzer).describe()

    def _scores(
        self,
        query: str,
        method: RankingMethod,
        parameters: Bm25Parameters | None,
        stats: RankingStats | None,
        k: int | None,
    ) -> Accumulators:
        """El puntaje de cada documento que contiene algún término. Con BM25 y `k`
        positivo, solo se garantiza que estén los que pueden quedar entre los `k` mejores
        (ver `_bm25`)."""
        stats = RankingStats() if stats is None else stats
        require_fitting_parameters(method, parameters)
        if is_boolean_query(query):
            boolean = parse_boolean_query(query, self._analyzer)
            query_terms = self._query_terms(boolean.positive_terms, stats)
            read: PostingsReader = _BooleanPostings(self._index, boolean.expression, stats).read
        else:
            query_terms = self._query_terms(self._analyzer.analyze(query).frequencies, stats)
            read = partial(self._read, stats=stats)
        if self._index.total_length == 0:
            # Ningún documento vivo tiene términos: lo que haya en las listas es de
            # documentos borrados, y N o avgdl serían 0 en las fórmulas.
            return {}
        if method is RankingMethod.TF_IDF:
            accumulators = self._tf_idf(query_terms, read)
        else:
            bm25 = self._default_bm25 if parameters is None else parameters
            accumulators = self._bm25(query_terms, bm25, read, stats, k)
        deleted = self._index.document_columns().deleted
        if deleted:
            accumulators = {doc: score for doc, score in accumulators.items() if doc not in deleted}
        stats.documents_scored = len(accumulators)
        return accumulators

    def _query_terms(
        self, frequencies: Mapping[str, int], stats: RankingStats
    ) -> list[_QueryTerm]:
        """Los términos de la consulta que están en el índice. Los que no están no aportan
        a ningún documento y, con TF-IDF, tampoco a la norma de la consulta."""
        query_terms = []
        for term, frequency in frequencies.items():
            entry = self._index.lookup(term)
            stats.document_frequencies[term] = 0 if entry is None else entry.document_frequency
            if entry is not None:
                query_terms.append(_QueryTerm(entry, frequency))
        return query_terms

    def _tf_idf(self, query_terms: list[_QueryTerm], read: PostingsReader) -> Accumulators:
        """lnc.ltc: `Σ_t q_t · (1 + log₁₀ tf_t,d) / ‖d‖`, con `q_t` el peso ltc de la consulta
        ya normalizado. La norma del documento es común a todos sus términos, así que se
        divide una sola vez, al final.

        No poda como BM25: la cota de un término sin estadísticas por lista es `q_t`, la
        de un documento de un solo término, y una ficha tiene decenas. Con las 100 000
        fichas de ESCI la poda casi no se activaba y buscar el umbral costaba más de lo
        que ahorraba (medido en el README)."""
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
            _add_tf_idf(accumulators, read(term.entry), query_weight)
        norms = self._index.document_columns().norms
        return {doc_id: score / norms[doc_id] for doc_id, score in accumulators.items()}

    def _bm25(
        self,
        query_terms: list[_QueryTerm],
        parameters: Bm25Parameters,
        read: PostingsReader,
        stats: RankingStats,
        k: int | None,
    ) -> Accumulators:
        """`Σ_t idf_t · tf · (k1 + 1) / (tf + k1 · (1 - b + b · dl / avgdl))` sobre los
        términos distintos de la consulta.

        Con `k` positivo, poda **MaxScore** (Turtle y Flood, 1995), término a término y
        en el orden de la consulta. Un término aporta como mucho `idf_t · (k1 + 1)`,
        porque `tf / (tf + k1 · (…))` no pasa de 1. Tras cada término, si lo que pueden
        sumar los que faltan no alcanza al `k`-ésimo puntaje parcial, ningún documento
        nuevo puede entrar entre los `k` mejores, y uno que ya está solo sigue si su
        puntaje más esa cota lo alcanza (`_prune`). Desde ahí, de cada lista solo se
        puntúan los documentos que siguen. Sus puntajes son los mismos que sin poda: cada
        uno suma las mismas contribuciones en el mismo orden. En una consulta booleana la
        poda sigue valiendo: solo usa que los parciales crecen, y los candidatos son menos.
        """
        document_count = self._index.document_count
        k1, b = parameters.k1, parameters.b
        scaled_idfs = [
            _bm25_idf(document_count, self._document_frequency(term.entry)) * (k1 + 1)
            for term in query_terms
        ]
        # k1 · (1 - b + b · dl / avgdl) = intercept + slope · dl; `_scores` ya descartó
        # avgdl = 0.
        add = partial(
            _add_bm25,
            intercept=k1 * (1 - b),
            slope=k1 * b / self._index.average_length,
            lengths=self._index.document_columns().lengths,
        )
        return self._accumulate_with_pruning(query_terms, scaled_idfs, add, read, stats, k)

    def _accumulate_with_pruning(
        self,
        query_terms: list[_QueryTerm],
        scaled_idfs: list[float],
        add: Callable[[Accumulators, PostingList, float], None],
        read: PostingsReader,
        stats: RankingStats,
        k: int | None,
    ) -> Accumulators:
        """Término a término, con la poda MaxScore de `_bm25` si `k` es positivo: el
        `scaled_idf` de cada término es a la vez su peso y su cota."""
        accumulators: Accumulators = {}
        seen_bound = 0.0
        pruning = False
        for position, (term, scaled_idf, remaining_bound) in enumerate(
            zip(query_terms, scaled_idfs, _remaining_sums(scaled_idfs), strict=True)
        ):
            postings = read(term.entry)
            if pruning:
                postings = postings.restricted_to(accumulators)
            add(accumulators, postings, scaled_idf)
            seen_bound += scaled_idf
            # Ningún parcial pasa de `seen_bound`: si lo que falta llega a eso, no hay nada
            # que podar; tras el último término, podar ya no ahorra ninguna lista.
            if not k or position == len(query_terms) - 1 or remaining_bound >= seen_bound:
                continue
            pruned = self._prune(accumulators, k, remaining_bound)
            if pruned is None:
                continue
            if not pruning:
                stats.pruned_from = query_terms[position + 1].entry.term
            accumulators = pruned
            pruning = True
        return accumulators

    def _prune(
        self, accumulators: Accumulators, k: int, remaining_bound: float
    ) -> Accumulators | None:
        """Los acumuladores de los documentos que todavía pueden quedar entre los `k`
        mejores, o `None` si los términos que faltan aún pueden meter a cualquiera.

        El umbral es el `k`-ésimo mejor puntaje parcial entre los documentos vivos: los
        puntajes solo crecen, así que nunca pasa del `k`-ésimo final. Un documento se
        descarta solo si su cota queda **por debajo** del umbral, nunca si lo iguala: un
        empate con el `k`-ésimo lo resuelve después la dirección de la fila.
        """
        deleted = self._index.document_columns().deleted
        best = heapq.nlargest(
            k, (score for doc_id, score in accumulators.items() if doc_id not in deleted)
        )
        if len(best) < k or remaining_bound * UPPER_BOUND_SLACK_FACTOR >= best[-1]:
            return None
        threshold = best[-1]
        return {
            doc_id: score
            for doc_id, score in accumulators.items()
            if (score + remaining_bound) * UPPER_BOUND_SLACK_FACTOR >= threshold
            and doc_id not in deleted
        }

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


class _BooleanPostings:
    """Las listas de una consulta booleana. Cada término se lee una sola vez: para mezclar
    las listas y saber qué documentos cumplen la expresión, y para puntuar, donde de cada
    lista solo cuentan esos documentos. Se mezcla la primera vez que se puntúa un término:
    si ninguno positivo está en el índice, no se lee nada."""

    def __init__(
        self, index: InvertedIndex, expression: BooleanExpression | None, stats: RankingStats
    ) -> None:
        self._index = index
        self._expression = expression
        self._stats = stats
        self._lists: dict[str, PostingList] = {}
        self._matching: set[int] | None = None

    def read(self, entry: TermEntry) -> PostingList:
        return self._postings(entry.term).restricted_to(self._matching_documents())

    def _matching_documents(self) -> set[int]:
        """Los documentos que cumplen la expresión, borrados incluidos: `_scores` los quita
        de los acumuladores como en el texto libre, y quitarlos de cada lista o del
        resultado da lo mismo."""
        if self._matching is None:
            self._matching = set(self._found())
        return self._matching

    def _found(self) -> DocIds:
        if self._expression is None:
            return ()
        return matching_documents(self._expression, self._doc_ids)

    def _doc_ids(self, term: str) -> DocIds:
        return self._postings(term).doc_ids

    def _postings(self, term: str) -> PostingList:
        postings = self._lists.get(term)
        if postings is None:
            postings = self._index.postings(term)
            self._stats.postings_read += len(postings)
            self._lists[term] = postings
        return postings


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


def _remaining_sums(bounds: list[float]) -> list[float]:
    """Para cada posición, la suma de las cotas que vienen después."""
    return [math.fsum(bounds[after:]) for after in range(1, len(bounds) + 1)]


def _ranking_keys(scores: Accumulators) -> list[RankingKey]:
    return [(-score, doc_id) for doc_id, score in scores.items()]
