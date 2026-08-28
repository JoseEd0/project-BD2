import pytest

from config import EngineConfig
from storage.pager import HEADER_PAGE_ID, PageNotFoundError, Pager
from storage.types import StorageError


def test_new_file_has_no_pages(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager:
        assert pager.page_count == 0


def test_allocate_returns_consecutive_ids(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager:
        assert [pager.allocate() for _ in range(3)] == [0, 1, 2]


def test_read_returns_what_was_written(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager:
        page_id = pager.allocate()
        pager.write(page_id, b"\x07" * config.page_size)
        assert pager.read(page_id) == b"\x07" * config.page_size


def test_read_returns_a_copy(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager:
        page_id = pager.allocate()
        copy = bytearray(pager.read(page_id))
        copy[0] = 9
        assert pager.read(page_id)[0] == 0


def test_pages_survive_reopening(config: EngineConfig):
    path = config.data_directory / "a.dat"
    with Pager(path, config) as pager:
        for value in range(5):
            pager.write(pager.allocate(), bytes([value]) * config.page_size)
    with Pager(path, config) as pager:
        assert pager.page_count == 5
        assert pager.read(3)[0] == 3


def test_eviction_writes_dirty_pages(config: EngineConfig):
    path = config.data_directory / "a.dat"
    with Pager(path, config) as pager:
        for value in range(config.buffer_pool_pages * 3):
            pager.write(pager.allocate(), bytes([value % 256]) * config.page_size)
        assert pager.writes > 0
    with Pager(path, config) as pager:
        for value in range(config.buffer_pool_pages * 3):
            assert pager.read(value)[0] == value % 256


def test_unknown_page_is_rejected(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager, pytest.raises(PageNotFoundError):
        pager.read(HEADER_PAGE_ID)


def test_writing_a_block_of_the_wrong_size_is_rejected(config: EngineConfig):
    with Pager(config.data_directory / "a.dat", config) as pager:
        pager.allocate()
        with pytest.raises(StorageError):
            pager.write(0, b"corto")



def test_closing_twice_is_harmless(config: EngineConfig):
    pager = Pager(config.data_directory / "a.dat", config)
    pager.close()
    pager.close()
