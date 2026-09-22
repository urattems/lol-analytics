"""Offline Streamlit writes, conflicts, navigation and explicit sharing consent."""
import json

import pytest

from core.journal import save_annotation, load_annotations, load_goals
from core.models import RiotAccount
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, app_run, assert_clean, browser_page


def journal_app():
    app = app_run()
    browser_page(app, 'journal')
    app.run(timeout=40)
    assert_clean(app)
    return app


def section(app, name):
    app.session_state['journal_section'] = name
    app.run(timeout=40)
    assert_clean(app)


def test_note_save_rerun_clear_and_profile_isolation(demo_review):
    db, _ = demo_review
    app = journal_app()
    match = app.selectbox(key='journal_match').value
    app.text_area(key='journal_note_body').set_value('<b>private owner</b>').run(timeout=40)
    app.text_input(key='journal_note_tags').set_value('Tilt, duo, TILT').run(timeout=40)
    assert not load_annotations(db, DEMO_PUUID)  # no implicit save on rerun
    app.button(key='journal_note_save').click().run(timeout=40)
    assert_clean(app)
    assert load_annotations(db, DEMO_PUUID)[match]['tags'] == ['duo', 'tilt']
    assert any('enregistrés localement' in message.value for message in app.success)
    app.run(timeout=40)
    assert load_annotations(db, DEMO_PUUID)[match]['revision'] == 1
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert 'private owner' not in app.text_area(key='journal_note_body').value
    assert 'journal_share_prepared' not in app.session_state


def test_two_browsers_conflict_preserves_draft_then_explicit_reload(demo_review):
    db, _ = demo_review
    app = journal_app()
    match = app.selectbox(key='journal_match').value
    app.text_area(key='journal_note_body').set_value('my unsaved draft').run(timeout=40)
    save_annotation(db, DEMO_PUUID, match, 'other window', ['kept'], 0)
    app.button(key='journal_note_save').click().run(timeout=40)
    assert_clean(app)
    assert app.text_area(key='journal_note_body').value == 'my unsaved draft'
    assert any('modifiée ailleurs' in error.value for error in app.error)
    assert load_annotations(db, DEMO_PUUID)[match]['body'] == 'other window'
    app.button(key='journal_note_reload').click().run(timeout=40)
    assert app.text_area(key='journal_note_body').value == 'other window'
    assert app.button(key='journal_note_delete').disabled
    app.checkbox(key='journal_note_delete_confirm').check().run(timeout=40)
    app.button(key='journal_note_delete').click().run(timeout=40)
    assert_clean(app)
    assert load_annotations(db, DEMO_PUUID)[match]['body'] == ''
    assert db.count_matches() == 72


def test_history_note_entry_and_journal_sources_timeline_roundtrip(demo_review):
    app = app_run()
    browser_page(app, 'history')
    app.run(timeout=40)
    target = next(b for b in app.button if str(b.key).startswith('history_note_'))
    match = target.key.removeprefix('history_note_')
    target.click().run(timeout=40)
    browser_page(app, 'journal')
    assert_clean(app)
    assert app.selectbox(key='journal_match').value == match
    app.button(key='journal_note_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert app.session_state['history_evidence']['origin'] == 'journal'
    assert app.session_state['history_evidence']['match_ids'] == (match,)
    app.button(key='history_timeline_' + match).click().run(timeout=40)
    browser_page(app, 'timeline')
    app.button(key='history_back').click().run(timeout=40)
    browser_page(app, 'history')
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'journal')
    assert_clean(app)
    assert app.selectbox(key='journal_match').value == match


