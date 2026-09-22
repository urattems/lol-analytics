"""Analytical contracts, using only invented matches and offline UI fixtures."""
from dataclasses import replace

import pytest

from analytics.stuff import Acquisition, StuffMatch, state_timing_comparisons, timing_groups, load_stuff_matches
from analytics.progression import comparison_highlights
from ui.stuff import timing_delta_label
from tests.test_stuff import catalog, catalog_payload, analysis
from tests.test_ranks import rank_library
from tests.test_session_review_ui import demo_review


def cohort(ahead=5, behind=5):
    return [StuffMatch(f'{state}-{i}', 'Shyvana', 'JUNGLE', '16.17', 420, 1,
            600 if state == 'ahead' else -600, state,
            (Acquisition(3115, (600 if state == 'ahead' else 780) * 1000, 0),), None, ())
            for state, count in [('ahead', ahead), ('behind', behind)] for i in range(count)]


def test_state_comparison_exact_groups_quantiles_and_evidence():
    matches = cohort()
    matches.append(replace(matches[0], match_id='unknown', personal_state_10=None))
    row, = state_timing_comparisons(matches)
    assert row['sufficient_sample']
    assert row['delta_seconds'] == -180
    assert len(row['comparable_source_ids']) == len(set(row['comparable_source_ids'])) == 10
    assert 'unknown' not in row['comparable_source_ids']
    groups = timing_groups(matches, by_state=True)
    for state in ('ahead', 'behind'):
        assert row[state] == next(g for g in groups if g['state'] == state)


@pytest.mark.parametrize('change', [dict(champion='Ahri'), dict(role='MIDDLE'), dict(patch='16.18'),
    dict(major_items=(Acquisition(3089, 780000, 0),))])
def test_state_comparison_never_crosses_cohort_boundaries(change):
    rows = [replace(m, **change) if m.personal_state_10 == 'behind' else m for m in cohort()]
    assert all(r['delta_seconds'] is None and not r['sufficient_sample'] for r in state_timing_comparisons(rows))


def test_small_sample_and_missing_slot_do_not_become_highlights():
    assert not state_timing_comparisons(cohort(4, 10))[0]['sufficient_sample']
    assert state_timing_comparisons(cohort(), slot=2) == []


@pytest.mark.parametrize('delta,label', [(-180, '3 min plus tôt'), (-173, '2 min 53 plus tôt'),
    (72, '1 min 12 plus tard'), (0, 'des timings médians très proches'),
    (4.9, 'des timings médians très proches'), (5, '5 s plus tard')])
def test_readable_delta_boundary(delta, label):
    assert timing_delta_label(delta) == label


@pytest.mark.parametrize('timestamp', [None, -1, 'invalid', 86400001])
def test_invalid_item_time_survives_parse_and_persistence(database, riot_match_payload, catalog, timestamp):
    from core.models import parse_riot_match
    from core.timeline import parse_timeline
    from tests.test_timeline import timeline_payload
    database.insert_match(parse_riot_match(riot_match_payload))
    payload = timeline_payload()
    payload['info']['frames'][0]['events'].append(dict(type='ITEM_PURCHASED', participantId=1,
                                                      itemId=3115, timestamp=timestamp))
    parsed = parse_timeline(payload)
    assert parsed.dropped_item_events == 1
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events,
                           dropped_item_events=parsed.dropped_item_events)
    database.initialize()
    facts = load_stuff_matches(database, 'player-puuid', {'16.17': catalog})
    assert 'invalid_event' in facts[parsed.match_id].issues
    assert not facts[parsed.match_id].eligible
    with database.connection() as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_progression_highlights_use_support_and_product_order():
    def pair(n, delta):
        return {'recent': {'n': n}, 'previous': {'n': n}, 'delta': delta}
    pairs = {key: pair(10, delta) for key, delta in [('gold_diff_15', 1), ('cs_per_minute', 100),
              ('deaths_per_minute', -.1), ('vision_per_minute', 200)]}
    assert [r['key'] for r in comparison_highlights(pairs)] == ['gold_diff_15', 'cs_per_minute', 'deaths_per_minute']
    pairs['gold_diff_15'] = pair(9, 5000)
    assert [r['key'] for r in comparison_highlights(pairs)] == ['cs_per_minute', 'deaths_per_minute', 'vision_per_minute']


def test_champions_with_matches_but_no_timelines(tmp_path, monkeypatch):
    from config.settings import Settings
    from scripts.create_demo import create_demo_database
    from tests.test_ui_smoke import _offline_settings
    from streamlit.testing.v1 import AppTest
    db = create_demo_database(tmp_path / 'synthetic.db')
    with db.connection() as c:
        for table in ('timeline_events', 'timeline_frames', 'timeline_status'):
            c.execute('DELETE FROM ' + table)
    _offline_settings(monkeypatch, Settings(database_path=db.path, riot_game_name='Demo Dragon', riot_tag_line='DEMO', demo_mode=True))
    app = AppTest.from_string('from pages.champions import show_champions\nshow_champions()').run(timeout=60)
    assert not app.exception


@pytest.mark.parametrize('points,median_label', [([], 'Indisponible'), ([0], 'Iron IV · ~0 LP'),
    ([0, 20], 'Iron IV · ~10 LP'), ([0, 20, 100], 'Iron IV · ~20 LP')])
