"""Real native navigation, widgets and dialogs on isolated synthetic profiles."""
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from core.models import RiotAccount
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, app_run, assert_clean, browser_page


def history_app():
    app = app_run()
    app.button(key="overview_history").click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    return app


def cards(app):
    return [b.key.removeprefix('history_expand_') for b in app.button if str(b.key).startswith('history_expand_')]


def test_history_pagination_filter_reset_and_timeline_return(demo_review):
    app = history_app()
    assert app.checkbox(key='history_filter_include_short').value is False
    first = cards(app)
    assert len(first) == 20
    app.button(key='history_next').click().run(timeout=40)
    second = cards(app)
    assert len(second) == 20 and set(first).isdisjoint(second)
    target = second[0]
    app.button(key='history_expand_' + target).click().run(timeout=40)
    assert any('Vous' in m.value for m in app.markdown)
    app.button(key='history_timeline_' + target).click().run(timeout=40)
    browser_page(app, 'timeline')
    assert_clean(app)
    assert app.selectbox(key='timeline_selected_match').value == target
    app.button(key='history_back').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert cards(app) == second
    assert app.session_state['history_expanded_match'] == target
    app.selectbox(key='history_filter_champion').set_value('Leona').run(timeout=40)
    assert app.session_state['history_offset'] == 0
    assert len(cards(app)) < 20
    app.button(key='history_reset').click().run(timeout=40)
    assert cards(app) == first
    assert app.checkbox(key='history_filter_include_short').value is False


def test_history_profile_switch_clears_social_expansion_and_filters(demo_review):
    database, _ = demo_review
    database.upsert_player(RiotAccount(puuid='demo-c', gameName='Demo C', tagLine='DEMO'), 'EUW1', 'EUROPE')
    app = history_app()
    app.selectbox(key='history_filter_teammate').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    app.button(key='history_expand_' + cards(app)[0]).click().run(timeout=40)
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert app.selectbox(key='history_filter_teammate').value is None
    assert 'history_expanded_match' not in app.session_state or app.session_state['history_expanded_match'] is None
    assert set(cards(app)) <= {r['match_id'] for r in database.player_matches(DEMO_SECOND_PUUID)}
    app.selectbox(key='profile_selector').set_value('demo-c').run(timeout=40)
    assert_clean(app)
    assert not cards(app)
    app.selectbox(key='profile_selector').set_value('main').run(timeout=40)
    assert_clean(app)
    assert len(cards(app)) == 20


def test_history_player_dialog_explicit_import_and_main_return(demo_review, monkeypatch):
    database, settings = demo_review
    # Only the service boundary is mocked. The dialog, request parameters,
    # active-profile callback and native page changes execute normally.
    enabled = settings.model_copy(update={'demo_mode': False, 'riot_api_key': 'fake-key'})
    monkeypatch.setattr('ui.profiles.get_settings', lambda: enabled)
    monkeypatch.setattr('app.get_settings', lambda: enabled)
    calls = []
    def import_friend(base, riot_id, platform, count, progress, *, expected_puuid):
        calls.append((riot_id, platform, count, expected_puuid))
        name, tag = riot_id.split('#')
        account = RiotAccount(puuid=expected_puuid, gameName=name, tagLine=tag)
        database.upsert_player(account, platform, 'EUROPE')
        return SimpleNamespace(account=account)
    monkeypatch.setattr('ui.player_actions.import_profile', import_friend)
    app = history_app()
    app.button(key='history_expand_' + cards(app)[0]).click().run(timeout=40)
    candidates = [b for b in app.button if str(b.key).startswith('history_player_') and 'Atlas' not in b.label]
    candidates[0].click().run(timeout=40)
    assert_clean(app)
    assert not calls
    assert app.selectbox(key='identity_count').value == 20
    app.button(key='identity_import').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert len(calls) == 1 and calls[0][2] == 20
    assert app.session_state['active_profile_puuid'] == calls[0][3]
    app.selectbox(key='profile_selector').set_value('main').run(timeout=40)
    assert_clean(app)
    assert len(cards(app)) == 20


def test_history_short_toggle_filters_newest_games_and_resets_off(demo_review):
    database, _ = demo_review
    with database.connection() as c:
        c.execute("UPDATE matches SET duration=240 WHERE match_id IN ('DEMO_000072','DEMO_000071')")
    app = history_app()
    assert 'DEMO_000072' not in cards(app) and 'DEMO_000071' not in cards(app)
    app.checkbox(key='history_filter_include_short').check().run(timeout=40)
    assert cards(app)[:2] == ['DEMO_000072', 'DEMO_000071']
    assert any('Partie courte' in m.value for m in app.markdown)
    app.button(key='history_reset').click().run(timeout=40)
    assert 'DEMO_000072' not in cards(app)


def test_insights_short_default_and_opt_in(demo_review, monkeypatch):
    database, _ = demo_review
    with database.connection() as c:
        c.execute("UPDATE matches SET duration=240 WHERE match_id IN ('DEMO_000072','DEMO_000071')")
    from pages.insights import build_insight_bundle
    seen = []
    def capture(games, *args):
        seen.append(set(games['match_id'].to_list()))
        return build_insight_bundle(games, *args)
    monkeypatch.setattr('pages.insights.build_insight_bundle', capture)
    app = AppTest.from_string('from pages.insights import show_insights\nshow_insights()').run(timeout=40)
    assert_clean(app)
    assert len(seen[-1]) == 48 and 'DEMO_000072' not in seen[-1]
    app.checkbox(key='insights_include_short_games').check().run(timeout=40)
    assert len(seen[-1]) == 50 and 'DEMO_000072' in seen[-1]
