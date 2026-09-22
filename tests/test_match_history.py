"""Visual match-history contracts (separate from historical backfill tests)."""
from copy import deepcopy
import json
import io
import zipfile

import pytest

from analytics.history import prepare_history, inventory
from analytics.explorer import ExplorerSelection
from analytics.overview import get_player_matches
from analytics.session_review import build_session_review, prepare_review_history
from analytics.exports import build_full_export_payload
from analytics.ai_bundle import build_ai_bundle
from core.models import parse_riot_match


@pytest.mark.parametrize('count', [0, 1, 20, 500])
def test_history_batches_have_exact_local_rows(database, riot_match_payload, count):
    for i in range(count):
        payload = deepcopy(riot_match_payload)
        payload['metadata']['matchId'] = f'TEST_{i:05}'
        payload['info']['gameCreation'] += i * 2400000
        database.insert_match(parse_riot_match(payload))
    games = get_player_matches(database, 'player-puuid', None)
    library = prepare_history(games, database.participants_for_player_matches('player-puuid'), 'player-puuid')
    selected = library.select(ExplorerSelection(base_game_limit=None))
    assert selected.height == count
    cards = library.batch(selected)
    assert len(cards) == min(20, count)
    for card in cards:
        actual = next(row for row in games.to_dicts() if row['match_id'] == card.match_id)
        for key in ('champion', 'kills', 'deaths', 'assists', 'duration', 'win'):
            assert card.facts[key] == actual[key]
        assert len(card.players) == 4 and sum(p.own for p in card.players) == 1
        assert [item.item_id for item in card.items] == json.loads(actual['items'])
        assert not card.context.timeline_available
    assert library.select(ExplorerSelection(base_game_limit=None), teammate='blue-ally').height == count
    assert library.select(ExplorerSelection(base_game_limit=None), teammate='red-one').is_empty()
    assert library.select(ExplorerSelection(base_game_limit=None), opponent='red-one').height == count
    assert library.select(ExplorerSelection(base_game_limit=None), opponent='foreign').is_empty()
    if count > 20:
        assert {c.match_id for c in cards}.isdisjoint(c.match_id for c in library.batch(selected, 20))


def test_short_filter_keeps_session_membership_and_full_export(database, riot_match_payload):
    start = 1700000000000
    for i, duration in enumerate([1800, 180, 1800, 1800, 240, 1800]):
        payload = deepcopy(riot_match_payload)
        payload['metadata']['matchId'] = f'TEST_{i}'
        payload['info'].update(gameCreation=start, gameDuration=duration)
        start += (duration + 2400) * 1000  # <45 min individually, >45 if short game removed
        database.insert_match(parse_riot_match(payload))
    games = get_player_matches(database, 'player-puuid', None)
    library = prepare_history(games, database.participants_for_player_matches('player-puuid'), 'player-puuid')
    assert library.select(ExplorerSelection(base_game_limit=None)).height == 4
    assert library.select(ExplorerSelection(base_game_limit=None, include_short_games=True)).height == 6
    assert len(library.anchors) == 1
    review = build_session_review(prepare_review_history(games))
    assert review['summary']['games'] == 6 and review['analysis_summary']['games'] == 4
    assert review['metrics']['kda_mean']['n'] == 4 and review['excluded_short_games'] == 2
    assert [r['session_game_number'] for r in review['matches']] == list(range(1, 7))
    payload = build_full_export_payload(games, 'Synthetic#TEST', 'EUW1', 'EUROPE')
    assert len(payload['matches']) == 6 and payload['selection']['filters'] == {}
    with zipfile.ZipFile(io.BytesIO(build_ai_bundle(games, database, 'player-puuid', 'Synthetic#TEST', 'EUW1', 'EUROPE'))) as z:
        assert len(z.read('matches.jsonl').splitlines()) == 6


@pytest.mark.parametrize('value', [None, 'bad json', '{}', 'null', '[null,-1,true,123,0,999999,3340]'])
def test_inventory_retains_missing_and_trinket(value):
    result = inventory(value)
    assert len(result) == 7 and result[-1].trinket and not any(i.trinket for i in result[:6])
    if value and value.startswith('[null'):
        assert [i.item_id for i in result] == [None, None, None, 123, 0, 999999, 3340]


def test_history_html_offline_fallback_escapes_labels_and_never_displays_puuid(monkeypatch):
    from analytics.history import HistoryMatch, HistoryContext
    from core.static_data import DataDragonService
    from ui.history_cards import card_html
    service = DataDragonService()
    monkeypatch.setattr(service, '_safe_json', lambda *a: None)
    monkeypatch.setattr('ui.history_cards.get_static_data_service', lambda: service)
    facts = dict(champion='<script>alert(1)</script>', role='UNKNOWN', queue_id=999999,
                 duration=None, game_creation=None, win=None, kills=None, deaths=None, assists=None)
    card = HistoryMatch('SYNTHETIC', facts, (), HistoryContext(None, None, False), inventory('[999999,0,null]'))
    html = card_html(card)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'N/A' in html and '999999' in html and 'Date indisponible' in html
    service.close()


def test_expanded_item_events_are_owner_scoped(tmp_path):
    from scripts.create_demo import create_demo_database, DEMO_PUUID
    db = create_demo_database(tmp_path / 'synthetic.db')
    events = db.history_item_events(DEMO_PUUID, 'DEMO_000001')
    assert events and all(e['timestamp_ms'] >= 0 for e in events)
    assert events == sorted(events, key=lambda e: e['timestamp_ms'])
    assert db.history_item_events('foreign-player', 'DEMO_000001') == []
    assert db.history_item_events(DEMO_PUUID, 'FOREIGN_MATCH') == []
    with db.connection() as c:
        c.execute("UPDATE timeline_frames SET puuid=? WHERE match_id=? AND participant_id=?", (DEMO_PUUID, 'DEMO_000001', 3))
    assert db.history_item_events(DEMO_PUUID, 'DEMO_000001') == []


def test_short_only_session_has_no_analytical_sample_or_highlight():
    import polars as pl
    from tests.test_session_review import game
    history = prepare_review_history(pl.from_dicts([game(0, duration=180), game(1, duration=240)]))
    review = build_session_review(history)
    assert review['summary']['games'] == 2 and len(review['matches']) == 2
    assert review['analysis_summary']['games'] == 0
    assert review['analysis_summary']['match_ids'] == []
    assert review['analysis_summary']['play_seconds'] == 0
    assert review['highlights'] == [] and review['comparisons'] == []
    assert all(m['n'] == 0 and m['value'] is None for m in review['metrics'].values())
    assert all(p['n'] == 0 and p['median'] is None for p in review['phases'])
    assert build_session_review(history, include_short_games=True)['analysis_summary']['games'] == 2


def test_context_null_is_unavailable_not_no_objective():
    from ui.formatting import context_label
    assert context_label(None) == 'N/A'
    assert context_label('none') == 'Aucun'
