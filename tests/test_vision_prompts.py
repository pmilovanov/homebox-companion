"""The assembled vision prompts: what every variant must carry.

The builders in ai/prompts.py have snapshot tests of their own; these check
that the assembled system prompts actually include the parts that were added
after watching gpt-5-mini tag a baby as an Appliance and describe a cat's ear.
"""

from __future__ import annotations

import pytest

from homebox_companion.ai.prompts import build_purpose
from homebox_companion.tools.vision.prompts import (
    build_analysis_system_prompt,
    build_detection_system_prompt,
    build_detection_user_prompt,
    build_discriminatory_system_prompt,
    build_multi_image_system_prompt,
)

pytestmark = pytest.mark.unit

TAGS = [{"id": "tag-1", "name": "Appliances"}]
PREFS = {"name": "n", "quantity": "q", "description": "d", "naming_examples": '"A"'}


@pytest.mark.parametrize(
    "build",
    [
        lambda: build_detection_system_prompt(TAGS, field_preferences=PREFS),
        lambda: build_detection_system_prompt(TAGS, single_item=True, field_preferences=PREFS),
        lambda: build_multi_image_system_prompt(TAGS, field_preferences=PREFS),
        lambda: build_discriminatory_system_prompt(TAGS, field_preferences=PREFS),
        lambda: build_analysis_system_prompt("Lamp", None, TAGS, field_preferences=PREFS),
    ],
    ids=["detection", "detection-single", "multi-image", "discriminatory", "analysis"],
)
def test_every_variant_states_the_purpose_before_the_rules(build) -> None:
    prompt = build()
    purpose = build_purpose()
    assert purpose in prompt
    assert prompt.index(purpose) < prompt.index("Appliances")


def test_tags_are_offered_as_optional() -> None:
    prompt = build_detection_system_prompt(TAGS, field_preferences=PREFS)
    assert "TAGS (optional)" in prompt
    assert "empty unless a tag clearly applies" in prompt


def test_the_user_turn_example_does_not_model_tagging_everything() -> None:
    assert '"tagIds":[]' in build_detection_user_prompt()
