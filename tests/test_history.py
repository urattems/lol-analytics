from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx
import pytest

from core.db import Database
from core.models import RiotAccount
from core.riot_api import RiotAPIClient
from core.sync import ImportProgress, SyncService


class PagedRiotAPI:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.id_calls: list[tuple[int, int]] = []
        self.match_detail_calls: list[str] = []
        self.fail_on: str | None = None

    def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount:
        return RiotAccount(puuid="player-puuid", gameName=game_name, tagLine=tag_line)

    def get_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, queue: int | None = None
    ) -> list[str]:
        self.id_calls.append((start, count))
        return list(self.payloads)[start : start + count]

    def get_match(self, match_id: str) -> dict[str, Any]:
        self.match_detail_calls.append(match_id)
        if match_id == self.fail_on:
            raise RuntimeError("simulated interruption")
        return self.payloads[match_id]


def _payloads(template: dict[str, Any], count: int) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for index in range(count, 0, -1):
        match_id = f"EUW1_{index:04d}"
        payload = deepcopy(template)
        payload["metadata"]["matchId"] = match_id
        payload["info"]["gameCreation"] = 1_700_000_000_000 + index
        payloads[match_id] = payload
    return payloads


def test_backfill_empty_database_to_twenty(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    api = PagedRiotAPI(_payloads(riot_match_payload, 120))
    progress: list[ImportProgress] = []
    result = SyncService(api, database).backfill_history(
        "Synthetic Player", "EUW", 20, progress.append
    )

    assert result.local_before == 0
    assert result.local_after == 20
    assert result.inserted == 20
    assert len(api.match_detail_calls) == 20
    assert progress[-1] == ImportProgress("importing", 20, 20, 20)


def test_backfill_target_is_total_and_already_reached_is_network_light(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    api = PagedRiotAPI(_payloads(riot_match_payload, 130))
    service = SyncService(api, database)
    service.backfill_history("Synthetic Player", "EUW", 20)
    api.match_detail_calls.clear()

    result = service.backfill_history("Synthetic Player", "EUW", 100)
    assert result.local_before == 20
    assert result.inserted == 80
    assert result.local_after == 100
    assert len(api.match_detail_calls) == 80

    api.id_calls.clear()
    api.match_detail_calls.clear()
    sufficient = service.backfill_history("Synthetic Player", "EUW", 100)
    assert sufficient.already_sufficient is True
    assert sufficient.inserted == 0
    assert api.id_calls == []
    assert api.match_detail_calls == []


def test_backfill_all_history_paginates_until_an_empty_page(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    api = PagedRiotAPI(_payloads(riot_match_payload, 200))
    result = SyncService(api, database).backfill_history("Synthetic Player", "EUW", None)

    assert result.inserted == 200
    assert result.history_exhausted is True
    assert api.id_calls == [(0, 100), (100, 100), (200, 100)]
    assert database.count_player_matches("player-puuid") == 200


def test_backfill_interruption_keeps_progress_and_retry_resumes(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    api = PagedRiotAPI(_payloads(riot_match_payload, 20))
    service = SyncService(api, database)
    api.fail_on = list(api.payloads)[5]

    with pytest.raises(RuntimeError, match="simulated interruption"):
        service.backfill_history("Synthetic Player", "EUW", 10)
    assert database.count_player_matches("player-puuid") == 5

    api.fail_on = None
    api.match_detail_calls.clear()
    resumed = service.backfill_history("Synthetic Player", "EUW", 10)
    assert resumed.inserted == 5
    assert resumed.local_after == 10
    assert len(api.match_detail_calls) == 5
    assert database.count_matches() == 10


def test_complete_sync_supports_more_than_one_hundred_new_matches(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    old_api = PagedRiotAPI(_payloads(riot_match_payload, 1))
    service = SyncService(old_api, database)
    service.sync_player("Synthetic Player", "EUW", count=1)
    old_id = list(old_api.payloads)[0]

    all_payloads = _payloads(riot_match_payload, 124)
    all_payloads[old_id] = old_api.payloads[old_id]
    api = PagedRiotAPI(all_payloads)
    service.api = api
    result = service.sync_player("Synthetic Player", "EUW")

    assert result.inserted == 123
    assert api.id_calls == [(0, 100), (100, 100)]
    assert old_id not in api.match_detail_calls
    assert database.count_player_matches("player-puuid") == 124


def test_sync_interruption_resumes_past_newly_known_prefix(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    old_api = PagedRiotAPI(_payloads(riot_match_payload, 1))
    service = SyncService(old_api, database)
    service.sync_player("Synthetic Player", "EUW", count=1)
    old_id = list(old_api.payloads)[0]

    payloads = _payloads(riot_match_payload, 7)
    payloads[old_id] = old_api.payloads[old_id]
    api = PagedRiotAPI(payloads)
    api.fail_on = list(payloads)[2]
    service.api = api
    with pytest.raises(RuntimeError):
        service.sync_player("Synthetic Player", "EUW")
    assert database.count_player_matches("player-puuid") == 3

    api.fail_on = None
    api.match_detail_calls.clear()
    resumed = service.sync_player("Synthetic Player", "EUW")
    assert resumed.inserted == 4
    assert len(api.match_detail_calls) == 4
    assert old_id not in api.match_detail_calls
    assert database.count_player_matches("player-puuid") == 7


def test_backfill_uses_rate_limit_retry_without_real_network(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    match_id = "EUW1_RATE_LIMITED"
    payload = deepcopy(riot_match_payload)
    payload["metadata"]["matchId"] = match_id
    id_attempts = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal id_attempts
        if "/riot/account/" in request.url.path:
            return httpx.Response(
                200, json={"puuid": "player-puuid", "gameName": "Synthetic Player", "tagLine": "EUW"}
            )
        if request.url.path.endswith("/ids"):
            id_attempts += 1
            if id_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json=[match_id])
        return httpx.Response(200, json=payload)

    with RiotAPIClient(
        "test-key", transport=httpx.MockTransport(handler), sleep=waits.append
    ) as api:
        result = SyncService(api, database).backfill_history("Synthetic Player", "EUW", 1)

    assert result.inserted == 1
    assert id_attempts == 2
    assert waits == [0.0]
