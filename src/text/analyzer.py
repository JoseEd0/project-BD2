"""El analizador que convierte un texto en términos, el mismo para documentos y consultas.

Si el índice y la consulta no cortaran el texto exactamente igual, una consulta no
encontraría lo que se indexó; por eso solo hay un camino: plegar (`fold`), cortar en tokens,
filtrar por longitud y stopwords y reducir cada palabra a su raíz.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import snowballstemmer

from config import EngineConfig

from .languages import DEFAULT_LANGUAGE, language_named
from .normalization import fold

# Un token es una cifra o una palabra, y entre una y otra siempre hay un corte: «128gb» y
# «128 GB», «3,5mm» y «3.5 mm», «iPhone12» e «iPhone 12» dan los mismos términos, y en
# las fichas de producto las dos formas aparecen por igual. Una cifra puede llevar
# separadores decimales o de miles entre dígitos. Todo lo demás (guiones, puntos,
# barras, espacios) separa. Una palabra nunca contiene un dígito, así que el primer
# carácter de un token dice si es una cifra.
TOKEN_PATTERN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+")
DECIMAL_COMMA = ","
DECIMAL_POINT = "."
# El vocabulario sigue la ley de Zipf: unas decenas de miles de palabras cubren casi todas
# las apariciones, mientras que la cola (modelos, marcas, erratas) no deja de crecer con el
# corpus. La caché guarda como mucho dos generaciones de este tamaño: se queda con las
# frecuentes sin crecer con la cola.
TERM_CACHE_GENERATION_ENTRIES = 1 << 16
# Ningún término contiene un carácter de control, así que este valor marca en la caché un
# token que no está, distinto del `None` de un token que se descarta.
_UNCACHED = "\x00"


@dataclass(frozen=True, slots=True)
class AnalyzedText:
    """Los términos de un texto con su frecuencia, lo que SPIMI y el ranking necesitan.

    Attributes:
        frequencies: veces que aparece cada término.
        length: número de términos del texto, repeticiones incluidas; es la longitud del
            documento que usan la normalización del coseno y BM25.
    """

    frequencies: Mapping[str, int]
    length: int


class Analyzer:
    """Analizador de texto de un idioma.

    Recuerda el término de cada token que ya vio —su raíz, su cifra con punto decimal o
    `None` si se descarta— en dos generaciones de `TERM_CACHE_GENERATION_ENTRIES`
    tokens: cuando la reciente se llena pasa a ser la anterior y la anterior se olvida;
    un token que se encuentra en la anterior vuelve a la reciente.

    Es seguro entre hilos: el stemmer de Snowball guarda estado mientras procesa una
    palabra, así que se protege con un candado, que solo se toma cuando el token no está
    en la caché. Dos hilos pueden calcular el mismo término a la vez; dan el mismo valor.

    Raises:
        UnknownLanguageError: al crearse, si el idioma no está registrado.
    """

    def __init__(self, config: EngineConfig, language: str = DEFAULT_LANGUAGE) -> None:
        registered = language_named(language)
        self.language = registered.code
        self._stopwords = registered.stopwords
        self._min_token_length = config.text_min_token_length
        self._max_token_length = config.text_max_token_length
        self._stemmer = snowballstemmer.stemmer(registered.stemmer_algorithm)
        self._stemmer_lock = threading.Lock()
        self._recent_terms: dict[str, str | None] = {}
        self._previous_terms: dict[str, str | None] = {}

    def terms(self, text: str) -> list[str]:
        """Términos del texto en el orden en que aparecen, con repeticiones.

        Las cifras no pasan por las stopwords ni por el stemmer; su coma decimal se
        escribe como punto, de modo que «3,5» y «3.5» son el mismo término.
        """
        # Si la caché rota a mitad del texto, `recent` sigue siendo la generación que ahora
        # es la anterior: lo que no esté en ella se busca por el camino lento, con el mismo
        # resultado.
        recent = self._recent_terms
        terms = []
        for token in TOKEN_PATTERN.findall(fold(text)):
            term = recent.get(token, _UNCACHED)
            if term == _UNCACHED:
                term = self._term_of_uncached(token)
            if term is not None:
                terms.append(term)
        return terms

    def analyze(self, text: str) -> AnalyzedText:
        """Frecuencia de cada término y longitud del texto, en una sola pasada."""
        terms = self.terms(text)
        return AnalyzedText(Counter(terms), len(terms))

    def _term_of_uncached(self, token: str) -> str | None:
        term = self._previous_terms.get(token, _UNCACHED)
        if term == _UNCACHED:
            term = self._term_of(token)
        if len(self._recent_terms) >= TERM_CACHE_GENERATION_ENTRIES:
            self._previous_terms = self._recent_terms
            self._recent_terms = {}
        self._recent_terms[token] = term
        return term

    def _term_of(self, token: str) -> str | None:
        if not self._min_token_length <= len(token) <= self._max_token_length:
            return None
        if token[0].isdecimal():
            return token.replace(DECIMAL_COMMA, DECIMAL_POINT)
        if token in self._stopwords:
            return None
        with self._stemmer_lock:
            stem: str = self._stemmer.stemWord(token)
        return stem
