"""Local annotations and immutable goal definitions; all writes owner-scoped."""
from dataclasses import dataclass
import math
import time
import unicodedata
import uuid

from core.db import Database

TAGS = ('tilt', 'autofill', 'duo', 'fatigué', 'nouveau champion', 'nouveau build', 'objectif travaillé')
GOAL_METRICS = {
    'cs_per_minute': ('CS/min', 0, 50),
    'deaths_per_game': ('Morts / partie', 0, 100),
    'gold_diff_15': ('Gold diff @15', -30000, 30000),
    'vision_per_minute': ('Vision/min', 0, 50),
}


class JournalConflict(ValueError):
    """Another browser changed the record; never silently overwrite its work."""


def _stamp(clock):
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253370764800:
        raise ValueError('Horloge locale invalide.')
    return value


def _text(value, maximum, *, empty=False, multiline=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'Texte requis, {maximum} caractères maximum.')
    if any(unicodedata.category(c).startswith('C') and not (multiline and c in '\n\r\t') for c in value):
        raise ValueError('Caractères de contrôle non pris en charge.')
    return value


def normalize_tags(tags):
    if not isinstance(tags, (list, tuple)) or len(tags) > 10:
        raise ValueError('Choisissez au maximum dix tags.')
    normalized = {unicodedata.normalize('NFKC', _text(tag, 40)).strip().casefold() for tag in tags}
    return tuple(sorted(_text(tag, 40) for tag in normalized))


def _owned(connection, owner, match_id=None):
    if not connection.execute('SELECT 1 FROM players WHERE puuid=?', (owner,)).fetchone():
        raise ValueError('Ce profil ne figure plus dans la bibliothèque.')
    if match_id is not None and not connection.execute('SELECT 1 FROM participants WHERE puuid=? AND match_id=?', (owner, match_id)).fetchone():
        raise ValueError('Cette partie ne fait pas partie du profil sélectionné.')


def load_annotations(database: Database, owner: str) -> dict[str, dict]:
    with database.connection() as connection:
        notes = connection.execute('SELECT * FROM match_notes WHERE owner_puuid=?', (owner,)).fetchall()
        tags = connection.execute('SELECT match_id,tag FROM match_tags WHERE owner_puuid=? ORDER BY tag', (owner,)).fetchall()
    result = {row['match_id']: {**dict(row), 'tags': []} for row in notes}
    for row in tags:
        if row['match_id'] in result:
            result[row['match_id']]['tags'].append(row['tag'])
    return result


def save_annotation(database, owner, match_id, body, tags, expected_revision, *, clock=time.time):
    body, tags = _text(body, 5000, empty=True, multiline=True), normalize_tags(tags)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Révision invalide.')
    stamp = _stamp(clock)
    with database.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _owned(connection, owner, match_id)
        current = connection.execute('SELECT revision FROM match_notes WHERE owner_puuid=? AND match_id=?', (owner, match_id)).fetchone()
        revision = current['revision'] if current else 0
        if revision != expected_revision:
            raise JournalConflict('Annotation modifiée ailleurs. Votre brouillon est conservé ; rechargez la version enregistrée avant de réessayer.')
        connection.execute('''INSERT INTO match_notes VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_puuid,match_id) DO UPDATE SET body=excluded.body, revision=excluded.revision, updated_at=excluded.updated_at''',
            (owner, match_id, body, revision+1, stamp))
        connection.execute('DELETE FROM match_tags WHERE owner_puuid=? AND match_id=?', (owner, match_id))
        connection.executemany('INSERT INTO match_tags VALUES (?, ?, ?)', [(owner, match_id, tag) for tag in tags])
    # Empty body/tags are an invisible tombstone: revision survives deletion so
    # an old browser cannot recreate deleted content with a stale revision (ABA).
    return revision+1


@dataclass(frozen=True)
class GoalSpec:
    title: str
    metric_key: str | None = None
    comparator: str | None = None
    target_value: float | None = None
    horizon: int | None = None
    champion: str | None = None
    role: str | None = None
    queue_id: int | None = None
    patch_policy: str = 'mixed'
    patch: str | None = None
    include_short: bool = False

    def __post_init__(self):
        _text(self.title, 120)
        for value in (self.champion, self.role, self.patch):
            if value is not None: _text(value, 100)
        if self.patch_policy not in ('specific', 'mixed') or (self.patch_policy == 'specific' and not self.patch):
            raise ValueError('Choisissez un patch précis ou un mélange explicite.')
        if type(self.include_short) is not bool or (self.queue_id is not None and (type(self.queue_id) is not int or self.queue_id < 0)):
            raise ValueError('Périmètre invalide.')
        if self.metric_key is None:
            if any(v is not None for v in (self.comparator, self.target_value, self.horizon)):
                raise ValueError('Un objectif libre n’a pas de score automatique.')
        else:
            if self.metric_key not in GOAL_METRICS or self.comparator not in ('gte', 'lte') or type(self.horizon) is not int or self.horizon not in (5, 10):
                raise ValueError('Métrique, comparaison ou horizon invalide.')
            low, high = GOAL_METRICS[self.metric_key][1:]
            if type(self.target_value) not in (int, float) or not math.isfinite(self.target_value) or not low <= self.target_value <= high:
                raise ValueError('Seuil hors des bornes de la métrique.')


def create_personal_goal(database, owner, spec: GoalSpec, *, clock=time.time):
    stamp, goal_id = _stamp(clock), uuid.uuid4().hex
    with database.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _owned(connection, owner)
        marker = connection.execute('SELECT COALESCE(MAX(id),0) FROM participants WHERE puuid=?', (owner,)).fetchone()[0]
        connection.execute('''INSERT INTO personal_goals
            (goal_id,owner_puuid,title,metric_key,comparator,target_value,horizon,champion,role,queue_id,
             patch_policy,patch,include_short,baseline_participant_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (goal_id, owner, spec.title, spec.metric_key, spec.comparator, spec.target_value, spec.horizon,
             spec.champion, spec.role, spec.queue_id, spec.patch_policy, spec.patch, int(spec.include_short), marker, stamp))
    return goal_id


def load_goals(database, owner):
    with database.connection() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM personal_goals WHERE owner_puuid=? ORDER BY created_at DESC,goal_id', (owner,))]


def participant_markers(database, owner):
    with database.connection() as connection:
        return {row['match_id']: row['id'] for row in connection.execute('SELECT id,match_id FROM participants WHERE puuid=?', (owner,))}


def archive_goal(database, owner, goal_id, expected_revision, *, clock=time.time):
    stamp = _stamp(clock)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError('Révision invalide.')
    with database.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _owned(connection, owner)
        row = connection.execute('SELECT * FROM personal_goals WHERE owner_puuid=? AND goal_id=?', (owner, goal_id)).fetchone()
        if not row or row['revision'] != expected_revision or row['closed_at'] is not None:
            raise JournalConflict('Objectif modifié ou déjà archivé. Rechargez la page.')
        if stamp < row['created_at']:
            raise ValueError('L’horloge précède la création de cet objectif.')
        connection.execute('UPDATE personal_goals SET closed_at=?, revision=revision+1 WHERE goal_id=? AND owner_puuid=?', (stamp, goal_id, owner))
