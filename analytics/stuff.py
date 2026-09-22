"""Observed item acquisitions with evidence, never inventory-derived purchase order."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

from analytics.constants import LANE_GOLD_LEAD_THRESHOLD, SHORT_GAME_THRESHOLD_SECONDS
from analytics.timeline import checkpoint, resolve_direct_opponent
from core.db import Database
from core.item_catalog import ItemCatalog, integer

DEFINITION_VERSION = 2
SUPPORTED_QUEUES = frozenset({400, 420, 430, 440, 480})
ITEM_EVENTS = frozenset({'ITEM_PURCHASED', 'ITEM_SOLD', 'ITEM_UNDO', 'ITEM_DESTROYED'})
ISSUE_LABELS = {
    'short_game': 'Partie de moins de 5 minutes',
    'unsupported_queue': 'File non qualifiée pour cette analyse',
    'missing_catalog': 'Catalogue du patch absent ou inexploitable',
    'partial_timeline': 'Frames absentes, ambiguës ou couverture partielle',
    'invalid_event': 'Événement d’objet sans ordre/temps exploitable',
    'unknown_item': 'Objet non classifiable avec ce catalogue',
    'ambiguous_undo': 'Annulation non résolue sans supposition',
    'missing_timeline': 'Timeline non disponible',
}
STATE_LABELS = {'ahead': 'En avance', 'even': 'Équilibré', 'behind': 'En retard', None: 'État indisponible'}


@dataclass(frozen=True)
class Acquisition:
    item_id: int
    timestamp_ms: int
    event_index: int


@dataclass(frozen=True)
class StuffMatch:
    match_id: str
    champion: str | None
    role: str | None
    patch: str | None
    queue_id: int | None
    win: int | None
    gold_diff_10: int | None
    personal_state_10: str | None
    major_items: tuple[Acquisition, ...]
    boots_t2: Acquisition | None
    issues: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.issues


def personal_state(gold_diff) -> str | None:
    if type(gold_diff) not in (int, float) or not math.isfinite(gold_diff):
        return None
    if gold_diff >= LANE_GOLD_LEAD_THRESHOLD:
        return 'ahead'
    if gold_diff <= -LANE_GOLD_LEAD_THRESHOLD:
        return 'behind'
    return 'even'


def _profile_participant(frames, owner):
    identifiers = {integer(f.get('participant_id'), minimum=1) for f in frames if f.get('puuid') == owner}
    return next(iter(identifiers)) if len(identifiers) == 1 and None not in identifiers else None


def analyze_stuff_match(match, participants, frames, events, owner: str, catalog: ItemCatalog | None,
                        *, include_short_games=False) -> StuffMatch:
    issues = set()
    if match.get('dropped_item_events', 0):
        issues.add('invalid_event')
    duration = integer(match.get('duration'))
    if duration is None or (duration < SHORT_GAME_THRESHOLD_SECONDS and not include_short_games):
        issues.add('short_game')
    if match.get('queue_id') not in SUPPORTED_QUEUES:
        issues.add('unsupported_queue')
    if catalog is None or catalog.patch != match.get('patch'):
        issues.add('missing_catalog')
        catalog = None
    participant_id = _profile_participant(frames, owner)
    own_times = sorted({f['timestamp_ms'] for f in frames if f.get('puuid') == owner and integer(f.get('timestamp_ms')) is not None})
    if (participant_id is None or not own_times or duration is None or own_times[0] > 60_000
            or own_times[-1] < duration * 1000 - 90_000
            or any(right - left > 90_000 for left, right in zip(own_times, own_times[1:]))):
        issues.add('partial_timeline')
    difference = None
    opponent = resolve_direct_opponent(owner, participants)
    opponent_id = _profile_participant(frames, opponent) if opponent else None
    if participant_id is not None and opponent_id is not None and duration is not None:
        mine = checkpoint(frames, participant_id, 10, duration)
        theirs = checkpoint(frames, opponent_id, 10, duration)
        if mine and theirs and mine['gold'] is not None and theirs['gold'] is not None:
            difference = mine['gold'] - theirs['gold']

    observed = []
    for event in events:
        if event.get('participant_id') != participant_id or event.get('event_type') not in ITEM_EVENTS:
            continue
        timestamp, index = integer(event.get('timestamp_ms')), integer(event.get('event_index'))
        if timestamp is None or index is None or duration is None or timestamp > duration * 1000:
            issues.add('invalid_event')
        else:
            observed.append(event)
    observed.sort(key=lambda e: (e['timestamp_ms'], e['event_index']))
    purchases: list[Acquisition | None] = []
    transactions = []
    for event in observed:
        kind = event['event_type']
        if kind == 'ITEM_DESTROYED':
            continue  # recipes/transforms consume inventory, not earlier historical acquisitions
        if kind == 'ITEM_UNDO':
            before, after = integer(event.get('item_before_id')), integer(event.get('item_after_id'))
            expected = ('ITEM_PURCHASED', before) if before and after == 0 else ('ITEM_SOLD', after) if after and before == 0 else None
            if expected is None or not transactions or transactions[-1][:2] != expected:
                issues.add('ambiguous_undo')
                continue
            operation, _item, purchase_index = transactions.pop()
            if operation == 'ITEM_PURCHASED':
                purchases[purchase_index] = None
            continue
        identifier = integer(event.get('item_id'), minimum=1)
        if identifier is None:
            issues.add('invalid_event')
            continue
        purchase_index = len(purchases) if kind == 'ITEM_PURCHASED' else None
        transactions.append((kind, identifier, purchase_index))
        if purchase_index is not None:
            purchases.append(Acquisition(identifier, event['timestamp_ms'], event['event_index']))
    major, boots, seen = [], None, set()
    for purchase in purchases:
        if purchase is None or catalog is None:
            continue
        category = catalog.classify(purchase.item_id, str(match.get('champion') or ''))
        if category == 'unknown':
            issues.add('unknown_item')
        elif category == 'major' and purchase.item_id not in seen:
            major.append(purchase)
            seen.add(purchase.item_id)
        elif category == 'boots_t2' and boots is None:
            boots = purchase
    return StuffMatch(str(match['match_id']), match.get('champion'), match.get('role'), match.get('patch'),
        match.get('queue_id'), match.get('win'), difference, personal_state(difference), tuple(major), boots, tuple(sorted(issues)))


def load_stuff_matches(database: Database, owner: str, catalogs: dict[str, ItemCatalog], *, include_short_games=False) -> dict[str, StuffMatch]:
    """Four batched SQLite reads, then pure per-match calculations."""
    matches, participants, frames, events = database.timeline_analysis_source(owner)
    grouped = []
    for rows in (participants, frames, events):
        groups = defaultdict(list)
        for row in rows:
            groups[row['match_id']].append(row)
        grouped.append(groups)
    return {row['match_id']: analyze_stuff_match(row, *(g[row['match_id']] for g in grouped), owner,
                catalogs.get(row.get('patch')), include_short_games=include_short_games) for row in matches}


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left, right = math.floor(position), math.ceil(position)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def timing_groups(matches: list[StuffMatch], *, slot: int = 1, by_state=False) -> list[dict]:
    """One source ID per observed acquisition; quartiles use linear interpolation.

    Champion, role and patch ALWAYS delimit a group, even for a mixed input.
    slot=0 denotes T2 boots; 1/2/3 denote distinct major item acquisitions.
    """
    if type(slot) is not int or slot not in {0, 1, 2, 3}:
        raise ValueError('Unsupported acquisition slot')
    groups = defaultdict(list)
    for match in matches:
        if not match.eligible:
            continue
        acquisition = match.boots_t2 if slot == 0 else match.major_items[slot - 1] if len(match.major_items) >= slot else None
        if acquisition is not None:
            state = match.personal_state_10 if by_state else None
            groups[(match.champion, match.role, match.patch, acquisition.item_id, state)].append((match, acquisition))
    results = []
    for (champion, role, patch, item_id, state), rows in groups.items():
        values = [acquisition.timestamp_ms / 1000 for _, acquisition in rows]
        ids = tuple(match.match_id for match, _ in rows)
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate source match')
        results.append({'champion': champion, 'role': role, 'patch': patch, 'item_id': item_id,
            'state': state, 'n': len(ids), 'median': quantile(values, .5), 'p25': quantile(values, .25),
            'p75': quantile(values, .75), 'source_ids': ids, 'small_sample': len(ids) < 5})
    return sorted(results, key=lambda row: (-row['n'], str(row['patch']), str(row['role']), row['item_id'], str(row['state'])))


def state_timing_comparisons(matches: list[StuffMatch], *, slot: int = 1) -> list[dict]:
    """Compare identical acquisitions; delta is ahead minus behind, in seconds.

    Unknown states remain in coverage, never in the comparison's evidence.
    Priority is sample support, not the largest observed effect.
    """
    cohorts = {}
    for group in timing_groups(matches, slot=slot, by_state=True):
        key = tuple(group[k] for k in ('champion', 'role', 'patch', 'item_id'))
        cohorts.setdefault(key, {})[group['state']] = group
    result = []
    for (champion, role, patch, item_id), states in cohorts.items():
        ahead, behind = states.get('ahead'), states.get('behind')
        if not ahead and not behind:
            continue
        sources = tuple(sorted(set((ahead or {}).get('source_ids', ()) + (behind or {}).get('source_ids', ()))))
        result.append(dict(champion=champion, role=role, patch=patch, item_id=item_id, slot=slot,
            ahead=ahead, behind=behind, even=states.get('even'),
            delta_seconds=ahead['median'] - behind['median'] if ahead and behind else None,
            comparable_source_ids=sources,
            sufficient_sample=bool(ahead and behind and min(ahead['n'], behind['n']) >= 5)))
    return sorted(result, key=lambda r: (not r['sufficient_sample'],
        -min((r['ahead'] or {}).get('n', 0), (r['behind'] or {}).get('n', 0)),
        str(r['champion']), str(r['role']), str(r['patch']), r['item_id']))


def path_groups(matches: list[StuffMatch], *, length: int = 2, state: str | None = 'all') -> list[dict]:
    if type(length) is not int or length not in {2, 3} or state not in {'all', 'ahead', 'even', 'behind', None}:
        raise ValueError('Invalid item path selection')
    groups = defaultdict(list)
    for match in matches:
        if match.eligible and len(match.major_items) >= length and (state == 'all' or match.personal_state_10 == state):
            ids = tuple(item.item_id for item in match.major_items[:length])
            groups[(match.champion, match.role, match.patch, ids)].append(match)
    result = []
    for (champion, role, patch, path), rows in groups.items():
        ids = tuple(row.match_id for row in rows)
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate source match')
        outcomes = [row.win for row in rows if row.win in (0, 1)]
        values = [row.major_items[length - 1].timestamp_ms / 1000 for row in rows]
        result.append({'champion': champion, 'role': role, 'patch': patch, 'path': path, 'n': len(ids),
            'winrate': 100 * sum(outcomes) / len(outcomes) if outcomes else None, 'outcome_n': len(outcomes),
            'median': quantile(values, .5), 'p25': quantile(values, .25), 'p75': quantile(values, .75),
            'source_ids': ids, 'small_sample': len(ids) < 5})
    return sorted(result, key=lambda row: (-row['n'], str(row['patch']), str(row['role']), row['path']))
