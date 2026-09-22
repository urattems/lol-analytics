"""Opt-in sharing, separate from historical AI bundles; closed field projection.

No original match IDs, PUUIDs, raw identities, settings, paths, or opaque JSON
are serialized. Free text is additionally redacted and requires explicit review.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import io
from itertools import groupby
import json
import math
import re
import unicodedata
import zipfile

import polars as pl

from analytics.context import build_context_dataset
from analytics.exports import markdown_text, to_csv
from analytics.overview import get_player_matches
from core.db import Database
from core.journal import load_annotations


@dataclass(frozen=True)
class ShareOptions:
    performance: bool = True
    checkpoints: bool = False
    rosters: bool = False
    dates: bool = False
    annotations: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in vars(self).values()):
            raise ValueError('Options de partage invalides.')


@dataclass(frozen=True)
class SharePreview:
    # JSON strings keep the reviewed payload immutable across UI reruns.
    matches_json: str
    annotations_json: str
    options: ShareOptions
    redacted_values: int


def _identity_pattern(tokens):
    """Factor shared prefixes instead of trying every identity at every letter.

    Compressed branches avoid constructing a dictionary node for each PUUID
    character. Terminal branches remain greedy, like longest-first matching.
    """
    def branch(words, offset):
        first, last = words[0], words[-1]
        end = offset
        while end < min(len(first), len(last)) and first[end] == last[end]:
            end += 1
        prefix = re.escape(first[offset:end])
        remaining = [word for word in words if len(word) > end]
        if not remaining:
            return prefix
        alternatives = [branch(list(group), end) for _, group in groupby(remaining, lambda word: word[end])]
        tail = '(?:' + '|'.join(alternatives) + ')'
        return prefix + tail + ('?' if len(remaining) < len(words) else '')
    return branch(sorted(set(tokens)), 0) if tokens else ''


class Redactor:
    def __init__(self, identities, participants, match_ids):
        tokens = set(match_ids)
        for row in [*identities, *participants]:
            tokens.add(str(row['puuid']))
            name = row.get('game_name') or row.get('teammate_game_name')
            tag = row.get('tag_line') or row.get('teammate_tag_line')
            if name:
                tokens.add(str(name))
                if tag: tokens.add(f'{name}#{tag}')
        self.tokens = sorted((unicodedata.normalize('NFKC', t) for t in tokens if t), key=len, reverse=True)
        self.known = re.compile(_identity_pattern(self.tokens), re.IGNORECASE) if self.tokens else None
        self.changed = 0
        # Champions, roles and patches repeat across thousands of roster rows.
        # Cache only inside this export: never reuse another profile's identities.
        self.cache = {}

    def text(self, raw):
        if raw is None:
            return None
        value = unicodedata.normalize('NFKC', str(raw))
        original = value
        if original in self.cache:
            value = self.cache[original]
            self.changed += value != original
            return value
        # Normalize before matching: removing a zero-width character afterwards
        # could otherwise reconstruct an identity that escaped the matcher.
        value = ''.join(c for c in value if not unicodedata.category(c).startswith('C') or c in '\n\t')
        if self.known:
            value = self.known.sub('[identité retirée]', value)
        # Known identities alone cannot sanitize user-authored notes. Remove
        # URLs, common credential signatures and absolute path forms as well.
        patterns = (
            r'(?i)\b(?:RGAPI-|ghp_|github_pat_)[\w-]+',
            r'(?i)\b(?:bearer|api[_ -]?key|token|password|secret)\s*[:= ]\s*\S+',
            r'(?i)\bhttps?://\S+|\bfile://\S+',
            r'[A-Za-z]:[\\/][^\r\n]*|\\\\[^\r\n]*',
            r'(?<!\w)/[^\s/]+(?:/[^\s/]*)*',
            r'(?<!\w)[\w-]{40,}(?!\w)',
            r'[\w .\-]{1,32}#[\w\-]{1,16}',
        )
        for pattern in patterns:
            value = re.sub(pattern, '[contenu retiré]', value)
        value = ''.join(c for c in value if not unicodedata.category(c).startswith('C') or c in '\n\t')
        self.changed += value != original
        self.cache[original] = value
        return value


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def prepare_share(database, owner, match_ids, options=ShareOptions()):
    if not isinstance(options, ShareOptions):
        raise ValueError('Options de partage invalides.')
    if not isinstance(match_ids, (list, tuple)) or any(not isinstance(i, str) for i in match_ids) or len(set(match_ids)) != len(match_ids):
        raise ValueError('La sélection doit contenir des IDs uniques.')
    if len(match_ids) > 2500:
        raise ValueError('Le partage accepte au maximum 2 500 parties ; réduisez la sélection.')
    # One read transaction for matches, identities, timeline fields and notes.
    # Reuse all existing database reads without creating a second connection.
    with database.connection() as connection:
        connection.execute('BEGIN')
        snapshot = Database(database.path)
        @contextmanager
        def same_connection():
            yield connection
        snapshot.connection = same_connection
        if not snapshot.get_player(owner):
            raise ValueError('Profil non enregistré.')
        games = get_player_matches(snapshot, owner, None)
        canonical_ids = set(games['match_id'])
        if not set(match_ids) <= canonical_ids:
            raise ValueError('Sélection périmée ou étrangère au profil. Rechargez le journal.')
        if options.checkpoints:
            games = build_context_dataset(games, snapshot, owner)
        games = games.filter(pl.col('match_id').is_in(match_ids)).sort(['game_creation', 'match_id'], descending=[True, True], nulls_last=True)
        participants = snapshot.participants_for_player_matches(owner)
        redactor = Redactor(snapshot.list_players(), participants, canonical_ids)
        selected_ids = set(match_ids)
        aliases = {identifier: f'Joueur {i+1:02}' for i, identifier in enumerate(sorted({str(p['puuid']) for p in participants if p['puuid'] != owner and p['match_id'] in selected_ids}))}
        rosters = {}
        if options.rosters:
            for row in participants:
                rosters.setdefault(row['match_id'], []).append(row)
        notes = load_annotations(snapshot, owner) if options.annotations else {}
        rows, annotations = [], []
        for index, row in enumerate(games.to_dicts(), 1):
            reference = f'Partie {index:03}'
            result = {'match_ref': reference, 'champion': redactor.text(row['champion']), 'role': redactor.text(row['role']),
                      'patch': redactor.text(row['patch']), 'queue_id': _number(row['queue_id']), 'win': row['win'] if row['win'] in (0, 1) else None}
            if options.dates:
                result['game_creation_ms'] = row['game_creation']
            if options.performance:
                result.update({key: _number(row[key]) for key in ('duration', 'kills', 'deaths', 'assists', 'cs_total', 'vision_score', 'damage_dealt')})
            if options.checkpoints:
                result.update({key: _number(row.get(key)) for key in ('gold_diff_10', 'gold_diff_15', 'cs_diff_15')})
            if options.rosters:
                result['players'] = [{'alias': 'Profil analysé' if p['puuid'] == owner else aliases[p['puuid']],
                    'champion': redactor.text(p.get('champion')), 'role': redactor.text(p.get('role')),
                    'side': p.get('side') if p.get('side') in ('blue', 'red') else None} for p in rosters.get(row['match_id'], ())]
            rows.append(result)
            note = notes.get(row['match_id'])
            if note and (note['body'] or note['tags']):
                annotations.append({'match_ref': reference, 'note': redactor.text(note['body']), 'tags': [redactor.text(t) for t in note['tags']]})
    return SharePreview(json.dumps(rows, ensure_ascii=False, allow_nan=False),
                        json.dumps(annotations, ensure_ascii=False, allow_nan=False), options, redactor.changed)


def share_zip(preview: SharePreview, *, annotations_reviewed=False):
    if preview.options.annotations and annotations_reviewed is not True:
        raise ValueError('Relisez les annotations expurgées et confirmez leur inclusion.')
    rows, annotations = json.loads(preview.matches_json), json.loads(preview.annotations_json)
    manifest = {'schema': 'lol-analytics-share-1', 'games': len(rows), 'fields': vars(preview.options),
                'redacted_values': preview.redacted_values, 'annotation_count': len(annotations),
                'privacy': 'Identifiants retirés ; contexte et texte libre peuvent encore permettre un recoupement. Pas une garantie d’anonymat.'}
    report = ['# Revue partagée', '', f'{len(rows)} parties sélectionnées. Observations descriptives, pas des causes ni du MMR.',
              '', manifest['privacy'], '', '## Parties']
    for row in rows:
        report.append(f'- {row["match_ref"]} · {markdown_text(row["champion"] or "N/A")} · patch {markdown_text(row["patch"] or "N/A")}')
    if preview.options.annotations:
        report.extend(['', '## Annotations relues et incluses volontairement'])
        for note in annotations:
            report.extend(['', f'### {note["match_ref"]}', markdown_text(note['note']),
                           'Tags : ' + ', '.join(markdown_text(t) for t in note['tags'])])
    flat = [{k: v for k, v in row.items() if k != 'players'} for row in rows]
    csv = to_csv(pl.from_dicts(flat, infer_schema_length=None)) if flat else 'match_ref\n'
    def jsonl(values):
        return ''.join(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n' for value in values)
    files = {'manifest.json': json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2),
             'report.md': '\n'.join(report), 'matches.jsonl': jsonl(rows), 'matches.csv': csv}
    if preview.options.annotations:
        files['annotations.jsonl'] = jsonl(annotations)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode('utf-8'))
    return output.getvalue()
