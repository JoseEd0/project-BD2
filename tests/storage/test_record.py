import pytest

from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType, InvalidValueError


def test_size_is_constant(serializer: RecordSerializer):
    short = serializer.pack((1, "a", 0.0))
    long = serializer.pack((2, "doce-letras", 1.5))
    assert len(short) == len(long) == serializer.size


def test_round_trip(serializer: RecordSerializer):
    row = (7, "josé", -2.5)
    assert serializer.unpack(serializer.pack(row)) == row


def test_nulls_survive_the_round_trip(serializer: RecordSerializer):
    assert serializer.unpack(serializer.pack((1, None, None))) == (1, None, None)


def test_null_on_a_required_field_is_rejected(serializer: RecordSerializer):
    with pytest.raises(InvalidValueError):
        serializer.pack((None, "a", 1.0))


def test_wrong_arity_is_rejected(serializer: RecordSerializer):
    with pytest.raises(InvalidValueError):
        serializer.pack((1, "a"))


def test_unpacking_a_truncated_record_is_rejected(serializer: RecordSerializer):
    with pytest.raises(InvalidValueError):
        serializer.unpack(serializer.pack((1, "a", 1.0))[:-1])


def test_unpack_field_matches_full_unpack(serializer: RecordSerializer):
    raw = serializer.pack((9, "ana", 4.25))
    assert tuple(serializer.unpack_field(raw, i) for i in range(3)) == serializer.unpack(raw)


def test_unpack_field_reports_null(serializer: RecordSerializer):
    raw = serializer.pack((9, None, 4.25))
    assert serializer.unpack_field(raw, 1) is None


def test_null_bitmap_grows_past_eight_fields():
    schema = Schema([Field(f"c{i}", FieldType.INT) for i in range(9)])
    packer = RecordSerializer(schema)
    values = tuple(None if i == 8 else i for i in range(9))
    assert packer.unpack(packer.pack(values)) == values
