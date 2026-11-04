"""Cabecera de archivo, lista de páginas libres y cadenas de páginas."""

import pytest

from config import EngineConfig
from storage.layout import (
    CorruptFileError,
    FileHeader,
    FreePageList,
    chained_links,
    chained_pages,
)
from storage.page import NO_PAGE
from storage.pager import Pager
from storage.types import StorageError

FIELDS = "iQ"


class ToyFormatError(StorageError):
    pass


def header(version: int = 1) -> FileHeader:
    return FileHeader(b"TOYS", version, FIELDS, ToyFormatError, "un archivo de prueba")


def test_a_header_round_trips_its_fields(config: EngineConfig):
    with Pager(config.data_directory / "t", config) as pager:
        pager.allocate()
        header().write(pager, -7, 42)
        assert header().read(pager) == (-7, 42)


def test_a_header_of_another_kind_is_rejected(config: EngineConfig):
    with Pager(config.data_directory / "t", config) as pager:
        pager.allocate()
        FileHeader(b"ELSE", 1, FIELDS, ToyFormatError, "otro").write(pager, 0, 0)
        with pytest.raises(ToyFormatError, match="no es un archivo de prueba"):
            header().read(pager)


def test_a_header_of_another_version_is_rejected(config: EngineConfig):
    with Pager(config.data_directory / "t", config) as pager:
        pager.allocate()
        header(version=1).write(pager, 0, 0)
        with pytest.raises(ToyFormatError, match=r"versión 1 .* lee la 2"):
            header(version=2).read(pager)


def test_released_pages_are_reused_before_growing(config: EngineConfig):
    with Pager(config.data_directory / "t", config) as pager:
        free = FreePageList(pager, NO_PAGE)
        pages = [free.allocate() for _ in range(3)]
        free.release(pages[0])
        free.release(pages[2])
        assert list(free.pages()) == [pages[2], pages[0]]
        assert free.allocate() == pages[2]
        assert free.allocate() == pages[0]
        assert free.allocate() == 3
        assert free.head == NO_PAGE


def test_a_chain_that_loops_is_reported_instead_of_followed(config: EngineConfig):
    def looping(page_id: int) -> tuple[int, None]:
        return (page_id + 1) % 3, None

    with pytest.raises(CorruptFileError, match="vuelve sobre sí"):
        list(chained_pages(0, looping, 3, "t"))


def test_a_chain_ends_at_no_page():
    def ending(page_id: int) -> tuple[int, str]:
        return (page_id + 1 if page_id < 2 else NO_PAGE), f"p{page_id}"

    assert list(chained_pages(0, ending, 10, "t")) == [(0, "p0"), (1, "p1"), (2, "p2")]


def test_a_chain_of_any_kind_of_link_ends_at_its_end_link():
    links = {("a", 1): ("b", 2), ("b", 2): ("fin", 0)}

    def follow(link: tuple[str, int]) -> tuple[tuple[str, int], int]:
        return links[link], link[1]

    assert list(chained_links(("a", 1), follow, ("fin", 0), 5, "t")) == [
        (("a", 1), 1),
        (("b", 2), 2),
    ]
    with pytest.raises(CorruptFileError):
        list(chained_links(("a", 1), follow, ("fin", 0), 1, "t"))


def test_a_header_that_does_not_fit_in_a_page_is_a_format_error(config: EngineConfig):
    wide = FileHeader(b"TOYS", 1, "Q" * config.page_size, ToyFormatError, "un archivo de prueba")
    with Pager(config.data_directory / "t", config) as pager:
        pager.allocate()
        with pytest.raises(ToyFormatError, match="mide"):
            wide.write(pager)
