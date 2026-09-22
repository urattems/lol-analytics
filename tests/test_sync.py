from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.db import Database
from core.models import RiotAccount, parse_riot_match
from core.sync import SyncService


class FakeRiotAPI:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.match_detail_calls: list[str] = []

    def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount:
        return RiotAccount(puuid="player-puuid", gameName=game_name, tagLine=tag_line)

    def get_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, queue: int | None = None
    ) -> list[str]:
        ids = list(self.payloads)
        return ids[start : start + count]

    def get_match(self, match_id: str) -> dict[str, Any]:
        self.match_detail_calls.append(match_id)
        return self.payloads[match_id]


def test_first_sync_inserts_and_second_sync_downloads_no_details(
    database: Database,
    riot_match_payload: dict[str, Any],
    second_riot_match_payload: dict[str, Any],
) -> None:
    api = FakeRiotAPI(
        {
            "EUW1_123457": second_riot_match_payload,
            "EUW1_123456": riot_match_payload,
        }
    )
    service = SyncService(api, database)

    first = service.sync_player("Synthetic Player", "EUW", count=2)
    second = service.sync_player("Synthetic Player", "EUW", count=2)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.up_to_date is True
    assert api.match_detail_calls == ["EUW1_123457", "EUW1_123456"]
    assert database.count_matches() == 2
    assert database.count_participants() == 8


def test_incremental_sync_fetches_only_new_match_details(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    old_payload = deepcopy(riot_match_payload)
    old_payload["metadata"]["matchId"] = "EUW1_OLD"
    api = FakeRiotAPI({"EUW1_OLD": old_payload})
    service = SyncService(api, database)
    service.sync_player("Synthetic Player", "EUW", count=20)
    api.match_detail_calls.clear()

    new_payloads: dict[str, dict[str, Any]] = {}
    for number in range(3, 0, -1):
        payload = deepcopy(riot_match_payload)
        match_id = f"EUW1_NEW_{number}"
        payload["metadata"]["matchId"] = match_id
        payload["info"]["gameCreation"] = 1_700_000_000_000 + number
        new_payloads[match_id] = payload
    api.payloads = {**new_payloads, **api.payloads}

    result = service.sync_player("Synthetic Player", "EUW", count=20)

    assert result.inserted == 3
    assert api.match_detail_calls == ["EUW1_NEW_3", "EUW1_NEW_2", "EUW1_NEW_1"]
    assert database.count_matches() == 4


def test_unanchored_profile_continues_after_a_shared_known_match(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    newer = deepcopy(riot_match_payload)
    newer["metadata"]["matchId"] = "EUW1_NEWER"
    older = deepcopy(riot_match_payload)
    older["metadata"]["matchId"] = "EUW1_OLDER"
    api = FakeRiotAPI({
        "EUW1_NEWER": newer,
        "EUW1_123456": riot_match_payload,
        "EUW1_OLDER": older,
    })
    result = SyncService(api, database).sync_player("Friend", "TAG")
    assert result.inserted == 2
    assert api.match_detail_calls == ["EUW1_NEWER", "EUW1_OLDER"]
    assert database.get_sync_state("player-puuid") == "EUW1_NEWER"


def test_unanchored_limited_sync_does_not_skip_an_unfilled_shared_history_gap(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    newer = deepcopy(riot_match_payload)
    newer["metadata"]["matchId"] = "EUW1_NEWER"
    older = deepcopy(riot_match_payload)
    older["metadata"]["matchId"] = "EUW1_OLDER"
    api = FakeRiotAPI({
        "EUW1_NEWER": newer,
        "EUW1_123456": riot_match_payload,
        "EUW1_OLDER": older,
    })
    service = SyncService(api, database)
    service.sync_player("Friend", "TAG", count=1)
    assert database.get_sync_state("player-puuid") is None
    api.match_detail_calls.clear()
    service.sync_player("Friend", "TAG")
    assert api.match_detail_calls == ["EUW1_OLDER"]
    assert database.get_sync_state("player-puuid") == "EUW1_NEWER"
