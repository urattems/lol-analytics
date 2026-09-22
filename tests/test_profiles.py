from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from config.settings import Settings
from core.db import Database
from core.models import RiotAccount, parse_riot_match
from core.profiles import (
    import_profile,
    parse_riot_id,
    register_local_profile,
    settings_for_lookup,
    settings_for_profile,
)
from core.sync import SyncService


class ProfileAPI:
    def __init__(self, puuid: str, payloads: dict[str, dict[str, Any]]) -> None:
        self.puuid = puuid
        self.payloads = payloads
        self.id_calls: list[tuple[str, int, int]] = []
        self.detail_calls: list[str] = []

    def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount:
        return RiotAccount(puuid=self.puuid, game_name=game_name, tag_line=tag_line)

    def get_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, queue: int | None = None
    ) -> list[str]:
        self.id_calls.append((puuid, start, count))
        return list(self.payloads)[start:start + count]

    def get_match(self, match_id: str) -> dict[str, Any]:
        self.detail_calls.append(match_id)
        return self.payloads[match_id]


def test_social_import_refuses_reassigned_riot_id_before_any_write(database):
    api = ProfileAPI('reassigned-account', {})
    with pytest.raises(ValueError, match='ne correspond plus'):
        import_profile(Settings(database_path=database.path), 'Synthetic#TEST', 'EUW1',
                       api=api, expected_puuid='original-player')
    assert database.list_players() == [] and api.id_calls == []


def _friend_payload(
    template: dict[str, Any], match_id: str, *, shared: bool = False
) -> dict[str, Any]:
    payload = deepcopy(template)
    payload["metadata"]["matchId"] = match_id
    if not shared:
        payload["info"]["participants"][0]["puuid"] = "other-player"
    return payload


@pytest.mark.parametrize("riot_id", ["", "Player", "#TAG", "Name#", "A#B#C", "A\n#TAG", "A#T AG"])
def test_invalid_riot_ids_are_rejected(riot_id: str) -> None:
    with pytest.raises(ValueError):
        parse_riot_id(riot_id)


def test_lookup_settings_preserve_primary_and_separate_platform_from_tag() -> None:
    primary = Settings(riot_api_key="fake-key", riot_game_name="Primary", riot_tag_line="HOME")
    original = primary.model_dump()
    friend = settings_for_lookup(primary, "  Mon Amié  # EUW ", " kr ")

    assert friend.riot_id == "Mon Amié#EUW"
    assert (friend.riot_platform_region, friend.riot_routing_region) == ("KR", "ASIA")
    assert friend.riot_api_key == primary.riot_api_key
    assert primary.model_dump() == original
    assert settings_for_profile(primary, {
        "game_name": "Friend", "tag_line": "ANY", "platform_region": "NA1", "routing_region": "EUROPE"
    }).riot_routing_region == "AMERICAS"
    with pytest.raises(ValueError):
        settings_for_lookup(primary, "Friend#TAG", "not-a-server")


