"""Página ranurada: directorio de ranuras, huecos y compactación."""

import pytest

from config import EngineConfig
from storage.page import EmptySlotError, PageOverflowError, SlotOutOfRangeError
from storage.varlen.slotted_page import (
    PAGE_HEADER_SIZE,
    SLOT_ENTRY_SIZE,
    FragmentKind,
    SlottedPage,
    max_fragment_bytes,
)

WHOLE = FragmentKind.WHOLE


def filler(marker: int, size: int) -> bytes:
    return bytes([marker]) * size


@pytest.fixture
def page(config: EngineConfig) -> SlottedPage:
    return SlottedPage.create(config.page_size)


def largest(page: SlottedPage) -> int:
    return max_fragment_bytes(len(page.to_bytes()))


def test_an_empty_page_accepts_the_largest_fragment(page: SlottedPage):
    assert page.slot_count == 0
    assert list(page.fragments()) == []
    assert page.insertable_bytes() == largest(page)
    assert largest(page) == len(page.to_bytes()) - PAGE_HEADER_SIZE - SLOT_ENTRY_SIZE


def test_a_fragment_round_trips(page: SlottedPage):
    slot = page.insert(FragmentKind.HEAD, b"hola")
    assert page.fragment(slot) == (FragmentKind.HEAD, b"hola")
    assert list(page.fragments()) == [(slot, FragmentKind.HEAD, b"hola")]


def test_a_zero_byte_fragment_costs_only_its_slot(page: SlottedPage):
    before = page.insertable_bytes()
    slot = page.insert(WHOLE, b"")
    assert page.fragment(slot) == (WHOLE, b"")
    assert page.insertable_bytes() == before - SLOT_ENTRY_SIZE


def test_a_fragment_can_fill_the_page_exactly(page: SlottedPage):
    slot = page.insert(WHOLE, filler(7, largest(page)))
    assert page.insertable_bytes() == 0
    assert page.fragment(slot) == (WHOLE, filler(7, largest(page)))
    with pytest.raises(PageOverflowError):
        page.insert(WHOLE, b"")


def test_a_fragment_larger_than_the_free_space_is_rejected(page: SlottedPage):
    with pytest.raises(PageOverflowError):
        page.insert(WHOLE, filler(1, largest(page) + 1))
    assert page.slot_count == 0


def test_a_freed_slot_is_reused_before_the_directory_grows(page: SlottedPage):
    slots = [page.insert(WHOLE, filler(marker, 10)) for marker in range(3)]
    page.free(slots[1])
    assert page.insert(WHOLE, b"x") == slots[1]
    assert page.slot_count == 3


def test_freeing_the_last_slot_shrinks_the_directory(page: SlottedPage):
    slots = [page.insert(WHOLE, filler(marker, 10)) for marker in range(3)]
    page.free(slots[1])
    page.free(slots[2])
    assert page.slot_count == 1
    assert page.insertable_bytes() == largest(page) - SLOT_ENTRY_SIZE - 10


def test_freeing_everything_gives_back_the_whole_page(page: SlottedPage):
    slots = [page.insert(WHOLE, filler(marker, 20)) for marker in range(5)]
    for slot in (slots[3], slots[0], slots[4], slots[2], slots[1]):
        page.free(slot)
    assert page.slot_count == 0
    assert page.insertable_bytes() == largest(page)


def test_holes_are_compacted_when_an_insert_needs_them(page: SlottedPage):
    size = 40
    slots = [page.insert(WHOLE, filler(marker, size)) for marker in range(5)]
    page.free(slots[1])
    page.free(slots[3])
    large = filler(9, page.insertable_bytes())
    assert len(large) > size, "solo cabe juntando los dos huecos y el espacio contiguo"
    slot = page.insert(WHOLE, large)
    assert page.fragment(slot) == (WHOLE, large)
    for marker in (0, 2, 4):
        assert page.fragment(slots[marker]) == (WHOLE, filler(marker, size))
    assert page.insertable_bytes() == 0


def test_rewrite_shrinks_in_place_and_leaves_a_hole(page: SlottedPage):
    slot = page.insert(WHOLE, filler(1, 50))
    neighbour = page.insert(WHOLE, filler(2, 30))
    before = page.insertable_bytes()
    page.rewrite(slot, FragmentKind.HEAD, b"corto")
    assert page.fragment(slot) == (FragmentKind.HEAD, b"corto")
    assert page.fragment(neighbour) == (WHOLE, filler(2, 30))
    assert page.insertable_bytes() == before + 50 - len(b"corto")


def test_rewrite_grows_by_compacting_and_keeps_the_slot(page: SlottedPage):
    slot = page.insert(WHOLE, filler(1, 30))
    neighbour = page.insert(WHOLE, filler(2, 30))
    grown = filler(3, page.rewrite_capacity(slot))
    page.rewrite(slot, WHOLE, grown)
    assert page.fragment(slot) == (WHOLE, grown)
    assert page.fragment(neighbour) == (WHOLE, filler(2, 30))
    assert page.insertable_bytes() == 0


def test_a_rewrite_that_does_not_fit_leaves_the_page_untouched(page: SlottedPage):
    slot = page.insert(WHOLE, filler(1, 30))
    page.insert(WHOLE, filler(2, 30))
    before = page.to_bytes()
    with pytest.raises(PageOverflowError):
        page.rewrite(slot, WHOLE, filler(3, page.rewrite_capacity(slot) + 1))
    assert page.to_bytes() == before


def test_a_page_survives_its_bytes(page: SlottedPage):
    slots = [page.insert(WHOLE, filler(marker, 12)) for marker in range(4)]
    page.free(slots[2])
    reloaded = SlottedPage.from_bytes(page.to_bytes())
    assert list(reloaded.fragments()) == list(page.fragments())
    assert reloaded.insertable_bytes() == page.insertable_bytes()


def test_a_free_slot_has_no_fragment(page: SlottedPage):
    slots = [page.insert(WHOLE, b"a"), page.insert(WHOLE, b"b")]
    page.free(slots[0])
    with pytest.raises(EmptySlotError):
        page.fragment(slots[0])
    with pytest.raises(EmptySlotError):
        page.free(slots[0])
    with pytest.raises(EmptySlotError):
        page.rewrite(slots[0], WHOLE, b"c")


@pytest.mark.parametrize("slot", [-1, 1, 500])
def test_a_slot_outside_the_directory_is_rejected(page: SlottedPage, slot: int):
    page.insert(WHOLE, b"a")
    with pytest.raises(SlotOutOfRangeError):
        page.fragment(slot)
