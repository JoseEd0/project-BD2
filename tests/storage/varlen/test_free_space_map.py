"""Mapa de espacio libre: posiciones fijas de las páginas de mapa y búsqueda de sitio."""

from pathlib import Path

import pytest

from config import EngineConfig
from storage.pager import Pager
from storage.varlen.free_space_map import FIRST_MAP_PAGE, FreeSpaceMap
from storage.varlen.slotted_page import max_fragment_bytes

WIDE_PAGE_SIZES = (64, 1000, 4096, 1 << 15)
CAPACITY_SAMPLES = 97


def open_map(pager: Pager) -> FreeSpaceMap:
    return FreeSpaceMap(pager, max_fragment_bytes(pager.page_size))


@pytest.fixture
def pager(config: EngineConfig):
    with Pager(config.data_directory / "t.fsm", config) as opened:
        opened.allocate()
        yield opened


def test_an_empty_map_finds_nothing(pager: Pager):
    space = open_map(pager)
    assert space.find(0) is None
    assert space.map_page_count == 0
    assert space.data_page_count == 0
    assert list(space.data_pages()) == []


def test_the_first_allocation_adds_the_map_page_before_the_data(pager: Pager):
    space = open_map(pager)
    page_id = space.allocate()
    assert page_id == FIRST_MAP_PAGE + 1
    assert space.map_page_count == 1
    assert not space.is_data_page(FIRST_MAP_PAGE)
    assert space.is_data_page(page_id)
    assert space.find(1) is None, "una página recién creada no tiene sitio hasta que se anota"


def test_find_returns_the_first_page_with_enough_room(pager: Pager):
    space = open_map(pager)
    pages = [space.allocate() for _ in range(3)]
    space.record(pages[0], 10)
    space.record(pages[1], 100)
    space.record(pages[2], 200)
    assert space.find(5) == pages[0]
    assert space.find(50) == pages[1]
    assert space.find(150) == pages[2]
    assert space.find(201) is None


def test_lowering_the_only_roomy_page_hides_it(pager: Pager):
    space = open_map(pager)
    page_id = space.allocate()
    space.record(page_id, 120)
    space.record(page_id, 0)
    assert space.find(1) is None


@pytest.mark.parametrize("page_size", WIDE_PAGE_SIZES)
def test_a_category_never_promises_more_room_than_there_is(tmp_path: Path, page_size: int):
    """Las categorías redondean: una página puede quedar fuera teniendo sitio, nunca dentro
    sin tenerlo."""
    wide = EngineConfig(page_size=page_size, buffer_pool_pages=4, data_directory=tmp_path)
    capacity = max_fragment_bytes(page_size)
    samples = range(0, capacity + 1, max(1, capacity // CAPACITY_SAMPLES))
    with Pager(tmp_path / "w.fsm", wide) as wide_pager:
        wide_pager.allocate()
        space = open_map(wide_pager)
        page_id = space.allocate()
        for room in samples:
            space.record(page_id, room)
            assert all(space.find(size) is None for size in samples if size > room)


@pytest.mark.parametrize("page_size", WIDE_PAGE_SIZES)
def test_an_empty_page_takes_a_fragment_of_a_whole_page(tmp_path: Path, page_size: int):
    wide = EngineConfig(page_size=page_size, buffer_pool_pages=4, data_directory=tmp_path)
    capacity = max_fragment_bytes(page_size)
    with Pager(tmp_path / "w.fsm", wide) as wide_pager:
        wide_pager.allocate()
        space = open_map(wide_pager)
        page_id = space.allocate()
        space.record(page_id, capacity)
        assert space.find(capacity) == page_id


def test_data_pages_skip_the_map_pages_of_every_group(config: EngineConfig, pager: Pager):
    space = open_map(pager)
    pages = [space.allocate() for _ in range(config.page_size + 2)]
    second_map = FIRST_MAP_PAGE + config.page_size + 1
    assert space.map_page_count == 2
    assert second_map not in pages
    assert list(space.data_pages()) == pages
    assert space.data_page_count == len(pages)
    space.record(pages[-1], 60)
    assert space.find(30) == pages[-1]


def test_the_map_survives_reopening(config: EngineConfig):
    path = config.data_directory / "r.fsm"
    with Pager(path, config) as pager:
        pager.allocate()
        space = open_map(pager)
        pages = [space.allocate() for _ in range(config.page_size + 2)]
        space.record(pages[-1], 60)
    with Pager(path, config) as pager:
        reopened = open_map(pager)
        assert reopened.map_page_count == 2
        assert reopened.find(30) == pages[-1]
