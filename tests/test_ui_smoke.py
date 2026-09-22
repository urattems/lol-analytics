"""Offline UI regressions: empty libraries and cross-profile navigation."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from config.settings import Settings
from core.models import RiotAccount
from core.static_data import DataDragonService
from scripts.create_demo import create_demo_database, DEMO_SECOND_PUUID


def _offline_settings(monkeypatch, settings):
    monkeypatch.setattr("config.settings.get_settings", lambda: settings)
    monkeypatch.setattr("ui.profiles.get_settings", lambda: settings)
    monkeypatch.setattr("pages.profiles.get_settings", lambda: settings)
    monkeypatch.setattr(DataDragonService, "_safe_json", lambda *args: None)


def test_all_pages_handle_missing_and_empty_registered_profile(database, monkeypatch):
    settings = Settings(database_path=database.path, riot_game_name="Empty", riot_tag_line="DEMO", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    pages = ("overview", "session_review", "history", "champions", "progression", "journal", "insights", "contexts", "build_lab", "timeline", "explorer", "ai_export", "profiles")
    for registered in (False, True):
        if registered:
            database.upsert_player(RiotAccount(puuid="demo-empty", game_name="Empty", tag_line="DEMO"), "EUW1", "EUROPE")
        for page in pages:
            app = AppTest.from_string(f"from pages.{page} import show_{page}\nshow_{page}()").run(timeout=30)
            assert not app.exception, (registered, page, [e.message for e in app.exception])


def test_sidebar_switch_keeps_main_and_friend_libraries_isolated(tmp_path, monkeypatch):
    database = create_demo_database(tmp_path / "demo.db")
    settings = Settings(database_path=database.path, riot_game_name="Demo Dragon", riot_tag_line="DEMO", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=60)
    assert not app.exception
    assert any("72 parties locales" in block.value for block in app.markdown)
    app.selectbox(key="profile_selector").set_value(DEMO_SECOND_PUUID).run(timeout=60)
    assert not app.exception
    assert app.session_state["active_profile_puuid"] == DEMO_SECOND_PUUID
    assert any("48 parties locales" in block.value for block in app.markdown)
    app.selectbox(key="profile_selector").set_value("main").run(timeout=60)
    assert not app.exception
    assert app.session_state["active_profile_puuid"] is None
    assert any("72 parties locales" in block.value for block in app.markdown)


def test_profile_card_updates_sidebar_before_navigating_again(tmp_path, monkeypatch):
    database = create_demo_database(tmp_path / "demo.db")
    settings = Settings(database_path=database.path, riot_game_name="Demo Dragon", riot_tag_line="DEMO", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=60)
    next(button for button in app.button if button.label == "Chercher un profil").click().run(timeout=60)
    app.button(key=f"profile_open_{DEMO_SECOND_PUUID}").click().run(timeout=60)
    assert not app.exception
    assert app.session_state["active_profile_puuid"] == DEMO_SECOND_PUUID
    assert app.selectbox(key="profile_selector").value == DEMO_SECOND_PUUID
    next(button for button in app.button if button.label == "Chercher un profil").click().run(timeout=60)
    assert not app.exception
    assert app.session_state["active_profile_puuid"] == DEMO_SECOND_PUUID
    assert app.selectbox(key="profile_selector").value == DEMO_SECOND_PUUID
