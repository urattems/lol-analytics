"""Exact evidence, native navigation, migrations and lazy profile UI."""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy

import polars as pl
import pytest
import streamlit as st

from analytics.evidence import EvidenceUnavailable, select_evidence
from analytics.stuff import load_stuff_matches, timing_groups
from core.item_catalog import load_catalogs, parse_catalog, save_catalog
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, app_run, assert_clean, browser_page
from tests.test_stuff import item, catalog_payload, catalog


def seed_demo_catalogs(database):
    # Explicitly invented metadata for synthetic fixtures, never downloaded or
    # installed in a personal database. Only serves contract/UI tests.
    _, _, _, events = database.timeline_analysis_source(DEMO_PUUID)
    ids = {e['item_id'] for e in events if e['event_type'] == 'ITEM_PURCHASED' and e.get('item_id')}
    data = {str(i): item(f'Demo item {i}', recipe=(100,)) for i in ids}
    data.update({'100': item('Demo component'), '1036': item('Demo sword'),
                 '1001': item('Demo base boots', tags=('Boots',)),
                 '3047': item('Demo T2 boots', recipe=(1001,), tags=('Boots',))})
    for patch in ('16.16', '16.17'):
        version = patch + '.1'
        save_catalog(database, parse_catalog({'version': version, 'data': data}, patch, version, 1_789_200_000))


@pytest.mark.parametrize('context', [None, {}, {'owner': 'other', 'match_ids': ['A']},
    {'owner': 'own', 'match_ids': ['A', 'foreign']}, {'owner': 'own', 'match_ids': []},
    {'owner': 'own', 'match_ids': ['A', 'A']}, {'owner': 'own', 'match_ids': [None]},
    {'owner': 'own', 'match_ids': 'A'}])
def test_invalid_stale_and_foreign_evidence_never_falls_back_to_full(context):
    games = pl.DataFrame({'match_id': ['A', 'B']})
    with pytest.raises(EvidenceUnavailable):
        select_evidence(games, 'own', context)


def test_exact_sources_keep_canonical_rows_and_chronology():
    games = pl.DataFrame({'match_id': ['new', 'middle', 'old'], 'gold': [300, 200, 100]})
    result = select_evidence(games, 'own', {'owner': 'own', 'match_ids': ['old', 'new']})
    assert result.to_dicts() == [{'match_id': 'new', 'gold': 300}, {'match_id': 'old', 'gold': 100}]


def test_additive_catalog_migration_preserves_old_rows_and_cache(database, catalog):
    save_catalog(database, catalog)
    with database.connection() as c:
        before = {table: list(c.execute(f'SELECT * FROM {table}')) for table in ('players', 'matches', 'participants', 'timeline_events', 'item_catalogs')}
    for _ in range(3):
        database.initialize()
    with database.connection() as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        for table, rows in before.items():
            assert list(c.execute(f'SELECT * FROM {table}')) == rows
    assert load_catalogs(database, ['16.17']) == {'16.17': catalog}


@pytest.mark.parametrize('mutation', ['restricted', 'duplicate', 'boolean_id', 'bad_date', 'bad_flag'])
def test_corrupt_catalog_does_not_silently_change_classification(database, catalog, mutation):
    save_catalog(database, catalog)
    with database.connection() as c:
        data = json.loads(c.execute('SELECT catalog_json FROM item_catalogs').fetchone()[0])
        if mutation == 'restricted':
            data[0]['restricted'] = 'False'
        elif mutation == 'duplicate':
            data.append(data[0])
        elif mutation == 'boolean_id':
            data[0]['item_id'] = True
        elif mutation == 'bad_flag':
            data[0]['purchasable'] = 'true'
        else:
            c.execute('UPDATE item_catalogs SET fetched_at = -1')
        c.execute('UPDATE item_catalogs SET catalog_json=?', (json.dumps(data),))
    assert load_catalogs(database, ['16.17']) == {}


def test_dense_demo_batch_sources_have_real_purchase_slots(demo_review):
    database, _ = demo_review
    seed_demo_catalogs(database)
    facts = load_stuff_matches(database, DEMO_PUUID, load_catalogs(database, ['16.16', '16.17']))
    assert len(facts) == 72
    assert all(f.eligible for f in facts.values()), {i: f.issues for i, f in facts.items() if f.issues}
    assert all(len(f.major_items) == 3 and f.boots_t2 is not None for f in facts.values())
    for row in timing_groups(list(facts.values())):
        assert row['n'] == len(row['source_ids'])
        assert all(facts[i].patch == row['patch'] and facts[i].role == row['role'] for i in row['source_ids'])


def champions_app():
    app = app_run()
    browser_page(app, 'champions')
    app.run(timeout=40)
    assert_clean(app)
    return app


def select_section(app, section):
    # AppTest currently exposes tabs as containers, not click controls. Browser
    # tests cover actual mouse clicks; this uses the public tab state contract.
    app.session_state['champion_section'] = section
    app.run(timeout=40)
    assert_clean(app)


def test_champion_stuff_is_lazy_and_offline_until_explicit_action(demo_review, monkeypatch):
    def no_stuff(*args, **kwargs):
        pytest.fail('Stuff engine ran on Summary')
    monkeypatch.setattr('ui.stuff.cached_stuff', no_stuff)
    app = champions_app()
    assert 'Champion' in [s.label for s in app.selectbox]
    assert not any(b.key and b.key.startswith('champion_pick_') for b in app.button)
    assert app.session_state['champion_section'] == 'Résumé'


def test_missing_catalog_shows_honest_empty_state_without_network(demo_review, monkeypatch):
    monkeypatch.setattr('ui.stuff.fetch_catalog', lambda *a, **kw: pytest.fail('implicit fetch'))
    app = champions_app()
    select_section(app, 'Stuff & timings')
    assert any('Pas encore de timings' in v.value for v in app.info)
    assert app.button(key='champion_catalog_fetch')


