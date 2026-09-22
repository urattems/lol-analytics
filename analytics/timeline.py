"""Framework-independent temporal analytics derived from persisted Riot frames."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable

import polars as pl

from analytics.constants import (
    CHECKPOINT_MINUTES,
    CHECKPOINT_TOLERANCE_MS,
    TEAM_GOLD_LEAD_THRESHOLD,
)
from core.db import Database


def _participant_id(frames: Iterable[dict[str, object]], puuid: str) -> int | None:
    values = {
        int(frame["participant_id"])
        for frame in frames
        if frame.get("puuid") == puuid and frame.get("participant_id") is not None
    }
    return next(iter(values)) if len(values) == 1 else None


def resolve_direct_opponent(
    player_puuid: str, participants: list[dict[str, object]]
) -> str | None:
    """Resolve exactly one opposing participant with the same declared role."""

    player_rows = [row for row in participants if row.get("puuid") == player_puuid]
    if len(player_rows) != 1:
        return None
    player = player_rows[0]
    role = str(player.get("role") or "").upper()
    side = player.get("side")
    if not role or role == "UNKNOWN" or side not in {"blue", "red"}:
        return None
    opponents = [
        row
        for row in participants
        if row.get("side") in {"blue", "red"}
        and row.get("side") != side
        and str(row.get("role") or "").upper() == role
        and isinstance(row.get("puuid"), str)
    ]
    return str(opponents[0]["puuid"]) if len(opponents) == 1 else None


def checkpoint(
    frames: list[dict[str, object]],
    participant_id: int,
    minute: int,
    duration_seconds: int,
    tolerance_ms: int = CHECKPOINT_TOLERANCE_MS,
) -> dict[str, int | None] | None:
    """Return the nearest real frame, never a final-frame substitute or interpolation."""

    requested = minute * 60_000
    if duration_seconds * 1_000 < requested:
        return None
    candidates = [
        frame
        for frame in frames
        if frame.get("participant_id") == participant_id
        and frame.get("timestamp_ms") is not None
    ]
    if not candidates:
        return None
    selected = min(candidates, key=lambda frame: abs(int(frame["timestamp_ms"]) - requested))
    actual = int(selected["timestamp_ms"])
    if abs(actual - requested) > tolerance_ms:
        return None
    return {
        "requested_timestamp_ms": requested,
        "actual_frame_timestamp_ms": actual,
        "gold": _int_or_none(selected.get("total_gold")),
        "cs": _int_or_none(selected.get("cs_total")),
        "xp": _int_or_none(selected.get("xp")),
        "level": _int_or_none(selected.get("level")),
    }


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def event_item_id(event: dict[str, object]) -> int | None:
    """Resolve the item represented by purchase/sale/undo without emitting Item 0."""

    preferred = (
        event.get("item_before_id")
        if event.get("event_type") == "ITEM_UNDO"
        else event.get("item_id")
    )
    value = _int_or_none(preferred)
    if value is None and event.get("event_type") == "ITEM_UNDO":
        value = _int_or_none(event.get("item_id"))
    return value if value is not None and value > 0 else None


def _difference(left: int | None, right: int | None) -> int | None:
    return left - right if left is not None and right is not None else None


def _extra(event: dict[str, object]) -> dict[str, Any]:
    raw = event.get("extra_json")
    if not isinstance(raw, str):
        return {}
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _team_gold_at(
    frames: list[dict[str, object]],
    participant_ids: set[int],
    minute: int,
    duration_seconds: int,
) -> int | None:
    values: list[int] = []
    for participant_id in participant_ids:
        point = checkpoint(frames, participant_id, minute, duration_seconds)
        if point is None or point["gold"] is None:
            return None
        values.append(int(point["gold"]))
    return sum(values) if values else None


def _event_metrics(
    events: list[dict[str, object]], participant_id: int
) -> dict[str, object]:
    kills = [event for event in events if event.get("event_type") == "CHAMPION_KILL" and event.get("killer_id") == participant_id]
    deaths = [event for event in events if event.get("event_type") == "CHAMPION_KILL" and event.get("victim_id") == participant_id]
    purchases = [
        {
            "timestamp_ms": _int_or_none(event.get("timestamp_ms")),
            "event_type": event.get("event_type"),
            "item_id": event_item_id(event),
            "item_before_id": _int_or_none(event.get("item_before_id")),
            "item_after_id": _int_or_none(event.get("item_after_id")),
            "extra": _extra(event),
        }
        for event in events
        if event.get("participant_id") == participant_id
        and event.get("event_type") in {"ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO"}
    ]
    first_blood = False
    for event in events:
        if event.get("event_type") != "CHAMPION_KILL":
            continue
        extra = _extra(event)
        participants = [event.get("killer_id"), *extra.get("assistingParticipantIds", [])]
        if extra.get("killType") == "KILL_FIRST_BLOOD" and participant_id in participants:
            first_blood = True
            break
    objectives = [
        {
            "timestamp_ms": _int_or_none(event.get("timestamp_ms")),
            "event_type": event.get("event_type"),
            "team_id": _int_or_none(event.get("team_id")),
            "monster_type": event.get("monster_type"),
            "monster_subtype": event.get("monster_subtype"),
            "building_type": event.get("building_type"),
            "tower_type": event.get("tower_type"),
            "lane_type": event.get("lane_type"),
        }
        for event in events
        if event.get("event_type") in {"ELITE_MONSTER_KILL", "BUILDING_KILL"}
    ]
    first_death_ms = min((_int_or_none(event.get("timestamp_ms")) or 0 for event in deaths), default=None)
    death_timestamps = [(_int_or_none(event.get("timestamp_ms")) or 0) for event in deaths]
    return {
        "first_kill_ms": min((_int_or_none(event.get("timestamp_ms")) or 0 for event in kills), default=None),
        "first_death_ms": first_death_ms,
        "kills_before_10": sum((_int_or_none(event.get("timestamp_ms")) or 0) < 600_000 for event in kills),
        "kills_before_15": sum((_int_or_none(event.get("timestamp_ms")) or 0) < 900_000 for event in kills),
        "deaths_before_10": sum((_int_or_none(event.get("timestamp_ms")) or 0) < 600_000 for event in deaths),
        "deaths_before_15": sum((_int_or_none(event.get("timestamp_ms")) or 0) < 900_000 for event in deaths),
        "deaths_before_20": sum(timestamp < 1_200_000 for timestamp in death_timestamps),
        "deaths_0_10": sum(timestamp < 600_000 for timestamp in death_timestamps),
        "deaths_10_20": sum(600_000 <= timestamp < 1_200_000 for timestamp in death_timestamps),
        "deaths_20_plus": sum(timestamp >= 1_200_000 for timestamp in death_timestamps),
        "first_death_before_10": first_death_ms is not None and first_death_ms < 600_000,
        "first_blood_participation": first_blood,
        "purchases": purchases,
        "objectives": objectives,
    }


def gold_state(value: object, threshold: int = TEAM_GOLD_LEAD_THRESHOLD) -> str | None:
    """Classify a real team-gold difference with explicit symmetric thresholds."""

    difference = _int_or_none(value)
    if difference is None:
        return None
    if difference > threshold:
        return "AHEAD"
    if difference < -threshold:
        return "BEHIND"
    return "EVEN"


def _first_objective(
    events: list[dict[str, object]],
    own_team_id: int | None,
    objective: str,
    participant_teams: dict[int, int] | None = None,
) -> str:
    """Return ours/theirs/none/ambiguous for the first observed objective."""

    if objective == "dragon":
        candidates = [
            event for event in events
            if event.get("event_type") == "ELITE_MONSTER_KILL"
            and str(event.get("monster_type") or "").upper() == "DRAGON"
        ]
    elif objective == "herald":
        candidates = [
            event for event in events
            if event.get("event_type") == "ELITE_MONSTER_KILL"
            and str(event.get("monster_type") or "").upper() in {"RIFTHERALD", "HERALD"}
        ]
    else:
        candidates = [
            event for event in events
            if event.get("event_type") == "BUILDING_KILL"
            and (
                str(event.get("building_type") or "").upper() == "TOWER_BUILDING"
                or bool(event.get("tower_type"))
            )
        ]
    if not candidates:
        return "none"
    first_timestamp = min(_int_or_none(event.get("timestamp_ms")) or 0 for event in candidates)
    participant_teams = participant_teams or {}

    def capturing_team(event: dict[str, object]) -> int | None:
        killer_id = _int_or_none(event.get("killer_id"))
        if killer_id in participant_teams:
            return participant_teams[killer_id]
        event_team = _int_or_none(event.get("team_id"))
        # BUILDING_KILL.teamId is the team whose structure was destroyed.
        if objective == "tower" and event_team in {100, 200}:
            return 200 if event_team == 100 else 100
        return event_team

    first_teams = {
        capturing_team(event)
        for event in candidates
        if (_int_or_none(event.get("timestamp_ms")) or 0) == first_timestamp
    }
    first_teams.discard(None)
    if own_team_id is None or len(first_teams) != 1:
        return "ambiguous"
    return "ours" if next(iter(first_teams)) == own_team_id else "theirs"


def analyze_match_timeline(
    match: dict[str, object],
    participants: list[dict[str, object]],
    frames: list[dict[str, object]],
    events: list[dict[str, object]],
    player_puuid: str,
) -> dict[str, object]:
    """Derive checkpoints, opponent/team differences and factual event metrics."""

    duration = int(match.get("duration") or 0)
    player_id = _participant_id(frames, player_puuid)
    if player_id is None:
        return {**match, "timeline_available": False}
    opponent_puuid = resolve_direct_opponent(player_puuid, participants)
    opponent_id = _participant_id(frames, opponent_puuid) if opponent_puuid else None
    player = next((row for row in participants if row.get("puuid") == player_puuid), {})
    player_side = player.get("side")
    ids_by_side: dict[object, set[int]] = defaultdict(set)
    participant_teams: dict[int, int] = {}
    incomplete_sides: set[object] = set()
    for participant in participants:
        puuid = participant.get("puuid")
        if not isinstance(puuid, str):
            incomplete_sides.add(participant.get("side"))
            continue
        mapped_id = _participant_id(frames, puuid)
        if mapped_id is None:
            incomplete_sides.add(participant.get("side"))
        if mapped_id is not None:
            ids_by_side[participant.get("side")].add(mapped_id)
            side = participant.get("side")
            if side == "blue":
                participant_teams[mapped_id] = 100
            elif side == "red":
                participant_teams[mapped_id] = 200

    for side in incomplete_sides:
        ids_by_side.pop(side, None)

    result: dict[str, object] = {
        **match,
        "timeline_available": True,
        "player_participant_id": player_id,
        "opponent_puuid": opponent_puuid,
        "opponent_champion": next(
            (row.get("champion") for row in participants if row.get("puuid") == opponent_puuid),
            None,
        ),
        "opponent_participant_id": opponent_id,
    }
    for minute in CHECKPOINT_MINUTES:
        own = checkpoint(frames, player_id, minute, duration)
        opposing = checkpoint(frames, opponent_id, minute, duration) if opponent_id else None
        result[f"checkpoint_{minute}"] = own
        for metric in ("gold", "cs", "xp", "level"):
            result[f"{metric}_{minute}"] = own.get(metric) if own else None
        for metric in ("gold", "cs", "xp"):
            result[f"{metric}_diff_{minute}"] = _difference(
                _int_or_none(own.get(metric)) if own else None,
                _int_or_none(opposing.get(metric)) if opposing else None,
            )
        own_team = _team_gold_at(frames, ids_by_side.get(player_side, set()), minute, duration)
        opposing_side = "red" if player_side == "blue" else "blue" if player_side == "red" else None
        enemy_team = _team_gold_at(frames, ids_by_side.get(opposing_side, set()), minute, duration)
        result[f"team_gold_diff_{minute}"] = _difference(own_team, enemy_team)

    result.update(_event_metrics(events, player_id))
    states = [gold_state(result.get(f"team_gold_diff_{minute}")) for minute in (10, 15, 20)]
    result["trajectory"] = " → ".join(states) if all(states) else None
    result["gold_state_10"] = states[0]
    result["gold_state_15"] = states[1]
    result["gold_state_20"] = states[2]
    own_team_id = 100 if player_side == "blue" else 200 if player_side == "red" else None
    result["first_dragon"] = _first_objective(events, own_team_id, "dragon", participant_teams)
    result["first_herald"] = _first_objective(events, own_team_id, "herald", participant_teams)
    result["first_tower"] = _first_objective(events, own_team_id, "tower", participant_teams)
    deaths_before_10 = int(result.get("deaths_before_10") or 0)
    result["death_profile"] = (
        "0 deaths before 10" if deaths_before_10 == 0
        else "1 death before 10" if deaths_before_10 == 1
        else "2+ deaths before 10"
    )
    diff_15 = _int_or_none(result.get("team_gold_diff_15"))
    win = match.get("win") in {1, True}
    if diff_15 is None:
        archetype = None
    elif diff_15 > TEAM_GOLD_LEAD_THRESHOLD:
        archetype = "ahead_win" if win else "ahead_loss"
    elif diff_15 < -TEAM_GOLD_LEAD_THRESHOLD:
        archetype = "behind_win" if win else "behind_loss"
    else:
        archetype = "even"
    result["archetype"] = archetype
    return result


def load_timeline_analytics(database: Database, puuid: str) -> list[dict[str, object]]:
    """Analyze every available timeline from four bulk SQLite queries."""

    matches, participants, frames, events = database.timeline_analysis_source(puuid)
    participants_by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    frames_by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    events_by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in participants:
        participants_by_match[str(row["match_id"])].append(row)
    for row in frames:
        frames_by_match[str(row["match_id"])].append(row)
    for row in events:
        events_by_match[str(row["match_id"])].append(row)
    return [
        analyze_match_timeline(
            match,
            participants_by_match[str(match["match_id"])],
            frames_by_match[str(match["match_id"])],
            events_by_match[str(match["match_id"])],
            puuid,
        )
        for match in matches
    ]


def timeline_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """Return the scalar analytics fields as a Polars frame for preset joins."""

    scalar_rows = [
        {
            key: value
            for key, value in row.items()
            if not isinstance(value, (dict, list))
        }
        for row in rows
    ]
    return pl.from_dicts(scalar_rows, strict=False, infer_schema_length=None) if scalar_rows else pl.DataFrame()
