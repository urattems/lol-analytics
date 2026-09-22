"""Offline native navigation for home, comparison evidence, ranks and matchups."""
import polars as pl
import pytest
import streamlit as st

from analytics.progression import compare_windows, compare_metrics, ProgressionScope
from core.models import RiotAccount
from scripts.create_demo import DEMO_PUUID, DEMO_SECOND_PUUID
from tests.test_session_review_ui import demo_review, app_run, assert_clean, browser_page
from tests.test_champion_experience import seed_demo_catalogs, champions_app, select_section


def progression_app():
    app = app_run()
    browser_page(app, 'progression')
    app.run(timeout=40)
    assert_clean(app)
    return app


def section(app, name):
    app.session_state['progression_section'] = name
    app.run(timeout=40)
    assert_clean(app)


def test_comparison_exact_metric_sources_and_return_restores_filters(demo_review):
    database, _ = demo_review
    app = progression_app()
    app.selectbox(key='progression_window').set_value('20 / 50').run(timeout=40)
    app.selectbox(key='progression_patch_policy').set_value('mixed').run(timeout=40)
    app.selectbox(key='progression_metric').set_value('cs_per_minute').run(timeout=40)
    assert_clean(app)
    assert any('Mélange de patchs' in w.value for w in app.warning)
    app.button(key='progression_metric_previous').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    evidence = app.session_state['history_evidence']
    assert evidence['owner'] == DEMO_PUUID and evidence['origin'] == 'progression'
    assert not any(s.key == 'history_filter_champion' for s in app.selectbox)
    from ui.page_helpers import cached_context_dataset, database_revision
    games = cached_context_dataset(str(database.path), DEMO_PUUID, database_revision(database.path))
    scope = ProgressionScope(champion='Shyvana', role='JUNGLE', patch_policy='mixed', recent_count=20, previous_count=50)
    expected = compare_metrics(compare_windows(games, scope))['cs_per_minute']['previous']['source_ids']
    assert set(evidence['match_ids']) == set(expected)
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'progression')
    assert_clean(app)
    assert app.selectbox(key='progression_window').value == '20 / 50'
    assert app.selectbox(key='progression_patch_policy').value == 'mixed'
    assert app.selectbox(key='progression_metric').value == 'cs_per_minute'


def test_progression_rank_reads_are_offline_and_not_interpolated(demo_review):
    app = progression_app()
    section(app, 'Rang daté')
    assert app.button(key='progression_rank_capture').disabled
    assert any('Démo synthétique' in str(f.value) for f in app.dataframe)
    from core.profile_ranks import load_profile_ranks
    from ui.profile_ranks import rank_figure
    rows = load_profile_ranks(demo_review[0], DEMO_PUUID, 'EUW1', 420)
    figure = rank_figure(rows)
    assert len(figure.data[0].x) == 6 and figure.data[0].mode == 'markers'
    app.selectbox(key='progression_rank_queue').set_value(440).run(timeout=40)
    assert_clean(app)


def test_progression_empty_library_still_allows_rank_history(demo_review):
    database, _ = demo_review
    with database.connection() as c:
        c.execute('DELETE FROM matches')
    app = progression_app()
    assert any('Pas de parties datées' in message.value for message in app.info)
    section(app, 'Rang daté')
    assert any('Dernière observation' == metric.label for metric in app.metric)


def test_progression_profile_change_clears_proofs_and_saved_filters(demo_review):
    app = progression_app()
    app.button(key='progression_recent_sources').click().run(timeout=40)
    browser_page(app, 'history')
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    assert_clean(app)
    assert 'history_evidence' not in app.session_state
    assert 'progression_saved_widgets' not in app.session_state


def test_progression_timings_only_read_local_metadata(demo_review):
    database, _ = demo_review
    seed_demo_catalogs(database)
    app = progression_app()
    section(app, 'Achats & timings')
    assert app.button(key='progression_timing_recent')
    app.button(key='progression_timing_recent').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    ids = set(app.session_state['history_evidence']['match_ids'])
    assert ids <= {r['match_id'] for r in database.player_matches(DEMO_PUUID)}


