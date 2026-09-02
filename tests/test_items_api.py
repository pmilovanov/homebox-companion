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

# A GET /entities/{id} response carrying everything a PUT wipes when left out.
FULL_ITEM: dict[str, Any] = {
    "id": "item-1",
    "assetId": "000-042",
    "name": "Drill",
    "description": "Cordless",
    "quantity": 2,
    "insured": True,
    "archived": False,
    "serialNumber": "SN-1",
    "modelNumber": "DCD-777",
    "manufacturer": "DeWalt",
    "lifetimeWarranty": False,
    "warrantyExpires": "2027-05-01",
    "warrantyDetails": "3 years",
    "purchaseDate": "2024-05-01",
    "purchaseFrom": "Hardware store",
    "purchasePrice": 129.5,
    "soldDate": "",
    "soldTo": "",
    "soldPrice": 0,
    "soldNotes": "",
    "notes": "Scuffed",
    "syncChildEntityLocations": False,
    "fields": [
        {
            "id": "field-1",
            "type": "text",
            "name": "Color",
            "textValue": "yellow",
            "numberValue": 0,
            "booleanValue": False,
        }
    ],
    "parent": {"id": "loc-1", "name": "Garage"},
    "tags": [{"id": "tag-1", "name": "Tools"}],
    "attachments": [],
    "createdAt": "2024-05-01T00:00:00Z",
}

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
        self.asset_lookups: list[str] = []
        self.by_asset: dict[str, dict[str, Any]] = {}

    async def get_item(self, token: str, item_id: str) -> dict[str, Any]:
        return dict(self.items[item_id])

    async def update_item(self, token: str, item_id: str, item_data: dict[str, Any]) -> dict[str, Any]:
        self.updates.append((item_id, item_data))
        return {**self.items[item_id], **item_data, "id": item_id}

    async def create_item(self, token: str, item: Any) -> dict[str, Any]:
        created = {**CREATED_ITEM, "name": item.name}
        self.items[created["id"]] = created
        return created

    async def get_item_by_asset_id(self, token: str, asset_id: str) -> dict[str, Any]:
        self.asset_lookups.append(asset_id)
        if asset_id not in self.by_asset:
            raise ValueError(f"No item found with asset ID: {asset_id}")
        return self.by_asset[asset_id]

    async def delete_item(self, token: str, item_id: str) -> None:
        self.items.pop(item_id, None)


@pytest.fixture
def api() -> tuple[TestClient, FakeHomebox]:
    fake = FakeHomebox({FULL_ITEM["id"]: FULL_ITEM})
    app = FastAPI()
    app.include_router(items_module.router)
    app.dependency_overrides[get_client] = lambda: fake
    app.dependency_overrides[get_token] = lambda: "token"
    return TestClient(app), fake


# ---- the PUT body ----------------------------------------------------------


class TestUpdatePayload:
    def test_carries_everything_the_put_replaces(self) -> None:
        payload = update_payload(FULL_ITEM)
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
            assert payload[key] == FULL_ITEM[key], key
        assert payload["parentId"] == "loc-1"
        assert payload["tagIds"] == ["tag-1"]
        assert payload["assetId"] == "000-042"

    def test_leaves_out_what_is_not_an_update_field(self) -> None:
        payload = update_payload(FULL_ITEM)
        for key in ("id", "attachments", "createdAt", "parent", "tags"):
            assert key not in payload, key

    def test_unassigned_asset_id_is_left_out_rather_than_sent_empty(self) -> None:
        """An empty string would make Homebox store -1, which its ensure-asset-ids sweep skips."""
        assert "assetId" not in update_payload({**FULL_ITEM, "assetId": ""})

    def test_no_parent_no_tags(self) -> None:
        assert update_payload({"name": "Lamp"}) == {"name": "Lamp", "parentId": None, "tagIds": []}


# ---- PUT /items/{id} -------------------------------------------------------


class TestUpdateItem:
    def test_assigning_an_asset_id_keeps_every_other_field(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        response = client.put("/items/item-1", json={"assetId": "90026843450000"})
        assert response.status_code == 200, response.text
        [(item_id, body)] = fake.updates
        assert item_id == "item-1"
        assert body == {**update_payload(FULL_ITEM), "assetId": "90026843450000"}

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


# ---- GET /items/by-asset-id/{id} -------------------------------------------


class TestAssetIdLookup:
    @pytest.mark.parametrize(
        "asset_id",
        [
            "abc",  # Homebox answers 500 to this
            "0",  # matches every entity without an asset ID
            "000-0",
            "9００",  # fullwidth digits
            "99999999999999999999",  # past int64
            "9" * 5000,  # past Python's int-parsing limit, never mind int64
        ],
    )
    def test_an_id_no_item_can_carry_is_free_without_asking(
        self, api: tuple[TestClient, FakeHomebox], asset_id: str
    ) -> None:
        client, fake = api
        response = client.get(f"/items/by-asset-id/{asset_id}")
        assert response.status_code == 200
        assert response.json() == {"found": False}
        assert fake.asset_lookups == []

    def test_hyphens_are_dropped_before_asking(self, api: tuple[TestClient, FakeHomebox]) -> None:
        """As Homebox does: "900-26843450000" and "-5" read as 90026843450000 and 5."""
        client, fake = api
        fake.by_asset["90026843450000"] = {"id": "item-9", "name": "Bench vise"}
        response = client.get("/items/by-asset-id/900-26843450000")
        assert response.json() == {"found": True, "id": "item-9", "name": "Bench vise"}
        client.get("/items/by-asset-id/-5")
        assert fake.asset_lookups == ["90026843450000", "5"]

    def test_a_free_id(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        response = client.get("/items/by-asset-id/90026843450001")
        assert response.json() == {"found": False}
        assert fake.asset_lookups == ["90026843450001"]
