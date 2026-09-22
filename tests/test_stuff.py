"""Synthetic purchases, patch metadata and exact source membership."""
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from analytics.stuff import Acquisition, StuffMatch, analyze_stuff_match, timing_groups, path_groups, personal_state
from core.item_catalog import CatalogUnavailable, fetch_catalog, load_catalogs, parse_catalog, save_catalog


def item(name, *, recipe=(), upgrades=(), total=3000, base=1000, tags=(), **extra):
    return {'name': name, 'from': list(map(str, recipe)), 'into': list(map(str, upgrades)),
            'gold': {'total': total, 'base': base, 'purchasable': True}, 'maps': {'11': True}, 'tags': list(tags), **extra}


@pytest.fixture
def catalog_payload():
    return {'version': '16.17.1', 'data': {
        '1001': item('Boots', upgrades=(3006,), total=300, base=300, tags=('Boots',)),
        '1042': item('Dagger', upgrades=(3006, 3115), total=250, base=250),
        '1058': item('Rod', upgrades=(3089,), total=1200, base=1200),
        '1082': item('Dark Seal', upgrades=(3041,), total=350, base=350),
        '3006': item('T2 boots', recipe=(1001, 1042, 1042), upgrades=(3172,), total=1100, tags=('Boots',)),
        '3172': item('T3 boots', recipe=(3006,), tags=('Boots',)),
        '3115': item('Nashor', recipe=(1042,)),
        '3089': item('Rabadon', recipe=(1058, 1058)),
        '3041': item('Mejai', recipe=(1082,), total=1500),
        '4646': item('Stormsurge', recipe=(1058,), tags=('GoldPer', 'SpellDamage')),
        '3869': item('Quest reward', recipe=(1082,), base=0, total=400, tags=('GoldPer', 'Vision')),
        '2003': item('Potion', total=50, base=50, consumed=True, tags=('Consumable',)),
        '3340': item('Trinket', total=0, base=0, tags=('Trinket',)),
        '2055': item('Ward', total=75, base=75, tags=('Consumable', 'Vision')),
    }}


@pytest.fixture
def catalog(catalog_payload):
    return parse_catalog(catalog_payload, '16.17', '16.17.1', 1000)


def purchase(item_id, second, index, kind='ITEM_PURCHASED', **extra):
    return dict(event_type=kind, participant_id=1, item_id=item_id, timestamp_ms=int(second * 1000), event_index=index, **extra)


def undo(before, after, second, index):
    return purchase(None, second, index, 'ITEM_UNDO', item_before_id=before, item_after_id=after)


def analysis(catalog, events, *, match=None, frames=None, participants=None, **kwargs):
    match = match or dict(match_id='SYNTHETIC_1', champion='Shyvana', role='JUNGLE', patch='16.17', duration=1800, queue_id=420, win=1)
    frames = frames if frames is not None else [dict(puuid=owner, participant_id=pid, timestamp_ms=minute * 60000, total_gold=500 + minute * income)
              for minute in range(31) for owner, pid, income in [('own', 1, 450), ('enemy', 2, 400)]]
    participants = participants if participants is not None else [dict(puuid='own', role='JUNGLE', side='blue'), dict(puuid='enemy', role='JUNGLE', side='red')]
    return analyze_stuff_match(match, participants, frames, events, 'own', catalog, **kwargs)


@pytest.mark.parametrize('identifier,expected', [(3115, 'major'), (3089, 'major'), (3041, 'major'), (4646, 'major'),
    (1001, 'excluded'), (3006, 'boots_t2'), (3172, 'boots_t3'), (2003, 'excluded'), (3340, 'excluded'),
    (3869, 'excluded'), (2055, 'excluded'), (999999, 'unknown')])
def test_recipe_classification_not_a_price_threshold_or_terminal_item_rule(catalog, identifier, expected):
    assert catalog.classify(identifier, 'Shyvana') == expected


