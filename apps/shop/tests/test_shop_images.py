"""Fotos: reducción, recodificación, deduplicación por contenido y reanudación."""

import io
from pathlib import Path

import pytest
from apps.shop.tests.shop_support import FakeTransport, image_bytes
from PIL import Image

from shop.dataset.images import (
    LEDGER_NAME,
    ImageDecodeError,
    ImageFetcher,
    ImageSettings,
    ImageStore,
    image_report,
    to_jpeg,
)

MAX_SIDE = 64
QUALITY = 85
SETTINGS = ImageSettings(
    max_side=MAX_SIDE, jpeg_quality=QUALITY, workers=2, attempts=3, backoff_seconds=0.0
)
RED = (200, 10, 10)
BLUE = (10, 10, 200)


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def fetcher(root: Path, transport: FakeTransport) -> ImageFetcher:
    return ImageFetcher(root, transport, SETTINGS, sleep=lambda _: None)


def test_a_large_image_is_reduced_to_the_max_side_keeping_its_shape() -> None:
    image = opened(to_jpeg(image_bytes((256, 128), RED), MAX_SIDE, QUALITY))
    assert (image.format, image.mode, image.size) == ("JPEG", "RGB", (64, 32))


def test_a_small_image_is_never_enlarged() -> None:
    image = opened(to_jpeg(image_bytes((20, 10), RED), MAX_SIDE, QUALITY))
    assert image.size == (20, 10)


def test_transparency_is_flattened_on_white() -> None:
    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(output, "PNG")
    image = opened(to_jpeg(output.getvalue(), MAX_SIDE, QUALITY))
    assert image.mode == "RGB"
    assert all(channel > 245 for channel in image.getpixel((4, 4)))  # type: ignore[union-attr]


def test_the_same_input_always_gives_the_same_bytes() -> None:
    data = image_bytes((100, 80), BLUE, "JPEG")
    assert to_jpeg(data, MAX_SIDE, QUALITY) == to_jpeg(data, MAX_SIDE, QUALITY)


def test_bytes_that_are_not_an_image_are_rejected() -> None:
    with pytest.raises(ImageDecodeError):
        to_jpeg(b"<html>not found</html>", MAX_SIDE, QUALITY)


def test_the_store_names_files_by_content_and_verifies_them(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    jpeg = to_jpeg(image_bytes((10, 10), RED), MAX_SIDE, QUALITY)
    path = store.save(jpeg)
    assert store.save(jpeg) == path
    assert store.verify(path)
    (tmp_path / path).write_bytes(b"corrupted")
    assert not store.verify(path)
    assert not store.verify("images/00/missing.jpg")


def test_identical_photos_from_two_urls_share_one_file(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/a.jpg", image_bytes((90, 90), RED))
    transport.serve("https://cdn/b.jpg", image_bytes((90, 90), RED))
    transport.serve("https://cdn/c.jpg", image_bytes((90, 90), BLUE))
    images = fetcher(tmp_path, transport)
    outcomes = images.fetch(["https://cdn/a.jpg", "https://cdn/b.jpg", "https://cdn/c.jpg"])
    assert outcomes["https://cdn/a.jpg"].image == outcomes["https://cdn/b.jpg"].image
    assert outcomes["https://cdn/a.jpg"].image != outcomes["https://cdn/c.jpg"].image
    report = image_report(
        {"P1": "https://cdn/a.jpg", "P2": "https://cdn/b.jpg", "P3": "https://cdn/c.jpg"},
        outcomes,
        images.store,
    )
    assert (report.with_image, report.files, report.deduplicated) == (3, 2, 1)
    assert len(list((tmp_path / "images").rglob("*.jpg"))) == 2


def test_a_shared_url_is_downloaded_once(tmp_path: Path, transport: FakeTransport) -> None:
    transport.serve("https://cdn/a.jpg", image_bytes((9, 9), RED))
    fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg", "https://cdn/a.jpg"])
    assert transport.requests == ["https://cdn/a.jpg"]


def test_failures_are_recorded_and_never_stop_the_others(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/ok.jpg", image_bytes((9, 9), RED))
    transport.serve("https://cdn/html.jpg", b"<html>")
    outcomes = fetcher(tmp_path, transport).fetch(
        ["https://cdn/ok.jpg", "https://cdn/gone.jpg", "https://cdn/html.jpg"]
    )
    assert outcomes["https://cdn/ok.jpg"].image is not None
    assert outcomes["https://cdn/gone.jpg"].error is not None
    assert not outcomes["https://cdn/gone.jpg"].retryable
    assert not outcomes["https://cdn/html.jpg"].retryable
    report = image_report(
        {"P1": "https://cdn/ok.jpg", "P2": "https://cdn/gone.jpg", "P3": None},
        outcomes,
        ImageStore(tmp_path),
    )
    assert (report.with_image, report.failed, report.without_url) == (1, 1, 1)


def test_a_server_error_is_retried_until_it_works(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/a.jpg", 503, 429, image_bytes((9, 9), RED))
    outcome = fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])["https://cdn/a.jpg"]
    assert outcome.image is not None
    assert len(transport.requests) == 3


def test_a_second_run_does_not_go_to_the_network(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/a.jpg", image_bytes((9, 9), RED))
    urls = ["https://cdn/a.jpg", "https://cdn/gone.jpg"]
    first = fetcher(tmp_path, transport).fetch(urls)
    transport.requests.clear()
    second = fetcher(tmp_path, transport).fetch(urls)
    assert transport.requests == []
    assert second == first


def test_a_lasting_server_error_is_retried_on_the_next_run(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/a.jpg", 503)
    assert fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])["https://cdn/a.jpg"].retryable
    transport.serve("https://cdn/a.jpg", image_bytes((9, 9), RED))
    outcome = fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])["https://cdn/a.jpg"]
    assert outcome.image is not None


def test_a_deleted_or_corrupted_file_is_downloaded_again(
    tmp_path: Path, transport: FakeTransport
) -> None:
    transport.serve("https://cdn/a.jpg", image_bytes((9, 9), RED))
    path = fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])["https://cdn/a.jpg"].image
    assert path is not None
    (tmp_path / path).write_bytes(b"truncated")
    transport.requests.clear()
    fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])
    assert transport.requests == ["https://cdn/a.jpg"]
    assert ImageStore(tmp_path).verify(path)


def test_a_ledger_line_cut_in_half_is_ignored(tmp_path: Path, transport: FakeTransport) -> None:
    transport.serve("https://cdn/a.jpg", image_bytes((9, 9), RED))
    (tmp_path / LEDGER_NAME).write_text('{"url": "https://cdn/a.jpg", "ima')
    outcome = fetcher(tmp_path, transport).fetch(["https://cdn/a.jpg"])["https://cdn/a.jpg"]
    assert outcome.image is not None
