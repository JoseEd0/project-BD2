import pytest

from storage.page import (
    NO_PAGE,
    EmptySlotError,
    PageOverflowError,
    RecordPage,
    SlotOutOfRangeError,
    SlotState,
    slot_capacity,
)
from storage.types import StorageError

PAGE_SIZE = 64
RECORD_SIZE = 8


def record(marker: int) -> bytes:
    return bytes([marker]) * RECORD_SIZE


@pytest.fixture
def page() -> RecordPage:
    return RecordPage.create(PAGE_SIZE, RECORD_SIZE)


def test_capacity_leaves_room_for_the_header():
    assert slot_capacity(PAGE_SIZE, RECORD_SIZE) == (PAGE_SIZE - 8) // (1 + RECORD_SIZE)


def test_record_larger_than_the_page_is_rejected():
    with pytest.raises(PageOverflowError):
        slot_capacity(PAGE_SIZE, PAGE_SIZE)


def test_new_page_is_empty(page: RecordPage):
    assert page.used_slots == 0
    assert list(page.live_slots()) == []
    assert page.next_page == NO_PAGE


def test_insert_and_read(page: RecordPage):
    slot = page.insert(record(1))
    assert page.read(slot) == record(1)
    assert page.state_of(slot) is SlotState.USED


def test_insert_until_full(page: RecordPage):
    for marker in range(page.capacity):
        page.insert(record(marker))
    assert page.is_full
    with pytest.raises(PageOverflowError):
        page.insert(record(99))


def test_reading_an_empty_slot_is_rejected(page: RecordPage):
    with pytest.raises(EmptySlotError):
        page.read(0)


def test_slot_beyond_capacity_is_rejected(page: RecordPage):
    with pytest.raises(SlotOutOfRangeError):
        page.read(page.capacity)


def test_record_of_the_wrong_size_is_rejected(page: RecordPage):
    with pytest.raises(StorageError):
        page.insert(b"corto")


def test_free_reuses_the_slot(page: RecordPage):
    page.insert(record(1))
    page.insert(record(2))
    page.free(0)
    assert page.insert(record(3)) == 0


def test_freeing_the_last_slot_shrinks_the_page(page: RecordPage):
    page.insert(record(1))
    page.insert(record(2))
    page.free(1)
    assert page.used_slots == 1


def test_tombstone_keeps_the_slot_occupied(page: RecordPage):
    page.insert(record(1))
    page.tombstone(0)
    assert page.state_of(0) is SlotState.DELETED
    assert page.used_slots == 1
    assert page.deleted_slots() == 1
    assert list(page.live_slots()) == []


def test_tombstone_on_an_empty_slot_is_rejected(page: RecordPage):
    with pytest.raises(EmptySlotError):
        page.tombstone(0)


def test_record_at_reads_a_tombstone(page: RecordPage):
    page.insert(record(7))
    page.tombstone(0)
    assert page.record_at(0) == record(7)


def test_insert_at_shifts_to_the_right(page: RecordPage):
    for marker in (1, 2, 3):
        page.insert(record(marker))
    page.insert_at(1, record(9))
    assert [page.read(slot) for slot in page.live_slots()] == [
        record(1),
        record(9),
        record(2),
        record(3),
    ]


def test_insert_at_the_end(page: RecordPage):
    page.insert(record(1))
    page.insert_at(1, record(2))
    assert page.read(1) == record(2)


def test_insert_at_beyond_the_sequence_is_rejected(page: RecordPage):
    with pytest.raises(SlotOutOfRangeError):
        page.insert_at(1, record(1))


def test_insert_at_on_a_full_page_is_rejected(page: RecordPage):
    for marker in range(page.capacity):
        page.insert(record(marker))
    with pytest.raises(PageOverflowError):
        page.insert_at(0, record(99))


def test_remove_at_shifts_to_the_left(page: RecordPage):
    for marker in (1, 2, 3):
        page.insert(record(marker))
    page.remove_at(0)
    assert [page.read(slot) for slot in page.live_slots()] == [record(2), record(3)]
    assert page.used_slots == 2


def test_next_page_and_flags_survive_serialization(page: RecordPage):
    page.next_page = 7
    page.flags = 3
    page.insert(record(1))
    restored = RecordPage.from_bytes(page.to_bytes(), RECORD_SIZE)
    assert restored.next_page == 7
    assert restored.flags == 3
    assert restored.read(0) == record(1)
