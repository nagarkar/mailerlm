"""Make an image fit an email: resize, convert to a format every client renders, compress.

Display width is at most 600 px; the file is up to twice that for sharp (retina) screens when the
source is large enough. Photos become JPEG, line art and transparent images become PNG
(palette-reduced when needed), animated GIFs keep their frames only when they fit, otherwise the
first frame becomes a PNG. WebP and other formats Pillow can read are converted; clients such as
Outlook do not render them. The result is remembered per process by source bytes and budget.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageOps

DISPLAY_WIDTH = 600
MAX_FILE_WIDTH = DISPLAY_WIDTH * 2


class ImageTooLarge(ValueError):
    """The image cannot be made small enough for its budget."""


@dataclass(frozen=True, slots=True)
class PreparedImage:
    data: bytes
    mime: str
    extension: str
    display_width: int
    display_height: int
    source_sha256: str


def _is_photo(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA", "P") and "A" in image.getbands():
        return False
    thumbnail = image.convert("RGB").resize((64, 64))
    colors = thumbnail.getcolors(maxcolors=64 * 64) or []
    return len(colors) > 400


def _encode_jpeg(image: Image.Image, budget: int) -> bytes | None:
    rgb = image.convert("RGB")
    for quality in (85, 78, 70, 62, 54, 46, 38):
        buffer = io.BytesIO()
        rgb.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= budget:
            return buffer.getvalue()
    return None


def _encode_png(image: Image.Image, budget: int) -> bytes | None:
    for colors in (None, 128, 64, 32):
        candidate = image
        if colors:
            has_alpha = "A" in image.getbands()
            candidate = image.convert("RGBA" if has_alpha else "RGB").quantize(
                colors, method=Image.Quantize.FASTOCTREE if has_alpha else Image.Quantize.MEDIANCUT
            )
        buffer = io.BytesIO()
        candidate.save(buffer, "PNG", optimize=True)
        if buffer.tell() <= budget:
            return buffer.getvalue()
    return None


def prepare(source: bytes, budget_bytes: int) -> PreparedImage:
    digest = hashlib.sha256(source).hexdigest()
    try:
        image = Image.open(io.BytesIO(source))
        image.load()
    except Exception as error:  # Pillow raises many types for a bad file
        raise ImageTooLarge(
            f"the file is not an image Pillow can read ({type(error).__name__})"
        ) from error
    animated = getattr(image, "is_animated", False)
    if animated and image.format == "GIF" and len(source) <= budget_bytes:
        return PreparedImage(
            source,
            "image/gif",
            "gif",
            min(image.width, DISPLAY_WIDTH),
            round(image.height * min(image.width, DISPLAY_WIDTH) / image.width),
            digest,
        )
    image = ImageOps.exif_transpose(image)
    if image.width > MAX_FILE_WIDTH:
        image = image.resize(
            (MAX_FILE_WIDTH, round(image.height * MAX_FILE_WIDTH / image.width)),
            Image.Resampling.LANCZOS,
        )
    display_width = (
        min(image.width, DISPLAY_WIDTH) if image.width <= DISPLAY_WIDTH else DISPLAY_WIDTH
    )
    display_height = round(image.height * display_width / image.width)
    photo = _is_photo(image)
    data = _encode_jpeg(image, budget_bytes) if photo else _encode_png(image, budget_bytes)
    width = image.width
    while data is None and width > 200:  # shrink the file (display size stays) until it fits
        width = round(width * 0.8)
        image = image.resize((width, round(image.height * 0.8)), Image.Resampling.LANCZOS)
        data = _encode_jpeg(image, budget_bytes) if photo else _encode_png(image, budget_bytes)
    if data is None:
        raise ImageTooLarge(f"cannot fit {budget_bytes} bytes; use a simpler or smaller image")
    mime, extension = ("image/jpeg", "jpg") if photo else ("image/png", "png")
    return PreparedImage(data, mime, extension, display_width, display_height, digest)


@lru_cache(maxsize=64)
def prepare_cached(source: bytes, budget_bytes: int) -> PreparedImage:
    """`prepare`, remembered for the life of the process (the same bytes and budget cost nothing twice)."""
    return prepare(source, budget_bytes)
