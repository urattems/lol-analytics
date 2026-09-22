from ui.profiles import clear_profile_state
from ui.formatting import decimal_label, signed_label, context_label, selection_filter_label
from ui.charts import match_tick_step
import ui.profiles as profiles
from config.settings import Settings
from core.models import RiotAccount, parse_riot_match
from analytics.overview import get_player_matches
from analytics.ai_bundle import build_ai_bundle
import io
import json
import zipfile


def test_profile_switch_discards_foreign_selection_and_keeps_independent_preferences():
    state = {"explorer_filters": {"champion": "Shyvana"}, "timeline_match": "MAIN_1", "champions_dataset": "20 dernières", "library_flash": "main result", "app_preference": "dark"}
    clear_profile_state(state, "friend")
    assert state == {"active_profile_puuid": "friend", "app_preference": "dark"}


def test_unavailable_metrics_are_not_printed_as_nan_or_infinity():
    for value in (None, float("nan"), float("inf"), float("-inf")):
        assert decimal_label(value) == signed_label(value) == "N/A"
    assert signed_label(-1210) == "-1 210g"
    assert decimal_label(51.60000001, 1, " %") == "51.6 %"
    assert context_label(False, "Squad context") == "Aucun récurrent"
    assert match_tick_step(500) > 1
    assert selection_filter_label("first_death_before_10", False) == "Mort avant 10 min : Non"
    assert selection_filter_label("include_short_games", False) == "Parties courtes : Exclues"
    assert selection_filter_label("gold_diff_15_min", -1200) == "GD @15 ≥ -1 200g"


def test_active_identity_wins_over_reassigned_historical_name(database, riot_match_payload, monkeypatch):
    for puuid in ("old-owner", "player-puuid"):
        database.upsert_player(RiotAccount(puuid=puuid, game_name="Same Name", tag_line="TAG"), "EUW1", "EUROPE")
    database.insert_match(parse_riot_match(riot_match_payload))
    settings = Settings(database_path=database.path, riot_game_name="Same Name", riot_tag_line="TAG")
    monkeypatch.setattr(profiles.st, "session_state", {"active_profile_puuid": "player-puuid"})
    selected = profiles.get_active_player(database, settings)
    assert selected["puuid"] == "player-puuid"
    games = get_player_matches(database, selected["puuid"], None)
    bundle = build_ai_bundle(games, database, selected["puuid"], settings.riot_id, "EUW1", "EUROPE")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["local_games"] == manifest["exported_games"] == 1
        assert manifest["filters"] == {}


def test_main_friend_main_isolates_region_and_saved_filters(database, monkeypatch):
    primary = Settings(database_path=database.path, riot_game_name="Primary", riot_tag_line="HOME")
    database.upsert_player(RiotAccount(puuid="friend", game_name="Mate", tag_line="EUW"), "KR", "ASIA")
    state = {}
    monkeypatch.setattr(profiles, "get_settings", lambda: primary)
    monkeypatch.setattr(profiles.st, "session_state", state)
    assert profiles.get_active_settings() == primary
    state["explorer_filters"] = {"champion": "Shyvana"}
    profiles.clear_profile_state(state, "friend")
    friend = profiles.get_active_settings()
    assert friend.riot_id == "Mate#EUW"
    assert (friend.riot_platform_region, friend.riot_routing_region) == ("KR", "ASIA")
    assert "explorer_filters" not in state
    state["timeline_selected_match"] = "KR_FRIEND"
    profiles.clear_profile_state(state, None)
    assert profiles.get_active_settings() == primary
    assert "timeline_selected_match" not in state
    assert primary.riot_id == "Primary#HOME"
