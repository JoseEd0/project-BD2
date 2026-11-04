"""Plegado del texto a la forma en que se compara: la misma para documentos, consultas y
listas de stopwords.

El plegado es NFKC, `casefold()`, sin tildes ni diéresis y sin apóstrofos. La ñ se
conserva, porque en español distingue palabras: «año» y «ano» no son la misma.
"""

from __future__ import annotations

import unicodedata
from typing import Final

UNICODE_FORM: Final = "NFKC"
DECOMPOSED_FORM: Final = "NFD"
NONSPACING_MARK = "Mn"
PRESERVED_LETTERS = frozenset("ñÑ")
# El apóstrofo se borra en vez de separar: «L'Oréal» y «Kellogg's» quedan como un solo
# término («loreal», «kelloggs»), que es como se suelen buscar las marcas.
APOSTROPHES = "'\u2019\u02bc"
FIRST_NON_ASCII_CODE_POINT = 0x80
LAST_BMP_CODE_POINT = 0xFFFF


def fold(text: str) -> str:
    """Texto en NFKC, en minúsculas de `casefold()`, sin tildes (salvo la ñ) ni apóstrofos.

    Es idempotente: `fold(fold(texto)) == fold(texto)`. Coste lineal en el texto; el
    borrado de tildes es un solo `str.translate`, sin bucles de Python por carácter.
    """
    return unicodedata.normalize(UNICODE_FORM, text).casefold().translate(_FOLDING_TABLE)


def _folding_table() -> dict[int, str | None]:
    """Tabla de `str.translate`: cada letra con diacrítico va a su letra base y cada marca
    diacrítica suelta se borra.

    Se recorre todo el plano básico una sola vez, al importar el módulo (unos 30 ms), en
    lugar de descomponer cada texto: así el plegado de un documento no paga ninguna
    llamada a `unicodedata` por carácter. Una marca suelta solo llega aquí si NFKC no
    pudo componerla con la letra anterior.
    """
    table: dict[int, str | None] = {ord(apostrophe): None for apostrophe in APOSTROPHES}
    for code_point in range(FIRST_NON_ASCII_CODE_POINT, LAST_BMP_CODE_POINT + 1):
        character = chr(code_point)
        if unicodedata.category(character) == NONSPACING_MARK:
            table[code_point] = None
        elif character not in PRESERVED_LETTERS:
            base = _without_marks(character)
            if base is not None:
                table[code_point] = base
    return table


def _without_marks(character: str) -> str | None:
    """La letra sin sus diacríticos, o `None` si no tiene ninguno."""
    decomposed = unicodedata.normalize(DECOMPOSED_FORM, character)
    base = "".join(part for part in decomposed if unicodedata.category(part) != NONSPACING_MARK)
    return None if base == decomposed else base


_FOLDING_TABLE = _folding_table()
