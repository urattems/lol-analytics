"""Real Streamlit callbacks, offline rendering and profile-scoped navigation."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

from analytics.context import build_context_dataset
from analytics.overview import get_player_matches
from analytics.session_review import prepare_review_history, build_session_review, session_catalog
from config.settings import Settings
from core.models import RiotAccount
from scripts.create_demo import create_demo_database, DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_ui_smoke import _offline_settings


@pytest.fixture
def demo_review(tmp_path, monkeypatch):
    database = create_demo_database(tmp_path / "demo.db")
    settings = Settings(database_path=database.path, riot_game_name="Demo Dragon", riot_tag_line="DEMO", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    monkeypatch.setattr("app.get_settings", lambda: settings)
    def no_riot(*args, **kwargs):
        raise AssertionError("Review rendering must not call Riot")
    monkeypatch.setattr("core.riot_api.RiotAPIClient.__init__", no_riot)
    st.cache_data.clear()
    return database, settings


def app_run():
    # A temporary entry point avoids AppTest's legacy pages/ file discovery
    # colliding with callable navigation hashes such as "timeline".
    return AppTest.from_string("from app import main\nmain()").run(timeout=40)


def assert_clean(app):
    assert not app.exception, [e.message for e in app.exception]


def browser_page(app, path):
    # AppTest retains its initial URL after st.switch_page for callable pages.
    # Preserve the new URL for the next widget event, as a browser would. The
    # preceding click still invokes the real callback and st.switch_page.
    app._page_hash = calc_hash(path)


def test_overview_review_game2_next_game3_back_to_same_session(demo_review):
    app = app_run()
    app.button(key="overview_review").click().run(timeout=40)
    browser_page(app, "session-review")
    assert_clean(app)
    anchor = app.session_state["review_anchor_match"]
    assert anchor == "DEMO_000069"
    assert app.selectbox(key="review_selector").value == anchor
    app.button(key="review_game_DEMO_000070").click().run(timeout=40)
    browser_page(app, "timeline")
    assert_clean(app)
    assert app.selectbox(key="timeline_selected_match").value == "DEMO_000070"
    assert any("Partie 2/4" in caption.value for caption in app.caption)
    app.button(key="review_next").click().run(timeout=40)
    assert_clean(app)
    assert app.selectbox(key="timeline_selected_match").value == "DEMO_000071"
    assert any("Partie 3/4" in caption.value for caption in app.caption)
    app.button(key="review_back").click().run(timeout=40)
    browser_page(app, "session-review")
    assert_clean(app)
    assert app.selectbox(key="review_selector").value == anchor
    assert app.session_state["review_focus_match"] == "DEMO_000071"
    assert any("la-review-focus" in block.value and "PARTIE 3" in block.value for block in app.markdown)
    # A widget selection persists on rerun and a changed gap keeps the anchor.
    app.selectbox(key="review_selector").set_value("DEMO_000058").run(timeout=40)
    selected = app.session_state["review_anchor_match"]
    assert selected != anchor
    app.run(timeout=40)
    assert app.session_state["review_anchor_match"] == selected
    app.selectbox(key="review_gap").set_value(60).run(timeout=40)
    assert_clean(app)
    assert app.session_state["review_anchor_match"] == selected


def test_review_profile_abc_and_missing_session_fallback(demo_review):
    database, _ = demo_review
    database.upsert_player(RiotAccount(puuid="demo-c", game_name="Demo C", tag_line="DEMO"), "EUW1", "EUROPE")
    app = app_run()
    app.button(key="overview_review").click().run(timeout=40)
    browser_page(app, "session-review")
    app.session_state["review_focus_match"] = "FOREIGN_SENTINEL"
    app.session_state["review_timeline_context"] = {"owner": DEMO_PUUID, "anchor": "DEMO_000069"}
    app.selectbox(key="profile_selector").set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert "review_timeline_context" not in app.session_state
    assert "review_focus_match" not in app.session_state
    history = prepare_review_history(build_context_dataset(get_player_matches(database, DEMO_SECOND_PUUID, None), database, DEMO_SECOND_PUUID))
    expected = build_session_review(history)
    assert app.session_state["review_anchor_match"] == expected["summary"]["anchor_match_id"]
    assert len([b for b in app.button if str(b.key).startswith("review_game_")]) == expected["summary"]["games"]
    app.selectbox(key="profile_selector").set_value("demo-c").run(timeout=40)
    assert_clean(app)
    assert not app.get("download_button")
    app.selectbox(key="profile_selector").set_value("main").run(timeout=40)
    app.session_state["review_requested_match"] = "deleted-or-other-profile"
    app.run(timeout=40)
    assert_clean(app)
    assert app.session_state["review_anchor_match"] == "DEMO_000069"
    assert any("plus récente" in c.value for c in app.caption)


@pytest.mark.parametrize("size", [1, 2, 4, 8])
def test_demo_review_sizes_and_export_render_without_riot(demo_review, size):
    database, _ = demo_review
    history = prepare_review_history(build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID))
    selected = next(s for s in session_catalog(history) if s["games"] == size)
    app = AppTest.from_string("from pages.session_review import show_session_review\nshow_session_review()").run(timeout=40)
    app.session_state["review_requested_match"] = selected["anchor_match_id"]
    app.run(timeout=40)
    assert_clean(app)
    assert len([b for b in app.button if str(b.key).startswith("review_game_")]) == size
    assert len(app.get("download_button")) == 1
    assert not any(b.key == "review_enrich" for b in app.button)


def test_contexts_opens_complete_session(demo_review):
    app = app_run()
    app.session_state["contexts_open_tab"] = "Sessions"
    # Native navigation is exercised through a tiny launcher so the Contexts
    # button belongs to the same registered navigation tree as the real app.
    code = '''
import streamlit as st
from pages.contexts import show_contexts
from pages.session_review import show_session_review
page = st.navigation([st.Page(show_contexts, title="Contexts", url_path="contexts"), st.Page(show_session_review, title="Session Review", url_path="session-review")])
page.run()
'''
    app = AppTest.from_string(code).run(timeout=40)
    assert_clean(app)
    app.button(key="contexts_review").click().run(timeout=40)
    assert_clean(app)
    # Contexts defaults to Shyvana: its latest selected games belong to the
    # preceding full eight-game session, which includes other champions too.
    assert app.session_state["review_anchor_match"] == "DEMO_000061"
    assert len([b for b in app.button if str(b.key).startswith("review_game_")]) == 8


def test_missing_timeline_keeps_exact_session_game_and_scoped_enrichment(demo_review, monkeypatch):
    database, settings = demo_review
    with database.connection() as connection:
        # Only this disposable synthetic fixture is changed.
        connection.execute("DELETE FROM timeline_frames WHERE match_id='DEMO_000070'")
        connection.execute("DELETE FROM timeline_status WHERE match_id='DEMO_000070'")
    st.cache_data.clear()
    app = app_run()
    app.button(key="overview_review").click().run(timeout=40)
    browser_page(app, "session-review")
    app.button(key="review_game_DEMO_000070").click().run(timeout=40)
    browser_page(app, "timeline")
    assert_clean(app)
    assert any("Partie 2/4" in c.value for c in app.caption)
    assert any("cette partie est indisponible" in c.value for c in app.info)
    assert not any(s.key == "timeline_selected_match" for s in app.selectbox)
    app.button(key="review_next").click().run(timeout=40)
    assert app.selectbox(key="timeline_selected_match").value == "DEMO_000071"
    app.button(key="review_back").click().run(timeout=40)
    browser_page(app, "session-review")
    settings.demo_mode, settings.riot_api_key = False, "synthetic-placeholder"
    calls = []
    def enrich(settings, puuid, **kwargs):
        calls.append((puuid, kwargs))
        return SimpleNamespace(available=0, unavailable=1, errors=0)
    monkeypatch.setattr("pages.session_review.enrich_timelines_from_settings", enrich)
    app.run(timeout=40)
    app.button(key="review_enrich").click().run(timeout=40)
    assert_clean(app)
    assert calls == [(DEMO_PUUID, {"retry_errors": True, "match_ids": ["DEMO_000070"]})]


def test_demo_latest_review_has_repeated_role_baseline_and_unique_comeback(demo_review):
    database, _ = demo_review
    review = build_session_review(prepare_review_history(build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID)))
    assert review["summary"]["games"] == review["summary"]["timeline_games"] == 4
    assert review["summary"]["wins"] == review["summary"]["losses"] == 2
    assert any(c["eligible"] and c["champion"] == "Leona" and c["role"] == "UTILITY" for c in review["comparisons"])
    assert any(h["kind"] == "trajectory" and h["match_id"] == "DEMO_000071" for h in review["highlights"])


@pytest.mark.parametrize("operation,changed", [("sync", 0), ("sync", 1), ("enrich", 1)])
def test_post_import_cta_is_optional_and_profile_scoped(demo_review, monkeypatch, operation, changed):
    database, settings = demo_review
    settings.demo_mode = False
    settings.riot_api_key = "synthetic-placeholder"
    calls = []
    def sync(*args, **kwargs):
        from core.import_jobs import JobStore
        calls.append("sync")
        assert args[1:] == ('sync', None) and kwargs['owner'] == DEMO_PUUID
        store = JobStore(database)
        job_id = store.create(settings, 'sync', None, owner=DEMO_PUUID)
        job = store.get(job_id)
        store.update(job_id, status='completed', local_count=job['local_before'] + changed, message='Synthetic sync completed')
        return job_id
    def enrich(*args, **kwargs):
        calls.append("enrich")
        return SimpleNamespace(available=changed, unavailable=0, errors=0)
    monkeypatch.setattr("ui.library.start_import_job", sync)
    monkeypatch.setattr("ui.library.enrich_timelines_from_settings", enrich)
    app = app_run()
    app.button(key="library_sync" if operation == "sync" else "library_timeline_enrich").click().run(timeout=40)
    assert_clean(app)
    assert calls == [operation]
    assert any(b.key == "overview_review" for b in app.button)  # No forced redirect.
    assert any(b.key == "review_after_sync" for b in app.button) == bool(changed)
    if changed:
        assert app.session_state["review_post_sync"]["owner"] == DEMO_PUUID
        app.button(key="review_after_sync").click().run(timeout=40)
        assert_clean(app)
        assert app.session_state["review_anchor_match"] == "DEMO_000069"
        assert "review_post_sync" not in app.session_state


def test_overview_latest_valid_session_survives_undated_match(demo_review):
    database, _ = demo_review
    with database.connection() as connection:
        connection.execute("UPDATE matches SET game_creation=NULL WHERE match_id='DEMO_000072'")
    app = app_run()
    assert_clean(app)
    assert not app.button(key="overview_review").disabled
    app.button(key="overview_review").click().run(timeout=40)
    assert_clean(app)
    assert app.session_state["review_anchor_match"] == "DEMO_000069"
    assert len([b for b in app.button if str(b.key).startswith("review_game_")]) == 3
