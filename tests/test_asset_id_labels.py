"""Pre-printed asset ID label detection in photos.

These generate real QR codes and decode them back through the production
path, so they exercise the decoder, the payload parsing and the acceptance
pattern together rather than mocking any of it.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
import zxingcpp
from PIL import Image

from homebox_companion.tools.vision.labels import (
    LabelPolicy,
    accept_label,
    decode_qr_payloads,
    find_asset_id_labels,
    is_label_asset_id,
    parse_asset_id,
    parse_homebox_asset_url,
)

PATTERN = r"^100[0-9]{13}$"
POLICY = LabelPolicy(pattern=PATTERN)
HOMEBOX_ONLY = LabelPolicy(homebox_urls=True)
BOTH = LabelPolicy(pattern=PATTERN, homebox_urls=True)
# What Homebox's own label maker prints for asset ID 000-013.
HOMEBOX_LABEL = "https://homebox.example.com/a/000-013"
LABEL = "1000268434500000"
OTHER_LABEL = "1000268434500001"
# How Homebox itself spells LABEL: split after three digits, in its UI and its /a/ URLs.
HOMEBOX_SPELLING = "100-0268434500000"


def qr_png(payload: str, px: int, canvas: tuple[int, int] | None = None, at: tuple[int, int] = (0, 0)) -> bytes:
    """A QR code as PNG bytes, `px` wide, optionally pasted into a larger canvas.

    The canvas case stands in for a label on an item in a wide photo, which is
    the situation full-resolution decoding exists for.
    """
    barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode)
    symbol = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=4)).convert("L")  # ty: ignore[invalid-argument-type]
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
        (f"https://homebox.example.com/a/{HOMEBOX_SPELLING}", LABEL),
        (HOMEBOX_SPELLING, LABEL),
        ("https://homebox.example.com/a/000-042", "000042"),
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
        ("90026843450000", False),  # the earlier 14-digit scheme
        ("100026843450000", False),  # 15 digits
        ("10002684345000000", False),  # 17 digits
        ("9000000000000000", False),  # 16 digits, outside the 100 block
        ("000042", False),  # a native Homebox id
        ("100" + "０" * 13, False),  # fullwidth digits: \d would take these, [0-9] does not
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
    assert is_label_asset_id(LABEL, r"100[0-9]{13}")
    assert not is_label_asset_id(LABEL + "7", r"100[0-9]{13}")
    assert not is_label_asset_id("x" + LABEL, r"100[0-9]{13}")


def test_empty_pattern_disables_acceptance() -> None:
    assert not is_label_asset_id(LABEL, "")


def test_malformed_pattern_rejects_rather_than_raising() -> None:
    assert not is_label_asset_id(LABEL, r"^100([0-9]{13}$")


# ---- decoding --------------------------------------------------------------


def test_decodes_a_label_from_a_photo() -> None:
    assert find_asset_id_labels([qr_png(LABEL, 300)], POLICY) == [LABEL]


def test_finds_a_small_label_in_a_full_size_frame() -> None:
    """A 100 px label in a 12 MP frame is what a 40 mm sticker looks like in a
    wide shot of a shelf. This is the case that would be lost after resizing
    for the vision model, and the reason detection runs on the original bytes."""
    photo = qr_png(LABEL, 100, canvas=(4032, 3024), at=(2900, 2100))
    assert find_asset_id_labels([photo], POLICY) == [LABEL]


def test_accepts_a_homebox_asset_url_payload() -> None:
    photo = qr_png(f"https://homebox.example.com/a/{LABEL}", 300)
    assert find_asset_id_labels([photo], POLICY) == [LABEL]


def test_accepts_the_url_homebox_itself_prints() -> None:
    """Homebox's label generator writes the id with a hyphen; that is the same id."""
    photo = qr_png(f"https://homebox.example.com/a/{HOMEBOX_SPELLING}", 300)
    assert find_asset_id_labels([photo], POLICY) == [LABEL]


def test_ignores_a_qr_that_is_not_a_label() -> None:
    """Packaging is full of QR codes. None of them may become an asset id."""
    assert find_asset_id_labels([qr_png("https://acme.example/promo", 300)], POLICY) == []


def test_a_label_seen_twice_counts_once() -> None:
    """The same sticker in the main photo and in a close-up is one label."""
    photos = [qr_png(LABEL, 300), qr_png(LABEL, 200)]
    assert find_asset_id_labels(photos, POLICY) == [LABEL]


def test_two_different_labels_are_both_reported_in_order() -> None:
    """Several distinct labels is the caller's ambiguity to resolve, so both
    come back rather than one being silently chosen."""
    photos = [qr_png(LABEL, 300), qr_png(OTHER_LABEL, 300)]
    assert find_asset_id_labels(photos, POLICY) == [LABEL, OTHER_LABEL]


