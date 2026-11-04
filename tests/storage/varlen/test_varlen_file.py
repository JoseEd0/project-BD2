"""Archivo de valores de longitud variable: valores en línea, cadenas, huecos y reapertura."""

import random
from pathlib import Path

import pytest

from config import EngineConfig
from storage.buffer_pool import BufferPool
from storage.layout import CorruptFileError
from storage.record_id import RecordId
from storage.varlen import ValueNotFoundError, VariableLengthFile
from storage.varlen.slotted_page import FragmentKind
from storage.varlen.varlen_file import (
    VARLEN_MAGIC,
    VARLEN_VERSION,
    VariableLengthFormatError,
)

MODEL_SEEDS = (7, 23, 101)
MODEL_OPERATIONS = 400
MODEL_PAGE_SIZES = (64, 256, 1024)
SMALL_VALUE_LIMIT = 40
LONG_VALUE_PAGES = 10
REUSE_PAGE_SIZES = (256, 1000, 4096)


def pattern(marker: int, size: int) -> bytes:
    """Bytes que cambian a lo largo del valor: un fragmento cruzado o desordenado se nota."""
    return bytes((marker + position) % 256 for position in range(size))


def reopen(file: VariableLengthFile, config: EngineConfig) -> VariableLengthFile:
    path = file._pager.path
    file.close()
    return VariableLengthFile(path, config)


@pytest.fixture
def values(config: EngineConfig):
    with VariableLengthFile(config.data_directory / "t.var", config) as opened:
        yield opened


def test_a_new_file_is_empty(values: VariableLengthFile):
    assert values.value_count == 0
    assert list(values.scan()) == []
    assert values.describe()["data_pages"] == 0


def test_a_single_value_round_trips(values: VariableLengthFile):
    address = values.put(b"hola mundo")
    assert values.get(address) == b"hola mundo"
    assert list(values.scan()) == [(address, b"hola mundo")]
    assert values.describe()["value_bytes"] == len(b"hola mundo")


def test_a_zero_byte_value_is_a_value(values: VariableLengthFile):
    address = values.put(b"")
    assert values.get(address) == b""
    assert values.value_count == 1
    values.delete(address)
    assert values.value_count == 0


def test_a_value_that_exactly_fills_a_page(values: VariableLengthFile):
    first = values.put(pattern(1, values.inline_limit))
    second = values.put(pattern(2, values.inline_limit))
    assert first.page_id != second.page_id
    assert values.describe()["data_pages"] == 2
    assert values.get(first) == pattern(1, values.inline_limit)
    small = values.put(b"x")
    assert small.page_id not in {first.page_id, second.page_id}


def test_one_byte_over_a_page_becomes_a_chain(values: VariableLengthFile):
    value = pattern(3, values.inline_limit + 1)
    address = values.put(value)
    assert values.get(address) == value
    assert values.describe()["data_pages"] == 2


def test_a_value_of_ten_pages(values: VariableLengthFile):
    value = pattern(4, values.chunk_size * LONG_VALUE_PAGES)
    address = values.put(value)
    assert values.get(address) == value
    assert values.describe()["data_pages"] == LONG_VALUE_PAGES
    assert list(values.scan()) == [(address, value)]


def test_get_of_a_small_value_reads_one_page(config: EngineConfig):
    path = config.data_directory / "t.var"
    with VariableLengthFile(path, config) as written:
        addresses = [written.put(pattern(marker, 30)) for marker in range(40)]
    with VariableLengthFile(path, config) as reopened:
        reopened._pager.reads = 0
        assert reopened.get(addresses[17]) == pattern(17, 30)
        assert reopened._pager.reads == 1


