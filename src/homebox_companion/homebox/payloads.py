"""Request bodies for Homebox's entity API.

``PUT /entities/{id}`` is a full replace, not a patch. Every field the body
leaves out is reset: strings to "", numbers to 0, dates cleared, the tag list
emptied, custom fields not listed by id deleted, and the asset ID set to 0.
So an update has to start from the item as Homebox currently holds it and
change only what it means to. This module builds that starting point.
"""

from __future__ import annotations

from typing import Any

# Everything PUT /entities/{id} overwrites, under the names the API uses.
# Mirrors EntityUpdate in Homebox's repo_entities.go.
_REPLACED_FIELDS = (
    "name",
    "description",
    "quantity",
    "insured",
    "archived",
    "serialNumber",
    "modelNumber",
    "manufacturer",
    "purchaseDate",
    "purchaseFrom",
    "purchasePrice",
    "soldDate",
    "soldTo",
    "soldPrice",
    "soldNotes",
    "lifetimeWarranty",
    "warrantyExpires",
    "warrantyDetails",
    "notes",
    "syncChildEntityLocations",
    "fields",
)


def update_payload(item: dict[str, Any]) -> dict[str, Any]:
    """The PUT body that saves ``item`` (a ``GET /entities/{id}`` response) back unchanged.

    Edit the result, then send it: overwrite the keys being changed, extend
    ``fields`` with new custom fields (existing ones keep their ``id``; an
    entry without one is created), set or drop ``assetId``.

    Only keys present in ``item`` are copied, so a trimmed test double works.
    Homebox returns every one of them for a real item.
    """
    payload = {key: item[key] for key in _REPLACED_FIELDS if key in item}
    payload["parentId"] = (item.get("parent") or {}).get("id")
    payload["tagIds"] = [tag["id"] for tag in item.get("tags") or [] if tag.get("id")]

    # An item without an asset ID reads back as "". Sending "" makes Homebox
    # store -1, which its ensure-asset-ids sweep (looking for exactly 0) then
    # never assigns; leaving the key out keeps it at 0. Never send null: the
    # parser rejects it.
    asset_id = item.get("assetId")
    if asset_id:
        payload["assetId"] = asset_id
    return payload
