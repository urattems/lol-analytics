from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import httpx

from core.db import Database


@pytest.fixture(autouse=True)
def forbid_unmocked_riot_network(monkeypatch):
    """A stale UI mock must never accidentally send requests to real Riot."""
    original = httpx.Client.send
    def send(client, request, *args, **kwargs):
        if request.url.host.endswith('.api.riotgames.com') and not isinstance(client._transport, httpx.MockTransport):
            raise AssertionError('Riot network is forbidden in tests; provide MockTransport.')
        return original(client, request, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, 'send', send)


def make_participant(
    puuid: str,
    team_id: int,
    champion: str,
    win: bool,
    damage: int | None,
) -> dict[str, object]:
    participant: dict[str, object] = {
        "puuid": puuid,
        "championName": champion,
        "teamId": team_id,
        "teamPosition": "JUNGLE",
        "individualPosition": "JUNGLE",
        "win": win,
        "kills": 8,
        "deaths": 3,
        "assists": 11,
        "goldEarned": 12345,
        "totalMinionsKilled": 42,
        "neutralMinionsKilled": 120,
        "visionScore": 28,
        "item0": 1001,
        "item1": 3078,
        "item2": 3153,
        "item3": 3053,
        "item4": 3047,
        "item5": 0,
        "item6": 3340,
    }
    if damage is not None:
        participant["totalDamageDealtToChampions"] = damage
    return participant


@pytest.fixture
def riot_match_payload() -> dict[str, object]:
    return {
        "metadata": {"matchId": "EUW1_123456"},
        "info": {
            "gameVersion": "16.17.123.456",
            "queueId": 420,
            "gameDuration": 1902,
            "gameCreation": 1_700_000_000_000,
            "participants": [
                make_participant("player-puuid", 100, "Shyvana", True, 10_000),
                make_participant("blue-ally", 100, "Ahri", True, 5_000),
                make_participant("red-one", 200, "Garen", False, 8_000),
                make_participant("red-two", 200, "Lux", False, 12_000),
            ],
        },
    }


@pytest.fixture
def second_riot_match_payload(riot_match_payload: dict[str, object]) -> dict[str, object]:
    payload = deepcopy(riot_match_payload)
    payload["metadata"]["matchId"] = "EUW1_123457"  # type: ignore[index]
    payload["info"]["gameCreation"] = 1_700_000_100_000  # type: ignore[index]
    return payload


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db
