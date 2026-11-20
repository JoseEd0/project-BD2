"""Corrección de búsquedas mal escritas («guitr» → «guitar») contra el vocabulario de las
fichas, con distancia de edición. La búsqueda sigue siendo el BM25 del gestor: esto solo
cambia las palabras que se le mandan."""

from .distance import edit_distance
from .vocabulary import Vocabulary, load_vocabulary

__all__ = ["Vocabulary", "edit_distance", "load_vocabulary"]
