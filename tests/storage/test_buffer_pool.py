"""Un solo buffer pool para todos los archivos de una configuración."""

import threading
from dataclasses import replace

from config import EngineConfig
from storage.buffer_pool import BufferPool
from storage.pager import Pager

POOL_PAGES = 4
READERS = 4


def page(config: EngineConfig, marker: int) -> bytes:
    return bytes([marker]) * config.page_size


def filled(path_config: EngineConfig, name: str, pages: int, marker: int) -> Pager:
    pager = Pager(path_config.data_directory / name, path_config)
    for number in range(pages):
        pager.write(pager.allocate(), page(path_config, marker + number))
    return pager


def test_every_file_shares_one_budget(config: EngineConfig):
    small = replace(config, buffer_pool_pages=POOL_PAGES)
    with filled(small, "a", 3, 10) as first, filled(small, "b", 3, 20) as second:
        assert BufferPool.shared(small).resident_pages <= POOL_PAGES
        assert [first.read(number) for number in range(3)] == [page(small, 10 + n) for n in range(3)]
        assert [second.read(number) for number in range(3)] == [page(small, 20 + n) for n in range(3)]


def test_one_busy_file_can_use_the_whole_budget(config: EngineConfig):
    small = replace(config, buffer_pool_pages=POOL_PAGES)
    with filled(small, "a", 1, 1), filled(small, "b", POOL_PAGES, 2):
        assert BufferPool.shared(small).resident_pages == POOL_PAGES


def test_a_page_evicted_while_dirty_reaches_its_file(config: EngineConfig):
    small = replace(config, buffer_pool_pages=POOL_PAGES)
    path = small.data_directory / "a"
    with filled(small, "a", POOL_PAGES * 3, 0) as pager:
        assert pager.writes > 0
    with Pager(path, small) as reopened:
        assert reopened.read(0) == page(small, 0)
        assert reopened.read(POOL_PAGES * 3 - 1) == page(small, POOL_PAGES * 3 - 1)


def test_closing_a_file_frees_its_pages(config: EngineConfig):
    pool = BufferPool.shared(config)
    with filled(config, "a", 2, 1):
        before = pool.resident_pages
        with filled(config, "b", 2, 5):
            assert pool.resident_pages == before + 2
        assert pool.resident_pages == before


def test_files_of_one_configuration_share_the_pool(config: EngineConfig):
    with Pager(config.data_directory / "a", config) as first:
        assert BufferPool.shared(config) is BufferPool.shared(config)
        assert first._pool is BufferPool.shared(config)
        other = replace(config, page_size=config.page_size * 2)
        assert BufferPool.shared(other) is not BufferPool.shared(config)


def test_concurrent_readers_see_their_own_pages(config: EngineConfig):
    small = replace(config, buffer_pool_pages=POOL_PAGES)
    pagers = [filled(small, f"f{number}", 6, number * 10) for number in range(READERS)]
    failures: list[str] = []

    def read_back(pager: Pager, marker: int) -> None:
        for _ in range(20):
            for number in range(6):
                if pager.read(number) != page(small, marker + number):
                    failures.append(pager.path.name)

    threads = [
        threading.Thread(target=read_back, args=(pager, number * 10))
        for number, pager in enumerate(pagers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for pager in pagers:
        pager.close()
    assert failures == []
