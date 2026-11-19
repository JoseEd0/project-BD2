"""El idioma de una pregunta, adivinado por sus palabras vacías: inglés, español o ninguno.

El índice invertido de los fragmentos está en inglés (raíces y palabras vacías del inglés), y
casi todo el corpus también. El analizador inglés no quita «de», «la» ni «qué», así que una
pregunta en español encuentra por esas palabras los documentos del curso, los únicos en
español, aunque trate de otra cosa. BM25 recibe la pregunta sin sus palabras vacías
(`without_stopwords`): le quedan los nombres y términos que comparte con los papers.

Cuenta las palabras de la pregunta que son palabras vacías de cada idioma; en español suman
también las letras con tilde, la eñe y los signos de apertura `¿` `¡`, que el inglés no usa.
Gana el que más cuenta. Sin ninguna (`B+ tree range query`, siglas, nombres) o con empate, el
idioma es `UNKNOWN`: una consulta por palabras clave, que BM25 resuelve bien.
"""

from __future__ import annotations

import re
from enum import StrEnum

WORD = re.compile(r"\w+")
SPANISH_MARKS = re.compile(r"[áéíóúñü¿¡]")
# Lo que rodea a una palabra en una pregunta y no es parte de ella (`B-tree` y `B+` quedan).
PUNCTUATION = "¿?¡!.,;:«»\"'()"
# Palabras frecuentes que solo existen en un idioma: «a», «no» o «sin» no deciden nada.
ENGLISH_STOPWORDS = frozenset(
    {
        "the", "of", "and", "to", "in", "is", "are", "was", "for", "on", "with", "how",
        "what", "why", "which", "who", "when", "where", "does", "do", "did", "can", "by",
        "from", "an", "that", "this", "these", "those", "it", "its", "be", "as", "at", "or",
        "between", "into", "than", "their", "used", "use", "about", "has", "have",
    }
)  # fmt: skip
# Las palabras vacías más frecuentes del español (las primeras de la lista de Snowball, más los
# interrogativos): las que el analizador inglés del índice no quita y que, en una pregunta en
# español, solo encuentran los documentos del curso, los únicos en español.
SPANISH_STOPWORDS = frozenset(
    {
        "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por", "un",
        "para", "con", "no", "una", "su", "al", "lo", "como", "más", "pero", "sus", "le", "ya",
        "o", "este", "sí", "porque", "esta", "entre", "cuando", "muy", "sin", "sobre",
        "también", "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos", "durante",
        "todos", "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante", "ellos", "e",
        "esto", "antes", "algunos", "qué", "unos", "otro", "otras", "otra", "él", "tanto",
        "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual", "poco", "ella", "estar",
        "estas", "algunas", "algo", "esos", "esas", "está", "están", "es", "son", "sea",
        "sean", "era", "eran", "fue", "fueron", "ser", "sido", "ha", "han", "hace", "hacen",
        "tiene", "tienen", "puede", "pueden", "cómo", "cuál", "cuáles", "cuándo", "cuánto",
        "cuánta", "cuántos", "cuántas", "dónde", "quién", "quiénes", "frente", "según",
        "mediante", "tras", "cada", "nuestro", "nuestra",
    }
)  # fmt: skip
# Las que también son palabras del inglés: no dicen en qué idioma está la pregunta.
SHARED_WORDS = frozenset({"a", "no", "e", "o", "me", "he", "has", "son", "era", "ante"})
SPANISH_ONLY = SPANISH_STOPWORDS - SHARED_WORDS


class Language(StrEnum):
    ENGLISH = "en"
    SPANISH = "es"
    UNKNOWN = "unknown"


def detect_language(text: str) -> Language:
    """El idioma más probable de `text` (una pregunta corta), o `UNKNOWN` si no hay indicios."""
    words = WORD.findall(text.casefold())
    english = sum(word in ENGLISH_STOPWORDS for word in words)
    spanish = sum(word in SPANISH_ONLY for word in words) + len(
        SPANISH_MARKS.findall(text.casefold())
    )
    if english > spanish:
        return Language.ENGLISH
    if spanish > english:
        return Language.SPANISH
    return Language.UNKNOWN


def without_stopwords(text: str, language: Language) -> str:
    """`text` sin las palabras vacías de `language` (solo el español tiene lista: el
    analizador del índice ya quita las del inglés). Si no queda nada, `text` tal cual."""
    if language is not Language.SPANISH:
        return text
    kept = [
        token
        for token in text.split()
        if token.strip(PUNCTUATION).casefold() not in SPANISH_STOPWORDS
    ]
    return " ".join(kept) if kept else text
