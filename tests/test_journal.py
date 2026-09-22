"""Owner isolation, CAS conflicts, atomic cleanup, goals and hostile sharing."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import io
import itertools
import json
import sqlite3
import zipfile

import polars as pl
import pytest

from analytics.journal import goal_progress, recurring_players, tag_observations
from analytics.share import ShareOptions, prepare_share, share_zip
from core.journal import (GoalSpec, JournalConflict, archive_goal, create_personal_goal,
                          load_annotations, load_goals, normalize_tags, participant_markers, save_annotation)
from core.models import RiotAccount, parse_riot_match
from tests.test_progression import games

OWNER, PEER, MATCH = 'player-puuid', 'blue-ally', 'EUW1_123456'


@pytest.fixture
def journal_library(database, riot_match_payload):
    database.upsert_player(RiotAccount(puuid=OWNER, gameName='Private Owner', tagLine='TEST'))
    database.upsert_player(RiotAccount(puuid=PEER, gameName='Private Peer', tagLine='TEST'))
    payload = deepcopy(riot_match_payload)
    for row in payload['info']['participants']:
        row.update(riotIdGameName='Secret Name ' + row['puuid'], riotIdTagline='TAG')
    database.insert_match(parse_riot_match(payload))
    return database


def test_annotations_are_per_profile_and_delete_revision_survives_aba(journal_library):
    db = journal_library
    save_annotation(db, OWNER, MATCH, '<b>private</b>', ['Tilt', 'ＴＩＬＴ', 'DUO'], 0)
    save_annotation(db, PEER, MATCH, 'different', [], 0)
    assert load_annotations(db, OWNER)[MATCH]['tags'] == ['duo', 'tilt']
    assert load_annotations(db, PEER)[MATCH]['body'] == 'different'
    with pytest.raises(JournalConflict):
        save_annotation(db, OWNER, MATCH, 'stale tab', [], 0)
    save_annotation(db, OWNER, MATCH, '', [], 1)
    assert load_annotations(db, OWNER)[MATCH]['revision'] == 2
    with pytest.raises(JournalConflict):
        save_annotation(db, OWNER, MATCH, 'resurrect', [], 1)
    assert load_annotations(db, OWNER)[MATCH]['body'] == ''
    assert db.count_matches() == 1


@pytest.mark.parametrize('bad', [None, 'x'*5001, 'secret\x00', 'bidi\u202e'])
def test_bad_notes_do_not_write(journal_library, bad):
    with pytest.raises(ValueError):
        save_annotation(journal_library, OWNER, MATCH, bad, [], 0)
    assert not load_annotations(journal_library, OWNER)


@pytest.mark.parametrize('bad', [['x']*11, ['x'*41], [' '], 'tilt', [None], ['\nsecret']])
def test_tags_are_bounded_and_canonical(bad):
    with pytest.raises(ValueError): normalize_tags(bad)


@pytest.mark.parametrize('owner,match', [('foreign', MATCH), (OWNER, 'foreign')])
def test_foreign_annotation_cannot_be_written(journal_library, owner, match):
    with pytest.raises(ValueError): save_annotation(journal_library, owner, match, 'no', [], 0)


def test_annotation_tags_fail_as_one_transaction(journal_library):
    db = journal_library
    save_annotation(db, OWNER, MATCH, 'before', ['kept'], 0)
    with db.connection() as c:
        c.execute("CREATE TRIGGER reject_tag BEFORE INSERT ON match_tags WHEN NEW.tag='blocked' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError): save_annotation(db, OWNER, MATCH, 'after', ['allowed', 'blocked'], 1)
    row = load_annotations(db, OWNER)[MATCH]
    assert row['body'] == 'before' and row['tags'] == ['kept'] and row['revision'] == 1


def test_profile_cleanup_preserves_shared_notes_and_backs_up_all_journal_rows(journal_library):
    db = journal_library
    for owner in (OWNER, PEER):
        save_annotation(db, owner, MATCH, owner + ' private', ['duo'], 0)
        create_personal_goal(db, owner, GoalSpec('my goal'), clock=lambda: 1000)
    result = db.remove_library_profile(PEER, 'Private Owner', 'TEST')
    assert result.shared_matches == 1 and result.deleted_matches == 0
    assert load_annotations(db, OWNER)[MATCH]['body'] == OWNER + ' private'
    assert load_annotations(db, PEER) == {} and load_goals(db, PEER) == []
    from core.db import Database
    assert load_annotations(Database(result.backup_path), PEER)[MATCH]['tags'] == ['duo']
    with db.connection() as c:
        c.execute('DELETE FROM matches WHERE match_id=?', (MATCH,))
        assert c.execute('SELECT COUNT(*) FROM match_notes').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM match_tags').fetchone()[0] == 0
        assert len(load_goals(db, OWNER)) == 1  # goals do not belong to a match
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_repeat_migration_preserves_notes_goals_and_tags(journal_library):
    db = journal_library
    save_annotation(db, OWNER, MATCH, 'private', ['autofill'], 0)
    create_personal_goal(db, OWNER, GoalSpec('free'), clock=lambda: 1000)
    before = (load_annotations(db, OWNER), load_goals(db, OWNER))
    for _ in range(3): db.initialize()
    assert (load_annotations(db, OWNER), load_goals(db, OWNER)) == before


@pytest.mark.parametrize('fields', [{'title': ''}, {'title': 'x'*121}, {'metric_key': 'invented'},
    {'metric_key': 'cs_per_minute', 'target_value': float('inf')}, {'horizon': True}, {'patch_policy': 'specific'},
    {'metric_key': None}, {'target_value': True}, {'comparator': 'automatic'}, {'queue_id': True}])
def test_goal_contract_rejects_unmeasurable_or_invalid_specs(fields):
    base = dict(title='CS', metric_key='cs_per_minute', comparator='gte', target_value=6.5, horizon=5)
    with pytest.raises(ValueError): GoalSpec(**(base | fields))


def test_goal_creation_marker_and_archiving_are_owner_scoped(journal_library):
    db = journal_library
    identifier = create_personal_goal(db, OWNER, GoalSpec('CS', 'cs_per_minute', 'gte', 6.5, 5), clock=lambda: 1000)
    goal, = load_goals(db, OWNER)
    assert goal['baseline_participant_id'] == participant_markers(db, OWNER)[MATCH]
    with pytest.raises(JournalConflict): archive_goal(db, PEER, identifier, 1, clock=lambda: 1100)
    with pytest.raises(ValueError): archive_goal(db, OWNER, identifier, 1, clock=lambda: 999)
    archive_goal(db, OWNER, identifier, 1, clock=lambda: 1100)
    with pytest.raises(JournalConflict): archive_goal(db, OWNER, identifier, 1, clock=lambda: 1200)
    assert load_goals(db, OWNER)[0]['closed_at'] == 1100 and db.count_matches() == 1


def goal_dict(**updates):
    return dict(title='CS', metric_key='cs_per_minute', comparator='gte', target_value=6, horizon=5,
                champion='Shyvana', role='JUNGLE', queue_id=420, patch_policy='specific', patch='16.17',
                include_short=0, created_at=1000, closed_at=None, baseline_participant_id=10) | updates


def test_next_games_never_count_backfill_known_or_future_dates_and_keep_unknown():
    frame = games(10).with_columns(pl.Series('game_creation', [None, 900000, 1000000, 1100000, 1200000, 1300000, 1400000, 1500000, 1600000, 9999999]),
        pl.Series('cs_total', [180]*5 + [None, 150, 210, 240, 180]))
    markers = {f'M{i:03}': i+10 for i in range(10)}
    result = goal_progress(frame, goal_dict(), markers, now=2000)
    assert [r['match_id'] for r in result['observed']] == ['M003','M004','M005','M006','M007']
    assert (result['successes'], result['failures'], result['unknown'], result['remaining']) == (3, 1, 1, 0)
    older = goal_progress(frame, goal_dict(baseline_participant_id=14), markers, now=2000)
    assert older['observed'][0]['match_id'] == 'M005'
    closed = goal_progress(frame, goal_dict(closed_at=1250), markers, now=2000)
    assert len(closed['observed']) == 2 and closed['remaining'] == 3


def test_goal_patch_short_scope_and_unknown_gold_not_failure():
    frame = games(7).with_columns(pl.Series('game_creation', [1100000+i*1000 for i in range(7)]),
        pl.Series('patch', ['16.16','16.17','16.17','16.17','16.17', None, '16.17']),
        pl.Series('duration', [1800, 100, 1800, 1800, 1800, 1800, 1800]), pl.lit(None).cast(pl.Float64).alias('gold_diff_15'))
    markers = {f'M{i:03}': i+11 for i in range(7)}
    result = goal_progress(frame, goal_dict(metric_key='gold_diff_15', target_value=0), markers, now=2000)
    assert len(result['observed']) == 4 and result['unknown'] == 4 and result['failures'] == 0
    assert goal_progress(frame, goal_dict(metric_key=None), markers, now=2000)['manual'] is True


def test_tag_sources_do_not_widen_selection_or_fill_missing_values():
    frame = games(3).with_columns(pl.Series('deaths', [None, 2, 3]))
    annotations = {'M000': {'tags': ['tilt']}, 'M001': {'tags': ['tilt','duo']}, 'foreign': {'tags': ['tilt']}}
    rows = tag_observations(frame.head(1), annotations)
    assert len(rows) == 1 and rows[0]['n'] == 1 and rows[0]['source_ids'] == ('M000',)
    assert rows[0]['kda']['n'] == rows[0]['deaths']['n'] == 0


def test_recurring_players_thresholds_and_context_stay_inside_selection(journal_library):
    from analytics.history import prepare_history
    from analytics.overview import get_player_matches
    db = journal_library
    library = prepare_history(get_player_matches(db, OWNER, None), db.participants_for_player_matches(OWNER), OWNER, db.list_players())
    assert recurring_players(library, library.games) == []
    with pytest.raises(ValueError): recurring_players(library, library.games, minimum=0)


@pytest.mark.parametrize('flags', list(itertools.product((False, True), repeat=5)))
def test_all_share_field_options_are_explicit_and_private_by_default(journal_library, flags):
    db = journal_library
    save_annotation(db, OWNER, MATCH, 'My deliberate private note', ['tilt'], 0)
    save_annotation(db, PEER, MATCH, 'OTHER PROFILE PRIVATE', [], 0)
    options = ShareOptions(*flags)
    preview = prepare_share(db, OWNER, [MATCH], options)
    if options.annotations:
        with pytest.raises(ValueError): share_zip(preview)
    payload = share_zip(preview, annotations_reviewed=options.annotations)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'manifest.json','report.md','matches.jsonl','matches.csv'} | ({'annotations.jsonl'} if options.annotations else set())
        raw = '\n'.join(archive.read(name).decode() for name in archive.namelist())
        assert all(token not in raw for token in (OWNER, PEER, MATCH, 'Private Owner', 'Private Peer', 'Secret Name', 'OTHER PROFILE PRIVATE'))
        assert ('My deliberate private note' in raw) is options.annotations
        row = json.loads(archive.read('matches.jsonl'))
        assert ('kills' in row) is options.performance
        assert ('players' in row) is options.rosters
        assert ('game_creation_ms' in row) is options.dates
        assert ('gold_diff_15' in row) is options.checkpoints


def test_share_redacts_hostile_free_text_and_neutralizes_csv(journal_library):
    db = journal_library
    note = f'{OWNER} Private Peer#TEST Secret Name red-one#TAG {MATCH}\nC:\\private\\secret.txt\n/etc/passwd /private.txt\nhttps://private.example/account\nRGAPI-not-a-real-key\ntoken=secret-value\n<html>[click](https://private.example)</html>'
    save_annotation(db, OWNER, MATCH, note, ['Private Peer'], 0)
    with db.connection() as c:
        c.execute('UPDATE participants SET champion=? WHERE puuid=?', ('=1+1', OWNER))
    preview = prepare_share(db, OWNER, [MATCH], ShareOptions(annotations=True, rosters=True))
    with zipfile.ZipFile(io.BytesIO(share_zip(preview, annotations_reviewed=True))) as archive:
        raw = '\n'.join(archive.read(n).decode() for n in archive.namelist())
        assert all(secret not in raw for secret in ('Private Peer','secret.txt','passwd','private.txt','private.example','RGAPI-', 'secret-value', OWNER, PEER, MATCH))
        assert '<html>' not in archive.read('report.md').decode()  # JSON text is inert; Markdown must escape HTML
        assert "'=1+1" in archive.read('matches.csv').decode()
    assert load_annotations(db, OWNER)[MATCH]['body'] == note
    assert preview.redacted_values > 0


def test_legacy_control_characters_cannot_reconstruct_a_redacted_identity(journal_library):
    save_annotation(journal_library, OWNER, MATCH, 'placeholder', [], 0)
    with journal_library.connection() as c:
        c.execute('UPDATE match_notes SET body=?', ('player-\u200bpuuid',))
    preview = prepare_share(journal_library, OWNER, [MATCH], ShareOptions(annotations=True))
    assert OWNER not in preview.annotations_json


def test_redaction_cache_is_per_export_and_counts_repeated_fields():
    from analytics.share import Redactor
    first = Redactor([{'puuid': 'private-identifier'}], [], [])
    second = Redactor([], [], [])
    assert first.text('private-identifier') == '[identité retirée]'
    assert first.text('private-identifier') == '[identité retirée]'
    assert first.changed == 2
    assert second.text('private-identifier') == 'private-identifier'
    assert second.changed == 0


def test_factored_identity_matching_keeps_full_names_and_literal_unicode():
    from analytics.share import Redactor
    scrub = Redactor([{'puuid': 'puuid-' + str(i)} for i in range(300)],
                     [{'puuid':'someone', 'game_name':'Zoé [MID]', 'tag_line':'ABC'}], ['EUW1_1','EUW1_123'])
    assert scrub.text('EUW1_123 ; EUW1_1 ; ZOÉ [MID]#abc ; puuid-299') == ' ; '.join(['[identité retirée]'] * 4)


@pytest.mark.parametrize('ids', [[MATCH, MATCH], ['foreign'], [None], MATCH])
def test_stale_or_malformed_share_cohort_is_refused(journal_library, ids):
    with pytest.raises(ValueError): prepare_share(journal_library, OWNER, ids)


def test_empty_share_and_single_read_snapshot(journal_library):
    preview = prepare_share(journal_library, OWNER, [])
    assert json.loads(preview.matches_json) == []
    original, calls = journal_library.connection, []
    @contextmanager
    def observed():
        calls.append(1)
        with original() as c: yield c
    journal_library.connection = observed
    prepare_share(journal_library, OWNER, [MATCH], ShareOptions(checkpoints=True, annotations=True))
    assert len(calls) == 1


def test_demo_journal_is_opt_in_invented_deterministic_and_never_overwrites(tmp_path):
    from scripts.create_demo import create_demo_database, DEMO_PUUID
    one = create_demo_database(tmp_path / 'one.db', journal_examples=True)
    two = create_demo_database(tmp_path / 'two.db', journal_examples=True)
    assert load_annotations(one, DEMO_PUUID) == load_annotations(two, DEMO_PUUID)
    assert load_goals(one, DEMO_PUUID) == load_goals(two, DEMO_PUUID)
    assert len(load_annotations(one, DEMO_PUUID)) == 2
    assert load_goals(one, DEMO_PUUID)[0]['source'] == 'synthetic-demo'
    with pytest.raises(FileExistsError): create_demo_database(one.path, journal_examples=True)


def test_existing_additions_survive_journal_migration(tmp_path, riot_match_payload):
    from pathlib import Path
    from core.db import Database
    from tests.test_foundation_v26 import graph, snapshot
    db = Database(tmp_path / 'before-journal.db')
    fixtures = Path(__file__).parent / 'fixtures'
    with db.connection() as connection:
        connection.executescript((fixtures / 'schema-v2.5.0.sql').read_text(encoding='utf-8'))
        connection.executescript((fixtures / 'schema-before-journal.sql').read_text(encoding='utf-8'))
    graph(db, riot_match_payload)
    with db.connection() as connection:
        connection.execute("INSERT INTO profile_rank_observations(owner_puuid,platform_region,queue_id,observed_at,status) VALUES ('main','EUW1',420,1000,'unranked')")
        connection.execute("INSERT INTO item_catalogs VALUES ('16.17','16.17.1',1000,1,'[]')")
    before = snapshot(db)
    for _ in range(3):
        db.initialize()
        after = snapshot(db)
        # The migration adds one quality counter; all previous cells remain byte-for-byte
        # equivalent, and legacy rows receive exactly the documented default.
        for table, rows in before.items():
            expected = [row + (0,) for row in rows] if table == 'timeline_status' else rows
            assert after[table] == expected
        assert all(after[table] == [] for table in ('match_notes','match_tags','personal_goals'))
        with db.connection() as connection:
            assert not connection.execute('PRAGMA foreign_key_check').fetchall()
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_two_concurrent_writers_cannot_lose_each_others_revision(journal_library):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def write(body):
        barrier.wait(timeout=5)
        try:
            save_annotation(journal_library, OWNER, MATCH, body, [], 0)
        except JournalConflict:
            return 'conflict'
        return body
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(write, label) for label in ('first writer', 'second writer')]
        results = [task.result(timeout=10) for task in tasks]
    assert results.count('conflict') == 1
    row = load_annotations(journal_library, OWNER)[MATCH]
    assert row['revision'] == 1 and row['body'] == next(r for r in results if r != 'conflict')


def test_share_snapshot_is_coherent_during_a_concurrent_wal_note_commit(journal_library, monkeypatch):
    from core.db import Database
    db = journal_library
    with db.connection() as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    save_annotation(db, OWNER, MATCH, 'before concurrent commit', [], 0)
    original = Database.participants_for_player_matches
    writes = []
    def interleaved(instance, owner):
        rows = original(instance, owner)
        if not writes:
            save_annotation(db, OWNER, MATCH, 'after concurrent commit', [], 1)
            writes.append(1)
        return rows
    monkeypatch.setattr(Database, 'participants_for_player_matches', interleaved)
    preview = prepare_share(db, OWNER, [MATCH], ShareOptions(annotations=True))
    assert 'before concurrent commit' in preview.annotations_json
    assert 'after concurrent commit' not in preview.annotations_json
    assert load_annotations(db, OWNER)[MATCH]['body'] == 'after concurrent commit'
