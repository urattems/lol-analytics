"""Exact source cohorts. Never replace a stale/foreign proof with a broad history."""
import polars as pl


class EvidenceUnavailable(ValueError):
    pass


def select_evidence(games: pl.DataFrame, owner: str, context: dict) -> pl.DataFrame:
    if not isinstance(context, dict) or context.get('owner') != owner:
        raise EvidenceUnavailable('Ces matchs sources appartiennent à un autre profil. Revenez à l’analyse d’origine.')
    identifiers = context.get('match_ids')
    if (not isinstance(identifiers, (list, tuple)) or not identifiers
            or any(not isinstance(value, str) or not value for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise EvidenceUnavailable('La sélection de matchs sources est invalide. Recalculez la statistique.')
    available = set(games['match_id'].to_list()) if 'match_id' in games.columns else set()
    if not set(identifiers) <= available:
        raise EvidenceUnavailable('La bibliothèque a changé : des matchs sources sont absents. Recalculez la statistique.')
    return games.filter(pl.col('match_id').is_in(identifiers))