def test_explicit_local_registration_and_counts_preserve_main(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    main = RiotAccount(puuid="player-puuid", gameName="Primary", tagLine="EUW")
    database.upsert_player(main, "EUW1", "EUROPE")
    shared = _friend_payload(riot_match_payload, "EUW1_SHARED", shared=True)
    shared["info"]["participants"][1].update(riotIdGameName="Friend", riotIdTagline="TAG")
    database.insert_match(parse_riot_match(shared))
    database.insert_match(parse_riot_match(_friend_payload(riot_match_payload, "EUW1_FRIEND")))
    database.update_sync_state(main.puuid, "EUW1_SHARED")

    assert len(database.list_players()) == 1
    assert database.get_player("blue-ally") is None
    friend = register_local_profile(database, "blue-ally", "EUW1")
    assert friend["game_name"] == "Friend"
    assert friend["local_games"] == 2
    assert database.get_player(main.puuid)["local_games"] == 1
    assert database.get_sync_state(main.puuid) == "EUW1_SHARED"
    assert len(database.list_players()) == 2
    assert register_local_profile(database, "blue-ally", "KR") == friend
    with pytest.raises(ValueError, match="Aucun Riot ID"):
        register_local_profile(database, "red-one", "EUW1")
    with pytest.raises(ValueError):
        register_local_profile(database, " ", "EUW1")


def test_profile_snapshot_counts_shared_matches_in_its_explicit_window(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    primary = Settings(database_path=database.path, riot_game_name="Primary")
    main = RiotAccount(puuid="player-puuid", gameName="Primary", tagLine="EUW")
    database.upsert_player(main, "EUW1", "EUROPE")
    shared = _friend_payload(riot_match_payload, "EUW1_SHARED", shared=True)
    database.insert_match(parse_riot_match(shared))
    database.update_sync_state(main.puuid, "EUW1_SHARED")
    payloads = {
        "EUW1_NEW": _friend_payload(riot_match_payload, "EUW1_NEW"),
        "EUW1_SHARED": shared,
        "EUW1_OLDER": _friend_payload(riot_match_payload, "EUW1_OLDER"),
    }
    api = ProfileAPI("blue-ally", payloads)
    result = import_profile(primary, "Friend#TAG", "EUW1", 2, api=api)

    assert (result.inserted, result.already_known) == (1, 1)
    assert api.id_calls == [("blue-ally", 0, 2)]
    assert api.detail_calls == ["EUW1_NEW"]
    assert database.get_player("blue-ally")["local_games"] == 2
    assert database.count_player_matches(main.puuid) == 1
    assert database.get_sync_state(main.puuid) == "EUW1_SHARED"
    assert database.get_sync_state("blue-ally") == "EUW1_NEW"
    assert not database.match_exists("EUW1_OLDER")


def test_snapshot_preserves_unfilled_incremental_gap(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    api = ProfileAPI("blue-ally", {
        "EUW1_OLD": _friend_payload(riot_match_payload, "EUW1_OLD")
    })
    service = SyncService(api, database)
    service.import_recent_player("Friend", "TAG", 1)
    api.payloads = {
        "EUW1_NEW": _friend_payload(riot_match_payload, "EUW1_NEW"),
        "EUW1_GAP": _friend_payload(riot_match_payload, "EUW1_GAP"),
        **api.payloads,
    }
    service.import_recent_player("Friend", "TAG", 1)
    assert database.get_sync_state("blue-ally") == "EUW1_OLD"
    api.detail_calls.clear()
    service.sync_player("Friend", "TAG")
    assert api.detail_calls == ["EUW1_GAP"]
    assert database.get_sync_state("blue-ally") == "EUW1_NEW"


def test_secondary_region_does_not_change_primary_region_or_settings(
    database: Database, riot_match_payload: dict[str, Any]
) -> None:
    primary = Settings(database_path=database.path, riot_game_name="Primary")
    main = RiotAccount(puuid="player-puuid", gameName="Primary", tagLine="EUW")
    database.upsert_player(main, "EUW1", "EUROPE")
    api = ProfileAPI("blue-ally", {
        "KR_100": _friend_payload(riot_match_payload, "KR_100")
    })
    import_profile(primary, "Friend#EUW", "KR", api=api)
    friend = database.get_player("blue-ally")
    assert (friend["platform_region"], friend["routing_region"]) == ("KR", "ASIA")
    assert database.get_player(main.puuid)["platform_region"] == "EUW1"
    assert primary.riot_game_name == "Primary"
    assert primary.riot_platform_region == "EUW1"


def test_invalid_snapshot_limit_makes_no_api_call(database: Database) -> None:
    api = ProfileAPI("friend", {})
    with pytest.raises(ValueError):
        import_profile(Settings(database_path=database.path), "Friend#TAG", "EUW1", 101, api=api)
    assert api.id_calls == []
    assert database.list_players() == []
