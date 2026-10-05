import random

import pytest

from config import EngineConfig
from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.sequential import SequentialFile
from storage.sequential.sequential_file import NullKeyError, SequentialFormatError
from storage.types import FieldType

SHUFFLE_SEED = 7


@pytest.fixture
def sequential(config: EngineConfig, serializer: RecordSerializer):
    with SequentialFile(config.data_directory / "t.seq", serializer, "id", config) as file:
        yield file


def keys_of(records: list[bytes] | object, serializer: RecordSerializer) -> list[int]:
    return [serializer.unpack(record)[0] for record in records]  # type: ignore[union-attr]


def row(serializer: RecordSerializer, key: int) -> bytes:
    return serializer.pack((key, f"r{key}", float(key)))


def test_new_file_is_empty(sequential: SequentialFile):
    assert sequential.record_count == 0
    assert list(sequential.scan()) == []
    assert sequential.search(1) == []
    assert sequential.waste_ratio == 0.0


def test_single_record(sequential: SequentialFile, serializer: RecordSerializer):
    sequential.insert(row(serializer, 5))
    assert keys_of(sequential.search(5), serializer) == [5]
    assert sequential.record_count == 1


def test_scan_is_ordered_whatever_the_insertion_order(
    sequential: SequentialFile, serializer: RecordSerializer
):
    keys = list(range(120))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        sequential.insert(row(serializer, key))
    assert keys_of(list(sequential.scan()), serializer) == sorted(keys)


def test_sorted_insertion_grows_the_main_area(
    sequential: SequentialFile, serializer: RecordSerializer
):
    for key in range(sequential.slots_per_page * 3):
        sequential.insert(row(serializer, key))
    assert sequential.main_page_count >= 3
    assert sequential.overflow_count == 0


def test_insertion_in_the_middle_of_a_full_page_uses_overflow(
    config: EngineConfig, serializer: RecordSerializer
):
    relaxed = EngineConfig(
        page_size=config.page_size,
        sequential_waste_ratio=1.0,
        data_directory=config.data_directory,
    )
    with SequentialFile(config.data_directory / "o.seq", serializer, "id", relaxed) as file:
        for key in range(0, file.slots_per_page * 2, 2):
            file.insert(row(serializer, key))
        assert file.overflow_count == 0
        file.insert(row(serializer, 1))
        assert file.overflow_count == 1
        assert keys_of(file.search(1), serializer) == [1]


def test_range_search_is_inclusive(sequential: SequentialFile, serializer: RecordSerializer):
    for key in range(50):
        sequential.insert(row(serializer, key))
    assert keys_of(list(sequential.range_search(10, 14)), serializer) == [10, 11, 12, 13, 14]


def test_range_search_outside_the_data_is_empty(
    sequential: SequentialFile, serializer: RecordSerializer
):
    sequential.insert(row(serializer, 5))
    assert list(sequential.range_search(10, 20)) == []
    assert list(sequential.range_search(20, 10)) == []


def test_duplicate_keys_are_all_returned(
    sequential: SequentialFile, serializer: RecordSerializer
):
    for _ in range(3):
        sequential.insert(row(serializer, 42))
    assert len(sequential.search(42)) == 3


def test_lazy_delete_hides_the_record(sequential: SequentialFile, serializer: RecordSerializer):
    for key in range(20):
        sequential.insert(row(serializer, key))
    assert sequential.delete(7) == 1
    assert sequential.search(7) == []
    assert sequential.record_count == 19
    assert 7 not in keys_of(list(sequential.scan()), serializer)


def test_deleting_a_missing_key_changes_nothing(
    sequential: SequentialFile, serializer: RecordSerializer
):
    sequential.insert(row(serializer, 1))
    assert sequential.delete(99) == 0
    assert sequential.record_count == 1


def test_waste_stays_under_the_threshold(
    sequential: SequentialFile, serializer: RecordSerializer, config: EngineConfig
):
    """La reorganización automática mantiene el desperdicio acotado sin perder datos."""
    for key in range(60):
        sequential.insert(row(serializer, key))
    for key in range(40):
        sequential.delete(key)
    assert sequential.waste_ratio <= config.sequential_waste_ratio
    assert sequential.record_count == 20
    assert keys_of(list(sequential.scan()), serializer) == list(range(40, 60))