def test_a_photo_with_no_code_is_not_an_error() -> None:
    blank = io.BytesIO()
    Image.new("L", (1200, 900), 190).save(blank, "PNG")
    assert find_asset_id_labels([blank.getvalue()], POLICY) == []


def test_unreadable_bytes_are_skipped_not_raised() -> None:
    assert decode_qr_payloads(b"not an image") == []
    assert find_asset_id_labels([b"not an image", qr_png(LABEL, 300)], POLICY) == [LABEL]


def test_nothing_switched_on_disables_detection_entirely() -> None:
    assert find_asset_id_labels([qr_png(LABEL, 300)], LabelPolicy()) == []


# ---- the labels Homebox prints itself --------------------------------------


@pytest.mark.parametrize(
    "payload,expected",
    [
        (HOMEBOX_LABEL, "000013"),
        ("http://192.168.1.10:7745/a/000-013", "000013"),
        ("https://homebox.example.com/a/000-013/", "000013"),
        ("https://homebox.example.com/homebox/a/000-013", "000013"),  # served under a path prefix
        ("/a/000-013", "000013"),
        (f"https://homebox.example.com/a/{HOMEBOX_SPELLING}", LABEL),
        ("HTTPS://HOMEBOX.EXAMPLE.COM/a/5", "5"),
        (f"  {HOMEBOX_LABEL}\n", "000013"),
        ("https://homebox.example.com/item/c1e6f830-1db5-44ba-93fc-97a6fb27e014", None),  # the "Page URL" QR
        ("https://homebox.example.com/a/000-013?print=1", None),
        ("https://homebox.example.com/a/000-013/photos", None),
        ("https://homebox.example.com/a/abc", None),
        ("https://homebox.example.com/a/", None),
        ("a/000-013", None),
        ("ftp://homebox.example.com/a/000-013", None),
        (LABEL, None),
    ],
)
def test_parse_homebox_asset_url(payload: str, expected: str | None) -> None:
    assert parse_homebox_asset_url(payload) == expected


def test_a_homebox_label_is_taken_on_its_url_alone() -> None:
    """Homebox's own labels carry no pattern of ours; the /a/ URL is the proof."""
    photo = qr_png(HOMEBOX_LABEL, 300)
    assert find_asset_id_labels([photo], HOMEBOX_ONLY) == ["000013"]
    # With only a pattern configured, 000-013 is not one of our ids.
    assert find_asset_id_labels([photo], POLICY) == []


def test_homebox_labels_are_read_from_any_host() -> None:
    """An instance is reached by more than one name; the path shape is the proof."""
    photo = qr_png("http://10.0.0.7:7745/a/000-013", 300)
    assert find_asset_id_labels([photo], HOMEBOX_ONLY) == ["000013"]


def test_a_bare_id_still_needs_the_pattern() -> None:
    assert find_asset_id_labels([qr_png(LABEL, 300)], HOMEBOX_ONLY) == []
    assert find_asset_id_labels([qr_png(LABEL, 300)], BOTH) == [LABEL]


def test_both_kinds_of_label_in_one_set_of_photos() -> None:
    photos = [qr_png(HOMEBOX_LABEL, 300), qr_png(LABEL, 300)]
    assert find_asset_id_labels(photos, BOTH) == ["000013", LABEL]


def test_the_item_page_qr_is_not_a_label() -> None:
    """Homebox also shows a "Page URL" QR for /item/{uuid}; that is a link, not an asset ID."""
    photo = qr_png("https://homebox.example.com/item/c1e6f830-1db5-44ba-93fc-97a6fb27e014", 300)
    assert find_asset_id_labels([photo], BOTH) == []


@pytest.mark.parametrize(
    "payload",
    [
        "https://homebox.example.com/a/000-000",  # 0 is "no asset ID" to Homebox
        "https://homebox.example.com/a/" + "9" * 20,  # past int64
    ],
)
def test_an_id_homebox_cannot_hold_is_not_a_label(payload: str) -> None:
    assert accept_label(payload, HOMEBOX_ONLY) is None


class TestLabelPolicy:
    @pytest.mark.parametrize(
        "pattern,homebox_urls,enabled",
        [
            ("", False, False),
            (PATTERN, False, True),
            ("", True, True),
            (r"^100([0-9]{13}$", False, False),  # does not compile: nothing to look for
            (r"^100([0-9]{13}$", True, True),
        ],
    )
    def test_enabled(self, pattern: str, homebox_urls: bool, enabled: bool) -> None:
        assert LabelPolicy(pattern=pattern, homebox_urls=homebox_urls).enabled is enabled

    def test_from_settings(self) -> None:
        settings = SimpleNamespace(asset_id_label_pattern=PATTERN, asset_id_homebox_labels=True)
        assert LabelPolicy.from_settings(settings) == BOTH
