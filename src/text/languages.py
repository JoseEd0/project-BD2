"""Los idiomas que entiende el analizador: su lista de stopwords y su stemmer Snowball.

Es el único registro de idiomas del motor. Añadir uno es añadir su archivo a `stopwords/`
y una línea a `_LANGUAGES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from errors import DomainError

from .normalization import fold

DEFAULT_LANGUAGE = "es"
STOPWORDS_DIRECTORY = "stopwords"
STOPWORDS_ENCODING = "utf-8"
# Formato de las listas de Snowball: la palabra va al inicio de la línea y todo lo que
# sigue a la barra vertical es comentario.
STOPWORDS_COMMENT = "|"


class UnknownLanguageError(DomainError):
    """El código no corresponde a ningún idioma con stopwords y stemmer."""


@dataclass(frozen=True, slots=True)
class Language:
    """Un idioma del analizador.

    Attributes:
        code: código con el que se elige en SQL (`WITH (idioma='es')`).
        stemmer_algorithm: nombre del algoritmo en `snowballstemmer`.
        stopwords: palabras vacías ya plegadas con `fold`, la misma forma que tienen los
            tokens cuando se comparan con ellas.
    """

    code: str
    stemmer_algorithm: str
    stopwords: frozenset[str]


def language_named(code: str) -> Language:
    """Idioma con ese código, sin distinguir mayúsculas ni espacios alrededor.

    Raises:
        UnknownLanguageError: si el código no es de ningún idioma registrado.
    """
    try:
        return _LANGUAGES[code.strip().lower()]
    except KeyError:
        known = ", ".join(sorted(_LANGUAGES))
        raise UnknownLanguageError(
            f"idioma desconocido '{code}'; los disponibles son: {known}"
        ) from None


def _load_language(code: str, stemmer_algorithm: str) -> Language:
    return Language(code, stemmer_algorithm, _load_stopwords(code))


def _load_stopwords(code: str) -> frozenset[str]:
    listing = Path(__file__).parent / STOPWORDS_DIRECTORY / f"{code}.txt"
    lines = listing.read_text(encoding=STOPWORDS_ENCODING).splitlines()
    words = (fold(line.split(STOPWORDS_COMMENT, 1)[0].strip()) for line in lines)
    return frozenset(word for word in words if word)


_LANGUAGES = {
    language.code: language
    for language in (_load_language("es", "spanish"), _load_language("en", "english"))
}