def test_manual_reorganization_drops_tombstones_and_overflow(
    config: EngineConfig, serializer: RecordSerializer
):
    relaxed = EngineConfig(
        page_size=config.page_size,
        sequential_waste_ratio=1.0,
        data_directory=config.data_directory,
    )
    with SequentialFile(config.data_directory / "r.seq", serializer, "id", relaxed) as file:
        keys = list(range(80))
        random.Random(SHUFFLE_SEED).shuffle(keys)
        for key in keys:
            file.insert(row(serializer, key))
        for key in range(0, 80, 2):
            file.delete(key)
        expected = keys_of(list(file.scan()), serializer)
        file.reorganize()
        assert file.deleted_count == 0
        assert file.overflow_count == 0
        assert keys_of(list(file.scan()), serializer) == expected
        assert keys_of(file.search(41), serializer) == [41]


def test_reorganizing_an_empty_file_is_harmless(sequential: SequentialFile):
    sequential.reorganize()
    assert sequential.record_count == 0
    assert list(sequential.scan()) == []


def test_null_key_is_rejected(config: EngineConfig):
    schema = Schema([Field("id", FieldType.INT), Field("n", FieldType.STRING, 4)])
    nullable = RecordSerializer(schema)
    path = config.data_directory / "n.seq"
    with SequentialFile(path, nullable, "id", config) as file, pytest.raises(NullKeyError):
        file.insert(nullable.pack((None, "x")))


def test_state_survives_reopening(config: EngineConfig, serializer: RecordSerializer):
    path = config.data_directory / "p.seq"
    with SequentialFile(path, serializer, "id", config) as file:
        for key in range(40):
            file.insert(row(serializer, key))
        file.delete(3)
    with SequentialFile(path, serializer, "id", config) as file:
        assert file.record_count == 39
        assert keys_of(file.search(4), serializer) == [4]
        assert file.search(3) == []


def test_opening_with_another_schema_is_rejected(
    config: EngineConfig, serializer: RecordSerializer
):
    path = config.data_directory / "p.seq"
    with SequentialFile(path, serializer, "id", config):
        pass
    other = RecordSerializer(Schema([Field("id", FieldType.INT)]))
    with pytest.raises(SequentialFormatError):
        SequentialFile(path, other, "id", config)


def test_an_empty_file_answers_ranges_and_deletes(sequential: SequentialFile):
    assert list(sequential.range_search(1, 100)) == []
    assert sequential.delete(5) == 0


def test_an_inverted_range_is_empty(sequential: SequentialFile, serializer: RecordSerializer):
    for key in range(10):
        sequential.insert(row(serializer, key))
    assert list(sequential.range_search(7, 3)) == []


def test_deleting_a_key_twice_removes_it_once(
    sequential: SequentialFile, serializer: RecordSerializer
):
    """La lápida conserva la clave en su sitio, así que el segundo borrado la encuentra y
    tiene que reconocer que ya no hay nada vigente que quitar."""
    for key in range(10):
        sequential.insert(row(serializer, key))
    assert sequential.delete(4) == 1
    assert sequential.delete(4) == 0
    assert sequential.record_count == 9
    assert keys_of(list(sequential.range_search(3, 5)), serializer) == [3, 5]


def test_a_file_that_is_not_sequential_is_rejected(
    config: EngineConfig, serializer: RecordSerializer
):
    path = config.data_directory / "ajeno.seq"
    path.write_bytes(b"x" * config.page_size)
    with pytest.raises(SequentialFormatError, match="no es un archivo secuencial"):
        SequentialFile(path, serializer, "id", config)


