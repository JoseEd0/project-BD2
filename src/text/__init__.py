"""Análisis de texto para la búsqueda de texto completo: plegado, tokens, stopwords y raíces.

Un solo analizador para documentos y consultas, de modo que una consulta corta el texto
exactamente igual que el índice que la responde, y el parser de las consultas booleanas
(`AND`, `OR`, `NOT`) que analiza cada operando con él.
"""

from .analyzer import TERM_CACHE_GENERATION_ENTRIES, TOKEN_PATTERN, AnalyzedText, Analyzer
from .boolean_query import (
    BooleanExpression,
    BooleanQuery,
    BooleanQueryError,
    Conjunction,
    Disjunction,
    Term,
    is_boolean_query,
    parse_boolean_query,
)
from .languages import DEFAULT_LANGUAGE, Language, UnknownLanguageError, language_named
from .normalization import fold

__all__ = [
    "DEFAULT_LANGUAGE",
    "TERM_CACHE_GENERATION_ENTRIES",
    "TOKEN_PATTERN",
    "AnalyzedText",
    "Analyzer",
    "BooleanExpression",
    "BooleanQuery",
    "BooleanQueryError",
    "Conjunction",
    "Disjunction",
    "Language",
    "Term",
    "UnknownLanguageError",
    "fold",
    "is_boolean_query",
    "language_named",
    "parse_boolean_query",
]
