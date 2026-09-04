"""Item routes: what they send to Homebox.

Homebox's ``PUT /entities/{id}`` replaces the whole item, so what matters
about these routes is the exact body they send, not merely that they send
one. The fake client below records it.
"""

from __future__ import annotations

import asyncio
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
        self.created_names: list[str] = []
        self.deleted: list[str] = []
        self.asset_lookups: list[str] = []
        self.by_asset: dict[str, dict[str, Any]] = {}
        # Stands in for entities this fake does not hold (a location, say).
        self.highest_floor = 0
        self.fail_updates = False
        # Yield to the event loop inside every call, so concurrent requests interleave.
        self.slow = False

    async def _io(self) -> None:
        if self.slow:
            await asyncio.sleep(0)

    async def get_item(self, token: str, item_id: str) -> dict[str, Any]:
        await self._io()
        return dict(self.items[item_id])

    async def update_item(self, token: str, item_id: str, item_data: dict[str, Any]) -> dict[str, Any]:
        await self._io()
        if self.fail_updates:
            raise RuntimeError("Homebox said no")
        self.updates.append((item_id, item_data))
        self.items[item_id] = {**self.items[item_id], **item_data, "id": item_id}
        return dict(self.items[item_id])

    async def create_item(self, token: str, item: Any) -> dict[str, Any]:
        await self._io()
        self.created_names.append(item.name)
        created = {**CREATED_ITEM, "name": item.name}
        self.items[created["id"]] = created
        return created

    async def get_item_by_asset_id(self, token: str, asset_id: str) -> dict[str, Any]:
        self.asset_lookups.append(asset_id)
        if asset_id not in self.by_asset:
            raise ValueError(f"No item found with asset ID: {asset_id}")
        return self.by_asset[asset_id]

    async def delete_item(self, token: str, item_id: str) -> None:
        self.deleted.append(item_id)
        self.items.pop(item_id, None)

    async def ensure_asset_ids(self, token: str) -> int:
        return 0

    async def highest_asset_id(self, token: str) -> int:
        await self._io()
        highest = self.highest_floor
        for item in self.items.values():
            digits = str(item.get("assetId") or "").replace("-", "")
            if digits.isdigit():
                highest = max(highest, int(digits))
        return highest


@pytest.fixture
def api() -> tuple[TestClient, FakeHomebox]:
    fake = FakeHomebox({FULL_ITEM["id"]: FULL_ITEM})
    app = FastAPI()
    app.include_router(items_module.router)
    app.dependency_overrides[get_client] = lambda: fake
    app.dependency_overrides[get_token] = lambda: "token"
    return TestClient(app, raise_server_exceptions=False), fake


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


# ---- the sentinel that keeps Homebox's own numbering above the labels --------

SENTINEL = "9000000000000000"


