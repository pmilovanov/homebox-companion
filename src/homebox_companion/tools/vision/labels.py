"""Pre-printed asset ID labels: find them in photos.

Decodes QR codes from image bytes and keeps only the payloads that look like
one of our asset ID labels. This runs on the original bytes, before any resize
for the vision model: a 40 mm label in a wide shot of a shelf can be ~100 px
across, which decodes fine at full resolution and is lost once the photo is
scaled to 2048 px.

The acceptance pattern is deliberately strict. Product packaging is full of QR
codes, and without a tight match a marketing URL would end up filed as an
item's asset ID.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from functools import lru_cache

import zxingcpp
from loguru import logger
from PIL import Image

# Homebox asset URL: https://homebox.example.com/a/{asset_id}
_ASSET_URL = re.compile(r"/a/([^/\s]+)")

_QR_ONLY = zxingcpp.BarcodeFormats([zxingcpp.BarcodeFormat.QRCode])


@lru_cache(maxsize=8)
def _compile(pattern: str) -> re.Pattern[str] | None:
    """Compile the acceptance pattern once; an unusable one disables detection."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning(f"Ignoring unusable HBC_ASSET_ID_LABEL_PATTERN {pattern!r}: {exc}")
        return None


def label_detection_enabled(pattern: str) -> bool:
    """Whether label detection is on: a non-empty pattern that compiles.

    The one answer for every caller, so that a pattern which fails to compile
    reads as "off" everywhere, not only inside the decoder.
    """
    return bool(pattern) and _compile(pattern) is not None


def parse_asset_id(payload: str) -> str:
    """Extract an asset ID from a QR payload, as Homebox would read it.

    Accepts either a Homebox asset URL or a bare ID. Hyphens are dropped
    because Homebox drops them: it shows this ID as ``900-26843450000``, puts
    that form in its own ``/a/`` URLs, and reads both spellings as one
    integer. Anything else comes back trimmed and hyphen-free but otherwise
    unchanged, for the pattern to reject.
    """
    match = _ASSET_URL.search(payload)
    candidate = match.group(1) if match else payload.strip()
    return candidate.replace("-", "")


def is_label_asset_id(candidate: str, pattern: str) -> bool:
    """Whether a candidate matches the configured label pattern in full.

    Matched against the whole string, so a pattern without anchors cannot
    accept a longer payload that merely contains an ID.
    """
    if not candidate or not pattern:
        return False
    regex = _compile(pattern)
    return regex is not None and regex.fullmatch(candidate) is not None


def decode_qr_payloads(data: bytes) -> list[str]:
    """Every QR payload in one image, or an empty list if it has none.

    Never raises: a photo with no code in it is the ordinary case, and a photo
    the decoder cannot open must not take item detection down with it.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Greyscale is all the decoder needs, and it avoids converting a
            # 12 MP RGB frame internally on every call.
            results = zxingcpp.read_barcodes(img.convert("L"), formats=_QR_ONLY)
    except Exception as exc:
        logger.debug(f"QR decode skipped, image could not be read: {exc}")
        return []
    return [r.text for r in results if r.text]


def find_asset_id_labels(images: Iterable[bytes], pattern: str) -> list[str]:
    """Asset IDs printed on labels visible in any of the images.

    Returns the accepted IDs in the order they were found, deduplicated, so a
    label that appears in both the main photo and a close-up counts once. The
    caller decides what to do when there is more than one: a photo of a
    single item should carry a single label, so several is ambiguous rather
    than a bonus.
    """
    if not label_detection_enabled(pattern):
        return []

    found: list[str] = []
    for data in images:
        for payload in decode_qr_payloads(data):
            candidate = parse_asset_id(payload)
            if not is_label_asset_id(candidate, pattern):
                logger.debug("QR code in photo is not an asset ID label; ignoring")
                continue
            if candidate not in found:
                found.append(candidate)

    if found:
        logger.info(f"Asset ID label(s) found in photo: {', '.join(found)}")
    return found