def test_home_observation_has_exact_sources_and_returns_home(demo_review):
    app = app_run()
    assert_clean(app)
    assert app.button(key='overview_observation')
    app.run(timeout=40)
    assert_clean(app)
    assert app.button(key='overview_observation')
    app.button(key='overview_observation').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    evidence = app.session_state['history_evidence']
    assert evidence['origin'] == 'overview' and len(evidence['match_ids']) >= 20
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'overview')
    assert_clean(app)
    assert app.button(key='overview_review')


def test_home_missing_timelines_link_only_the_announced_ids(demo_review):
    database, _ = demo_review
    with database.connection() as c:
        c.execute("UPDATE timeline_status SET status='missing' WHERE match_id IN ('DEMO_000072', 'DEMO_000071')")
    app = app_run()
    assert_clean(app)
    app.button(key='overview_missing').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert set(app.session_state['history_evidence']['match_ids']) == {'DEMO_000072', 'DEMO_000071'}


def test_matchup_medians_and_first_item_sources(demo_review):
    seed_demo_catalogs(demo_review[0])
    app = champions_app()
    select_section(app, 'Matchups')
    assert any('Historique limité' in row.value for row in app.info)
    assert app.button(key='champion_matchup_metric_sources')
    assert app.selectbox(key='champion_matchup_timing_group')
    app.button(key='champion_matchup_metric_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert app.session_state['history_evidence']['origin'] == 'champions'


def test_unknown_profile_progression_is_recoverable(demo_review):
    database, _ = demo_review
    database.upsert_player(RiotAccount(puuid='empty-c', gameName='Empty C', tagLine='DEMO'))
    app = progression_app()
    app.selectbox(key='profile_selector').set_value('empty-c').run(timeout=40)
    assert_clean(app)
    section(app, 'Rang daté')
    assert any('Aucun rang personnel' in row.value for row in app.info)


def test_missing_patch_never_blocks_personal_rank_tab(demo_review):
    with demo_review[0].connection() as connection:
        connection.execute('DELETE FROM matches')
    app = progression_app()
    app.selectbox(key='progression_patch_policy').set_value('specific').run(timeout=40)
    assert_clean(app)
    section(app, 'Rang daté')
    assert app.button(key='progression_rank_capture').disabled


def test_rank_tab_skips_match_analytics_and_restores_comparison_controls(demo_review, monkeypatch):
    app = progression_app()
    app.selectbox(key='progression_window').set_value('20 / 50').run(timeout=40)
    with monkeypatch.context() as patch:
        patch.setattr('pages.progression.cached_context_dataset', lambda *a: pytest.fail('match analytics loaded on rank tab'))
        section(app, 'Rang daté')
        assert not any(s.key == 'progression_champion' for s in app.selectbox)
    section(app, 'Comparaison')
    assert app.selectbox(key='progression_window').value == '20 / 50'


def test_rank_button_starts_only_the_selected_profiles_queue_job(demo_review, monkeypatch):
    from core.import_jobs import JobStore
    from tests.test_ui_smoke import _offline_settings
    database, demo_settings = demo_review
    settings = demo_settings.model_copy(update={'demo_mode': False, 'riot_api_key': 'synthetic-only'})
    _offline_settings(monkeypatch, settings)
    monkeypatch.setattr('app.get_settings', lambda: settings)
    calls = []
    def fake_start(active, kind, target, *, owner):
        calls.append((owner, kind, target, active.riot_game_name))
        return JobStore(database).create(active, kind, target, owner=owner)
    monkeypatch.setattr('core.import_jobs.start_import_job', fake_start)
    app = progression_app()
    app.selectbox(key='profile_selector').set_value(DEMO_SECOND_PUUID).run(timeout=40)
    section(app, 'Rang daté')
    app.selectbox(key='progression_rank_queue').set_value(440).run(timeout=40)
    assert not app.button(key='progression_rank_capture').disabled
    assert calls == []
    app.button(key='progression_rank_capture').click().run(timeout=40)
    assert_clean(app)
    assert calls == [(DEMO_SECOND_PUUID, 'profile_rank', 440, 'Demo Atlas')]
    assert JobStore(database).pending()[0]['puuid'] == DEMO_SECOND_PUUID
