"""Controlled optional-render failures log details without losing page content."""
import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

from core.static_data import DataDragonService
from scripts.create_demo import DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, assert_clean, browser_page
from tests.test_history_ui import history_app, cards
from tests.test_profile_removal import profiles_app


def test_timeline_failure_keeps_match_and_purchases_then_retry_recovers(demo_review, monkeypatch, caplog):
    from pages.timeline import _personal_figure
    calls = []
    def fail_once(*args):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('Synthetic Timeline chart failure')
        return _personal_figure(*args)
    monkeypatch.setattr('pages.timeline._personal_figure', fail_once)
    app = history_app()
    app.button(key='history_timeline_'+cards(app)[0]).click().run(timeout=40)
    browser_page(app, 'timeline')
    assert_clean(app)
    assert any('Impossible d’afficher ce graphique' in w.value for w in app.warning)
    assert any('Achats, ventes et annulations' in h.value for h in app.subheader)
    assert 'Synthetic Timeline chart failure' not in caplog.text
    assert 'Traceback' not in caplog.text
    assert '"phase": "chart"' in caplog.text
    app.button(key='chart_retry_timeline_curve').click().run(timeout=40)
    assert_clean(app)
    assert len(calls) == 2 and not app.warning


def test_failed_profile_removal_is_logged_and_retryable(demo_review, monkeypatch, caplog):
    db, _ = demo_review
    def fail(*args):
        raise RuntimeError('Synthetic optional profile action failure')
    monkeypatch.setattr('pages.profiles.remove_profile_from_library', fail)
    app = profiles_app()
    app.button(key='profile_remove_'+DEMO_SECOND_PUUID).click().run(timeout=40)
    app.button(key='profile_remove_confirm').click().run(timeout=40)
    assert_clean(app)
    assert db.get_player(DEMO_SECOND_PUUID)
    assert any('Retrait impossible' in error.value for error in app.error)
    assert 'Synthetic optional profile action failure' not in caplog.text
    assert '"error_code": "INTERNAL"' in caplog.text
    assert '"phase": "removal"' in caplog.text
    app.button(key='profile_remove_cancel').click().run(timeout=40)
    assert_clean(app)
    assert app.button(key='profile_open_'+DEMO_SECOND_PUUID)


def test_data_dragon_failure_logs_and_history_uses_text(monkeypatch, caplog):
    calls = []
    def offline(request):
        calls.append(request.url.path)
        raise httpx.ConnectError('Synthetic asset outage', request=request)
    with_context = DataDragonService(transport=httpx.MockTransport(offline))
    monkeypatch.setattr('ui.history_cards.get_static_data_service', lambda: with_context)
    app = AppTest.from_string('''
import streamlit as st
from ui.history_cards import champion_portrait, item_icons
from analytics.history import inventory
st.markdown(champion_portrait("Lux"), unsafe_allow_html=True)
st.markdown(item_icons(inventory('[3031,0,null]')), unsafe_allow_html=True)
st.success('Names and statistics still available')
''').run()
    assert_clean(app)
    assert any('Lux' in m.value for m in app.markdown)
    assert app.success
    assert 'Synthetic asset outage' not in caplog.text and 'Traceback' not in caplog.text
    assert '"error_code": "NETWORK"' in caplog.text
    before = len(calls)
    with_context.champion('Lux'); with_context.item(3031)
    assert len(calls) == before
    with_context.close()


def test_native_error_mode_normal_and_explicit_developer(monkeypatch):
    from ui.errors import configure_error_display
    monkeypatch.delenv('LOL_ANALYTICS_DEV_ERRORS', raising=False)
    configure_error_display()
    assert st.get_option('client.showErrorDetails') == 'none'
    monkeypatch.setenv('LOL_ANALYTICS_DEV_ERRORS', '1')
    configure_error_display()
    assert st.get_option('client.showErrorDetails') == 'full'
    monkeypatch.delenv('LOL_ANALYTICS_DEV_ERRORS')
    configure_error_display()
