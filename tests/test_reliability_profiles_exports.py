"""Cross-profile reruns, export completeness and fuzzing on invented libraries."""
from datetime import datetime, timezone
import io
import json
from pathlib import PurePosixPath
import random
import zipfile

import httpx
import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

from analytics.ai_bundle import BUNDLE_FILES, ai_bundle_filename, build_ai_bundle
from analytics.context import build_context_dataset
from analytics.explorer import AnalysisFilters, analysis_summary, build_analysis_dataset
from analytics.exports import build_export_payload, export_json, export_filename
from analytics.overview import get_player_matches
from analytics.teammates import teammate_summary, champion_pairings, squad_summary, _metric_row
from config.settings import Settings
from core.models import RiotAccount, parse_riot_match
from core.static_data import DataDragonService
from core.timeline import parse_timeline
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from scripts.reliability_benchmark import synthetic_library
from tests.test_ui_smoke import _offline_settings
from tests.test_timeline import timeline_payload


@pytest.mark.parametrize("size,coverage", [(0, 0), (1, 0), (1, 1), (500, 0), (500, .5), (500, 1)])
def test_full_export_cardinality_coverage_and_archive_privacy(tmp_path, size, coverage):
    database = synthetic_library(tmp_path / "synthetic.db", size, coverage)
    games = get_player_matches(database, DEMO_PUUID, None)
    bundle = build_ai_bundle(games, database, DEMO_PUUID, "Synthetic QA#TEST", "EUW1", "EUROPE")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == BUNDLE_FILES and len(archive.namelist()) == len(BUNDLE_FILES)
        assert all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts for name in archive.namelist())
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        matches = [json.loads(row) for row in archive.read("matches.jsonl").splitlines()]
        assert len(matches) == len({row["match_id"] for row in matches}) == size
        assert manifest["local_games"] == manifest["exported_games"] == database.count_player_matches(DEMO_PUUID)
        assert sum(row["timeline_available"] for row in matches) == int(size * coverage)
        assert manifest["timeline"] == {"available": int(size * coverage), "missing": size - int(size * coverage)}
        for name in archive.namelist():
            content = archive.read(name).decode()
            assert "demo-guest-" not in content and "Demo Guest " not in content and DEMO_SECOND_PUUID not in content
    if size:
        with pytest.raises(ValueError, match="profile"):
            build_ai_bundle(games.head(0), database, DEMO_PUUID, "QA#TEST", "EUW1", "EUROPE")


def test_filter_fuzz_is_exact_same_selection_as_custom_export(tmp_path):
    database = synthetic_library(tmp_path / "fuzz.db", 24, .5)
    games = build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID)
    cases = [dict(champion="does-not-exist"), dict(patch="999.999"),
             dict(date_from_ms=2000, date_to_ms=1000), dict(min_duration=3000, max_duration=60),
             dict(gold_diff_15_min=1000, gold_diff_15_max=-1000), dict(max_duration=299),
             dict(opponent_champion="absent"), dict(trajectory="unavailable"),
             dict(session_game_number="4+", date_to_ms=1)]
    rng = random.Random(222)
    for _ in range(40):
        cases.append(dict(champion=rng.choice([None, "Shyvana", "absent"]),
                          patch=rng.choice([None, "999.999"]),
                          timeline_requirement=rng.choice(["all", "available", "missing"]),
                          min_duration=rng.randrange(0, 4000), max_duration=rng.randrange(0, 4000)))
    for case in cases:
        filters = AnalysisFilters(base_game_limit=None, **case)
        selected = build_analysis_dataset(games, filters).filtered
        payload = build_export_payload(games, filters, "QA#TEST", "EUW1", "EUROPE")
        assert [row["match_id"] for row in payload["matches"]] == selected["match_id"].to_list()
        assert payload["summary"]["games"] == selected.height
        json.dumps(analysis_summary(selected), allow_nan=False)
        assert "NaN" not in export_json(payload)


def test_cohort_metrics_match_reference_and_respect_selected_dates(tmp_path):
    database = synthetic_library(tmp_path / "cohorts.db", 12)
    games = build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID).head(3)
    participants = database.participants_for_player_matches(DEMO_PUUID)
    rows = teammate_summary(games, participants, DEMO_PUUID)
    own_ids = set(games["match_id"])
    for row in rows:
        ids = {p["match_id"] for p in participants if p["puuid"] == row["teammate_puuid"]} & own_ids
        selected = games.filter(pl.col("match_id").is_in(list(ids)))
        for metric, expected in _metric_row(selected).items():
            if isinstance(expected, float):
                assert row[metric] == pytest.approx(expected)
            else:
                assert row[metric] == expected
        assert row["first_game"] >= games["game_creation"].min()
        assert row["last_game"] <= games["game_creation"].max()
    assert all(row["games"] > 0 for row in champion_pairings(games, participants, DEMO_PUUID))
    assert all(row["games"] >= 3 for row in squad_summary(games, participants, DEMO_PUUID))


