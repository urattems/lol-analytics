"""Framework-independent play-session analytics from local match timestamps."""

from __future__ import annotations

from collections import Counter
import math

import polars as pl

from analytics.explorer import analysis_summary


SESSION_GAP_MINUTES = 45


def assign_sessions(games: pl.DataFrame, gap_minutes: int = SESSION_GAP_MINUTES) -> pl.DataFrame:
    """Annotate matches using the gap between previous game end and next start."""

    if gap_minutes < 1:
        raise ValueError("gap_minutes must be positive")
    if games.is_empty():
        return games
    games = games.with_columns(
        pl.when(pl.col("game_creation").is_between(0, 253370764800000))
        .then(pl.col("game_creation")).otherwise(None).alias("game_creation")
    )
    annotation_columns = {
        "session_id", "session_game_number", "session_game_bucket", "session_length",
        "session_length_bucket", "minutes_since_previous_game", "previous_result",
        "previous_champion", "champion_switch",
    }
    base = games.drop([column for column in games.columns if column in annotation_columns])
    ordered = base.sort(["game_creation", "match_id"])
    annotations: list[dict[str, object]] = []
    session_number = 0
    previous_end: int | None = None
    previous_win: int | None = None
    previous_champion: str | None = None
    session_members: dict[int, list[int]] = {}
    for index, row in enumerate(ordered.to_dicts()):
        if row.get("game_creation") is None:
            annotations.append({"match_id": row["match_id"], **{name: None for name in annotation_columns}})
            previous_end = None
            continue
        start = int(row.get("game_creation") or 0)
        try:
            raw_duration = float(row["duration"])
            duration = int(raw_duration) if math.isfinite(raw_duration) and 0 <= raw_duration <= 86400 else None
        except (KeyError, TypeError, ValueError, OverflowError):
            duration = None
        gap = None if previous_end is None else (start - previous_end) / 60_000
        if previous_end is None or gap is None or gap > gap_minutes:
            session_number += 1
            game_number = 1
            previous_result = None
            previous_champion_for_row = None
        else:
            game_number = len(session_members[session_number]) + 1
            previous_result = previous_win
            previous_champion_for_row = previous_champion
        session_members.setdefault(session_number, []).append(index)
        annotations.append(
            {
                "match_id": row["match_id"],
                "session_id": f"session_{session_number:03d}",
                "session_game_number": game_number,
                "session_game_bucket": str(game_number) if game_number < 4 else "4+",
                "minutes_since_previous_game": round(gap, 2) if gap is not None else None,
                "previous_result": (
                    "win" if previous_result == 1 else "loss" if previous_result == 0 else "first"
                ),
                "previous_champion": previous_champion_for_row,
                "champion_switch": (
                    None
                    if previous_champion_for_row is None
                    else str(row.get("champion")) != previous_champion_for_row
                ),
            }
        )
        previous_end = start + duration * 1_000 if duration is not None else None
        previous_win = 1 if row.get("win") == 1 else 0 if row.get("win") == 0 else None
        previous_champion = str(row.get("champion") or "")
    lengths = Counter(str(row["session_id"]) for row in annotations)
    for row in annotations:
        if row["session_id"] is None:
            continue
        row["session_length"] = lengths[str(row["session_id"])]
        length = int(row["session_length"])
        row["session_length_bucket"] = str(length) if length < 5 else "5+"
    annotation_frame = pl.from_dicts(annotations, strict=False, infer_schema_length=None)
    return base.join(annotation_frame, on="match_id", how="left")


def _group_metrics(games: pl.DataFrame, column: str) -> list[dict[str, object]]:
    if games.is_empty() or column not in games.columns:
        return []
    rows: list[dict[str, object]] = []
    for value in games.get_column(column).drop_nulls().unique().to_list():
        selected = games.filter(pl.col(column) == value)
        summary = analysis_summary(selected)
        row: dict[str, object] = {
            "label": value,
            "games": selected.height,
            "wins": int(summary["wins"]),
            "winrate": float(summary["winrate"]),
            "kda": summary["kda"],
            "cs_per_min": summary["cs_per_minute"],
            "damage_per_min": summary["damage_per_minute"],
        }
        for metric in ("gold_diff_10", "gold_diff_15", "deaths_before_10"):
            row[metric] = (
                selected.select(pl.col(metric).drop_nulls().mean()).item()
                if metric in selected.columns else None
            )
        rows.append(row)
    return rows


def session_overview(games: pl.DataFrame) -> dict[str, float | int]:
    """Return session count, average size and longest session."""

    if games.is_empty() or "session_id" not in games.columns:
        return {"sessions": 0, "average_games": 0.0, "longest": 0}
    lengths = games.drop_nulls("session_id").group_by("session_id").len()["len"]
    return {
        "sessions": len(lengths),
        "average_games": round(float(lengths.mean() or 0), 2),
        "longest": int(lengths.max() or 0),
    }


def session_analytics(games: pl.DataFrame) -> dict[str, object]:
    """Build all descriptive session presets from an annotated dataset."""

    return {
        "overview": session_overview(games),
        "by_game_number": _group_metrics(games, "session_game_bucket"),
        "by_session_length": _group_metrics(games, "session_length_bucket"),
        "previous_result": _group_metrics(games, "previous_result"),
        "champion_switch": _group_metrics(games, "champion_switch"),
        "streaks": session_streaks(games),
    }


def session_streaks(games: pl.DataFrame) -> list[dict[str, object]]:
    """Count consecutive W/L sequences of length two or more within sessions."""

    if games.is_empty() or "session_id" not in games.columns:
        return []
    counts: Counter[str] = Counter()
    for session in games.drop_nulls("session_id").partition_by("session_id", maintain_order=True):
        outcomes = ["W" if value == 1 else "L" for value in session.sort("session_game_number")["win"]]
        run = ""
        for outcome in outcomes:
            run = run + outcome if not run or run[-1] == outcome else outcome
            if len(run) >= 2:
                counts[run] += 1
    return [
        {"label": key, "occurrences": value}
        for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
