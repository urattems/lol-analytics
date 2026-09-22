"""Security and reliability regressions using synthetic temporary databases."""
import io
import json
import sqlite3
import zipfile

import pytest

from analytics.ai_bundle import build_ai_bundle
from analytics.exports import export_filename, export_markdown, build_full_export_payload
from analytics.overview import get_player_matches
from core.models import parse_riot_match, RiotAccount
from core.sync import SyncService
from core.timeline import TimelineService
from ui.page_helpers import database_revision


class SnapshotAPI:
    def __init__(self, payload):
        self.payload = payload

    def resolve_account(self, *args):
        return RiotAccount(puuid="player-puuid", game_name="Primary", tag_line="TEST")

    def get_match_ids(self, *args, **kwargs):
        return ["EUW1_123456"]

    def get_match(self, match_id):
        return self.payload


@pytest.mark.parametrize("corruption", ["wrong_match", "wrong_player", "no_participants"])
def test_import_rejects_wrong_identity_before_commit(database, riot_match_payload, corruption):
    if corruption == "wrong_match":
        riot_match_payload["metadata"]["matchId"] = "EUW1_OTHER"
    elif corruption == "wrong_player":
        riot_match_payload["info"]["participants"][0]["puuid"] = "foreign-player"
    else:
        riot_match_payload["info"]["participants"] = []
    with pytest.raises(ValueError):
        SyncService(SnapshotAPI(riot_match_payload), database).import_recent_player("Primary", "TEST")
    assert database.count_matches() == database.count_participants() == 0
    assert database.get_sync_state("player-puuid") is None


def test_empty_timeline_is_retryable_not_available(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    class EmptyAPI:
        def get_timeline(self, match_id):
            return {"metadata": {"matchId": match_id}, "info": {"frames": []}}
    result = TimelineService(EmptyAPI(), database).enrich("player-puuid")
    assert result.errors == 1 and result.available == 0
    assert database.timeline_available_match_ids("player-puuid") == set()
    assert database.timeline_candidates("player-puuid", retry_errors=True) == ["EUW1_123456"]


def test_opaque_event_extras_cannot_leak_external_identities(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    database.save_timeline("EUW1_123456", [
        {"participant_id": 1, "puuid": "player-puuid", "timestamp_ms": 0, "total_gold": 500}
    ], [{"event_index": 0, "event_type": "ITEM_PURCHASED", "timestamp_ms": 0,
         "participant_id": 1, "item_id": 999999, "extra": {"futureField": "red-one", "nested": {"gameName": "Private Mate"}}}])
    games = get_player_matches(database, "player-puuid", None)
    bundle = build_ai_bundle(games, database, "player-puuid", "Primary#TEST", "EUW1", "EUROPE")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        content = "\n".join(archive.read(name).decode() for name in archive.namelist())
        assert "red-one" not in content and "Private Mate" not in content
        assert json.loads(archive.read("manifest.json"))["exported_games"] == 1


def test_full_bundle_refuses_foreign_shared_match_statistics(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    games = get_player_matches(database, "player-puuid", None)
    with pytest.raises(ValueError, match="profile|profil|owner|joueur"):
        build_ai_bundle(games, database, "blue-ally", "Mate#TEST", "EUW1", "EUROPE")


def test_timeline_row_counts_are_profile_scoped(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    database.save_timeline("EUW1_123456", [{"participant_id": 1, "puuid": "player-puuid", "timestamp_ms": 0}], [])
    coverage = database.timeline_coverage("absent-player")
    assert coverage["local"] == coverage["frames"] == coverage["events"] == 0


def test_wal_commit_invalidates_cached_dataset(database, riot_match_payload, second_riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    with sqlite3.connect(database.path) as keeper:
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM matches").fetchone()
        before = database_revision(database.path)
        database.insert_match(parse_riot_match(second_riot_match_payload))
        assert database_revision(database.path) != before


def test_export_filename_rejects_path_extension():
    with pytest.raises(ValueError):
        export_filename("../Name#TAG", 1, "../../outside.json")


def test_markdown_export_treats_riot_id_as_text(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    payload = build_full_export_payload(get_player_matches(database, "player-puuid", None),
                                        '<img src=x>![open](https://bad.invalid)#TAG', "EUW1", "EUROPE")
    text = export_markdown(payload)
    assert "<img src=x>" not in text
    assert "![open](https://bad.invalid)" not in text
