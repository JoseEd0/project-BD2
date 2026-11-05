"""La dirección de una fila cabe entera en el identificador de su vector."""

import pytest

from index.vector import record_id_of, vector_id_of
from storage.record_id import HIGHEST_RECORD_ID, LOWEST_RECORD_ID, RecordId


@pytest.mark.parametrize(
    "record_id", [LOWEST_RECORD_ID, RecordId(page_id=3, slot=17), HIGHEST_RECORD_ID]
)
def test_record_ids_round_trip(record_id):
    assert record_id_of(vector_id_of(record_id)) == record_id


def test_identifiers_follow_the_physical_order():
    first = vector_id_of(RecordId(page_id=1, slot=65_535))
    second = vector_id_of(RecordId(page_id=2, slot=0))

    assert first < second
