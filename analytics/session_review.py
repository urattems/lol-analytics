"""Local, descriptive session reviews. No database, network, UI or coaching."""
from __future__ import annotations

from collections import Counter
import math
from statistics import mean, median
from typing import Any

import polars as pl

from analytics.overview import per_match_kda_expression, per_minute_expression
from analytics.sessions import SESSION_GAP_MINUTES, assign_sessions
from analytics.constants import DEFAULT_INCLUDE_SHORT_GAMES, SHORT_GAME_THRESHOLD_SECONDS


METRICS = {
    "kda_mean": ("review_kda", "mean"), "kda_median": ("review_kda", "median"),
    "cs_per_min": ("review_cs", "mean"), "gold_per_min": ("review_gold", "mean"),
    "damage_per_min": ("review_damage", "mean"), "vision_per_min": ("review_vision", "mean"),
    "gold_diff_15": ("gold_diff_15", "median"),
}


def finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def prepare_review_history(games: pl.DataFrame, gap_minutes: int = SESSION_GAP_MINUTES) -> pl.DataFrame:
    """Use the existing session engine once on the entire active profile history."""
    prepared = assign_sessions(games, gap_minutes)
    if prepared.is_empty():
        return prepared
    required = ("duration", "kills", "deaths", "assists", "cs_total", "gold_earned", "damage_dealt", "vision_score")
    prepared = prepared.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in required if c not in prepared.columns])
    prepared = prepared.with_columns(pl.when(pl.col("duration").cast(pl.Float64).is_finite() & pl.col("duration").is_between(0, 86400))
                                     .then(pl.col("duration")).otherwise(None).alias("duration"))
    return prepared.with_columns(
        per_match_kda_expression().alias("review_kda"),
        *[per_minute_expression(source).alias(target) for source, target in (
            ("cs_total", "review_cs"), ("gold_earned", "review_gold"),
            ("damage_dealt", "review_damage"), ("vision_score", "review_vision"))],
    )


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    champions = Counter(str(row.get("champion") or "Inconnu") for row in rows)
    starts = [int(row["game_creation"]) for row in rows]
    durations = [finite(row.get("duration")) for row in rows]
    durations = [value if value is not None and value >= 0 else None for value in durations]
    wins = sum(row.get("win") == 1 for row in rows)
    losses = sum(row.get("win") == 0 for row in rows)
    return {
        "session_id": rows[0]["session_id"], "anchor_match_id": rows[0]["match_id"],
        "match_ids": [row["match_id"] for row in rows], "games": len(rows),
        "start_ms": min(starts),
        "end_ms": max(start + int(duration * 1000) for start, duration in zip(starts, durations))
            if all(duration is not None for duration in durations) else None,
        "play_seconds": sum(durations) if all(duration is not None for duration in durations) else None,
        "wins": wins, "losses": losses, "unknown_results": len(rows) - wins - losses,
        "winrate": 100 * wins / (wins + losses) if wins + losses else None,
        "champions": dict(champions.most_common()),
        "roles": sorted({str(row.get("role") or "UNKNOWN") for row in rows}),
        "queues": sorted({int(row["queue_id"]) for row in rows if row.get("queue_id") is not None}),
        "timeline_games": sum(bool(row.get("timeline_available")) for row in rows),
    }


def session_catalog(prepared: pl.DataFrame) -> list[dict[str, Any]]:
    """Recent sessions, ordered deterministically. Labels stay a UI concern."""
    if prepared.is_empty() or "session_id" not in prepared.columns:
        return []
    groups = prepared.drop_nulls("session_id").sort(["game_creation", "match_id"]).partition_by("session_id", maintain_order=True)
    return list(reversed([_summary(group.to_dicts()) for group in groups]))


def resolve_session(catalog: list[dict[str, Any]], anchor_match_id: str | None = None) -> dict[str, Any] | None:
    """Match anchors survive historical backfill renumbering internal session IDs."""
    return next((s for s in catalog if anchor_match_id in s["match_ids"]), catalog[0] if catalog else None)


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics = {}
    for name, (column, operation) in METRICS.items():
        values = [finite(row.get(column)) for row in rows
                  if column != "gold_diff_15" or row.get("timeline_available")]
        values = [value for value in values if value is not None]
        metrics[name] = {"value": (median(values) if operation == "median" else mean(values)) if values else None,
                         "n": len(values), "aggregation": operation}
    return metrics


