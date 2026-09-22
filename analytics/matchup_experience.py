"""Observed opponents with robust checkpoints, no 'counter' classification."""
import polars as pl

from analytics.progression import summarize_metric
from analytics.stuff import personal_state


def matchup_observations(games: pl.DataFrame) -> list[dict]:
    if games.is_empty() or 'opponent_champion' not in games.columns:
        return []
    rows = []
    identified = games.filter(pl.col('opponent_champion').is_not_null())
    for opponent, group in identified.group_by('opponent_champion'):
        states = {'ahead': 0, 'even': 0, 'behind': 0, None: 0}
        for row in group.to_dicts():
            states[personal_state(row.get('gold_diff_10'))] += 1
        rows.append({'opponent': opponent[0], 'n': group.height,
                     'wins': group.filter(pl.col('win') == 1).height, 'losses': group.filter(pl.col('win') == 0).height,
                     'source_ids': tuple(group['match_id']), 'states': states,
                     'metrics': {key: summarize_metric(group, key) for key in ('kda', 'gold_diff_10', 'gold_diff_15', 'cs_diff_15')}})
    return sorted(rows, key=lambda row: (-row['n'], row['opponent']))