def test_purchasable_recipe_successor_vs_automatic_transform(catalog_payload):
    data = catalog_payload['data']
    data['3115']['into'] = ['3089']
    assert parse_catalog(catalog_payload, '16.17', '16.17.1', 0).classify(3115, 'Shyvana') == 'component'
    data['3089']['gold']['purchasable'] = False
    assert parse_catalog(catalog_payload, '16.17', '16.17.1', 0).classify(3115, 'Shyvana') == 'major'
    data.pop('3089')
    assert parse_catalog(catalog_payload, '16.17', '16.17.1', 0).classify(3115, 'Shyvana') == 'unknown'


def test_boot_recipe_cycle_fails_closed(catalog_payload):
    catalog_payload['data']['1001']['from'] = ['3172']
    assert parse_catalog(catalog_payload, '16.17', '16.17.1', 0).classify(3006, 'Shyvana') == 'unknown'


def test_patch_catalog_roundtrip_is_offline_and_drops_opaque_html(database, catalog_payload, monkeypatch):
    catalog_payload['data']['3115']['description'] = '<script>opaque</script>'
    parsed = parse_catalog(catalog_payload, '16.17', '16.17.1', 1000)
    save_catalog(database, parsed)
    monkeypatch.setattr(httpx.Client, 'send', lambda *args, **kwargs: pytest.fail('read triggered HTTP'))
    assert load_catalogs(database, ['16.17'])['16.17'] == parsed
    assert load_catalogs(database, ['16.16']) == {}
    with database.connection() as c:
        assert 'opaque' not in c.execute('SELECT catalog_json FROM item_catalogs').fetchone()[0]
        c.execute("UPDATE item_catalogs SET catalog_json='not JSON'")
    assert load_catalogs(database, ['16.17']) == {}


def test_fetch_is_explicit_and_uses_highest_version_of_exact_patch(database, catalog_payload):
    calls = []
    def serve(request):
        calls.append(str(request.url))
        if request.url.path.endswith('versions.json'):
            return httpx.Response(200, json=['16.18.1', '16.17.1', '16.17.2', 'not/a/version'])
        return httpx.Response(200, json={**catalog_payload, 'version': '16.17.2'})
    result = fetch_catalog(database, '16.17', transport=httpx.MockTransport(serve), clock=lambda: 1000)
    assert result.version == '16.17.2' and len(calls) == 2
    assert '/cdn/16.17.2/data/fr_FR/item.json' in calls[-1]
    before = load_catalogs(database, ['16.17'])
    with pytest.raises(CatalogUnavailable):
        fetch_catalog(database, '16.15', transport=httpx.MockTransport(serve))
    assert load_catalogs(database, ['16.17']) == before
    with pytest.raises(CatalogUnavailable):
        fetch_catalog(database, '../../.env', transport=httpx.MockTransport(serve))
    assert len(calls) == 3


@pytest.mark.parametrize('value', [None, [], {'version': '16.18.1', 'data': {'1': {}}}, {'version': '16.17.1', 'data': {}}])
def test_empty_wrong_patch_or_malformed_catalog_is_unavailable(value):
    with pytest.raises(CatalogUnavailable):
        parse_catalog(value, '16.17', '16.17.1', 0)


def test_purchase_undo_sale_rebuy_and_destroy_do_not_invent_order(catalog):
    facts = analysis(catalog, [purchase(1001, 20, 0), purchase(3115, 600, 1), undo(3115, 0, 602, 2),
        purchase(3089, 700, 3), purchase(3006, 710, 4), purchase(3115, 900, 5),
        purchase(3089, 920, 6, 'ITEM_SOLD'), undo(0, 3089, 921, 7),
        purchase(3089, 1000, 8, 'ITEM_SOLD'), purchase(3089, 1100, 9),
        purchase(3115, 1200, 10, 'ITEM_DESTROYED')])
    assert facts.eligible and facts.personal_state_10 == 'ahead'
    assert [(i.item_id, i.timestamp_ms) for i in facts.major_items] == [(3089, 700000), (3115, 900000)]
    assert facts.boots_t2.item_id == 3006 and facts.boots_t2.timestamp_ms == 710000


