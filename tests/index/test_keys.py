"""Códecs de clave: lo que los índices escriben en disco y comparan en memoria."""

import pytest

from index.keys import CompositeKeyCodec, KeyShapeError, RecordIdKeyCodec, ScalarKeyCodec
from storage.record_id import RecordId
from storage.schema import Field
from storage.types import FieldType, InvalidValueError

CITY_LENGTH = 8


@pytest.fixture
def composite() -> CompositeKeyCodec:
    return CompositeKeyCodec(
        [
            ScalarKeyCodec(Field("ciudad", FieldType.STRING, CITY_LENGTH)),
            ScalarKeyCodec(Field("id", FieldType.INT)),
        ]
    )


@pytest.mark.parametrize(
    ("field", "key"),
    [
        (Field("id", FieldType.INT), -7),
        (Field("nota", FieldType.FLOAT), 12.5),
        (Field("ciudad", FieldType.STRING, CITY_LENGTH), "lima"),
    ],
)
def test_a_scalar_key_round_trips_in_a_fixed_size(field: Field, key: object):
    codec = ScalarKeyCodec(field)
    packed = codec.pack(key)
    assert len(packed) == codec.size
    assert codec.unpack(packed) == key


def test_a_scalar_key_of_the_wrong_type_is_rejected():
    with pytest.raises(InvalidValueError):
        ScalarKeyCodec(Field("id", FieldType.INT)).pack("siete")


def test_a_composite_key_round_trips(composite: CompositeKeyCodec):
    packed = composite.pack(("lima", 42))
    assert len(packed) == composite.size == CITY_LENGTH + 8
    assert composite.unpack(packed) == ("lima", 42)


def test_composite_keys_compare_component_by_component(composite: CompositeKeyCodec):
    keys = [("lima", 2), ("cusco", 9), ("lima", 1), ("cusco", 10)]
    restored = [composite.unpack(composite.pack(key)) for key in keys]
    assert sorted(restored) == [("cusco", 9), ("cusco", 10), ("lima", 1), ("lima", 2)]


def test_a_composite_key_needs_every_component(composite: CompositeKeyCodec):
    with pytest.raises(KeyShapeError, match="2"):
        composite.pack(("lima",))
    with pytest.raises(KeyShapeError):
        composite.pack(("lima", 1, 2))


def test_a_composite_codec_needs_at_least_one_component():
    with pytest.raises(KeyShapeError):
        CompositeKeyCodec([])


def test_a_composite_key_takes_the_bytes_of_its_components_one_after_another():
    """Es el formato en disco de las hojas del B+ no agrupado: no puede cambiar."""
    value, address = ScalarKeyCodec(Field("id", FieldType.INT)), RecordIdKeyCodec()
    composite = CompositeKeyCodec([value, address])
    key = (42, RecordId(7, 3))
    assert composite.pack(key) == value.pack(42) + address.pack(RecordId(7, 3))
    assert composite.size == value.size + address.size
    assert composite.unpack(composite.pack(key)) == key


def test_many_keys_round_trip_in_one_pass(composite: CompositeKeyCodec):
    keys = [("lima", 2), ("cusco", 9), ("piura", -4)]
    packed = composite.pack_many(keys)
    assert packed == b"".join(composite.pack(key) for key in keys)
    assert composite.unpack_many(packed) == keys


def test_no_keys_pack_to_nothing(composite: CompositeKeyCodec):
    assert composite.pack_many([]) == b""
    assert composite.unpack_many(b"") == []


def test_a_batch_rejects_a_key_of_the_wrong_shape(composite: CompositeKeyCodec):
    with pytest.raises(KeyShapeError):
        composite.pack_many([("lima", 1), ("cusco",)])