def test_get_of_a_long_value_reads_only_its_fragments(config: EngineConfig):
    path = config.data_directory / "t.var"
    with VariableLengthFile(path, config) as written:
        before = [written.put(pattern(marker, 100)) for marker in range(6)]
        long_value = pattern(9, written.chunk_size * LONG_VALUE_PAGES + 17)
        address = written.put(long_value)
        after = [written.put(pattern(marker, 100)) for marker in range(6)]
        assert written.describe()["data_pages"] > LONG_VALUE_PAGES + 1
    with VariableLengthFile(path, config) as reopened:
        reopened._pager.reads = 0
        assert reopened.get(address) == long_value
        assert reopened._pager.reads == LONG_VALUE_PAGES + 1
        assert reopened.get(before[0]) == pattern(0, 100)
        assert reopened.get(after[-1]) == pattern(5, 100)


def test_put_does_not_scan_the_file(config: EngineConfig):
    path = config.data_directory / "t.var"
    with VariableLengthFile(path, config) as written:
        for marker in range(60):
            written.put(pattern(marker, written.inline_limit))
    with VariableLengthFile(path, config) as reopened:
        reopened._pager.reads = 0
        reopened.put(b"nuevo")
        assert reopened._pager.reads <= 1, "solo la página del mapa, que ya estaba en memoria"


def test_deleting_frees_the_hole_for_the_next_value(values: VariableLengthFile):
    addresses = [values.put(pattern(marker, 30)) for marker in range(5)]
    pages = values.page_count
    values.delete(addresses[2])
    assert values.put(pattern(99, 30)) == addresses[2]
    assert values.page_count == pages
    assert values.value_count == 5


def test_holes_are_compacted_to_fit_a_larger_value(values: VariableLengthFile):
    addresses = [values.put(pattern(marker, 30)) for marker in range(7)]
    assert {address.page_id for address in addresses} == {addresses[0].page_id}
    for index in (1, 3, 5):
        values.delete(addresses[index])
    pages = values.page_count
    larger = pattern(50, 75)
    placed = values.put(larger)
    assert placed.page_id == addresses[0].page_id, "cabe juntando los huecos"
    assert values.page_count == pages
    assert values.get(placed) == larger
    for index in (0, 2, 4, 6):
        assert values.get(addresses[index]) == pattern(index, 30)


@pytest.mark.parametrize("page_size", REUSE_PAGE_SIZES)
def test_deleting_a_long_value_frees_all_its_pages(tmp_path: Path, page_size: int):
    """Una página vacía tiene que admitir un fragmento de página completa con cualquier
    tamaño de página, no solo con los que redondean justo en el mapa de espacio libre."""
    config = EngineConfig(page_size=page_size, buffer_pool_pages=4, data_directory=tmp_path)
    with VariableLengthFile(tmp_path / "t.var", config) as values:
        size = values.chunk_size * LONG_VALUE_PAGES
        address = values.put(pattern(1, size))
        pages = values.page_count
        values.delete(address)
        again = values.put(pattern(2, size))
        assert values.page_count == pages
        assert values.get(again) == pattern(2, size)


def test_deleting_twice_is_rejected(values: VariableLengthFile):
    address = values.put(b"a")
    values.delete(address)
    with pytest.raises(ValueNotFoundError):
        values.delete(address)
    with pytest.raises(ValueNotFoundError):
        values.get(address)


def test_replace_keeps_the_address_when_the_page_has_room(values: VariableLengthFile):
    address = values.put(b"corto")
    neighbour = values.put(b"vecino")
    assert values.replace(address, b"bastante mas largo") == address
    assert values.get(address) == b"bastante mas largo"
    assert values.replace(address, b"") == address
    assert values.get(address) == b""
    assert values.get(neighbour) == b"vecino"
    assert values.value_count == 2


def test_replace_moves_the_value_when_its_page_is_full(values: VariableLengthFile):
    half = values.inline_limit // 3
    address = values.put(pattern(1, half))
    neighbour = values.put(pattern(2, half))
    assert neighbour.page_id == address.page_id
    moved = values.replace(address, pattern(3, values.inline_limit))
    assert moved.page_id != address.page_id
    assert values.get(moved) == pattern(3, values.inline_limit)
    assert values.get(neighbour) == pattern(2, half)
    with pytest.raises(ValueNotFoundError):
        values.get(address)