def test_first_purchase_time_zero_is_not_missing(catalog):
    facts = analysis(catalog, [purchase(3115, 0, 1)])
    assert timing_groups([facts])[0]['median'] == 0


@pytest.mark.parametrize('event,issue', [(undo(3115, 0, 800, 3), 'ambiguous_undo'),
    (undo(3115, 1042, 800, 3), 'ambiguous_undo'), (purchase(999999, 800, 3), 'unknown_item'),
    (purchase(3115, -2, 3), 'invalid_event'), (purchase(3115, 1900, 3), 'invalid_event')])
def test_uncertain_events_are_excluded_not_filled_with_guesses(catalog, event, issue):
    facts = analysis(catalog, [purchase(3089, 700, 1), event])
    assert not facts.eligible and issue in facts.issues and timing_groups([facts]) == []


@pytest.mark.parametrize('frames', [[], [dict(puuid='own', participant_id=1, timestamp_ms=t, total_gold=500) for t in [0, 600000, 1800000]]])
def test_sparse_frames_do_not_certify_first_item(catalog, frames):
    facts = analysis(catalog, [purchase(3115, 700, 1)], frames=frames)
    assert not facts.eligible and 'partial_timeline' in facts.issues


def test_unknown_patch_short_game_and_unsupported_queue_stay_explicit(catalog):
    match = dict(match_id='M', champion='Shyvana', role='JUNGLE', patch='15.1', duration=200, queue_id=450, win=1)
    facts = analysis(catalog, [purchase(3115, 100, 1)], match=match)
    assert {'short_game', 'unsupported_queue', 'missing_catalog'} <= set(facts.issues)


@pytest.mark.parametrize('value,expected', [(500, 'ahead'), (499, 'even'), (-499, 'even'), (-500, 'behind'),
    (None, None), (float('nan'), None), (float('inf'), None), (True, None)])
def test_personal_gold_state_exact_boundaries(value, expected):
    assert personal_state(value) == expected


def test_ambiguous_opponent_does_not_become_even(catalog):
    facts = analysis(catalog, [purchase(3115, 700, 1)], participants=[dict(puuid='own', role='JUNGLE', side='blue'),
        dict(puuid='enemy', role='JUNGLE', side='red'), dict(puuid='another', role='JUNGLE', side='red')])
    assert facts.eligible and facts.gold_diff_10 is None and facts.personal_state_10 is None


def test_quartiles_source_ids_and_mixed_patches_roles(catalog):
    facts = analysis(catalog, [purchase(3115, 600, 1), purchase(3089, 1200, 2)])
    values = [replace(facts, match_id=f'M{i}', major_items=(Acquisition(3115, t * 1000, 1), *facts.major_items[1:]))
              for i, t in enumerate([600, 660, 720, 900])]
    group = timing_groups(values)[0]
    assert (group['n'], group['median'], group['p25'], group['p75']) == (4, 690, 645, 765)
    assert set(group['source_ids']) == {'M0', 'M1', 'M2', 'M3'} and group['small_sample']
    assert len(timing_groups([*values, replace(facts, match_id='other', patch='16.16'), replace(facts, match_id='top', role='TOP')])) == 3
    with pytest.raises(ValueError):
        timing_groups([facts, facts])
    reverse = replace(facts, match_id='reversed', major_items=tuple(reversed(facts.major_items)))
    groups = path_groups([facts, reverse])
    assert len(groups) == 2 and {g['path'] for g in groups} == {(3115, 3089), (3089, 3115)}
    assert path_groups([replace(facts, issues=('partial_timeline',))]) == []
    assert path_groups([facts], state='behind') == []