def test_three_profiles_page_reruns_reset_filters_and_download_owner(tmp_path, monkeypatch):
    database = synthetic_library(tmp_path / "profiles.db", 12)
    database.upsert_player(RiotAccount(puuid=DEMO_SECOND_PUUID, game_name="Mate B", tag_line="TEST"), "EUW1", "EUROPE")
    third = "demo-mate-top"
    database.upsert_player(RiotAccount(puuid=third, game_name="Mate C", tag_line="TEST"), "EUW1", "EUROPE")
    settings = Settings(database_path=database.path, riot_game_name="Synthetic QA", riot_tag_line="TEST", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    app = AppTest.from_string('''
import importlib
import streamlit as st
from core.db import Database
from config.settings import get_settings
from ui.profiles import render_profile_selector, get_active_player
from ui.page_helpers import cached_player_matches, database_revision
settings = get_settings()
database = Database(settings.database_path)
active = render_profile_selector(settings, database)
player = get_active_player(database, active)
st.session_state["qa_owner"] = player["puuid"]
st.session_state["qa_games"] = cached_player_matches(str(database.path), player["puuid"], None, database_revision(database.path)).to_dicts()
page = st.session_state.get("qa_page", "overview")
getattr(importlib.import_module("pages." + page), "show_" + page)()
''').run(timeout=60)
    sequence = [("main", "champions"), ("main", "timeline"), (DEMO_SECOND_PUUID, "explorer"),
                (third, "ai_export"), ("main", "contexts"), (DEMO_SECOND_PUUID, "champions"),
                ("main", "overview")]
    for profile, page in sequence:
        previous = app.selectbox(key="profile_selector").value
        app.session_state["qa_page"] = page
        app.session_state["explorer_sentinel"] = "foreign selection"
        app.session_state["timeline_requested_match"] = "OTHER_PROFILE_ONLY"
        app.selectbox(key="profile_selector").set_value(profile).run(timeout=60)
        assert not app.exception, (profile, page, [e.message for e in app.exception])
        expected = DEMO_PUUID if profile == "main" else profile
        assert app.session_state["qa_owner"] == expected
        assert app.session_state["qa_games"] == get_player_matches(database, expected, None).to_dicts()
        if previous != profile:
            assert "explorer_sentinel" not in app.session_state
            assert "timeline_requested_match" not in app.session_state
        if page == "explorer":
            app.button(key="explorer_reset").click().run(timeout=60)
            assert not app.exception
        if page == "ai_export":
            from pages.ai_export import _cached_bundle
            from ui.page_helpers import database_revision
            bundle = _cached_bundle(str(database.path), expected, "Mate C#TEST", "EUW1", "EUROPE", database_revision(database.path))
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                assert json.loads(archive.read("manifest.json"))["local_games"] == database.count_player_matches(expected)
                rows = [json.loads(line) for line in archive.read("matches.jsonl").splitlines()]
                assert {(r["match_id"], r["champion"]) for r in rows} == {(r["match_id"], r["champion"]) for r in app.session_state["qa_games"]}
        app.run(timeout=60)
        assert not app.exception


@pytest.mark.parametrize("failure", ["offline", "empty", "unknown_version", "missing_icons"])
def test_data_dragon_failures_keep_text_usable(failure):
    calls = []
    def handler(request):
        calls.append(request.url)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "empty":
            return httpx.Response(200, content=b"")
        if request.url.path.endswith("versions.json"):
            return httpx.Response(200, json=["999.999.999"])
        if failure == "unknown_version":
            return httpx.Response(404)
        return httpx.Response(200, json={"data": {"Shyvana": {"name": "Shyvana"}, "999999": {"name": "Unknown item"}}})
    service = DataDragonService(transport=httpx.MockTransport(handler))
    try:
        assert service.champion("Shyvana").display_name == "Shyvana"
        assert service.champion("Shyvana").image_url is None
        assert service.item(999999).image_url is None
        count = len(calls)
        service.champion("Shyvana")
        service.item(999999)
        assert len(calls) == count
    finally:
        service.close()


@pytest.mark.parametrize("identity", ["../CON#..\\TAG", "Étoile雪#タグ", '<script>alert(1)</script>#TAG', "![x](https://invalid)#TAG", "x" * 5000 + "#TAG"])
def test_identity_is_bound_parameter_url_component_and_safe_filename(database, identity):
    name, tag = identity.split("#", 1)
    database.upsert_player(RiotAccount(puuid="qa-id", game_name=name, tag_line=tag), "EUW1", "EUROPE")
    assert database.find_player(name, tag)["puuid"] == "qa-id"
    instant = datetime(2026, 9, 6, tzinfo=timezone.utc)
    for filename in (export_filename(identity, 1, "json", instant), ai_bundle_filename(identity, instant)):
        assert len(filename) < 160 and "/" not in filename and "\\" not in filename and ".." not in filename
    assert export_filename(identity, 1, "json", instant) == export_filename(identity, 1, "json", instant)
    requested = []
    def handler(request):
        requested.append(request.url)
        return httpx.Response(200, json={"puuid": "qa-id", "gameName": name, "tagLine": tag})
    from core.riot_api import RiotAPIClient
    with RiotAPIClient("fake", transport=httpx.MockTransport(handler)) as client:
        assert client.resolve_account(name, tag).puuid == "qa-id"
    assert requested[0].host == "europe.api.riotgames.com" and requested[0].query == b""


def test_optional_fields_unknown_queue_and_events(riot_match_payload):
    own = riot_match_payload["info"]["participants"][0]
    for key in ("goldEarned", "visionScore", "item1", "teamPosition", "totalDamageDealtToChampions"):
        own.pop(key)
    riot_match_payload["info"]["queueId"] = 999999
    parsed = parse_riot_match(riot_match_payload)
    assert parsed.participants[0].gold_earned is None and parsed.queue_id == 999999
    own.pop("kills")
    with pytest.raises(ValueError):
        parse_riot_match(riot_match_payload)
    timeline = timeline_payload()
    timeline["info"]["frames"][0]["events"].append({"type": "FUTURE_EVENT", "timestamp": 1})
    timeline["info"]["frames"][0]["participantFrames"]["1"].pop("totalGold")
    parsed_timeline = parse_timeline(timeline)
    assert len(parsed_timeline.events) == 6 and parsed_timeline.frames[0]["total_gold"] is None


def test_sparse_matchup_annotation_after_first_hundred_games():
    from analytics.matchups import identify_matchups
    games = pl.DataFrame({"match_id": [str(i) for i in range(121)]})
    participants = [{"match_id": "120", "puuid": "own", "side": "blue", "role": "TOP"},
                    {"match_id": "120", "puuid": "enemy", "side": "red", "role": "TOP", "champion": "Garen"}]
    result = identify_matchups(games, participants, "own")
    assert result["opponent_champion"].to_list() == [None] * 120 + ["Garen"]


@pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_lookup_failure_ui_is_recoverable(database, monkeypatch, failure):
    settings = Settings(database_path=database.path, riot_game_name="QA", riot_tag_line="TEST")
    _offline_settings(monkeypatch, settings)
    def fail(*args, **kwargs):
        raise failure("synthetic malformed response")
    monkeypatch.setattr("pages.profiles.import_profile", fail)
    app = AppTest.from_string("from pages.profiles import show_profiles\nshow_profiles()").run(timeout=30)
    app.text_input[0].set_value("Mate#TEST")
    next(button for button in app.button if button.label == "Rechercher et importer").click().run(timeout=30)
    assert not app.exception and app.error
    app.run(timeout=30)
    assert not app.exception
    assert database.count_matches() == 0


def test_export_ui_rejects_stale_cache_without_traceback(database, riot_match_payload, second_riot_match_payload, monkeypatch):
    from ui.page_helpers import cached_context_dataset, database_revision
    database.insert_match(parse_riot_match(riot_match_payload))
    database.upsert_player(RiotAccount(puuid="player-puuid", game_name="QA", tag_line="TEST"), "EUW1", "EUROPE")
    old_revision = database_revision(database.path)
    cached_context_dataset(str(database.path), "player-puuid", old_revision)
    database.insert_match(parse_riot_match(second_riot_match_payload))
    _offline_settings(monkeypatch, Settings(database_path=database.path, riot_game_name="QA", riot_tag_line="TEST", demo_mode=True))
    monkeypatch.setattr("pages.ai_export.database_revision", lambda path: old_revision)
    app = AppTest.from_string("from pages.ai_export import show_ai_export\nshow_ai_export()").run(timeout=30)
    assert not app.exception
    assert any("historique a changé" in warning.value for warning in app.warning)
    assert len(app.get("download_button")) == 0
