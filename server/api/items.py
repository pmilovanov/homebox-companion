"""Items API routes."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from loguru import logger

from homebox_companion import DetectedItem, HomeboxAuthError, HomeboxClient, settings
from homebox_companion.ai.images import compress_image_for_upload
from homebox_companion.homebox import ItemCreate, update_payload
from homebox_companion.tools.vision.labels import LabelPolicy

from ..dependencies import get_client, get_token, get_valid_tag_ids, validate_file_size
from ..schemas.items import BatchCreateRequest

router = APIRouter()

# Homebox stores asset IDs as int64.
_INT64_MAX = 2**63 - 1


@router.get("/items")
async def list_items(
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
    location_id: str | None = Query(None, alias="location_id"),
) -> list[dict]:
    """
    List items, optionally filtered by location.

    Returns a simplified list of items suitable for selection UI.
    """
    logger.debug(f"Fetching items for location_id={location_id}")

    response = await client.list_items(token, location_id=location_id)
    items = response.get("items", [])

    # Return simplified item data
    result = [
        {
            "id": item["id"],
            "name": item["name"],
            "quantity": item.get("quantity", 1),
            "thumbnailId": item.get("thumbnailId"),
        }
        for item in items
    ]

    logger.debug(f"Found {len(result)} items")
    return result


@router.get("/items/by-asset-id/{asset_id}")
async def get_item_by_asset_id(
    asset_id: str,
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, Any]:
    """Look up an item by its asset ID.

    Used to warn when an asset ID about to be assigned is already on another
    item: Homebox does not reject duplicates, it just leaves two items answering
    to one printed label. Returns {"found": false} rather than 404 when the ID
    is free, since "nothing has this ID" is the expected answer, not an error.
    """
    logger.debug(f"Looking up item by asset ID: {asset_id}")

    # Homebox reads the ID as an integer, hyphens ignored. Anything it cannot
    # read it answers with a 500, and 0 (or "000-0") matches every entity that
    # has no asset ID yet, which is not "taken". Settle both here: an ID no
    # item can carry is free.
    digits = asset_id.replace("-", "").strip()
    if not (digits.isascii() and digits.isdigit()) or len(digits) > 19 or not 0 < int(digits) <= _INT64_MAX:
        return {"found": False}

    try:
        item = await client.get_item_by_asset_id(token, digits)
    except HomeboxAuthError:
        # Let the global handler turn this into a 401 so the frontend can
        # refresh the token and retry, as it does for every other route.
        raise
    except ValueError:
        # No item carries this asset ID: it is free to use.
        return {"found": False}
    except Exception as e:
        # A lookup that cannot be completed is "cannot confirm", which the
        # caller treats as no news.
        logger.warning(f"Asset ID lookup failed for {asset_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not check asset ID") from None

    return {
        "found": True,
        "id": item.get("id"),
        "name": item.get("name"),
    }


SENTINEL_NAME = "Asset ID sentinel"
SENTINEL_DESCRIPTION = (
    "Keeps Homebox's own asset ID numbering above the pre-printed labels: every item Homebox "
    "numbers itself gets the next number after this one. Do not delete it. Without it, Homebox "
    "can hand a label's number to an item that has no label."
)


async def _sentinel_status(token: str, client: HomeboxClient) -> dict[str, Any]:
    """Whether Homebox's own numbering already sits above every printed label.

    Homebox hands out max(existing) + 1 at creation and to every unnumbered
    entity at every startup, and only ever counts upward. So the labels are
    safe exactly when the highest asset ID in the group is at or above the
    sentinel value, which itself sits above the whole label range.
    """
    sentinel = settings.asset_id_sentinel
    enabled = LabelPolicy.from_settings(settings).enabled and sentinel > 0
    if not enabled:
        return {"enabled": False, "sentinel": str(sentinel), "highest": None, "ok": True}
    highest = await client.highest_asset_id(token)
    return {
        "enabled": True,
        "sentinel": str(sentinel),
        "highest": str(highest) if highest > 0 else None,
        "ok": highest >= sentinel,
    }


@router.get("/items/asset-id-sentinel")
async def get_asset_id_sentinel(
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, Any]:
    """Report whether the sentinel that protects pre-printed labels is in place."""
    return await _sentinel_status(token, client)


# One creation at a time: two requests in flight would both find the sentinel
# missing and leave two behind.
_sentinel_lock = asyncio.Lock()


@router.post("/items/asset-id-sentinel")
async def create_asset_id_sentinel(
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, Any]:
    """Create the sentinel item, an archived item carrying the sentinel asset ID.

    Idempotent: if the highest asset ID is already at or above the sentinel,
    nothing is created. Archived so it stays out of everyday lists; Homebox
    counts archived entities when it looks for its highest asset ID.
    """
    async with _sentinel_lock:
        return await _create_sentinel(token, client)


async def _create_sentinel(token: str, client: HomeboxClient) -> dict[str, Any]:
    status = await _sentinel_status(token, client)
    if not status["enabled"]:
        raise HTTPException(status_code=400, detail="Pre-printed labels are disabled on this server")
    if status["ok"]:
        return {**status, "created": False}

    created = await client.create_item(
        token, ItemCreate(name=SENTINEL_NAME, quantity=1, description=SENTINEL_DESCRIPTION)
    )
    item_id = created["id"]
    try:
        # Asset IDs cannot be set at creation; PUT the whole item back with it.
        full_item = await client.get_item(token, item_id)
        update_data = {**update_payload(full_item), "assetId": str(settings.asset_id_sentinel), "archived": True}
        await client.update_item(token, item_id, update_data)
    except HomeboxAuthError:
        raise
    except Exception:
        # Half a sentinel is worse than none: an unnumbered item that says "do not delete".
        logger.exception(f"Could not assign the sentinel asset ID; removing item {item_id}")
        try:
            await client.delete_item(token, item_id)
        except Exception as delete_err:
            logger.error(f"Failed to clean up sentinel item {item_id}: {delete_err}")
        raise
    logger.info(f"Created asset ID sentinel item {item_id}")
    return {**(await _sentinel_status(token, client)), "created": True, "id": item_id}


@router.post("/items")
async def create_items(
    request: BatchCreateRequest,
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> JSONResponse:
    """Create multiple items in Homebox.

    For each item, first creates it with basic fields, then updates it with
    any extended fields since the Homebox API only accepts extended fields
    via update, not create.
    """
    logger.info(f"Creating {len(request.items)} items")
    logger.debug(f"Request location_id: {request.location_id}")

    created: list[dict[str, Any]] = []
    errors: list[str] = []

    # Fetch valid tag IDs once for the batch to validate against
    valid_tag_ids = await get_valid_tag_ids(token, client)

    for item_input in request.items:
        # Resolve parent (container) ID: item-level → request-level fallback
        # In 0.26, location_id and parent_id both map to the API's parentId field
        parent_id = item_input.location_id or request.location_id or item_input.parent_id

        logger.debug(f"Creating item: {item_input.name}")
        logger.debug(f"  parent_id: {parent_id}")
        logger.debug(f"  tag_ids: {item_input.tag_ids}")

        # Validate tag_ids against Homebox to filter out invalid/stale IDs
        validated_tag_ids: list[str] | None = None
        if item_input.tag_ids:
            validated_tag_ids = [tid for tid in item_input.tag_ids if tid in valid_tag_ids]
            filtered_count = len(item_input.tag_ids) - len(validated_tag_ids)
            if filtered_count > 0:
                logger.warning(f"Filtered out {filtered_count} invalid tag ID(s) for '{item_input.name}'")

        detected_item = DetectedItem(
            name=item_input.name,
            quantity=item_input.quantity,
            description=item_input.description,
            parent_id=parent_id,  # ty: ignore[unknown-argument]
            tag_ids=validated_tag_ids if validated_tag_ids else None,  # ty: ignore[unknown-argument]
            manufacturer=item_input.manufacturer,
            model_number=item_input.model_number,  # ty: ignore[unknown-argument]
            serial_number=item_input.serial_number,  # ty: ignore[unknown-argument]
            purchase_price=item_input.purchase_price,  # ty: ignore[unknown-argument]
            purchase_from=item_input.purchase_from,  # ty: ignore[unknown-argument]
            notes=item_input.notes,
        )

        try:
            # Step 1: Create item with basic fields
            item_create = ItemCreate(
                name=detected_item.name,
                quantity=detected_item.quantity,
                description=detected_item.description or "",
                parent_id=detected_item.parent_id,  # ty: ignore[unknown-argument]
                tag_ids=detected_item.tag_ids,  # ty: ignore[unknown-argument]
            )
            result = await client.create_item(token, item_create)
            item_id = result.get("id")
            logger.info(f"Created item: {result.get('name')} (id: {item_id})")

            # Step 2: If there are extended fields or custom fields, update the item
            has_custom = bool(item_input.custom_fields)
            if item_id and (detected_item.has_extended_fields() or has_custom):
                extended_payload = detected_item.get_extended_fields_payload() or {}
                if extended_payload or has_custom:
                    logger.debug(f"  Updating with extended fields: {extended_payload.keys()}")
                    try:
                        # PUT replaces the whole item, so start from what Homebox
                        # holds (including the asset ID it assigned at creation)
                        # and lay the extended fields over it.
                        full_item = await client.get_item(token, item_id)
                        update_data: dict[str, Any] = {**update_payload(full_item), **extended_payload}
                        # Include custom fields as typed Homebox ItemField objects
                        if item_input.custom_fields:
                            from homebox_companion.tools.vision.models import HomeboxItemField

                            update_data["fields"] = [
                                *update_data.get("fields", []),
                                *(
                                    HomeboxItemField(name=name, textValue=value).model_dump(by_alias=True)
                                    for name, value in item_input.custom_fields.items()
                                    if value  # skip empty/null values
                                ),
                            ]
                        # Preserve parentId if it was set
                        if item_input.parent_id:
                            update_data["parentId"] = item_input.parent_id
                        result = await client.update_item(token, item_id, update_data)
                        logger.info("  Updated item with extended fields")
                    except HomeboxAuthError:
                        # Auth failure during update - don't delete the item!
                        # The item was created successfully, user just needs fresh token.
                        # Re-raise to trigger the outer auth handler.
                        raise
                    except Exception as update_err:
                        # Non-auth update failures - clean up the partially created item
                        logger.warning(
                            f"Extended fields update failed for '{item_input.name}', "
                            f"cleaning up item {item_id}: {update_err}"
                        )
                        try:
                            await client.delete_item(token, item_id)
                            logger.info(f"  Cleaned up partial item {item_id}")
                        except Exception as delete_err:
                            logger.error(f"  Failed to clean up item {item_id}: {delete_err}")
                        raise update_err

            created.append(result)
        except HomeboxAuthError:
            # Auth failure means all subsequent items will also fail - abort early
            logger.error(f"Authentication failed while creating '{item_input.name}'")
            errors.append(f"Authentication failed for '{item_input.name}'")
            # Add remaining items as not attempted
            remaining = len(request.items) - len(created) - len(errors)
            if remaining > 0:
                errors.append(f"{remaining} more item(s) not attempted due to auth failure")
            break
        except Exception as e:
            # Log full error details and include error type in response
            logger.exception(f"Failed to create '{item_input.name}'")
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else "Unknown error"
            # Truncate long error messages for the response
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            errors.append(f"Failed to create '{item_input.name}': [{error_type}] {error_msg}")

    logger.info(f"Item creation complete: {len(created)} created, {len(errors)} failed")

    # Optionally ask Homebox to assign asset IDs to items that lack one.
    #
    # Off by default: ensure-asset-ids is a group-wide sweep that assigns
    # max(existing) + 1 to every zero-ID item in the group, including items this
    # app never touched. Homebox already numbers new items itself at creation
    # (while its auto-increment option is on) and sweeps at every startup, so
    # this is rarely needed; see config.py.
    if created and settings.asset_id_auto_assign:
        try:
            assigned = await client.ensure_asset_ids(token)
            if assigned > 0:
                logger.info(f"Assigned asset IDs to {assigned} item(s)")
        except Exception as e:
            # Non-fatal - log but don't fail the request
            logger.warning(f"Failed to ensure asset IDs: {e}")

    return JSONResponse(
        content={
            "created": created,
            "errors": errors,
            "message": (f"Created {len(created)} items" + (f", {len(errors)} failed" if errors else "")),
        },
        status_code=200 if not errors else 207,  # 207 Multi-Status if partial success
    )


@router.post("/items/{item_id}/attachments")
async def upload_item_attachment(
    item_id: str,
    file: Annotated[UploadFile, File(description="Image file to upload")],
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, Any]:
    """Upload an attachment (image) to an existing item."""
    logger.info(f"Uploading attachment to item: {item_id}")
    logger.debug(f"File: {file.filename}, content_type: {file.content_type}")

    # Validate file size (raises HTTPException if too large)
    file_bytes = await validate_file_size(file)

    # Log file size for diagnostics - helps identify empty/corrupted uploads
    file_size = len(file_bytes)
    logger.debug(f"Received file: {file.filename}, size: {file_size:,} bytes")
    if file_size == 0:
        logger.warning(f"Empty file received for item {item_id}: {file.filename}")
    elif file_size < 1000:
        logger.warning(f"Suspiciously small file for item {item_id}: {file.filename} ({file_size} bytes)")

    filename = file.filename or "image.jpg"
    mime_type = file.content_type or "image/jpeg"

    max_dimension, jpeg_quality = settings.image_quality_params
    file_bytes, mime_type = compress_image_for_upload(file_bytes, max_dimension, jpeg_quality)

    result = await client.upload_attachment(
        token=token,
        item_id=item_id,
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
        attachment_type="photo",
    )
    logger.info(f"Successfully uploaded attachment to item {item_id}")
    return result


@router.get("/items/{item_id}/attachments/{attachment_id}")
async def get_item_attachment(
    item_id: str,
    attachment_id: str,
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> Response:
    """Proxy attachment requests to Homebox with proper auth.

    This allows the frontend to load thumbnails without exposing auth tokens
    to the browser. The browser makes requests to this endpoint, and we
    forward them to Homebox with the proper Authorization header.
    """
    logger.debug(f"Proxying attachment request: item={item_id}, attachment={attachment_id}")

    try:
        content, content_type = await client.get_attachment(token, item_id, attachment_id)
        return Response(content=content, media_type=content_type)
    except FileNotFoundError as e:
        # Route-specific: 404 for missing attachments
        raise HTTPException(status_code=404, detail="Attachment not found") from e


@router.put("/items/{item_id}")
async def update_item(
    item_id: str,
    request: dict[str, Any],
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, Any]:
    """Update an existing item in Homebox.

    Used to set asset ID after item creation (since asset ID cannot be set during creation).
    Homebox's PUT replaces the whole item, so the body starts from everything
    it currently holds and changes only what was asked for.
    """
    logger.info(f"Updating item: {item_id}")
    logger.debug(f"Update data: {request}")

    full_item = await client.get_item(token, item_id)
    update_data = update_payload(full_item)

    if "assetId" in request:
        asset_id = str(request["assetId"] or "").strip()
        if asset_id:
            update_data["assetId"] = asset_id
        else:
            # Clearing: leave the key out so Homebox stores 0 (unassigned), not
            # the -1 it makes of "" or the parse error it makes of null.
            update_data.pop("assetId", None)
    if "name" in request:
        update_data["name"] = request["name"]
    if "description" in request:
        update_data["description"] = request["description"]

    result = await client.update_item(token, item_id, update_data)
    logger.info(f"Successfully updated item {item_id}")
    return result


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str,
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, str]:
    """Delete an item from Homebox.

    Used for cleanup when item creation succeeds but attachment upload fails.
    """
    logger.info(f"Deleting item: {item_id}")

    await client.delete_item(token, item_id)
    logger.info(f"Successfully deleted item {item_id}")
    return {"message": "Item deleted"}


@router.post("/items/{item_id}/print-label")
async def print_item_label(
    item_id: str,
    token: Annotated[str, Depends(get_token)],
    client: Annotated[HomeboxClient, Depends(get_client)],
) -> dict[str, str]:
    """Trigger server-side label printing for an item.

    Proxies to Homebox's undocumented labelmaker endpoint with ?print=true.
    Requires HBOX_LABEL_MAKER_PRINT_COMMAND to be configured on the Homebox server.
    """
    if not settings.print_enabled:
        raise HTTPException(
            status_code=403,
            detail="Label printing is not enabled on this server (HBC_PRINT_ENABLED=false).",
        )

    logger.info(f"Printing label for item: {item_id}")

    try:
        result = await client.print_label(token, item_id)
        logger.info(f"Label printed for item {item_id}: {result}")
        return {"message": result}
    except HomeboxAuthError:
        raise
    except Exception as e:
        logger.error(f"Failed to print label for item {item_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail="Failed to print label. Ensure HBOX_LABEL_MAKER_PRINT_COMMAND is configured on the Homebox server.",
        ) from e