class TestAssetIdSentinel:
    @pytest.fixture(autouse=True)
    def labels_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Detection is opt-in; the sentinel only matters while it is on."""
        monkeypatch.setattr(items_module.settings, "asset_id_label_pattern", r"^100[0-9]{13}$")

    def test_reports_missing_while_the_highest_id_is_below_it(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, _ = api  # the canned item carries 000-042
        response = client.get("/items/asset-id-sentinel")
        assert response.json() == {"enabled": True, "sentinel": SENTINEL, "highest": "42", "ok": False}

    def test_creating_it_archives_an_item_carrying_the_sentinel_id(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        response = client.post("/items/asset-id-sentinel")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] is True
        assert body["ok"] is True
        assert body["highest"] == SENTINEL
        assert fake.created_names == [items_module.SENTINEL_NAME]
        [(item_id, put)] = fake.updates
        assert item_id == body["id"]
        assert put["assetId"] == SENTINEL
        assert put["archived"] is True
        assert put["name"] == items_module.SENTINEL_NAME

    def test_does_nothing_once_it_is_in_place(self, api: tuple[TestClient, FakeHomebox]) -> None:
        client, fake = api
        fake.highest_floor = int(SENTINEL)
        assert client.get("/items/asset-id-sentinel").json()["ok"] is True
        assert client.post("/items/asset-id-sentinel").json()["created"] is False
        assert fake.created_names == []
        assert fake.updates == []

    def test_is_off_when_labels_are_off(
        self, api: tuple[TestClient, FakeHomebox], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(items_module.settings, "asset_id_label_pattern", "")
        client, _ = api
        assert client.get("/items/asset-id-sentinel").json() == {
            "enabled": False,
            "sentinel": SENTINEL,
            "highest": None,
            "ok": True,
        }
        assert client.post("/items/asset-id-sentinel").status_code == 400

    def test_is_on_for_homebox_labels_alone(
        self, api: tuple[TestClient, FakeHomebox], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Homebox's own printed labels need protecting just the same."""
        monkeypatch.setattr(items_module.settings, "asset_id_label_pattern", "")
        monkeypatch.setattr(items_module.settings, "asset_id_homebox_labels", True)
        client, _ = api
        assert client.get("/items/asset-id-sentinel").json()["enabled"] is True

    def test_is_off_when_the_pattern_is_unusable(
        self, api: tuple[TestClient, FakeHomebox], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pattern that does not compile turns detection off, so there is nothing to protect."""
        monkeypatch.setattr(items_module.settings, "asset_id_label_pattern", "[unclosed")
        client, _ = api
        assert client.get("/items/asset-id-sentinel").json()["enabled"] is False
        assert client.post("/items/asset-id-sentinel").status_code == 400

    @pytest.mark.asyncio
    async def test_two_requests_in_flight_create_one_sentinel(self) -> None:
        """Two clicks at once must not leave two sentinels behind."""
        fake = FakeHomebox({FULL_ITEM["id"]: dict(FULL_ITEM)})
        fake.slow = True
        results = await asyncio.gather(
            items_module.create_asset_id_sentinel("token", fake),  # ty: ignore[invalid-argument-type]
            items_module.create_asset_id_sentinel("token", fake),  # ty: ignore[invalid-argument-type]
        )
        assert sorted(r["created"] for r in results) == [False, True]
        assert fake.created_names == [items_module.SENTINEL_NAME]

    def test_a_failed_assignment_removes_the_half_made_item(self, api: tuple[TestClient, FakeHomebox]) -> None:
        """An unnumbered item that says "do not delete" is worse than no sentinel."""
        client, fake = api
        fake.fail_updates = True
        response = client.post("/items/asset-id-sentinel")
        assert response.status_code == 500
        assert fake.created_names == [items_module.SENTINEL_NAME]
        assert fake.deleted == [CREATED_ITEM["id"]]


class TestHighestAssetIdQuery:
    """The client reads the last page of Homebox's ascending asset-ID sort, items and locations both."""

    def test_reads_the_last_page_of_both_lists(self) -> None:
        import asyncio

        import httpx

        from homebox_companion import HomeboxClient

        seen: list[tuple[str, str]] = []
        entries = {("false", "1"): "", ("false", "3"): "000-042", ("true", "1"): "000-007"}
        totals = {"false": 3, "true": 1}

        def handler(request: httpx.Request) -> httpx.Response:
            params = dict(request.url.params)
            assert request.url.path.endswith("/entities")
            assert params["orderBy"] == "assetId"
            assert params["pageSize"] == "1"
            assert params["includeArchived"] == "true"
            key = (params["isLocation"], params["page"])
            seen.append(key)
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "x", "assetId": entries[key]}],
                    "page": int(params["page"]),
                    "pageSize": 1,
                    "total": totals[params["isLocation"]],
                },
            )

        async def run() -> int:
            transport = httpx.MockTransport(handler)
            async with HomeboxClient(
                base_url="http://homebox.test/api/v1", client=httpx.AsyncClient(transport=transport)
            ) as client:
                return await client.highest_asset_id("token")

        assert asyncio.run(run()) == 42
        assert seen == [("false", "1"), ("false", "3"), ("true", "1")]
