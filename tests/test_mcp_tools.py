"""Unit tests for MCP tools.

These tests verify the MCP tool implementations using mocked HomeboxClient
responses. For live integration tests with real Homebox, see the live marker tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from homebox_companion.homebox import update_payload
from homebox_companion.mcp.tools import (
    GetItemTool,
    GetLocationTool,
    ListItemsTool,
    ListLocationsTool,
    ListTagsTool,
    UpdateItemTool,
    get_tools,
)
from homebox_companion.mcp.types import ToolPermission, ToolResult

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_client() -> MagicMock:
    """Create a mock HomeboxClient."""
    client = MagicMock()
    # Make all methods async mocks
    client.list_locations = AsyncMock()
    client.get_location = AsyncMock()
    client.list_tags = AsyncMock()
    client.list_items = AsyncMock()
    client.get_item = AsyncMock()
    return client


# =============================================================================
# ToolResult Tests
# =============================================================================


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result_to_dict(self):
        """Successful result should include data and metadata in dict."""
        result = ToolResult(success=True, data={"id": "123", "name": "Test"})
        expected = {
            "success": True,
            "metadata": {"type": "object", "name": "Test"},
            "data": {"id": "123", "name": "Test"},
        }
        assert result.to_dict() == expected

    def test_success_result_with_list_data(self):
        """List data should include count in metadata."""
        result = ToolResult(success=True, data=[{"id": "1"}, {"id": "2"}, {"id": "3"}])
        expected = {
            "success": True,
            "metadata": {"count": 3, "type": "list"},
            "data": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        }
        assert result.to_dict() == expected

    def test_success_result_with_empty_list(self):
        """Empty list should show count of 0."""
        result = ToolResult(success=True, data=[])
        expected = {
            "success": True,
            "metadata": {"count": 0, "type": "list"},
            "data": [],
        }
        assert result.to_dict() == expected

    def test_success_result_with_custom_metadata(self):
        """Custom metadata should be included alongside computed metadata."""
        result = ToolResult(
            success=True,
            data=[{"id": "1"}],
            metadata={"custom_field": "value"},
        )
        expected = {
            "success": True,
            "metadata": {"count": 1, "type": "list", "custom_field": "value"},
            "data": [{"id": "1"}],
        }
        assert result.to_dict() == expected

    def test_success_result_with_none_data(self):
        """None data should not include metadata."""
        result = ToolResult(success=True, data=None)
        expected = {"success": True, "data": None}
        assert result.to_dict() == expected

    def test_error_result_to_dict(self):
        """Error result should include error message in dict."""
        result = ToolResult(success=False, error="Something went wrong")
        assert result.to_dict() == {"success": False, "error": "Something went wrong"}


# =============================================================================
# Tool Discovery Tests
# =============================================================================


class TestToolDiscovery:
    """Tests for tool discovery and metadata."""

    def test_get_tools_returns_all_tools(self):
        """Discovery should return all defined tools."""
        tools = get_tools()
        tool_names = {t.name for t in tools}

        expected_tools = [
            "list_locations",
            "get_location",
            "list_tags",
            "list_items",
            "get_item",
            "create_item",
            "update_item",
            "delete_item",
        ]
        assert all(tool in tool_names for tool in expected_tools)

    def test_all_read_tools_have_read_permission(self):
        """READ tools should have READ permission."""
        tools = get_tools()
        read_tools = ["list_locations", "get_location", "list_tags", "list_items", "get_item"]
        tools_by_name = {t.name: t for t in tools}

        for name in read_tools:
            assert tools_by_name[name].permission == ToolPermission.READ, f"{name} should be READ permission"

    def test_write_tools_have_correct_permissions(self):
        """Write tools should have WRITE or DESTRUCTIVE permissions."""
        tools = get_tools()
        tools_by_name = {t.name: t for t in tools}

        assert tools_by_name["create_item"].permission == ToolPermission.WRITE
        assert tools_by_name["update_item"].permission == ToolPermission.WRITE
        assert tools_by_name["delete_item"].permission == ToolPermission.DESTRUCTIVE

    def test_tools_have_params_class(self):
        """Each tool should have a Params class for schema generation."""
        tools = get_tools()

        for tool in tools:
            assert hasattr(tool, "Params"), f"{tool.name} missing Params class"
            # Verify it can generate JSON schema
            schema = tool.Params.model_json_schema()
            assert "type" in schema or "properties" in schema, f"{tool.name} Params has invalid schema"


# =============================================================================
# list_locations Tests
# =============================================================================


class TestListLocations:
    """Tests for list_locations tool."""

    @pytest.mark.asyncio
    async def test_returns_locations_on_success(self, mock_client: MagicMock):
        """Should return locations list on successful API call."""
        mock_locations = [
            {"id": "loc1", "name": "Living Room"},
            {"id": "loc2", "name": "Kitchen"},
        ]
        mock_client.list_locations.return_value = mock_locations

        tool = ListLocationsTool()
        params = tool.Params(filter_children=False)
        result = await tool.execute(mock_client, "test-token", params)

        assert result.success is True
        # Tool returns compacted locations with additional fields
        assert len(result.data) == 2
        assert result.data[0]["id"] == "loc1"
        assert result.data[0]["name"] == "Living Room"
        assert result.data[1]["id"] == "loc2"
        assert result.data[1]["name"] == "Kitchen"
        # Verify compact format adds expected fields
        assert "url" in result.data[0]
        assert "itemCount" in result.data[0]
        mock_client.list_locations.assert_called_once_with("test-token", filter_children=None)

    @pytest.mark.asyncio
    async def test_passes_filter_children_parameter(self, mock_client: MagicMock):
        """Should pass filter_children to client when True."""
        mock_client.list_locations.return_value = []

        tool = ListLocationsTool()
        params = tool.Params(filter_children=True)
        await tool.execute(mock_client, "test-token", params)

        mock_client.list_locations.assert_called_once_with("test-token", filter_children=True)

    @pytest.mark.asyncio
    async def test_raises_exception_on_api_error(self, mock_client: MagicMock):
        """Should propagate exception when API call fails.

        Note: Error handling (converting to ToolResult) is done by the
        ToolExecutor, not by individual tools. This keeps tools simple.
        """
        mock_client.list_locations.side_effect = Exception("Connection failed")

        tool = ListLocationsTool()
        params = tool.Params()

        with pytest.raises(Exception, match="Connection failed"):
            await tool.execute(mock_client, "test-token", params)


# =============================================================================
# get_location Tests
# =============================================================================


class TestGetLocation:
    """Tests for get_location tool."""

    @pytest.mark.asyncio
    async def test_returns_location_on_success(self, mock_client: MagicMock):
        """Should return location details on successful API call."""
        mock_location = {
            "id": "loc1",
            "name": "Living Room",
            "children": [{"id": "loc1a", "name": "Shelf"}],
        }
        mock_client.get_location.return_value = mock_location

        tool = GetLocationTool()
        params = tool.Params(location_id="loc1")
        result = await tool.execute(mock_client, "test-token", params)

        assert result.success is True
        # Tool returns LocationView with computed URL and additional fields
        assert result.data["id"] == "loc1"
        assert result.data["name"] == "Living Room"
        assert "url" in result.data  # Computed URL field
        assert len(result.data["children"]) == 1
        assert result.data["children"][0]["id"] == "loc1a"
        mock_client.get_location.assert_called_once_with("test-token", "loc1")

    @pytest.mark.asyncio
    async def test_raises_exception_for_missing_location(self, mock_client: MagicMock):
        """Should propagate exception when location not found.

        Note: Error handling (converting to ToolResult) is done by the
        ToolExecutor, not by individual tools. This keeps tools simple.
        """
        mock_client.get_location.side_effect = Exception("Location not found")

        tool = GetLocationTool()
        params = tool.Params(location_id="nonexistent")

        with pytest.raises(Exception, match="Location not found"):
            await tool.execute(mock_client, "test-token", params)


# =============================================================================
# list_tags Tests
# =============================================================================


class TestListTags:
    """Tests for list_tags tool."""

    @pytest.mark.asyncio
    async def test_returns_tags_on_success(self, mock_client: MagicMock):
        """Should return tags list with URLs on successful API call."""
        mock_tags = [
            {"id": "tag1", "name": "Electronics"},
            {"id": "tag2", "name": "Furniture"},
        ]
        mock_client.list_tags.return_value = mock_tags

        tool = ListTagsTool()
        params = tool.Params()
        result = await tool.execute(mock_client, "test-token", params)

        assert result.success is True
        # Check that URLs were added to each tag
        assert len(result.data) == 2
        assert result.data[0]["id"] == "tag1"
        assert result.data[0]["name"] == "Electronics"
        assert "url" in result.data[0]
        assert "items?tags=tag1" in result.data[0]["url"]
        assert result.data[1]["id"] == "tag2"
        assert result.data[1]["name"] == "Furniture"
        assert "url" in result.data[1]
        assert "items?tags=tag2" in result.data[1]["url"]
        mock_client.list_tags.assert_called_once_with("test-token")


# =============================================================================
# list_items Tests
# =============================================================================


class TestListItems:
    """Tests for list_items tool."""

    @pytest.mark.asyncio
    async def test_returns_items_on_success(self, mock_client: MagicMock):
        """Should return items list on successful API call."""
        mock_items = [
            {"id": "item1", "name": "TV"},
            {"id": "item2", "name": "Couch"},
        ]
        mock_client.list_items.return_value = {
            "items": mock_items,
            "page": 1,
            "pageSize": 50,
            "total": 2,
        }

        tool = ListItemsTool()
        # Use compact=False to get ItemView format (still has computed fields)
        params = tool.Params(compact=False)
        result = await tool.execute(mock_client, "test-token", params)

        assert result.success is True
        # Tool returns dict with items and pagination
        assert len(result.data["items"]) == 2
        # Items are sorted by location/name, so "Couch" comes before "TV"
        assert result.data["items"][0]["id"] == "item2"
        assert result.data["items"][0]["name"] == "Couch"
        assert "url" in result.data["items"][0]  # Computed URL field
        assert result.data["items"][1]["id"] == "item1"
        assert result.data["items"][1]["name"] == "TV"
        # Check pagination metadata is included
        assert "pagination" in result.data
        assert result.data["pagination"]["total"] == 2
        mock_client.list_items.assert_called_once_with(
            "test-token", location_id=None, tag_ids=None, page=None, page_size=50
        )

    @pytest.mark.asyncio
    async def test_filters_by_location(self, mock_client: MagicMock):
        """Should pass location_id filter to client."""
        mock_client.list_items.return_value = {
            "items": [],
            "page": 1,
            "pageSize": 50,
            "total": 0,
        }

        tool = ListItemsTool()
        params = tool.Params(location_id="loc1")
        await tool.execute(mock_client, "test-token", params)

        mock_client.list_items.assert_called_once_with(
            "test-token", location_id="loc1", tag_ids=None, page=None, page_size=50
        )


# =============================================================================
# get_item Tests
# =============================================================================


class TestGetItem:
    """Tests for get_item tool."""

    @pytest.mark.asyncio
    async def test_returns_item_on_success(self, mock_client: MagicMock):
        """Should return full item details on successful API call."""
        mock_item = {
            "id": "item1",
            "name": "Smart TV",
            "description": "65 inch OLED",
            "parent": {"id": "loc1", "name": "Living Room"},
            "tags": [{"id": "tag1", "name": "Electronics"}],
        }
        mock_client.get_item.return_value = mock_item

        tool = GetItemTool()
        params = tool.Params(item_id="item1")
        result = await tool.execute(mock_client, "test-token", params)

        assert result.success is True
        # Tool returns ItemView with computed URL and additional fields
        assert result.data["id"] == "item1"
        assert result.data["name"] == "Smart TV"
        assert result.data["description"] == "65 inch OLED"
        assert "url" in result.data  # Computed URL field
        assert result.data["location"]["id"] == "loc1"  # View normalizes parent → location
        assert result.data["location"]["name"] == "Living Room"
        assert len(result.data["tags"]) == 1
        assert result.data["tags"][0]["id"] == "tag1"
        mock_client.get_item.assert_called_once_with("test-token", "item1")

    @pytest.mark.asyncio
    async def test_raises_exception_for_missing_item(self, mock_client: MagicMock):
        """Should propagate exception when item not found.

        Note: Error handling (converting to ToolResult) is done by the
        ToolExecutor, not by individual tools. This keeps tools simple.
        """
        mock_client.get_item.side_effect = Exception("Item not found")

        tool = GetItemTool()
        params = tool.Params(item_id="nonexistent")

        with pytest.raises(Exception, match="Item not found"):
            await tool.execute(mock_client, "test-token", params)


# =============================================================================
# update_item Tests
# =============================================================================

# A GET /entities/{id} response carrying everything a PUT wipes when left out.
CURRENT_ITEM = {
    "id": "item1",
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
    "fields": [{"id": "field-1", "type": "text", "name": "Color", "textValue": "yellow"}],
    "parent": {"id": "loc1", "name": "Garage"},
    "tags": [{"id": "tag1", "name": "Tools"}],
}


class TestUpdateItem:
    """update_item sends Homebox the whole item back, changed only where asked.

    PUT /entities/{id} is a full replace: every field the body leaves out is
    wiped, so what matters is the exact body.
    """

    @pytest.fixture
    def client(self, mock_client: MagicMock) -> MagicMock:
        mock_client.get_item.return_value = dict(CURRENT_ITEM)
        mock_client.update_item = AsyncMock(side_effect=lambda token, item_id, data: {**data, "id": item_id})
        return mock_client

    @staticmethod
    def sent(client: MagicMock) -> dict:
        client.update_item.assert_called_once()
        return client.update_item.call_args.args[2]

    @pytest.mark.asyncio
    async def test_renaming_keeps_everything_else(self, client: MagicMock):
        tool = UpdateItemTool()
        result = await tool.execute(client, "test-token", tool.Params(item_id="item1", name="Hammer drill"))

        assert result.success is True
        assert self.sent(client) == {**update_payload(CURRENT_ITEM), "name": "Hammer drill"}
        body = self.sent(client)
        assert body["assetId"] == "000-042"
        assert body["fields"] == CURRENT_ITEM["fields"]
        assert body["warrantyExpires"] == "2027-05-01"
        assert body["purchasePrice"] == 129.5
        assert body["parentId"] == "loc1"
        assert body["tagIds"] == ["tag1"]

    @pytest.mark.asyncio
    async def test_changes_land_under_the_api_names(self, client: MagicMock):
        tool = UpdateItemTool()
        params = tool.Params(
            item_id="item1",
            quantity=3,
            purchasePrice=99.0,
            modelNumber="DCD-999",
            serialNumber="SN-2",
            purchaseFrom="Online",
            insured=False,
            archived=True,
            notes="",
        )
        await tool.execute(client, "test-token", params)

        body = self.sent(client)
        assert body["quantity"] == 3
        assert body["purchasePrice"] == 99.0
        assert body["modelNumber"] == "DCD-999"
        assert body["serialNumber"] == "SN-2"
        assert body["purchaseFrom"] == "Online"
        assert body["insured"] is False
        assert body["archived"] is True
        assert body["notes"] == ""
        assert body["manufacturer"] == "DeWalt"  # untouched

    @pytest.mark.asyncio
    async def test_an_unnumbered_item_stays_unnumbered(self, client: MagicMock):
        """Sending "" makes Homebox store -1; the key is left out instead."""
        client.get_item.return_value = {**CURRENT_ITEM, "assetId": ""}
        tool = UpdateItemTool()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", notes="Fixed"))

        assert "assetId" not in self.sent(client)

    @pytest.mark.asyncio
    async def test_moving_to_a_location_sticks(self, client: MagicMock):
        tool = UpdateItemTool()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", location_id="loc2"))

        assert self.sent(client)["parentId"] == "loc2"

    @pytest.mark.asyncio
    async def test_nesting_under_an_item_and_clearing_the_parent(self, client: MagicMock):
        tool = UpdateItemTool()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", parent_id="item9"))
        assert self.sent(client)["parentId"] == "item9"

        client.update_item.reset_mock()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", clear_parent=True, location_id="loc2"))
        assert self.sent(client)["parentId"] is None

    @pytest.mark.asyncio
    async def test_tags_are_replaced_only_when_given(self, client: MagicMock):
        tool = UpdateItemTool()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", tag_ids=["tag1", "tag2"]))
        assert self.sent(client)["tagIds"] == ["tag1", "tag2"]

        client.update_item.reset_mock()
        await tool.execute(client, "test-token", tool.Params(item_id="item1", tag_ids=[]))
        assert self.sent(client)["tagIds"] == []


# =============================================================================
# Live Integration Tests (require Docker — see homebox_container fixture)
# =============================================================================


@pytest.mark.live
class TestMCPToolsLive:
    """Live integration tests for MCP tools against Docker Homebox container."""

    @pytest.mark.asyncio
    async def test_list_locations_live(self, homebox_api_url: str, homebox_credentials: tuple[str, str]):
        """List locations should return data from demo server."""
        from homebox_companion import HomeboxClient

        username, password = homebox_credentials
        async with HomeboxClient(base_url=homebox_api_url) as client:
            response = await client.login(username, password)
            token = response["token"]

            tool = ListLocationsTool()
            params = tool.Params()
            result = await tool.execute(client, token, params)

            assert result.success is True
            assert isinstance(result.data, list)
            # Demo server should have at least some locations
            assert len(result.data) > 0

    @pytest.mark.asyncio
    async def test_list_tags_live(self, homebox_api_url: str, homebox_credentials: tuple[str, str]):
        """List tags should return data from demo server."""
        from homebox_companion import HomeboxClient

        username, password = homebox_credentials
        async with HomeboxClient(base_url=homebox_api_url) as client:
            response = await client.login(username, password)
            token = response["token"]

            tool = ListTagsTool()
            params = tool.Params()
            result = await tool.execute(client, token, params)

            assert result.success is True
            assert isinstance(result.data, list)
