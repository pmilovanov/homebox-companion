"""Pre-printed asset ID labels: find them in photos.

Decodes QR codes from image bytes and keeps only the payloads that look like
one of our asset ID labels. This runs on the original bytes, before any resize
for the vision model: a 40 mm label in a wide shot of a shelf can be ~100 px
across, which decodes fine at full resolution and is lost once the photo is
scaled to 2048 px.

Two kinds of label are recognised, each switched on separately:

- The labels Homebox prints itself, from its label maker and its label sheet
  generator: a QR code holding ``{homebox}/a/{asset id}``. The URL shape is
  proof enough, so no pattern applies to these.
- Anything else, matched against a configured pattern in full. Deliberately
  strict: product packaging is full of QR codes, and without a tight match a
  marketing URL would end up filed as an item's asset ID.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import zxingcpp
from loguru import logger
from PIL import Image

# What Homebox prints: an optional http(s) origin, an optional path prefix, then
# /a/ and the asset ID as digits, hyphenated or not (000-013, 100-0268434500000).
# Nothing may follow: no query, no fragment, no further path.
_HOMEBOX_ASSET_URL = re.compile(
    r"^(?:https?://[^/\s?#]+)?(?:/[^\s?#]*)?/a/([0-9](?:[0-9-]*[0-9])?)/?$",
    re.IGNORECASE,
)

# Homebox stores asset IDs as int64, with 0 meaning "none".
_INT64_MAX = 2**63 - 1

_QR_ONLY = zxingcpp.BarcodeFormats([zxingcpp.BarcodeFormat.QRCode])


@lru_cache(maxsize=8)
def _compile(pattern: str) -> re.Pattern[str] | None:
    """Compile the acceptance pattern once; an unusable one disables detection."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning(f"Ignoring unusable HBC_ASSET_ID_LABEL_PATTERN {pattern!r}: {exc}")
        return None


@dataclass(frozen=True)
class LabelPolicy:
    """Which QR payloads count as one of our asset ID labels.

    ``pattern`` accepts bare payloads that match it in full; empty accepts
    none. ``homebox_urls`` accepts Homebox's own ``/a/{asset id}`` URLs from
    any host: an instance is often reached by more than one name, and the
    path shape is specific enough on its own.
    """

    pattern: str = ""
    homebox_urls: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> LabelPolicy:
        """The policy the server is configured with (HBC_ASSET_ID_LABEL_PATTERN, HBC_ASSET_ID_HOMEBOX_LABELS)."""
        return cls(pattern=settings.asset_id_label_pattern, homebox_urls=settings.asset_id_homebox_labels)

    @property
    def pattern_usable(self) -> bool:
        """A non-empty pattern that compiles."""
        return bool(self.pattern) and _compile(self.pattern) is not None

    @property
    def enabled(self) -> bool:
        """Whether there is anything to look for at all.

        The one answer for every caller, so that the decoder, /config and the
        capture screen agree, and a pattern which fails to compile reads as
        "off" everywhere, not only inside the decoder.
        """
        return self.homebox_urls or self.pattern_usable


def parse_homebox_asset_url(payload: str) -> str | None:
    """The asset ID in a Homebox asset URL, hyphens removed, or None.

    Homebox puts ``{base url}/a/{asset id}`` in the QR code of every label it
    prints, the ID spelled ``000-013``. Accepted with any scheme and host,
    with or without a path prefix, or as the bare path; the path must end in
    ``/a/`` and digits, with nothing after. The item page URL Homebox shows
    as a "Page URL" QR (``/item/{uuid}``) is a link, not a label.
    """
    match = _HOMEBOX_ASSET_URL.match(payload.strip())
    return match.group(1).replace("-", "") if match else None


def parse_asset_id(payload: str) -> str:
    """Extract an asset ID from a QR payload, as Homebox would read it.

    Accepts either a Homebox asset URL or a bare ID. Hyphens are dropped
    because Homebox drops them: it shows this ID as ``900-26843450000``, puts
    that form in its own ``/a/`` URLs, and reads both spellings as one
    integer. Anything else comes back trimmed and hyphen-free but otherwise
    unchanged, for the pattern to reject.
    """
    from_url = parse_homebox_asset_url(payload)
    if from_url is not None:
        return from_url
    return payload.strip().replace("-", "")


def is_label_asset_id(candidate: str, pattern: str) -> bool:
    """Whether a candidate matches the configured label pattern in full.

    Matched against the whole string, so a pattern without anchors cannot
    accept a longer payload that merely contains an ID.
    """
    if not candidate or not pattern:
        return False
    regex = _compile(pattern)
    return regex is not None and regex.fullmatch(candidate) is not None


def _storable(digits: str) -> bool:
    """An ID Homebox can hold: a positive int64. 0 is "no asset ID" to Homebox."""
    return len(digits) <= 19 and 0 < int(digits) <= _INT64_MAX


def accept_label(payload: str, policy: LabelPolicy) -> str | None:
    """The asset ID a QR payload stands for under ``policy``, or None.

    A Homebox asset URL is taken on its shape when ``homebox_urls`` is on.
    Otherwise the ID it carries, like any bare payload, is held against the
    pattern, so a pattern user's own labels may be URLs too.
    """
    from_url = parse_homebox_asset_url(payload)
    if from_url is not None and policy.homebox_urls and _storable(from_url):
        return from_url
    candidate = parse_asset_id(payload)
    if is_label_asset_id(candidate, policy.pattern):
        return candidate
    return None


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


def find_asset_id_labels(images: Iterable[bytes], policy: LabelPolicy) -> list[str]:
    """Asset IDs printed on labels visible in any of the images.

    Returns the accepted IDs in the order they were found, deduplicated, so a
    label that appears in both the main photo and a close-up counts once. The
    caller decides what to do when there is more than one: a photo of a
    single item should carry a single label, so several is ambiguous rather
    than a bonus.
    """
    if not policy.enabled:
        return []

    found: list[str] = []
    for data in images:
        for payload in decode_qr_payloads(data):
            candidate = accept_label(payload, policy)
            if candidate is None:
                logger.debug("QR code in photo is not an asset ID label; ignoring")
                continue
            if candidate not in found:
                found.append(candidate)

    if found:
        logger.info(f"Asset ID label(s) found in photo: {', '.join(found)}")
    return found