def test_replace_from_inline_to_a_chain_and_back(values: VariableLengthFile):
    address = values.put(b"semilla")
    long_value = pattern(4, values.chunk_size * 3 + 5)
    grown = values.replace(address, long_value)
    assert values.get(grown) == long_value
    pages = values.page_count
    shrunk = values.replace(grown, b"otra vez corto")
    assert values.get(shrunk) == b"otra vez corto"
    assert values.describe()["value_bytes"] == len(b"otra vez corto")
    regrown = values.replace(shrunk, pattern(5, values.chunk_size * 3 + 5))
    assert values.get(regrown) == pattern(5, values.chunk_size * 3 + 5)
    assert values.page_count == pages, "las páginas de la primera cadena se reutilizaron"
    assert values.value_count == 1


def test_replace_of_a_chain_by_a_longer_chain(values: VariableLengthFile):
    address = values.put(pattern(1, values.chunk_size * 2))
    longer = pattern(2, values.chunk_size * LONG_VALUE_PAGES + 1)
    replaced = values.replace(address, longer)
    assert values.get(replaced) == longer
    assert [value for _, value in values.scan()] == [longer]


def test_scan_returns_every_live_value_in_physical_order(values: VariableLengthFile):
    sizes = [0, 10, values.inline_limit, values.chunk_size * 2 + 3, 50, values.inline_limit + 1]
    expected = {values.put(pattern(size, size)): pattern(size, size) for size in sizes}
    removed = next(iter(expected))
    values.delete(removed)
    del expected[removed]
    scanned = list(values.scan())
    assert dict(scanned) == expected
    assert [address for address, _ in scanned] == sorted(expected)


def test_state_survives_reopening(config: EngineConfig, values: VariableLengthFile):
    long_value = pattern(7, values.chunk_size * 4 + 9)
    addresses = [values.put(pattern(marker, 25)) for marker in range(8)]
    long_address = values.put(long_value)
    values.delete(addresses[3])
    reopened = reopen(values, config)
    try:
        assert reopened.value_count == 8
        assert reopened.describe()["value_bytes"] == 7 * 25 + len(long_value)
        assert reopened.get(long_address) == long_value
        assert reopened.get(addresses[0]) == pattern(0, 25)
        assert reopened.put(pattern(30, 25)) == addresses[3], "el mapa de espacio libre persiste"
    finally:
        reopened.close()


def test_many_values_cross_a_map_page(config: EngineConfig, values: VariableLengthFile):
    addresses = [
        values.put(pattern(marker, values.inline_limit)) for marker in range(config.page_size + 3)
    ]
    assert values.describe()["map_pages"] == 2
    values.delete(addresses[-1])
    values.delete(addresses[1])
    assert values.put(pattern(1, 10)) == addresses[1]
    reopened = reopen(values, config)
    try:
        assert reopened.get(addresses[-2]) == pattern(config.page_size + 1, reopened.inline_limit)
        assert reopened.put(pattern(2, reopened.inline_limit)).page_id == addresses[-1].page_id
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "address",
    [
        RecordId(page_id=0, slot=0),
        RecordId(page_id=1, slot=0),
        RecordId(page_id=99, slot=0),
        RecordId(page_id=2, slot=50),
    ],
    ids=["cabecera", "mapa", "fuera del archivo", "ranura inexistente"],
)
def test_an_invalid_address_is_rejected(values: VariableLengthFile, address: RecordId):
    values.put(b"a")
    with pytest.raises(ValueNotFoundError):
        values.get(address)
    with pytest.raises(ValueNotFoundError):
        values.delete(address)
    with pytest.raises(ValueNotFoundError):
        values.replace(address, b"b")
    assert values.value_count == 1


