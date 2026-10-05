"""Validador de los invariantes del hash extendible."""

from collections.abc import Callable

import pytest

from index.hash.extendible_hash import ExtendibleHashIndex, stable_hash
from index.keys import Key
from storage.page import NO_PAGE

HashValidator = Callable[[ExtendibleHashIndex, list[Key]], None]


@pytest.fixture
def assert_hash_is_valid() -> HashValidator:
    return _assert_hash_is_valid


def _assert_hash_is_valid(index: ExtendibleHashIndex, expected_keys: list[Key]) -> None:
    size = index.directory_size
    key_size = index._key_codec.size
    for directory_index in range(size):
        bucket_id = index._directory_entry(directory_index)
        local_depth = index._load(bucket_id).flags
        assert local_depth <= index.global_depth, "la profundidad local superó a la global"
        mask = (1 << local_depth) - 1
        for entry in index._entries_of(bucket_id):
            digest = stable_hash(entry[:key_size])
            assert digest & mask == directory_index & mask, "clave en la cubeta equivocada"
        pointers = sum(1 for other in range(size) if index._directory_entry(other) == bucket_id)
        assert pointers == 1 << (index.global_depth - local_depth), (
            "el número de punteros del directorio no cuadra con la profundidad local"
        )
    assert sorted(key for key, _ in index.scan()) == sorted(expected_keys)
    assert index.entry_count == len(expected_keys)
    _assert_every_page_is_accounted_for(index)


def _assert_every_page_is_accounted_for(index: ExtendibleHashIndex) -> None:
    """Cabecera, cubetas con sus cadenas y lista de libres suman el archivo entero: una
    página que no está en ninguno de los tres sitios se ha perdido."""
    buckets = {index._directory_entry(position) for position in range(index.directory_size)}
    in_use = {page_id for bucket_id in buckets for page_id, _ in index._chain_pages(bucket_id)}
    free: set[int] = set()
    page_id = index._free_head
    while page_id != NO_PAGE:
        assert page_id not in free and page_id not in in_use, "página libre y en uso a la vez"
        free.add(page_id)
        page_id = index._load(page_id).next_page
    assert len(in_use) + len(free) + 1 == index.page_count, "hay páginas perdidas"
