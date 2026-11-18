"""Valores de Python como los lee el gestor, y filas en NDJSON sin materializarlas."""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import AsyncIterator, Iterator

import numpy as np
import pytest

from minigestor_client import Point, UnencodableValueError
from minigestor_client.values import async_ndjson_chunks, encode_json, ndjson_chunks, ndjson_line

SMALL_CHUNK = 64


def test_a_point_is_a_lat_lon_object():
    assert json.loads(encode_json([Point(-12.05, -77.04)])) == [{"lat": -12.05, "lon": -77.04}]


def test_numpy_arrays_and_scalars_become_plain_numbers():
    value = {"emb": np.array([0.5, 1.5], dtype=np.float32), "n": np.int64(3)}
    assert json.loads(encode_json(value)) == {"emb": [0.5, 1.5], "n": 3}


def test_text_keeps_its_characters():
    assert encode_json("año") == '"año"'.encode()


@pytest.mark.parametrize("value", [math.nan, math.inf, object(), {1, 2}])
def test_values_the_engine_cannot_read_are_refused(value: object):
    with pytest.raises(UnencodableValueError):
        encode_json([value])


def test_a_row_is_one_json_line():
    assert ndjson_line({"id": 1, "v": [1, 2]}) == b'{"id":1,"v":[1,2]}\n'


def test_a_row_that_is_not_a_mapping_is_refused():
    with pytest.raises(UnencodableValueError):
        ndjson_line([1, 2])  # type: ignore[arg-type]


def test_rows_are_joined_into_chunks_of_about_the_size_asked():
    rows = [{"id": number} for number in range(100)]
    chunks = list(ndjson_chunks(rows, SMALL_CHUNK))
    assert b"".join(chunks) == b"".join(ndjson_line(row) for row in rows)
    assert all(len(chunk) >= SMALL_CHUNK for chunk in chunks[:-1])
    assert all(len(chunk) < SMALL_CHUNK + len(ndjson_line(rows[-1])) for chunk in chunks)


def test_rows_are_pulled_only_when_a_chunk_needs_them():
    pulled = []

    def endless() -> Iterator[dict[str, int]]:
        for number in itertools.count():
            pulled.append(number)
            yield {"id": number}

    first = next(ndjson_chunks(endless(), SMALL_CHUNK))
    assert len(pulled) == first.count(b"\n")


def test_no_rows_is_no_chunk():
    assert list(ndjson_chunks([])) == []


@pytest.mark.anyio
async def test_async_chunks_read_sync_and_async_rows_alike():
    rows = [{"id": number, "p": Point(1.0, 2.0)} for number in range(30)]

    async def produced() -> AsyncIterator[dict[str, object]]:
        for row in rows:
            yield row

    from_sync = [chunk async for chunk in async_ndjson_chunks(rows, SMALL_CHUNK)]
    from_async = [chunk async for chunk in async_ndjson_chunks(produced(), SMALL_CHUNK)]
    assert from_sync == from_async == list(ndjson_chunks(rows, SMALL_CHUNK))