def test_tags_open_exact_cohort_and_notes_restore_after_another_tab(demo_review):
    db, _ = demo_review
    for match in ('DEMO_000071', 'DEMO_000072'):
        save_annotation(db, DEMO_PUUID, match, 'saved note', ['tilt'], 0)
    app = journal_app()
    section(app, 'Objectifs')
    section(app, 'Notes & tags')
    assert app.text_area(key='journal_note_body').value == 'saved note'
    app.button(key='journal_tag_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert set(app.session_state['history_evidence']['match_ids']) == {'DEMO_000071','DEMO_000072'}


def test_goal_creation_uses_next_games_and_archives_without_deleting(demo_review):
    db, _ = demo_review
    app = journal_app()
    section(app, 'Objectifs')
    app.text_input(key='journal_goal_title').set_value('CS focus').run(timeout=40)
    app.selectbox(key='journal_goal_metric').set_value('cs_per_minute').run(timeout=40)
    app.number_input(key='journal_goal_value_cs_per_minute').set_value(6.5).run(timeout=40)
    assert not load_goals(db, DEMO_PUUID)
    app.button(key='journal_goal_create').click().run(timeout=40)
    assert_clean(app)
    goals = load_goals(db, DEMO_PUUID)
    assert len(goals) == 1 and goals[0]['target_value'] == 6.5
    assert any('0 parties observées' in c.value for c in app.caption)
    app.button(key='journal_goal_archive').click().run(timeout=40)
    assert_clean(app)
    assert load_goals(db, DEMO_PUUID)[0]['closed_at'] is not None and db.count_matches() == 72


def test_sharing_preview_consent_scope_and_stale_cache(demo_review):
    db, _ = demo_review
    save_annotation(db, DEMO_PUUID, 'DEMO_000072', 'My private note', ['tilt'], 0)
    app = journal_app()
    section(app, 'Partager')
    assert not app.get('download_button')
    app.checkbox(key='journal_share_annotations').check().run(timeout=40)
    app.button(key='journal_share_prepare').click().run(timeout=40)
    assert_clean(app)
    assert app.checkbox(key='journal_share_consent').value is False
    assert not any(d.key == 'journal_share_download' for d in app.get('download_button'))
    prepared = app.session_state['journal_share_prepared']
    assert len(json.loads(prepared['preview'].matches_json)) == 20
    app.checkbox(key='journal_share_consent').check().run(timeout=40)
    assert any(d.key == 'journal_share_download' for d in app.get('download_button'))
    save_annotation(db, DEMO_PUUID, 'DEMO_000072', 'changed private note', [], 1)
    app.run(timeout=40)
    assert_clean(app)
    assert 'journal_share_prepared' not in app.session_state
    assert not any(d.key == 'journal_share_download' for d in app.get('download_button'))


def test_sharing_profile_switch_drops_prepared_bytes_and_consent(demo_review):
    app = journal_app()
    section(app, 'Partager')
    app.button(key='journal_share_prepare').click().run(timeout=40)
    assert 'journal_share_prepared' in app.session_state
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert 'journal_share_prepared' not in app.session_state and 'journal_share_consent' not in app.session_state


def test_recurring_player_sources_obey_selection_and_return_to_tab(demo_review):
    app = journal_app()
    section(app, 'Joueurs retrouvés')
    assert app.button(key='journal_player_sources')
    app.button(key='journal_player_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert app.session_state['history_evidence']['origin'] == 'journal'
    assert len(app.session_state['history_evidence']['match_ids']) >= 2
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'journal')
    assert_clean(app)
    assert app.session_state['journal_section'] == 'Joueurs retrouvés'


def test_empty_registered_profile_can_create_a_manual_goal(demo_review):
    db, _ = demo_review
    db.upsert_player(RiotAccount(puuid='empty-journal', gameName='Empty', tagLine='DEMO'))
    app = journal_app()
    app.selectbox(key='profile_selector').set_value('empty-journal').run(timeout=40)
    assert_clean(app)
    section(app, 'Objectifs')
    app.text_input(key='journal_goal_title').set_value('Rester concentré').run(timeout=40)
    app.button(key='journal_goal_create').click().run(timeout=40)
    assert_clean(app)
    assert load_goals(db, 'empty-journal')[0]['metric_key'] is None
    for label in ('Joueurs retrouvés', 'Partager', 'Notes & tags'):
        section(app, label)