def test_keys_below_the_first_one_are_found_in_the_overflow_of_the_first_page(
    config: EngineConfig, serializer: RecordSerializer
):
    """Con la primera página llena, una clave menor que todas va a su desbordamiento. La
    primera clave de esa página deja entonces de ser la menor del archivo, y ni la
    búsqueda ni el rango pueden descartarla mirándola."""
    lenient = EngineConfig(
        page_size=config.page_size,
        sequential_waste_ratio=0.95,
        data_directory=config.data_directory,
    )
    with SequentialFile(lenient.data_directory / "bajo.seq", serializer, "id", lenient) as file:
        for key in range(100, 100 + file.slots_per_page * 2):
            file.insert(row(serializer, key))
        file.insert(row(serializer, 7))
        file.insert(row(serializer, 5))
        assert keys_of(file.search(5), serializer) == [5]
        assert keys_of(list(file.range_search(1, 50)), serializer) == [5, 7]
        assert keys_of(list(file.range_search(6, 100)), serializer) == [7, 100]
        assert keys_of(list(file.scan()), serializer)[:3] == [5, 7, 100]
        assert file.delete(5) == 1
        assert keys_of(list(file.range_search(1, 50)), serializer) == [7]


@pytest.mark.parametrize(("low", "high"), [(None, 12), (40, None), (None, None), (None, -1)])
def test_a_range_can_be_open_on_either_side(
    sequential: SequentialFile, serializer: RecordSerializer, low: int | None, high: int | None
):
    keys = list(range(0, 60, 2))
    for key in keys:
        sequential.insert(row(serializer, key))
    expected = [
        key for key in keys if (low is None or key >= low) and (high is None or key <= high)
    ]
    assert keys_of(list(sequential.range_search(low, high)), serializer) == expected


def test_a_key_repeated_across_several_pages_is_found_whole(
    sequential: SequentialFile, serializer: RecordSerializer
):
    """Tras reorganizar, las copias de una misma clave quedan seguidas y pueden cruzar de
    una página a la siguiente: buscar, recorrer un rango y borrar tienen que verlas todas,
    no solo las de la página donde la búsqueda binaria aterriza."""
    copies = sequential.slots_per_page
    for key in (1, 2, 3):
        for _ in range(copies):
            sequential.insert(row(serializer, key))
    sequential.reorganize()
    assert sequential.main_page_count > 3
    for key in (1, 2, 3):
        assert len(sequential.search(key)) == copies
        assert len(list(sequential.range_search(key, key))) == copies
    assert len(list(sequential.range_search(2, 3))) == copies * 2
    assert sequential.delete(2) == copies
    assert sequential.search(2) == []
    assert sequential.record_count == copies * 2
    assert keys_of(list(sequential.scan()), serializer) == [1] * copies + [3] * copies


def test_the_file_matches_a_sorted_list_through_random_operations(
    config: EngineConfig, serializer: RecordSerializer
):
    """Inserciones, borrados y reorganizaciones al azar, con claves repetidas y claves por
    debajo de la primera: tras cada tanda, el archivo responde como una lista ordenada."""
    generator = random.Random(SHUFFLE_SEED)
    model: list[int] = []
    with SequentialFile(config.data_directory / "azar.seq", serializer, "id", config) as file:
        for _ in range(60):
            for _ in range(20):
                key = generator.randint(-30, 120)
                if generator.random() < 0.7:
                    file.insert(row(serializer, key))
                    model.append(key)
                else:
                    assert file.delete(key) == model.count(key)
                    model = [kept for kept in model if kept != key]
            if generator.random() < 0.1:
                file.reorganize()
            model.sort()
            assert keys_of(list(file.scan()), serializer) == model
            assert file.record_count == len(model)
            probe = generator.randint(-30, 120)
            assert len(file.search(probe)) == model.count(probe)
            low, high = sorted((generator.randint(-40, 130), generator.randint(-40, 130)))
            assert keys_of(list(file.range_search(low, high)), serializer) == [
                key for key in model if low <= key <= high
            ]
            assert keys_of(list(file.range_search(low, None)), serializer) == [
                key for key in model if key >= low
            ]
            assert keys_of(list(file.range_search(None, high)), serializer) == [
                key for key in model if key <= high
            ]
