"""Compact names, explicit hydration and enemy navigation use real widgets."""
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from analytics.ai_bundle import build_ai_bundle
from analytics.context import build_context_dataset
from analytics.overview import get_player_matches
from analytics.session_export import build_session_package
from analytics.session_review import prepare_review_history, build_session_review
from core.identities import IdentityHydrationService
from scripts.create_demo import DEMO_PUUID
from tests.test_session_review_ui import demo_review, assert_clean, browser_page
from tests.test_history_ui import history_app, cards


def roster(app, match):
    buttons = [b for b in app.button if str(b.key).startswith('history_player_'+match+'_')]
    own = [m.value for m in app.markdown if 'class="la-roster-own"' in m.value]
    return buttons, own


@pytest.mark.parametrize('missing', [0, 3])
def test_ten_compact_labels_and_friendly_unknowns(demo_review, missing):
    db, _ = demo_review
    match = db.player_matches(DEMO_PUUID)[0]['match_id']
    participants = db.match_participants(match)
    unknown = [p['puuid'] for p in participants if p['puuid'] != DEMO_PUUID][:missing]
    with db.connection() as c:
        for puuid in unknown:
            c.execute('UPDATE participants SET teammate_game_name=NULL, teammate_tag_line=NULL WHERE puuid=?', (puuid,))
            # Registered fallback names are deliberately excluded from this fixture.
            c.execute('DELETE FROM players WHERE puuid=? AND puuid<>?', (puuid, DEMO_PUUID))
    app = history_app()
    buttons, own = roster(app, match)
    assert len(buttons) == 9 and len(own) == 20
    assert sum('Joueur non identifié' in b.label for b in buttons) == missing
    assert all('teammate_' not in b.label and 'demo-guest-' not in b.label for b in buttons)
    assert all('#' in b.label for b in buttons)
    assert all(b.proto.help and '#' in b.proto.help for b in buttons)
    assert 'history_expanded_match' not in app.session_state or app.session_state['history_expanded_match'] is None
    app.button(key='history_expand_'+match).click().run(timeout=40)
    assert len(roster(app, match)[0]) == 9  # Same actions, no duplicate identities.
    assert_clean(app)


def test_hydration_six_missing_five_recovered_rerenders_without_restart(demo_review, monkeypatch):
    db, settings = demo_review
    latest = db.player_matches(DEMO_PUUID)[0]['match_id']
    rows = db.match_participants(latest)
    ids = [p['puuid'] for p in rows if p['puuid'] != DEMO_PUUID][:6]
    with db.connection() as c:
        for puuid in ids:
            c.execute('UPDATE participants SET teammate_game_name=NULL, teammate_tag_line=NULL WHERE puuid=?', (puuid,))
            c.execute('DELETE FROM players WHERE puuid=? AND puuid<>?', (puuid, DEMO_PUUID))
    enabled = settings.model_copy(update={'demo_mode':False, 'riot_api_key':'fake-key'})
    monkeypatch.setattr('ui.profiles.get_settings', lambda: enabled)
    monkeypatch.setattr('app.get_settings', lambda: enabled)
    calls = []
    def fetch(match):
        calls.append(match)
        return {'metadata':{'matchId':match}, 'info':{'participants':[
            dict(puuid=p['puuid'], riotIdGameName=f'Recovered {ids.index(p["puuid"])}' if p['puuid'] in ids[:5] else None,
                 riotIdTagline='TEST' if p['puuid'] in ids[:5] else None)
            for p in db.match_participants(match)]}}
    def hydrate(base, owner, progress, **kwargs):
        return IdentityHydrationService(db, SimpleNamespace(get_match=fetch)).run(owner, progress, **kwargs)
    monkeypatch.setattr('ui.player_actions.hydrate_identities', hydrate)
    app = history_app()
    assert not calls
    assert sum('Joueur non identifié' in b.label for b in roster(app, latest)[0]) == 6
    app.button(key='identity_hydrate').click().run(timeout=40)
    assert_clean(app)
    assert calls
    assert any('5 identité(s) retrouvée(s) ; 1 indisponible(s)' in m.value for m in app.success)
    labels = [b.label for b in roster(app, latest)[0]]
    assert sum('Recovered ' in label for label in labels) == 5
    assert sum('Joueur non identifié' in label for label in labels) == 1
    count = len(calls)
    app.run(timeout=40)
    assert len(calls) == count  # No automatic hydration on rerun.


def test_compact_enemy_dialog_filters_opposite_camp_and_restores_page(demo_review):
    db, _ = demo_review
    app = history_app()
    app.button(key='history_next').click().run(timeout=40)
    before = cards(app)
    match = before[0]
    participants = db.match_participants(match)
    side = next(p['side'] for p in participants if p['puuid'] == DEMO_PUUID)
    other = 'red' if side == 'blue' else 'blue'
    app.button(key=f'history_player_{match}_{other}_0').click().run(timeout=40)
    target = app.session_state['identity_dialog_request'][0].puuid
    app.button(key='identity_shared').click().run(timeout=40)
    browser_page(app, 'history')
    assert_clean(app)
    assert app.selectbox(key='history_filter_opponent').value == target
    for key in cards(app):
        team = {p['puuid']:p['side'] for p in db.match_participants(key)}
        assert team[target] != team[DEMO_PUUID]
    app.button(key='history_social_return').click().run(timeout=40)
    assert_clean(app)
    assert cards(app) == before and app.session_state['history_offset'] == 20


def test_visible_local_names_stay_out_of_decompressed_full_and_session(demo_review):
    db, settings = demo_review
    app = history_app()
    match = cards(app)[0]
    labels = [p['teammate_game_name'] for p in db.match_participants(match) if p['puuid'] != DEMO_PUUID]
    assert all(any(label in b.label for b in roster(app, match)[0]) for label in labels)
    games = build_context_dataset(get_player_matches(db, DEMO_PUUID, None), db, DEMO_PUUID)
    full = build_ai_bundle(games, db, DEMO_PUUID, settings.riot_id, 'EUW1', 'EUROPE')
    session = build_session_package(build_session_review(prepare_review_history(games)), settings.riot_id)
    for blob, count in ((full, 16), (session, 2)):
        with ZipFile(BytesIO(blob)) as archive:
            assert len(archive.namelist()) == count
            text = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
            assert all(label not in text for label in labels)


def test_contexts_unknown_and_registered_fallbacks_are_human_labels(demo_review):
    from streamlit.testing.v1 import AppTest
    from scripts.create_demo import DEMO_SECOND_PUUID
    db, _ = demo_review
    with db.connection() as c:
        unknown = c.execute("SELECT puuid FROM participants WHERE teammate_game_name='Bastion Bleu' LIMIT 1").fetchone()[0]
        for puuid in [unknown, DEMO_SECOND_PUUID]:
            c.execute('UPDATE participants SET teammate_game_name=NULL,teammate_tag_line=NULL WHERE puuid=?',(puuid,))
    app = AppTest.from_string('from pages.contexts import show_contexts\nshow_contexts()').run(timeout=40)
    assert_clean(app)
    options = app.selectbox(key='contexts_teammate').options
    assert any('Demo Atlas#DEMO' in value for value in options)
    assert any('Joueur non identifié' in value for value in options)
    assert all('teammate_' not in value for value in options)
