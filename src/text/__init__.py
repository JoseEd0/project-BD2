"""Análisis de texto para la búsqueda de texto completo: plegado, tokens, stopwords y raíces.

Un solo analizador para documentos y consultas, de modo que una consulta corta el texto
exactamente igual que el índice que la responde.
"""

from .analyzer import TERM_CACHE_GENERATION_ENTRIES, TOKEN_PATTERN, AnalyzedText, Analyzer
from .languages import DEFAULT_LANGUAGE, Language, UnknownLanguageError, language_named
from .normalization import fold

__all__ = [
    "DEFAULT_LANGUAGE",
    "TERM_CACHE_GENERATION_ENTRIES",
    "TOKEN_PATTERN",
    "AnalyzedText",
    "Analyzer",
    "Language",
    "UnknownLanguageError",
    "fold",
    "language_named",
]
