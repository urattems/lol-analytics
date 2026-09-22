from __future__ import annotations

import httpx
import pytest

from core.exceptions import RiotAuthenticationError
from core.riot_api import RiotAPIClient


def test_429_uses_retry_after_then_succeeds() -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(
            200,
            json={"puuid": "p", "gameName": "Synthetic Player", "tagLine": "EUW"},
        )

    with RiotAPIClient(
        "test-key", transport=httpx.MockTransport(handler), sleep=waits.append
    ) as client:
        account = client.resolve_account("Synthetic Player", "EUW")

    assert account.puuid == "p"
    assert waits == [2.0]


def test_authentication_error_is_explicit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    with RiotAPIClient("test-key", transport=transport) as client:
        with pytest.raises(RiotAuthenticationError):
            client.get_match_ids("p")


def test_timeline_uses_match_v5_regional_endpoint() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            json={"metadata": {"matchId": "EUW1_42"}, "info": {"frames": []}},
        )

    with RiotAPIClient("test-key", routing_region="EUROPE", transport=httpx.MockTransport(handler)) as client:
        payload = client.get_timeline("EUW1_42")

    assert payload["metadata"]["matchId"] == "EUW1_42"  # type: ignore[index]
    assert requested == ["https://europe.api.riotgames.com/lol/match/v5/matches/EUW1_42/timeline"]


def test_sea_account_lookup_uses_asia_but_match_history_uses_sea() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if "/riot/account/" in request.url.path:
            return httpx.Response(200, json={"puuid": "friend", "gameName": "Friend", "tagLine": "TAG"})
        return httpx.Response(200, json=[])

    with RiotAPIClient("test-key", routing_region="SEA", platform_region="OC1", transport=httpx.MockTransport(handler)) as client:
        account = client.resolve_account("Friend", "TAG")
        client.get_match_ids(account.puuid)
    assert hosts == ["asia.api.riotgames.com", "sea.api.riotgames.com"]