def test_rank_median_even_odd_empty_and_single(rank_library, points, median_label):
    from core.ranks import load_rank_contexts
    db, match_id = rank_library
    with db.connection() as c:
        for owner, lp in zip(('player-puuid', 'blue-ally', 'red-one'), points):
            c.execute('INSERT INTO rank_snapshots VALUES (?, ?, 420, ?, ?, ?, ?, ?)',
                      (match_id, owner, 'ranked', 'IRON', 'IV', lp, 1000 + lp))
    context = load_rank_contexts(db, 'player-puuid', [match_id])[match_id]
    assert context.median_label == median_label
    assert context.total == 4
    assert context.missing == 4 - len(points)
    assert context.observed == context.ranked == len(points)
    if points:
        assert context.minimum_label == 'Iron IV · ~0 LP'
        assert context.first_observed == 1000
        assert context.last_observed == 1000 + max(points)


def test_rank_status_accounting_malformed_and_apex(rank_library):
    from core.ranks import load_rank_contexts
    db, match_id = rank_library
    with db.connection() as c:
        for owner, status, tier, lp in [('player-puuid', 'ranked', 'CHALLENGER', 700),
                ('blue-ally', 'unranked', None, None), ('red-one', 'ranked', 'INVALID', 20)]:
            c.execute('INSERT INTO rank_snapshots VALUES (?, ?, 420, ?, ?, ?, ?, ?)',
                      (match_id, owner, status, tier, 'I', lp, 1000))
    context = load_rank_contexts(db, 'player-puuid', [match_id])[match_id]
    assert (context.total, context.observed, context.ranked, context.unranked, context.unavailable, context.missing) == (4, 3, 1, 1, 1, 1)
    assert context.own_label == 'Challenger · 700 LP'
    assert context.median_label == context.minimum_label == context.maximum_label == 'Master+ · ~700 LP'
    assert context.distribution == (('Challenger', 1),)


def test_rank_ten_players_and_offline_ui(tmp_path, monkeypatch):
    from scripts.create_demo import create_demo_database, DEMO_PUUID
    from core.ranks import load_rank_contexts
    from core.static_data import DataDragonService
    from config.settings import Settings
    from streamlit.testing.v1 import AppTest
    import httpx
    db = create_demo_database(tmp_path / 'ranks.db')
    with db.connection() as c:
        match_id = c.execute('SELECT match_id FROM matches WHERE queue_id=420 LIMIT 1').fetchone()[0]
        owners = [r[0] for r in c.execute('SELECT puuid FROM participants WHERE match_id=? ORDER BY puuid', (match_id,))]
        c.execute('DELETE FROM rank_snapshots WHERE match_id=?', (match_id,))
        for i, owner in enumerate(owners[:9]):
            status = 'ranked' if i < 7 else 'unranked' if i == 7 else 'unavailable'
            c.execute('INSERT INTO rank_snapshots VALUES (?, ?, 420, ?, ?, ?, ?, ?)',
                      (match_id, owner, status, 'GOLD' if i < 7 else None, 'IV' if i < 7 else None, 20 if i < 7 else None, 1000 + i))
    context = load_rank_contexts(db, DEMO_PUUID, [match_id])[match_id]
    assert (context.total, context.observed, context.ranked, context.unranked, context.unavailable, context.missing) == (10, 9, 7, 1, 1, 1)
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('Implicit HTTP'))
    def render(context, settings):
        from ui.ranks import render_rank_panel
        render_rank_panel(context, settings, 'synthetic')
    app = AppTest.from_function(render, args=(context, Settings(database_path=db.path, demo_mode=True))).run()
    assert not app.exception
    assert any('9 / 10 joueurs observés' in c.value for c in app.caption)


def test_stuff_card_wording_and_exact_navigation(catalog, monkeypatch):
    from streamlit.testing.v1 import AppTest
    calls = []
    monkeypatch.setattr('ui.stuff.open_champion_sources', lambda *args: calls.append(args))
    def render(matches, catalogs):
        from ui.stuff import render_state_insight
        render_state_insight(matches, catalogs, 'owner', 1, 'Toutes files')
    matches = cohort()
    matches = [replace(m, major_items=(Acquisition(3115, 540000, 0),)) if m.personal_state_10 == 'ahead' else m for m in matches]
    app = AppTest.from_function(render, args=(matches, {'16.17': catalog})).run()
    assert not app.exception
    assert any('4 min plus tôt' in m.value for m in app.markdown)
    assert any('après l’achat' in c.value for c in app.caption)
    app.button(key='champion_state_insight_sources').click().run()
    assert not app.exception
    assert calls[0][0] == 'owner'
    assert set(calls[0][1]) == {m.match_id for m in matches}
    small = AppTest.from_function(render, args=(cohort(4, 10), {'16.17': catalog})).run()
    assert any('Échantillon insuffisant' in i.value for i in small.info)
    assert not any('plus tôt' in m.value for m in small.markdown)


def test_state_insight_return_keeps_selected_comparable_item(demo_review):
    from tests.test_champion_experience import seed_demo_catalogs, champions_app, select_section
    from tests.test_session_review_ui import browser_page, assert_clean
    seed_demo_catalogs(demo_review[0])
    app = champions_app()
    app.selectbox(key='champions_dataset').set_value("Tout l'historique").run(timeout=40)
    select_section(app, 'Stuff & timings')
    app.radio(key='champion_stuff_view').set_value('État personnel @10').run(timeout=40)
    app.selectbox(key='champion_state_insight_group').set_value(1).run(timeout=40)
    app.button(key='champion_state_insight_sources').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    sources = set(app.session_state['history_evidence']['match_ids'])
    assert sources
    app.button(key='history_evidence_back').click().run(timeout=40)
    browser_page(app, 'champions')
    assert_clean(app)
    assert app.selectbox(key='champion_state_insight_group').value == 1
    assert app.radio(key='champion_stuff_view').value == 'État personnel @10'
