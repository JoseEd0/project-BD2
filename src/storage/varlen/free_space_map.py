"""Mapa de espacio libre: cuánto cabe en cada página de datos, un byte por página.

El archivo se reparte en grupos de una página de mapa seguida de las páginas de datos que
ese mapa describe, tantas como bytes tiene una página:

    ┌──────────┬───────┬────────┬─────┬────────┬───────┬────────┬─────┐
    │ cabecera │ mapa 0│ datos  │ ... │ datos  │ mapa 1│ datos  │ ... │
    └──────────┴───────┴────────┴─────┴────────┴───────┴────────┴─────┘
       página 0  página 1  ← page_size páginas →   página 2 + page_size

Las posiciones son fijas: de un número de página se calcula, sin leer nada, si es de mapa o
de datos y qué byte de qué mapa la describe. Ningún puntero en disco puede romperse.

Cada byte es una categoría: la fracción de la capacidad de una página vacía que todavía
cabe en ella, en 255 escalones. La categoría de una página se redondea hacia abajo y la que
pide un fragmento hacia arriba, así que una página que la alcanza tiene sitio seguro para
él. La escala es la capacidad y no el tamaño de página para que una página vacía, de
categoría 255, admita un fragmento de página completa con cualquier tamaño de página.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache

from storage.pager import HEADER_PAGE_ID, Pager
from storage.record_id import BITS_PER_BYTE

CATEGORY_LEVELS = 1 << BITS_PER_BYTE
FULL_CATEGORY = CATEGORY_LEVELS - 1
FIRST_MAP_PAGE = HEADER_PAGE_ID + 1
# La categoría 0 también es la de las páginas que el archivo aún no tiene; pedir al menos 1
# evita entregarlas y entregar una página sin sitio ni para una ranura nueva.
MIN_WANTED_CATEGORY = 1
MATCH = 1


class FreeSpaceMap:
    """Encuentra una página de datos con sitio para un fragmento sin recorrer el archivo.

    Guarda en memoria solo la categoría más alta de cada página de mapa, una por cada
    `page_size` páginas de datos: con eso `find` lee como mucho una página de mapa.
    """

    def __init__(self, pager: Pager, page_capacity: int) -> None:
        """`page_capacity` es lo que cabe en una página de datos vacía."""
        self._pager = pager
        self._page_capacity = page_capacity
        self._group_size = 1 + pager.page_size
        self._maxima = [max(pager.read(page_id)) for page_id in self._map_pages()]

    @property
    def map_page_count(self) -> int:
        return len(self._maxima)

    @property
    def data_page_count(self) -> int:
        return self._pager.page_count - FIRST_MAP_PAGE - self.map_page_count

    def is_data_page(self, page_id: int) -> bool:
        return (
            FIRST_MAP_PAGE < page_id < self._pager.page_count
            and (page_id - FIRST_MAP_PAGE) % self._group_size != 0
        )

    def data_pages(self) -> Iterator[int]:
        """Las páginas de datos en orden físico."""
        for map_page in self._map_pages():
            yield from range(map_page + 1, min(map_page + self._group_size, self._pager.page_count))

    def find(self, size: int) -> int | None:
        """La primera página de datos donde caben `size` bytes, o `None` si ninguna.

        Lee una página de mapa, o ninguna si ningún grupo tiene sitio.
        """
        wanted = max(MIN_WANTED_CATEGORY, -(-size * FULL_CATEGORY // self._page_capacity))
        for group, maximum in enumerate(self._maxima):
            if maximum >= wanted:
                categories = self._pager.read(self._map_page(group))
                index = categories.translate(_at_least(wanted)).find(MATCH)
                return self._map_page(group) + 1 + index
        return None

    def record(self, page_id: int, insertable_bytes: int) -> None:
        """Anota cuánto cabe ahora en la página de datos."""
        group, index = divmod(page_id - FIRST_MAP_PAGE, self._group_size)
        position = index - 1
        category = insertable_bytes * FULL_CATEGORY // self._page_capacity
        categories = bytearray(self._pager.read(self._map_page(group)))
        previous = categories[position]
        if previous == category:
            return
        categories[position] = category
        self._pager.write(self._map_page(group), bytes(categories))
        if category > self._maxima[group]:
            self._maxima[group] = category
        elif previous == self._maxima[group]:
            self._maxima[group] = max(categories)

    def allocate(self) -> int:
        """Una página de datos nueva al final del archivo. Si su posición es la de una página
        de mapa, antes añade esa página, con todas sus categorías en 0."""
        page_id = self._pager.allocate()
        if (page_id - FIRST_MAP_PAGE) % self._group_size == 0:
            self._maxima.append(0)
            page_id = self._pager.allocate()
        return page_id

    def _map_pages(self) -> range:
        return range(FIRST_MAP_PAGE, self._pager.page_count, self._group_size)

    def _map_page(self, group: int) -> int:
        return FIRST_MAP_PAGE + group * self._group_size


@cache
def _at_least(wanted: int) -> bytes:
    """Tabla de `bytes.translate` que marca con `MATCH` las categorías de al menos `wanted`:
    la búsqueda de la primera página con sitio corre entera en C."""
    return bytes(MATCH if category >= wanted else 0 for category in range(CATEGORY_LEVELS))