def baseline_comparisons(prepared: pl.DataFrame, session: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Previous <=20 games of the exact champion/role, strictly before session start."""
    prior = prepared.filter(pl.col("game_creation") < session["start_ms"]).sort(["game_creation", "match_id"], descending=[True, True])
    history: dict[tuple[object, object], list[dict[str, Any]]] = {}
    for row in prior.to_dicts():
        key = (row.get("champion"), row.get("role"))
        bucket = history.setdefault(key, [])
        if len(bucket) < 20:
            bucket.append(row)
    groups: dict[tuple[object, object], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row.get("champion"), row.get("role")), []).append(row)
    comparisons = []
    for (champion, role), current in groups.items():
        baseline = history.get((champion, role), [])
        comparisons.append({
            "champion": champion, "role": role, "session_n": len(current), "baseline_n": len(baseline),
            "baseline_match_ids": [row["match_id"] for row in baseline],
            "scope": "Même champion et même rôle · parties strictement antérieures à la session",
            "baseline_label": "20 parties précédentes" if len(baseline) == 20 else "Historique antérieur disponible",
            "eligible": len(current) >= 2 and len(baseline) >= 5 and role not in (None, "", "UNKNOWN"),
            "session": metric_summary(current), "baseline": metric_summary(baseline),
            "wins": sum(row.get("win") == 1 for row in current), "losses": sum(row.get("win") == 0 for row in current),
        })
    return sorted(comparisons, key=lambda c: (-c["session_n"], str(c["champion"]), str(c["role"])))


def session_highlights(summary: dict[str, Any], rows: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank explicit descriptive rules. Never infer causality or psychological state."""
    candidates = []
    def add(key, score, text, n, match_id=None):
        candidates.append({"kind": key, "score": score, "text": text, "n": n, "match_id": match_id})
    known = summary["wins"] + summary["losses"]
    if known:
        wins, losses = summary["wins"], summary["losses"]
        add("results", 70, f"Sur cette session : {wins} victoire{'s' if wins != 1 else ''} et {losses} défaite{'s' if losses != 1 else ''}, sur {known} résultat{'s' if known != 1 else ''} connu{'s' if known != 1 else ''}.", known)
    if summary["champions"]:
        champion, n = next(iter(summary["champions"].items()))
        if n >= 2:
            add("champion", 45, f"{champion} représente {n} des {summary['games']} parties de cette session.", n)
    early = [r for r in rows if r.get("timeline_available") and finite(r.get("deaths_before_10")) is not None]
    if len(early) >= 2:
        count = sum(r["deaths_before_10"] == 0 for r in early)
        add("early_deaths", 65, f"{count}/{len(early)} parties observées sans mort avant 10 minutes.", len(early))
    trajectory_rows = [r for r in rows if r.get("timeline_available") and r.get("gold_state_10") and r.get("gold_state_20")]
    comebacks = [r for r in trajectory_rows if r["gold_state_10"] == "BEHIND" and r["gold_state_20"] == "AHEAD"]
    if len(comebacks) == 1 and len(trajectory_rows) >= 2:
        row = comebacks[0]
        add("trajectory", 90, f"Partie {row['session_game_number']} : seul passage d’une équipe en retard à 10 min à une équipe en avance à 20 min, parmi {len(trajectory_rows)} parties comparables.", len(trajectory_rows), row["match_id"])
    for comparison in comparisons:
        current = comparison["session"]["gold_diff_15"]
        baseline = comparison["baseline"]["gold_diff_15"]
        if comparison["eligible"] and current["n"] >= 2 and baseline["n"] >= 5:
            delta = current["value"] - baseline["value"]
            if abs(delta) >= 100:
                add("baseline_gold", 75 + min(abs(delta) / 1000, 5),
                    f"{comparison['champion']} ({comparison['role']}) : GD @15 médian {current['value']:+.0f}g sur {current['n']} parties, contre {baseline['value']:+.0f}g sur {baseline['n']} parties antérieures comparables.", current["n"])
    durations = [r for r in rows if finite(r.get("duration")) is not None and r["duration"] >= 0]
    if durations:
        longest = max(durations, key=lambda r: r["duration"])
        qualifier = ", durée la plus longue de cette session" if len(rows) > 1 else ""
        add("duration", 20, f"Partie {longest['session_game_number']} : {int(longest['duration']) // 60} min {int(longest['duration']) % 60:02d}{qualifier}.", len(durations), longest["match_id"])
    return sorted(candidates, key=lambda item: (-item["score"], item["kind"]))[:5]


def build_session_review(prepared: pl.DataFrame, anchor_match_id: str | None = None,
                         *, include_short_games: bool = DEFAULT_INCLUDE_SHORT_GAMES) -> dict[str, Any] | None:
    """Build one review from a prepared profile dataset, with no additional IO."""
    summary = resolve_session(session_catalog(prepared), anchor_match_id)
    if summary is None:
        return None
    selected = prepared.filter(pl.col("session_id") == summary["session_id"]).sort(["game_creation", "match_id"])
    rows = selected.to_dicts()
    eligible = lambda row: include_short_games or finite(row.get("duration")) is None or row["duration"] >= SHORT_GAME_THRESHOLD_SECONDS
    analytical_rows = [row for row in rows if eligible(row)]
    analytical_history = prepared if include_short_games else prepared.filter(pl.col("duration").is_null() | (pl.col("duration") >= SHORT_GAME_THRESHOLD_SECONDS))
    analysis_summary = _summary(analytical_rows) if analytical_rows else {
        **summary, "games": 0, "wins": 0, "losses": 0, "unknown_results": 0,
        "winrate": None, "champions": {}, "timeline_games": 0,
        "match_ids": [], "play_seconds": 0, "roles": [], "queues": [],
        "start_ms": None, "end_ms": None,
    }
    comparisons = baseline_comparisons(analytical_history, summary, analytical_rows)
    phases = []
    for minute in (10, 15, 20):
        values = [finite(row.get(f"team_gold_diff_{minute}")) for row in analytical_rows if row.get("timeline_available")]
        values = [value for value in values if value is not None]
        phases.append({"minute": minute, "median": median(values) if values else None, "n": len(values)})
    standout = [row for row in analytical_rows if row.get("timeline_available") and finite(row.get("gold_diff_15")) is not None]
    remarkable = max(standout, key=lambda r: abs(r["gold_diff_15"])) if len(standout) >= 2 else None
    return {"summary": summary, "matches": rows, "metrics": metric_summary(analytical_rows),
            "analysis_summary": analysis_summary, "excluded_short_games": len(rows) - len(analytical_rows),
            "comparisons": comparisons, "phases": phases,
            "highlights": session_highlights(analysis_summary, analytical_rows, comparisons),
            "remarkable_match_id": remarkable["match_id"] if remarkable else None,
            "remarkable_reason": "Plus grand écart absolu de gold face à l’adversaire direct à 15 min, parmi les parties observées." if remarkable else None}
