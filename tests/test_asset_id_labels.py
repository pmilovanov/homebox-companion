"""Pre-printed asset ID label detection in photos.

These generate real QR codes and decode them back through the production
path, so they exercise the decoder, the payload parsing and the acceptance
pattern together rather than mocking any of it.
"""

from __future__ import annotations

import io

import pytest
import zxingcpp
from PIL import Image

from homebox_companion.tools.vision.labels import (
    decode_qr_payloads,
    find_asset_id_labels,
    is_label_asset_id,
    parse_asset_id,
)

PATTERN = r"^9\d{13}$"
LABEL = "90026843450000"
OTHER_LABEL = "90026843450001"


def qr_png(payload: str, px: int, canvas: tuple[int, int] | None = None, at: tuple[int, int] = (0, 0)) -> bytes:
    """A QR code as PNG bytes, `px` wide, optionally pasted into a larger canvas.

    The canvas case stands in for a label on an item in a wide photo, which is
    the situation full-resolution decoding exists for.
    """
    barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode)
    symbol = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=4)).convert("L")
    symbol = symbol.resize((px, px), Image.Resampling.NEAREST)
    if canvas:
        page = Image.new("L", canvas, 190)
        page.paste(symbol, at)
        symbol = page
    buf = io.BytesIO()
    symbol.save(buf, "PNG")
    return buf.getvalue()


# ---- parsing and acceptance ------------------------------------------------


@pytest.mark.parametrize(
    "payload,expected",
    [
        (LABEL, LABEL),
        (f"https://homebox.example.com/a/{LABEL}", LABEL),
        ("https://homebox.example.com/a/000-042", "000-042"),
        (f"  {LABEL}\n", LABEL),
        ("Buy now at example.com", "Buy now at example.com"),
    ],
)
def test_parse_asset_id(payload: str, expected: str) -> None:
    assert parse_asset_id(payload) == expected


@pytest.mark.parametrize(
    "candidate,accepted",
    [
        (LABEL, True),
        ("0026843450000", False),  # leading zero: not one of ours
        ("9002684345000", False),  # 13 digits
        ("900268434500000", False),  # 15 digits
        ("000-042", False),  # legacy Homebox id
        ("https://acme.example/promo", False),
        ("012345678905", False),  # a UPC
        ("", False),
    ],
)
def test_is_label_asset_id(candidate: str, accepted: bool) -> None:
    assert is_label_asset_id(candidate, PATTERN) is accepted


def test_pattern_is_matched_in_full() -> None:
    """An unanchored pattern must not accept a longer payload that merely
    contains an id; the pattern decides what an id *is*, not what it contains."""
    assert is_label_asset_id(LABEL, r"9\d{13}")
    assert not is_label_asset_id(LABEL + "7", r"9\d{13}")
    assert not is_label_asset_id("x" + LABEL, r"9\d{13}")


def test_empty_pattern_disables_acceptance() -> None:
    assert not is_label_asset_id(LABEL, "")


def test_malformed_pattern_rejects_rather_than_raising() -> None:
    assert not is_label_asset_id(LABEL, r"^9(\d{13}$")


# ---- decoding --------------------------------------------------------------


def test_decodes_a_label_from_a_photo() -> None:
    assert find_asset_id_labels([qr_png(LABEL, 300)], PATTERN) == [LABEL]


def test_finds_a_small_label_in_a_full_size_frame() -> None:
    """A 100 px label in a 12 MP frame is what a 40 mm sticker looks like in a
    wide shot of a shelf. This is the case that would be lost after resizing
    for the vision model, and the reason detection runs on the original bytes."""
    photo = qr_png(LABEL, 100, canvas=(4032, 3024), at=(2900, 2100))
    assert find_asset_id_labels([photo], PATTERN) == [LABEL]


def test_accepts_a_homebox_asset_url_payload() -> None:
    photo = qr_png(f"https://homebox.example.com/a/{LABEL}", 300)
    assert find_asset_id_labels([photo], PATTERN) == [LABEL]


def test_ignores_a_qr_that_is_not_a_label() -> None:
    """Packaging is full of QR codes. None of them may become an asset id."""
    assert find_asset_id_labels([qr_png("https://acme.example/promo", 300)], PATTERN) == []


def test_a_label_seen_twice_counts_once() -> None:
    """The same sticker in the main photo and in a close-up is one label."""
    photos = [qr_png(LABEL, 300), qr_png(LABEL, 200)]
    assert find_asset_id_labels(photos, PATTERN) == [LABEL]


def test_two_different_labels_are_both_reported_in_order() -> None:
    """Several distinct labels is the caller's ambiguity to resolve, so both
    come back rather than one being silently chosen."""
    photos = [qr_png(LABEL, 300), qr_png(OTHER_LABEL, 300)]
    assert find_asset_id_labels(photos, PATTERN) == [LABEL, OTHER_LABEL]


def test_a_photo_with_no_code_is_not_an_error() -> None:
    blank = io.BytesIO()
    Image.new("L", (1200, 900), 190).save(blank, "PNG")
    assert find_asset_id_labels([blank.getvalue()], PATTERN) == []


def test_unreadable_bytes_are_skipped_not_raised() -> None:
    assert decode_qr_payloads(b"not an image") == []
    assert find_asset_id_labels([b"not an image", qr_png(LABEL, 300)], PATTERN) == [LABEL]


def test_empty_pattern_disables_detection_entirely() -> None:
    assert find_asset_id_labels([qr_png(LABEL, 300)], "") == []
