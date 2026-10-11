"""images: resize, convert and compress for email; cached by content."""

import io
import os
import random

import pytest
from PIL import Image

from mailerlm.campaigns import images


def photo(width=2400, height=1600):
    """A smooth, photo-like picture: small random noise scaled up."""
    random.seed(1)
    small = Image.new("RGB", (40, 30))
    small.putdata(
        [
            (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for _ in range(1200)
        ]
    )
    return small.resize((width, height), Image.Resampling.BICUBIC)


def encode(image, fmt, **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, fmt, **kwargs)
    return buffer.getvalue()


def test_a_large_photo_becomes_a_small_jpeg_displayed_at_600():
    prepared = images.prepare(encode(photo(), "PNG"), 60 * 1024)
    assert prepared.mime == "image/jpeg" and len(prepared.data) <= 60 * 1024
    assert prepared.display_width == 600 and prepared.display_height == 400
    assert Image.open(io.BytesIO(prepared.data)).width <= images.MAX_FILE_WIDTH


def test_line_art_stays_png():
    art = Image.new("RGB", (400, 200), "white")
    for x in range(400):
        art.putpixel((x, 100), (0, 0, 0))
    prepared = images.prepare(encode(art, "PNG"), 50 * 1024)
    assert prepared.mime == "image/png" and prepared.display_width == 400


def test_transparency_keeps_png():
    prepared = images.prepare(
        encode(Image.new("RGBA", (100, 100), (255, 0, 0, 80)), "PNG"), 50 * 1024
    )
    assert prepared.mime == "image/png"


def test_webp_is_converted_to_a_format_clients_render():
    prepared = images.prepare(encode(photo(800, 600), "WEBP"), 60 * 1024)
    assert prepared.mime in ("image/jpeg", "image/png")


def test_an_animated_gif_that_fits_is_kept():
    frames = [Image.new("RGB", (50, 50), c).convert("P") for c in ("red", "blue")]
    data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert images.prepare(data, 50 * 1024).mime == "image/gif"


def test_a_file_that_is_not_an_image_is_an_error_without_the_content():
    with pytest.raises(images.ImageTooLarge, match="not an image"):
        images.prepare(b"hello", 1000)


def test_an_impossible_budget_names_the_budget():
    noise = Image.frombytes("RGB", (400, 400), os.urandom(400 * 400 * 3))
    with pytest.raises(images.ImageTooLarge, match="500 bytes"):
        images.prepare(encode(noise, "PNG"), 500)


def test_the_same_bytes_and_budget_are_prepared_once():
    data = encode(photo(800, 600), "PNG")
    assert images.prepare_cached(data, 60 * 1024) is images.prepare_cached(data, 60 * 1024)