def test_the_address_of_a_continuation_is_not_a_value(values: VariableLengthFile):
    address = values.put(pattern(1, values.chunk_size * 3))
    continuation = next(
        RecordId(page_id=page_id, slot=slot)
        for page_id in values._space.data_pages()
        for slot, kind, _ in values._load(page_id).fragments()
        if kind is FragmentKind.PART
    )
    with pytest.raises(ValueNotFoundError, match="comienzo"):
        values.get(continuation)
    assert values.get(address) == pattern(1, values.chunk_size * 3)


def test_a_chain_that_points_back_to_its_head_is_corrupt(values: VariableLengthFile):
    address = values.put(pattern(1, values.chunk_size * 2))
    page = values._load(address.page_id)
    page.rewrite(address.slot, FragmentKind.HEAD, address.pack() + b"x")
    values._store(address.page_id, page)
    with pytest.raises(CorruptFileError):
        values.get(address)


def test_a_file_that_is_not_a_varlen_file_is_rejected(config: EngineConfig):
    path = config.data_directory / "ajeno.var"
    path.write_bytes(b"x" * config.page_size)
    with pytest.raises(VariableLengthFormatError, match="no es un archivo de valores"):
        VariableLengthFile(path, config)
    assert BufferPool.shared(config).resident_pages == 0, "el archivo rechazado quedó abierto"


def test_a_file_of_another_version_is_rejected(config: EngineConfig):
    path = config.data_directory / "t.var"
    with VariableLengthFile(path, config):
        pass
    raw = bytearray(path.read_bytes())
    raw[len(VARLEN_MAGIC)] = VARLEN_VERSION + 1
    path.write_bytes(bytes(raw))
    with pytest.raises(VariableLengthFormatError, match="versión"):
        VariableLengthFile(path, config)


def test_a_file_opened_with_another_page_size_is_rejected(config: EngineConfig):
    path = config.data_directory / "t.var"
    with VariableLengthFile(path, config) as written:
        written.put(b"a")
    other = EngineConfig(page_size=config.page_size * 2, data_directory=config.data_directory)
    with pytest.raises(VariableLengthFormatError, match="páginas de"):
        VariableLengthFile(path, other)


def boundary_sizes(file: VariableLengthFile) -> list[int]:
    chunk = file.chunk_size
    edges = [0, 1, file.inline_limit - 1, file.inline_limit, file.inline_limit + 1]
    edges += [chunk * pages + delta for pages in (2, 3) for delta in (-1, 0, 1)]
    return edges


def random_size(generator: random.Random, file: VariableLengthFile) -> int:
    if generator.random() < 0.5:
        return generator.choice(boundary_sizes(file))
    return generator.randrange(SMALL_VALUE_LIMIT)


@pytest.mark.parametrize("page_size", MODEL_PAGE_SIZES)
@pytest.mark.parametrize("seed", MODEL_SEEDS)
def test_random_operations_match_a_dictionary(tmp_path: Path, page_size: int, seed: int):
    config = EngineConfig(page_size=page_size, buffer_pool_pages=4, data_directory=tmp_path)
    generator = random.Random(seed)
    model: dict[RecordId, bytes] = {}
    file = VariableLengthFile(tmp_path / "model.var", config)
    try:
        for step in range(MODEL_OPERATIONS):
            operation = generator.random()
            if not model or operation < 0.45:
                value = pattern(step, random_size(generator, file))
                address = file.put(value)
                assert address not in model
                model[address] = value
            elif operation < 0.65:
                address = generator.choice(sorted(model))
                file.delete(address)
                del model[address]
            elif operation < 0.85:
                address = generator.choice(sorted(model))
                value = pattern(step, random_size(generator, file))
                del model[address]
                replaced = file.replace(address, value)
                assert replaced not in model
                model[replaced] = value
            elif operation < 0.95:
                address = generator.choice(sorted(model))
                assert file.get(address) == model[address]
            else:
                file = reopen(file, config)
        assert dict(file.scan()) == model
        assert file.value_count == len(model)
        assert file.describe()["value_bytes"] == sum(len(value) for value in model.values())
    finally:
        file.close()
