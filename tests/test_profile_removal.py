"""Secondary removal prunes only exclusive games; shared match graphs survive."""
import pytest
import sqlite3

from core.models import RiotAccount
from core.profiles import remove_profile_from_library
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, app_run, assert_clean, browser_page


def rows(database, tables):
    with database.connection() as c:
        return {t: [tuple(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}


def test_remove_exclusive_match_graph_preserves_shared_and_primary_sync(demo_review):
    db, settings = demo_review
    exclusive = db.player_matches(DEMO_SECOND_PUUID)[-1]['match_id']
    with db.connection() as c:
        c.execute("UPDATE participants SET puuid='demo-exclusive-owner' WHERE puuid=? AND match_id=?", (DEMO_PUUID, exclusive))
        c.execute("UPDATE timeline_frames SET puuid='demo-exclusive-owner' WHERE puuid=? AND match_id=?", (DEMO_PUUID, exclusive))
    tables = ('matches', 'participants', 'timeline_status', 'timeline_frames', 'timeline_events', 'identity_fetch_status')
    before = rows(db, tables)
    primary = db.get_player(DEMO_PUUID)
    sync = db.get_sync_state(DEMO_PUUID)
    result = remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    assert result and result.deleted_matches == 1 and result.shared_matches == 47
    assert db.get_player(DEMO_SECOND_PUUID) is None
    assert db.get_sync_state(DEMO_SECOND_PUUID) is None
    assert db.get_player(DEMO_PUUID) == primary and db.get_sync_state(DEMO_PUUID) == sync
    after = rows(db, tables)
    for table in tables:
        match_index = 0 if table in ('matches', 'timeline_status', 'identity_fetch_status') else 1
        assert after[table] == [row for row in before[table] if row[match_index] != exclusive]
    assert not remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    with db.connection() as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_primary_and_unresolved_primary_are_protected(demo_review):
    db, settings = demo_review
    before = rows(db, ('players', 'sync_state'))
    with pytest.raises(ValueError, match='principal'):
        remove_profile_from_library(settings, DEMO_PUUID)
    with pytest.raises(ValueError, match='principal'):
        remove_profile_from_library(settings.model_copy(update={'riot_game_name':'Missing'}), DEMO_SECOND_PUUID)
    assert rows(db, ('players', 'sync_state')) == before


def test_a_third_registered_profile_protects_a_game_without_primary(demo_review):
    db, settings = demo_review
    match_id = db.player_matches(DEMO_SECOND_PUUID)[-1]['match_id']
    with db.connection() as connection:
        connection.execute("UPDATE participants SET puuid='demo-third' WHERE puuid=? AND match_id=?", (DEMO_PUUID, match_id))
    db.upsert_player(RiotAccount(puuid='demo-third', gameName='Third', tagLine='DEMO'), 'EUW1', 'EUROPE')
    before = rows(db, ('matches', 'participants', 'timeline_frames', 'timeline_events'))
    assert db.profile_removal_counts(DEMO_SECOND_PUUID) == {'exclusive': 0, 'shared': 48}
    result = remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    assert result.deleted_matches == 0 and result.shared_matches == 48
    assert rows(db, tuple(before)) == before


def test_cascade_failure_rolls_back_profile_and_every_table(demo_review):
    db, settings = demo_review
    match_id = db.player_matches(DEMO_SECOND_PUUID)[-1]['match_id']
    with db.connection() as connection:
        connection.execute("UPDATE participants SET puuid='demo-unregistered' WHERE puuid=? AND match_id=?", (DEMO_PUUID, match_id))
        connection.execute("CREATE TRIGGER reject_cleanup BEFORE DELETE ON timeline_frames BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    before = rows(db, ('players', 'matches', 'participants', 'sync_state', 'timeline_status', 'timeline_frames', 'timeline_events'))
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    assert rows(db, tuple(before)) == before


def test_nonregistered_target_never_prunes_its_old_games(demo_review):
    db, settings = demo_review
    with db.connection() as connection:
        connection.execute('DELETE FROM players WHERE puuid=?', (DEMO_SECOND_PUUID,))
    before = rows(db, ('matches', 'participants', 'timeline_frames', 'timeline_events'))
    assert not remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    assert rows(db, tuple(before)) == before


def test_ambiguous_primary_fails_closed(demo_review):
    db, settings = demo_review
    db.upsert_player(RiotAccount(puuid='demo-ambiguous', gameName=settings.riot_game_name, tagLine=settings.riot_tag_line))
    before = rows(db, ('players', 'matches', 'participants'))
    with pytest.raises(ValueError, match='principal'):
        remove_profile_from_library(settings, DEMO_SECOND_PUUID)
    assert rows(db, tuple(before)) == before


def profiles_app():
    app = app_run()
    from streamlit.util import calc_hash
    app._page_hash = calc_hash('profiles')
    return app.run(timeout=40)


def test_cancel_then_active_removal_switches_before_delete_and_clears_scope(demo_review, monkeypatch):
    db, settings = demo_review
    app = profiles_app()
    assert not any(b.key == 'profile_remove_'+DEMO_PUUID for b in app.button)
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    app.button(key='profile_remove_'+DEMO_SECOND_PUUID).click().run(timeout=40)
    app.button(key='profile_remove_cancel').click().run(timeout=40)
    assert db.get_player(DEMO_SECOND_PUUID)
    assert app.session_state['active_profile_puuid'] == DEMO_SECOND_PUUID
    for prefix in ('history_', 'timeline_', 'review_', 'identity_', 'explorer_'):
        app.session_state[prefix+'sentinel'] = 'secondary'
    calls = []
    def remove(base, puuid):
        import streamlit as st
        assert st.session_state.get('active_profile_puuid') is None
        assert not any(k.endswith('_sentinel') for k in st.session_state)
        calls.append(puuid)
        return remove_profile_from_library(base, puuid)
    monkeypatch.setattr('pages.profiles.remove_profile_from_library', remove)
    app.button(key='profile_remove_'+DEMO_SECOND_PUUID).click().run(timeout=40)
    app.button(key='profile_remove_confirm').click().run(timeout=40)
    assert_clean(app)
    assert calls == [DEMO_SECOND_PUUID]
    assert app.session_state['active_profile_puuid'] is None
    assert app.selectbox(key='profile_selector').value == 'main'
    assert not any(b.key == 'profile_open_'+DEMO_SECOND_PUUID for b in app.button)
    assert any('conservées' in s.value for s in app.success)
    assert any('Aucun profil secondaire' in c.value for c in app.caption)
