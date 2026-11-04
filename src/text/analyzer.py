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
from functools import lru_cache

import snowballstemmer

from config import EngineConfig

from .languages import DEFAULT_LANGUAGE, language_named
from .normalization import fold

# Un token es una cifra o una palabra, y entre una y otra siempre hay un corte: «128gb» y
# «128 GB», «3,5mm» y «3.5 mm», «iPhone12» e «iPhone 12» dan los mismos términos, y en
# las fichas de producto las dos formas aparecen por igual. Una cifra puede llevar
# separadores decimales o de miles entre dígitos. Todo lo demás (guiones, puntos,
# barras, espacios) separa.
TOKEN_PATTERN = re.compile(r"(\d+(?:[.,]\d+)*)|([^\W\d_]+)")
DECIMAL_COMMA = ","
DECIMAL_POINT = "."
# El vocabulario sigue la ley de Zipf: unas decenas de miles de palabras cubren casi todas
# las apariciones, mientras que la cola (modelos, marcas, erratas) no deja de crecer con el
# corpus. Una caché acotada se queda con las frecuentes sin crecer con la cola.
STEM_CACHE_ENTRIES = 1 << 16


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

    Es seguro entre hilos: el stemmer de Snowball guarda estado mientras procesa una
    palabra, así que se protege con un candado, que solo se toma cuando la palabra no está
    en la caché.

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
        self._stem = lru_cache(maxsize=STEM_CACHE_ENTRIES)(self._stem_uncached)

    def terms(self, text: str) -> list[str]:
        """Términos del texto en el orden en que aparecen, con repeticiones.

        Las cifras no pasan por las stopwords ni por el stemmer; su coma decimal se
        escribe como punto, de modo que «3,5» y «3.5» son el mismo término.
        """
        minimum, maximum = self._min_token_length, self._max_token_length
        stopwords, stem = self._stopwords, self._stem
        return [
            number.replace(DECIMAL_COMMA, DECIMAL_POINT) if number else stem(word)
            for number, word in TOKEN_PATTERN.findall(fold(text))
            if minimum <= len(number or word) <= maximum and word not in stopwords
        ]

    def analyze(self, text: str) -> AnalyzedText:
        """Frecuencia de cada término y longitud del texto, en una sola pasada."""
        terms = self.terms(text)
        return AnalyzedText(Counter(terms), len(terms))

    def _stem_uncached(self, word: str) -> str:
        with self._stemmer_lock:
            stem: str = self._stemmer.stemWord(word)
        return stem
