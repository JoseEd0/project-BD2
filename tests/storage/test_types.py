from datetime import date, timedelta

import pytest

from storage.types import (
    EPOCH,
    MAX_INT,
    MIN_INT,
    FieldType,
    InvalidValueError,
    ValueTooLargeError,
    codec_for,
    date_from,
    real_from,
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


def test_an_integer_beyond_64_bits_is_rejected():
    codec = codec_for(FieldType.INT, None)
    assert codec.to_slots(MAX_INT) == (MAX_INT,)
    assert codec.to_slots(MIN_INT) == (MIN_INT,)
    with pytest.raises(ValueTooLargeError):
        codec.to_slots(MAX_INT + 1)
    with pytest.raises(ValueTooLargeError):
        codec.to_slots(MIN_INT - 1)


def test_an_integer_too_large_for_a_double_is_rejected():
    with pytest.raises(ValueTooLargeError):
        codec_for(FieldType.FLOAT, None).to_slots(10**400)
    with pytest.raises(ValueTooLargeError):
        real_from(10**400)


def test_a_date_can_be_given_as_iso_text():
    codec = codec_for(FieldType.DATE, None)
    assert codec.to_slots("1970-01-06") == (5,)
    assert date_from(" 2024-01-05 ") == date(2024, 1, 5)


@pytest.mark.parametrize("value", ["ayer", "2024-13-40", 20240105, None])
def test_what_is_not_a_date_is_rejected(value: object):
    with pytest.raises(InvalidValueError):
        date_from(value)


def test_an_integer_field_takes_a_real_without_decimals():
    codec = codec_for(FieldType.INT, None)
    stored = codec.to_slots(10.0)
    assert stored == (10,) and isinstance(stored[0], int)
    for value in (2.5, float("inf"), float("nan")):
        with pytest.raises(InvalidValueError):
            codec.to_slots(value)


@pytest.mark.parametrize(
    ("field_type", "length", "value"),
    [
        (FieldType.FLOAT, None, "1.5"),
        (FieldType.FLOAT, None, True),
        (FieldType.BOOL, None, 1),
        (FieldType.STRING, 4, 5),
        (FieldType.BYTES, 4, "ab"),
        (FieldType.POINT, None, (1.0,)),
        (FieldType.POINT, None, "POINT(1, 2)"),
        (FieldType.POINT, None, ("a", 2.0)),
        (FieldType.POINT, None, (True, 2.0)),
        (FieldType.POINT, None, (100.0, 2.0)),
        (FieldType.POINT, None, (2.0, 200.0)),
    ],
)
def test_a_value_of_another_type_is_rejected(field_type: FieldType, length: int | None, value):
    with pytest.raises(InvalidValueError):
        codec_for(field_type, length).to_slots(value)


def test_bytes_longer_than_the_field_are_rejected():
    with pytest.raises(ValueTooLargeError):
        codec_for(FieldType.BYTES, 2).to_slots(b"abc")


@pytest.mark.parametrize(
    ("field_type", "length"),
    [
        (FieldType.INT, None),
        (FieldType.FLOAT, None),
        (FieldType.BOOL, None),
        (FieldType.DATE, None),
        (FieldType.STRING, 6),
        (FieldType.BYTES, 6),
        (FieldType.POINT, None),
        (FieldType.VECTOR, 3),
    ],
)
def test_the_value_that_fills_a_null_field_can_be_stored(field_type: FieldType, length: int | None):
    """Un campo NULL ocupa su sitio igualmente: lo rellena el valor neutro de su tipo."""
    codec = codec_for(field_type, length)
    neutral = codec.neutral_value()
    assert codec.from_slots(codec.to_slots(neutral)) == neutral
