"""Decodificar lo que sube cualquiera: formatos, orientación, canales y tamaño declarado."""

from __future__ import annotations

import struct
import zlib

import cv2
import numpy as np
import pytest
from faces_support import encoded, solid
from PIL import Image

from faces import ImageTooLargeError, InvalidImageError, decode_image, resized_by

GENEROUS_CAP = 50_000_000
EXIF_ORIENTATION_TAG = 0x0112
# Orientación 6: la foto se guardó acostada y hay que girarla 90° en sentido horario.
ROTATE_CLOCKWISE = 6
WIDE, TALL = 40, 20
WHITE, BLACK, GRAY = 255, 0, 128
JPEG_TOLERANCE = 8


def png_claiming(width: int, height: int) -> bytes:
    """Un PNG de pocos bytes cuya cabecera promete `width` x `height` píxeles: lo que
    manda una bomba de descompresión."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + chunk(b"IEND", b"")
    )


def half_black(width: int, height: int) -> Image.Image:
    """Blanca con la mitad izquierda negra: se ve hacia dónde quedó girada."""
    pixels = np.full((height, width, 3), WHITE, dtype=np.uint8)
    pixels[:, : width // 2] = BLACK
    return Image.fromarray(pixels)


def test_a_jpeg_decodes_to_contiguous_bgr_of_eight_bits():
    image = decode_image(encoded(solid(WIDE, TALL, GRAY), "JPEG"), GENEROUS_CAP)

    assert image.shape == (TALL, WIDE, 3)
    assert image.dtype == np.uint8
    assert image.flags.c_contiguous


def test_channels_come_in_blue_green_red_order():
    red = Image.new("RGB", (WIDE, TALL), (WHITE, BLACK, BLACK))

    image = decode_image(encoded(red, "PNG"), GENEROUS_CAP)

    assert image[0, 0].tolist() == [BLACK, BLACK, WHITE]


@pytest.mark.parametrize("data", [b"", b"esto no es una imagen", b"\x89PNG\r\n\x1a\n" + b"\0" * 32])
def test_bytes_that_are_not_an_image_are_rejected(data: bytes):
    with pytest.raises(InvalidImageError, match="formato"):
        decode_image(data, GENEROUS_CAP)


def test_a_format_outside_the_accepted_ones_is_rejected():
    with pytest.raises(InvalidImageError, match="formato"):
        decode_image(encoded(solid(WIDE, TALL, GRAY), "PPM"), GENEROUS_CAP)


def test_a_truncated_jpeg_is_rejected():
    complete = encoded(solid(400, 300, GRAY), "JPEG")

    with pytest.raises(InvalidImageError, match="dañada"):
        decode_image(complete[: len(complete) // 2], GENEROUS_CAP)


def test_the_exif_orientation_is_applied():
    stored_lying_down = half_black(WIDE, TALL).rotate(90, expand=True)
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = ROTATE_CLOCKWISE

    image = decode_image(encoded(stored_lying_down, "JPEG", exif=exif), GENEROUS_CAP)

    assert image.shape[:2] == (TALL, WIDE)
    assert image[TALL // 2, 2].max() < JPEG_TOLERANCE
    assert image[TALL // 2, WIDE - 3].min() > WHITE - JPEG_TOLERANCE


def test_grayscale_becomes_three_equal_channels():
    gray = Image.new("L", (WIDE, TALL), GRAY)

    image = decode_image(encoded(gray, "PNG"), GENEROUS_CAP)

    assert image.shape == (TALL, WIDE, 3)
    assert set(image.reshape(-1).tolist()) == {GRAY}


def test_sixteen_bit_grayscale_keeps_its_high_byte():
    deep = Image.fromarray(np.full((TALL, WIDE), GRAY * 256, dtype=np.uint16))

    image = decode_image(encoded(deep, "PNG"), GENEROUS_CAP)

    assert set(image.reshape(-1).tolist()) == {GRAY}


def test_transparency_is_flattened_over_white():
    pixels = np.zeros((TALL, WIDE, 4), dtype=np.uint8)
    pixels[:, : WIDE // 2] = (BLACK, BLACK, BLACK, WHITE)
    with_alpha = Image.fromarray(pixels, mode="RGBA")

    image = decode_image(encoded(with_alpha, "PNG"), GENEROUS_CAP)

    assert image.shape == (TALL, WIDE, 3)
    assert image[0, 0].tolist() == [BLACK, BLACK, BLACK]
    assert image[0, WIDE - 1].tolist() == [WHITE, WHITE, WHITE]


def test_a_palette_with_a_transparent_colour_is_flattened_too():
    palette = solid(WIDE, TALL, BLACK).convert("P")

    image = decode_image(encoded(palette, "PNG", transparency=0), GENEROUS_CAP)

    assert set(image.reshape(-1).tolist()) == {WHITE}


def test_an_animated_gif_gives_its_first_frame():
    frames = [solid(WIDE, TALL, BLACK), solid(WIDE, TALL, WHITE)]

    image = decode_image(
        encoded(frames[0], "GIF", save_all=True, append_images=frames[1:]), GENEROUS_CAP
    )

    assert set(image.reshape(-1).tolist()) == {BLACK}


def test_an_image_declaring_more_pixels_than_allowed_is_rejected_before_decoding():
    with pytest.raises(ImageTooLargeError, match="2000 x 2000"):
        decode_image(png_claiming(2000, 2000), max_pixels=1_000_000)


def test_a_decompression_bomb_beyond_pillows_own_limit_is_rejected():
    with pytest.raises(ImageTooLargeError):
        decode_image(png_claiming(30_000, 30_000), GENEROUS_CAP)


def test_an_image_at_the_cap_is_accepted():
    image = decode_image(encoded(solid(WIDE, TALL, GRAY), "PNG"), max_pixels=WIDE * TALL)

    assert image.shape[:2] == (TALL, WIDE)


def test_resizing_keeps_the_proportions_both_ways():
    image = np.zeros((TALL, WIDE, 3), dtype=np.uint8)

    assert resized_by(image, 0.5).shape == (TALL // 2, WIDE // 2, 3)
    assert resized_by(image, 3).shape == (TALL * 3, WIDE * 3, 3)
    assert resized_by(image, 1) is image


def test_a_large_reduction_in_two_steps_averages_like_one():
    pixels = np.random.default_rng(89).integers(0, 256, (1000, 800, 3), dtype=np.uint8)
    smooth = cv2.GaussianBlur(pixels, (0, 0), 6)
    scale = 64 / 1000

    two_steps = resized_by(smooth, scale)
    one_step = cv2.resize(smooth, (51, 64), interpolation=cv2.INTER_AREA)

    assert two_steps.shape == one_step.shape == (64, 51, 3)
    assert np.abs(two_steps.astype(int) - one_step.astype(int)).mean() < 2