def test_stuff_paths_states_sources_timeline_and_champion_return(demo_review):
    database, _ = demo_review
    seed_demo_catalogs(database)
    app = champions_app()
    app.selectbox(key='champions_dataset').set_value("Tout l'historique").run(timeout=40)
    select_section(app, 'Stuff & timings')
    assert app.button(key='champion_timing_1_sources')
    app.radio(key='champion_stuff_view').set_value('État personnel @10').run(timeout=40)
    assert_clean(app)
    assert app.button(key='champion_state_sources')
    app.radio(key='champion_stuff_view').set_value('Chemins d’objets').run(timeout=40)
    assert_clean(app)
    app.selectbox(key='champion_path_length').set_value(3).run(timeout=40)
    app.session_state['history_saved_widgets'] = {'history_filter_champion': 'Nonexistent', 'history_filter_window': '20 dernières'}
    app.button(key='champion_path_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    context = app.session_state['history_evidence']
    source_ids = set(context['match_ids'])
    assert source_ids and context['owner'] == DEMO_PUUID
    assert not any(s.key == 'history_filter_champion' for s in app.selectbox)
    assert any(f'{len(source_ids)} matchs sources' in v.value for v in app.info)
    # All emitted cards are sources, even though the normal history had a
    # deliberately impossible filter. Detail button keys identify actual rows.
    from ui.history_navigation import cached_history_library
    from ui.page_helpers import database_revision
    library = cached_history_library(str(database.path), DEMO_PUUID, database_revision(database.path))
    expected = select_evidence(library.games, DEMO_PUUID, context)
    assert set(expected['match_id']) == source_ids
    cards = {b.key.removeprefix('history_expand_') for b in app.button if str(b.key).startswith('history_expand_')}
    assert cards == set(expected.head(20)['match_id'])
    timeline_buttons = [b for b in app.button if b.key and b.key.startswith('history_timeline_')]
    assert timeline_buttons
    timeline_buttons[0].click().run(timeout=40)
    browser_page(app, 'timeline')
    assert_clean(app)
    assert app.selectbox(key='timeline_selected_match').value in source_ids
    app.button(key='history_back').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert set(app.session_state['history_evidence']['match_ids']) == source_ids
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'champions')
    assert_clean(app)
    assert app.session_state['champion_section'] == 'Stuff & timings'
    assert app.radio(key='champion_stuff_view').value == 'Chemins d’objets'
    assert app.selectbox(key='champion_path_length').value == 3
    assert app.selectbox(key='champions_dataset').value == "Tout l'historique"


def test_profile_switch_clears_evidence_and_champion_context(demo_review):
    app = champions_app()
    select_section(app, 'Parties')
    app.button(key='champion_all_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert app.session_state['history_evidence']['owner'] == DEMO_PUUID
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert 'history_evidence' not in app.session_state
    assert 'champion_saved_widgets' not in app.session_state


def test_deleted_source_cohort_stays_explicit_even_when_library_is_now_empty(demo_review):
    database, _ = demo_review
    app = champions_app()
    select_section(app, 'Parties')
    app.button(key='champion_all_sources').click().run(timeout=40)
    browser_page(app, 'history')
    with database.connection() as connection:
        connection.execute('DELETE FROM matches')  # isolated synthetic fixture only
    app.run(timeout=40)
    assert_clean(app)
    assert any('La bibliothèque a changé' in row.value for row in app.warning)
    assert app.button(key='history_evidence_back')
    assert not any(str(b.key).startswith('history_expand_') for b in app.button)


def test_group_selection_is_repaired_when_scope_shrinks(demo_review):
    database, _ = demo_review
    seed_demo_catalogs(database)
    app = champions_app()
    app.session_state['champion_timing_1_group'] = 999
    select_section(app, 'Stuff & timings')
    assert app.selectbox(key='champion_timing_1_group').value == 0


@pytest.mark.parametrize('path', ['contexts', 'insights', 'build-lab', 'timeline', 'ai-export', 'explorer'])
def test_secondary_native_urls_remain_registered_and_render(demo_review, path, monkeypatch):
    navigation = st.navigation
    captured = {}
    def observe(*args, **kwargs):
        selected = navigation(*args, **kwargs)
        captured['path'] = selected.url_path
        return selected
    monkeypatch.setattr(st, 'navigation', observe)
    app = app_run()
    browser_page(app, path)
    app.run(timeout=40)
    assert_clean(app)
    assert captured['path'] == path


def test_stuff_batch_has_four_selects_independent_of_match_count(demo_review):
    database, _ = demo_review
    seed_demo_catalogs(database)
    catalogs = load_catalogs(database, ['16.16', '16.17'])
    original = database.connection
    queries = []
    @contextmanager
    def counted():
        with original() as connection:
            connection.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
            yield connection
    database.connection = counted
    for owner in (DEMO_PUUID, DEMO_SECOND_PUUID):
        queries.clear()
        facts = load_stuff_matches(database, owner, catalogs)
        assert len(facts) > 1
        assert len(queries) == 4


@pytest.mark.parametrize('page', ['champions', 'history', 'timeline', 'contexts', 'insights', 'overview',
                                 'profiles', 'explorer', 'ai_export', 'build_lab', 'session_review', 'progression', 'journal'])
def test_cold_legacy_discovery_bootstraps_shared_router(page, monkeypatch):
    calls = []
    monkeypatch.setattr('app.main', lambda: calls.append('router'))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'pages' / (page + '.py')), run_name='__main__')
    assert calls == ['router']
