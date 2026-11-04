"""Flujos de bytes sobre páginas: escritura de corrido, lectura por tramos y secuencial."""

import random

import pytest

from config import EngineConfig
from storage.pager import Pager
from storage.stream import ByteStreamReader, ByteStreamWriter, StreamBoundsError

SEED = 20261104


def write_stream(pager: Pager, pieces: list[bytes]) -> ByteStreamWriter:
    writer = ByteStreamWriter(pager)
    for piece in pieces:
        writer.write(piece)
    writer.finish()
    return writer


def test_an_empty_stream_takes_no_pages(config: EngineConfig):
    with Pager(config.data_directory / "vacio.stream", config) as pager:
        writer = write_stream(pager, [])
        assert writer.size == 0
        assert pager.page_count == 0
        reader = ByteStreamReader(pager, writer.first_page, writer.size)
        assert reader.read(0, 0) == b""
        assert reader.cursor().read(0) == b""


def test_the_stream_starts_after_the_pages_already_in_the_file(config: EngineConfig):
    with Pager(config.data_directory / "cabecera.stream", config) as pager:
        pager.allocate()
        writer = write_stream(pager, [b"abc"])
        assert writer.first_page == 1
        assert pager.page_count == 2
        assert ByteStreamReader(pager, 1, writer.size).read(0, 3) == b"abc"


def test_pieces_are_written_back_to_back_across_pages(config: EngineConfig):
    generator = random.Random(SEED)
    pieces = [generator.randbytes(generator.randrange(0, 3 * config.page_size)) for _ in range(40)]
    content = b"".join(pieces)
    with Pager(config.data_directory / "largo.stream", config) as pager:
        writer = write_stream(pager, pieces)
        assert writer.size == len(content)
        assert pager.page_count == -(-len(content) // config.page_size)
        reader = ByteStreamReader(pager, writer.first_page, writer.size)
        assert reader.read(0, len(content)) == content
        for _ in range(200):
            start = generator.randrange(len(content))
            length = generator.randrange(len(content) - start + 1)
            assert reader.read(start, length) == content[start : start + length]


def test_the_cursor_reads_in_order_and_chunks_never_pass_a_page(config: EngineConfig):
    generator = random.Random(SEED)
    content = generator.randbytes(5 * config.page_size + 17)
    with Pager(config.data_directory / "cursor.stream", config) as pager:
        writer = write_stream(pager, [content])
        cursor = ByteStreamReader(pager, writer.first_page, writer.size).cursor()
        assert cursor.read(3) == content[:3]
        chunks = list(cursor.chunks(2 * config.page_size))
        assert b"".join(chunks) == content[3 : 3 + 2 * config.page_size]
        assert all(0 < len(chunk) <= config.page_size for chunk in chunks)
        assert cursor.read(len(content) - cursor.position) == content[3 + 2 * config.page_size :]


def test_reading_past_the_end_is_an_error(config: EngineConfig):
    with Pager(config.data_directory / "corto.stream", config) as pager:
        writer = write_stream(pager, [b"hola"])
        reader = ByteStreamReader(pager, writer.first_page, writer.size)
        with pytest.raises(StreamBoundsError):
            reader.read(2, 3)
        cursor = reader.cursor()
        cursor.read(4)
        with pytest.raises(StreamBoundsError):
            cursor.read(1)
