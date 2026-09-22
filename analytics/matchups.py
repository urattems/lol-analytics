"""Personal same-role matchup analytics from local participants and timelines."""

from __future__ import annotations

from collections import defaultdict

import polars as pl

from analytics.explorer import analysis_summary
from analytics.samples import sample_size_label
from analytics.timeline import resolve_direct_opponent


VALID_ROLES = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}


def identify_matchups(
    games: pl.DataFrame,
    participants: list[dict[str, object]],
    player_puuid: str,
) -> pl.DataFrame:
    """Add an opponent only when exactly one same-role enemy exists."""

    by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in participants:
        by_match[str(row["match_id"])].append(row)
    annotations: list[dict[str, object]] = []
    for game in games.to_dicts():
        match_id = str(game["match_id"])
        rows = by_match.get(match_id, [])
        own = [row for row in rows if row.get("puuid") == player_puuid]
        opponent_puuid = resolve_direct_opponent(player_puuid, rows)
        opponent = next(
            (row for row in rows if row.get("puuid") == opponent_puuid), None
        )
        role = str(own[0].get("role") or "").upper() if len(own) == 1 else ""
        annotations.append(
            {
                "match_id": match_id,
                "matchup_available": bool(
                    opponent and role in VALID_ROLES and opponent.get("champion")
                ),
                "opponent_puuid": opponent_puuid,
                "opponent_champion": opponent.get("champion") if opponent else None,
            }
        )
    replaceable = [
        column for column in ("matchup_available", "opponent_puuid", "opponent_champion")
        if column in games.columns
    ]
    base = games.drop(replaceable) if replaceable else games
    return base.join(pl.from_dicts(annotations, strict=False, infer_schema_length=None), on="match_id", how="left")


def _mean(frame: pl.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    value = frame.select(pl.col(column).drop_nulls().mean()).item()
    return float(value) if value is not None else None


def matchup_matrix(
    games: pl.DataFrame,
    champion: str | None = None,
    role: str | None = None,
    patch: str | None = None,
) -> list[dict[str, object]]:
    """Aggregate the player-champion × opponent-champion matrix with explicit N."""

    if games.is_empty() or "opponent_champion" not in games.columns:
        return []
    selected = games.filter(pl.col("opponent_champion").is_not_null())
    if champion:
        selected = selected.filter(pl.col("champion") == champion)
    if role:
        selected = selected.filter(pl.col("role") == role)
    if patch:
        selected = selected.filter(pl.col("patch") == patch)
    rows: list[dict[str, object]] = []
    for key, group in selected.group_by(["champion", "opponent_champion"], maintain_order=True):
        own_champion, opponent_champion = key
        summary = analysis_summary(group)
        rows.append(
            {
                "champion": own_champion,
                "opponent_champion": opponent_champion,
                "games": group.height,
                "wins": int(summary["wins"]),
                "losses": int(summary["losses"]),
                "winrate": float(summary["winrate"]),
                "kda": summary["kda"],
                "gold_diff_10": _mean(group, "gold_diff_10"),
                "gold_diff_15": _mean(group, "gold_diff_15"),
                "cs_diff_10": _mean(group, "cs_diff_10"),
                "cs_diff_15": _mean(group, "cs_diff_15"),
                "xp_diff_15": _mean(group, "xp_diff_15"),
                "sample_size": sample_size_label(group.height),
                "timeline_games": (
                    group.filter(pl.col("timeline_available") == True).height  # noqa: E712
                    if "timeline_available" in group.columns else 0
                ),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["games"]), str(row["champion"]), str(row["opponent_champion"])))


def observed_extremes(
    matrix: list[dict[str, object]], minimum_games: int = 5, limit: int = 5
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Rank eligible pairs with modest Beta(2,2) shrinkage against tiny extremes."""

    eligible = [dict(row) for row in matrix if int(row["games"]) >= minimum_games]
    for row in eligible:
        row["observed_rank"] = (float(row["wins"]) + 2.0) / (float(row["games"]) + 4.0)
    favorable = sorted(
        eligible, key=lambda row: (float(row["observed_rank"]), int(row["games"])), reverse=True
    )[:limit]
    difficult = sorted(
        eligible, key=lambda row: (float(row["observed_rank"]), -int(row["games"]))
    )[:limit]
    return favorable, difficult


def matchup_detail(games: pl.DataFrame, champion: str, opponent: str) -> dict[str, object]:
    """Return a factual detail view and patch split for one observed pair."""

    selected = games.filter(
        (pl.col("champion") == champion) & (pl.col("opponent_champion") == opponent)
    )
    matrix = matchup_matrix(selected)
    base = matrix[0] if matrix else {
        "champion": champion, "opponent_champion": opponent, "games": 0,
        "wins": 0, "losses": 0, "winrate": 0.0,
    }
    durations = selected.get_column("duration").drop_nulls() if "duration" in selected.columns else []
    patch_rows: list[dict[str, object]] = []
    if not selected.is_empty() and "patch" in selected.columns:
        for patch in selected["patch"].drop_nulls().unique().sort(descending=True).to_list():
            group = selected.filter(pl.col("patch") == patch)
            summary = analysis_summary(group)
            patch_rows.append({
                "patch": patch,
                "games": group.height,
                "winrate": float(summary["winrate"]),
                "gold_diff_15": _mean(group, "gold_diff_15"),
            })
    return {
        **base,
        "average_duration": float(sum(durations) / len(durations)) if len(durations) else None,
        "median_duration": float(durations.median()) if len(durations) else None,
        "side": _categorical_counts(selected, "side"),
        "queue": _categorical_counts(selected, "queue_id"),
        "patches": patch_rows,
        "matches": selected.to_dicts(),
    }


def _categorical_counts(frame: pl.DataFrame, column: str) -> list[dict[str, object]]:
    if frame.is_empty() or column not in frame.columns:
        return []
    return frame.group_by(column).len().rename({"len": "games"}).to_dicts()
