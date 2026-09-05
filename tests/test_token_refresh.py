"""Token refresh: the response shape Homebox actually sends.

``GET /users/refresh`` answers with ``UserAuthTokenDetail``, whose token field
is named ``raw``. Login answers with ``TokenResponse``, whose field is
``token``. ``refresh_token()`` used to read only ``token``, so against a real
Homebox it returned a dict with no token at all, and ``POST /api/refresh``
answered 200 with an empty one. See Duelion/homebox-companion#160.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from homebox_companion.core.exceptions import HomeboxAuthError
from homebox_companion.homebox.client import HomeboxClient

pytestmark = pytest.mark.unit

EXPIRES_AT = "2026-09-03T16:24:39Z"


def _homebox_answering(payload: dict[str, Any]) -> tuple[HomeboxClient, list[httpx.Request]]:
    """A client whose Homebox answers every request with ``payload``, recording what it was asked."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    client = HomeboxClient(
        base_url="http://homebox.test/api/v1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client, seen


# ---- HomeboxClient.refresh_token() ------------------------------------------


@pytest.mark.asyncio
async def test_reads_the_token_from_the_raw_field() -> None:
    """The shape Homebox sends: UserAuthTokenDetail, token under ``raw``."""
    client, seen = _homebox_answering({"raw": "new-token", "attachmentToken": "att", "expiresAt": EXPIRES_AT})

    async with client:
        data = await client.refresh_token("old-token")

    assert data["token"] == "new-token"
    assert data["expiresAt"] == EXPIRES_AT
    assert seen[0].url.path == "/api/v1/users/refresh"
    assert seen[0].headers["Authorization"] == "Bearer old-token"


@pytest.mark.asyncio
async def test_still_accepts_a_token_field_and_strips_the_bearer_prefix() -> None:
    """The shape the code was written for keeps working, Bearer normalisation included."""
    client, _ = _homebox_answering({"token": "Bearer new-token", "expiresAt": EXPIRES_AT})

    async with client:
        data = await client.refresh_token("old-token")

    assert data["token"] == "new-token"


@pytest.mark.asyncio
async def test_a_response_without_a_token_is_an_error_not_a_success() -> None:
    """A refresh that yields no token must raise, as login() does, never return a dict without one."""
    client, _ = _homebox_answering({"attachmentToken": "att", "expiresAt": EXPIRES_AT})

    async with client:
        with pytest.raises(HomeboxAuthError, match="did not include a token"):
            await client.refresh_token("old-token")


# ---- POST /api/refresh --------------------------------------------------------


@pytest.fixture
def refresh_route(monkeypatch: pytest.MonkeyPatch):
    """The real app's refresh route, with a Homebox that answers ``payload`` behind it."""
    from server.api import auth as auth_module
    from server.app import app

    async def accept_any_token(authorization: str | None) -> str:
        return "old-token"

    monkeypatch.setattr(auth_module, "get_token", accept_any_token)

    def against(payload: dict[str, Any]) -> TestClient:
        client, _ = _homebox_answering(payload)
        monkeypatch.setattr(auth_module, "get_client", lambda: client)
        return TestClient(app, raise_server_exceptions=False)

    return against


def test_route_returns_the_refreshed_token(refresh_route) -> None:
    """Before the fix this answered 200 with ``"token": ""``, which the frontend stored."""
    response = refresh_route({"raw": "new-token", "attachmentToken": "att", "expiresAt": EXPIRES_AT}).post(
        "/api/refresh", headers={"Authorization": "Bearer old-token"}
    )

    assert response.status_code == 200
    assert response.json()["token"] == "new-token"
    assert response.json()["expires_at"] == EXPIRES_AT


def test_route_answers_401_rather_than_200_with_an_empty_token(refresh_route) -> None:
    """A refresh that produced no token is reported as a failure, so the frontend keeps its working session."""
    response = refresh_route({"attachmentToken": "att", "expiresAt": EXPIRES_AT}).post(
        "/api/refresh", headers={"Authorization": "Bearer old-token"}
    )

    assert response.status_code == 401
    assert "token" not in response.json()
