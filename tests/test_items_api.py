"""Item routes: what they send to Homebox.

Homebox's ``PUT /entities/{id}`` replaces the whole item, so what matters
about these routes is the exact body they send, not merely that they send
one. The fake client below records it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from homebox_companion.homebox import update_payload
from server.api import items as items_module
from server.dependencies import get_client, get_token

# What Homebox returns right after POST /entities: numbered already, nothing else set.
CREATED_ITEM: dict[str, Any] = {
    "id": "item-2",
    "assetId": "000-043",
    "name": "Lamp",
    "description": "",
    "quantity": 1,
    "insured": False,
    "archived": False,
    "serialNumber": "",
    "modelNumber": "",
    "manufacturer": "",
    "notes": "",
    "purchasePrice": 0,
    "fields": [],
    "tags": [],
}


class FakeHomebox:
    """Records every PUT body; answers GETs from canned items."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        self.items = items
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.created_names: list[str] = []
        self.deleted: list[str] = []

    async def get_item(self, token: str, item_id: str) -> dict[str, Any]:
        return dict(self.items[item_id])

    async def update_item(self, token: str, item_id: str, item_data: dict[str, Any]) -> dict[str, Any]:
        self.updates.append((item_id, item_data))
        self.items[item_id] = {**self.items[item_id], **item_data, "id": item_id}
        return dict(self.items[item_id])

    async def create_item(self, token: str, item: Any) -> dict[str, Any]:
        self.created_names.append(item.name)
        created = {**CREATED_ITEM, "name": item.name}
        self.items[created["id"]] = created
        return created

    async def delete_item(self, token: str, item_id: str) -> None:
        self.deleted.append(item_id)
        self.items.pop(item_id, None)

    async def ensure_asset_ids(self, token: str) -> int:
        return 0


@pytest.fixture
def api(full_item: dict[str, Any]) -> tuple[TestClient, FakeHomebox]:
    fake = FakeHomebox({full_item["id"]: full_item})
    app = FastAPI()
    app.include_router(items_module.router)
    app.dependency_overrides[get_client] = lambda: fake
    app.dependency_overrides[get_token] = lambda: "token"
    return TestClient(app, raise_server_exceptions=False), fake


# ---- the PUT body ----------------------------------------------------------


class TestUpdatePayload:
    def test_carries_everything_the_put_replaces(self, full_item: dict[str, Any]) -> None:
        payload = update_payload(full_item)
        for key in (
            "name",
            "description",
            "quantity",
            "insured",
            "serialNumber",
            "modelNumber",
            "manufacturer",
            "warrantyExpires",
            "warrantyDetails",
            "purchaseDate",
            "purchaseFrom",
            "purchasePrice",
            "notes",
            "fields",
        ):
            assert payload[key] == full_item[key], key
        assert payload["parentId"] == "loc-1"
        assert payload["tagIds"] == ["tag-1"]
        assert payload["assetId"] == "000-042"

    def test_leaves_out_what_is_not_an_update_field(self, full_item: dict[str, Any]) -> None:
        payload = update_payload(full_item)
        for key in ("id", "attachments", "createdAt", "parent", "tags"):
            assert key not in payload, key

    def test_unassigned_asset_id_is_left_out_rather_than_sent_empty(self, full_item: dict[str, Any]) -> None:
        """An empty string would make Homebox store -1, which its ensure-asset-ids sweep skips."""
        assert "assetId" not in update_payload({**full_item, "assetId": ""})

    def test_no_parent_no_tags(self) -> None:
        assert update_payload({"name": "Lamp"}) == {"name": "Lamp", "parentId": None, "tagIds": []}


# ---- PUT /items/{id} -------------------------------------------------------


class TestUpdateItem:
    def test_assigning_an_asset_id_keeps_every_other_field(
        self, api: tuple[TestClient, FakeHomebox], full_item: dict[str, Any]
    ) -> None:
        client, fake = api
        response = client.put("/items/item-1", json={"assetId": "90026843450000"})
        assert response.status_code == 200, response.text
        [(item_id, body)] = fake.updates
        assert item_id == "item-1"
        assert body == {**update_payload(full_item), "assetId": "90026843450000"}

    def test_renaming_keeps_the_asset_id(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        client.put("/items/item-1", json={"name": "Impact driver"})
        [(_, body)] = fake.updates
        assert body["name"] == "Impact driver"
        assert body["assetId"] == "000-042"
        assert body["manufacturer"] == "DeWalt"

    def test_typed_whitespace_is_trimmed(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        client.put("/items/item-1", json={"assetId": " 90026843450000 "})
        [(_, body)] = fake.updates
        assert body["assetId"] == "90026843450000"

    @pytest.mark.parametrize("cleared", ["", "  ", None])
    def test_clearing_the_asset_id_leaves_the_key_out(
        self, api: tuple[TestClient, FakeHomebox], cleared: str | None
    ) -> None:
        client, fake = api
        client.put("/items/item-1", json={"assetId": cleared})
        [(_, body)] = fake.updates
        assert "assetId" not in body
        assert body["name"] == "Drill"


# ---- POST /items -----------------------------------------------------------


class TestCreateItems:
    def test_extended_fields_update_keeps_the_asset_id_homebox_assigned(
        self, api: tuple[TestClient, FakeHomebox]
    ) -> None:
        client, fake = api
        response = client.post(
            "/items",
            json={"items": [{"name": "Lamp", "manufacturer": "Ikea", "custom_fields": {"Color": "red"}}]},
        )
        assert response.status_code == 200, response.text
        [(item_id, body)] = fake.updates
        assert item_id == "item-2"
        assert body["assetId"] == "000-043"
        assert body["name"] == "Lamp"
        assert body["manufacturer"] == "Ikea"
        assert [(field["name"], field["textValue"]) for field in body["fields"]] == [("Color", "red")]
