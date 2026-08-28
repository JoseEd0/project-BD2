from datetime import date, timedelta

import pytest

from storage.types import (
    EPOCH,
    FieldType,
    InvalidValueError,
    ValueTooLargeError,
    codec_for,
)


@pytest.mark.parametrize(
    ("field_type", "length", "value"),
    [
        (FieldType.INT, None, -42),
        (FieldType.FLOAT, None, 3.5),
        (FieldType.BOOL, None, True),
        (FieldType.DATE, None, date(2026, 8, 27)),
        (FieldType.STRING, 10, "hola"),
        (FieldType.BYTES, 4, b"\x01\x02"),
        (FieldType.POINT, None, (-12.5, 77.25)),
        (FieldType.VECTOR, 3, (0.5, 0.25, 0.125)),
    ],
)
def test_round_trip(field_type: FieldType, length: int | None, value: object):
    codec = codec_for(field_type, length)
    assert codec.from_slots(codec.to_slots(value)) == value


def test_date_is_stored_as_days_since_epoch():
    codec = codec_for(FieldType.DATE, None)
    assert codec.to_slots(EPOCH + timedelta(days=5)) == (5,)


def test_string_longer_than_the_field_is_rejected():
    with pytest.raises(ValueTooLargeError):
        codec_for(FieldType.STRING, 3).to_slots("cuatro")


def test_multibyte_string_is_measured_in_bytes():
    with pytest.raises(ValueTooLargeError):
        codec_for(FieldType.STRING, 3).to_slots("ñññ")


def test_bool_is_not_accepted_as_integer():
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.INT, None).to_slots(True)


def test_integer_is_accepted_as_float():
    assert codec_for(FieldType.FLOAT, None).to_slots(2) == (2.0,)


def test_vector_of_wrong_dimension_is_rejected():
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.VECTOR, 3).to_slots((1.0, 2.0))


def test_sized_type_without_length_is_rejected():
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.STRING, None)


def test_unsized_type_with_length_is_rejected():
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.INT, 4)


def test_zero_length_is_rejected():
    with pytest.raises(InvalidValueError):
        codec_for(FieldType.STRING, 0)


def test_codec_size_matches_its_format():
    assert codec_for(FieldType.VECTOR, 4).size == 16
    assert codec_for(FieldType.POINT, None).size == 16
