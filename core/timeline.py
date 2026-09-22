"""Riot Match V5 timeline parsing and resumable local enrichment."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from config.settings import Settings
from core.db import Database
from core.import_lock import ImportLock, ensure_riot_ready
from core.exceptions import (
    RiotAuthenticationError,
    RiotConfigurationError,
    RiotError,
    RiotNotFoundError,
    RiotRateLimitError,
)
from core.riot_api import RiotAPIClient


USEFUL_EVENT_TYPES = {
    "BUILDING_KILL",
    "CHAMPION_KILL",
    "CHAMPION_SPECIAL_KILL",
    "ELITE_MONSTER_KILL",
    "ITEM_DESTROYED",
    "ITEM_PURCHASED",
    "ITEM_SOLD",
    "ITEM_UNDO",
}


class TimelineAPI(Protocol):
    def get_timeline(self, match_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ParsedTimeline:
    match_id: str
    frames: list[dict[str, object]]
    events: list[dict[str, object]]
    dropped_item_events: int = 0


@dataclass(frozen=True)
class TimelineProgress:
    current: int
    total: int
    available: int
    unavailable: int
    errors: int
    match_id: str | None = None


@dataclass(frozen=True)
class TimelineEnrichmentResult:
    requested: int
    available: int
    unavailable: int
    errors: int
    stopped_early: bool = False


TimelineProgressCallback = Callable[[TimelineProgress], None]


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _participant_map(payload: dict[str, Any]) -> dict[int, str]:
    metadata = payload.get("metadata")
    info = payload.get("info")
    candidates: object = metadata.get("participants") if isinstance(metadata, dict) else None
    if not isinstance(candidates, list) and isinstance(info, dict):
        candidates = info.get("participants")
    mapping: dict[int, str] = {}
    if not isinstance(candidates, list):
        return mapping
    for index, participant in enumerate(candidates, start=1):
        if isinstance(participant, str):
            mapping[index] = participant
        elif isinstance(participant, dict):
            participant_id = _optional_int(participant.get("participantId")) or index
            puuid = participant.get("puuid")
            if isinstance(puuid, str):
                mapping[participant_id] = puuid
    return mapping


def parse_timeline(payload: dict[str, Any]) -> ParsedTimeline:
    """Normalize only stable, useful Match V5 timeline fields."""

    metadata = payload.get("metadata")
    info = payload.get("info")
    if not isinstance(metadata, dict) or not isinstance(info, dict):
        raise ValueError("Timeline payload is missing metadata or info.")
    match_id = metadata.get("matchId")
    source_frames = info.get("frames")
    if not isinstance(match_id, str) or not match_id or not isinstance(source_frames, list):
        raise ValueError("Timeline payload has an invalid match ID or frames list.")

    puuids = _participant_map(payload)
    frames: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    event_index = 0
    dropped_item_events = 0
    for source_frame in source_frames:
        if not isinstance(source_frame, dict):
            continue
        timestamp = _optional_int(source_frame.get("timestamp"))
        participant_frames = source_frame.get("participantFrames")
        if timestamp is not None and 0 <= timestamp <= 86_400_000 and isinstance(participant_frames, dict):
            for key, participant_frame in participant_frames.items():
                if not isinstance(participant_frame, dict):
                    continue
                participant_id = _optional_int(participant_frame.get("participantId"))
                if participant_id is None:
                    participant_id = _optional_int(key)
                if participant_id is None:
                    continue
                minions = _optional_int(participant_frame.get("minionsKilled"))
                jungle = _optional_int(participant_frame.get("jungleMinionsKilled"))
                position = participant_frame.get("position")
                frames.append(
                    {
                        "participant_id": participant_id,
                        "puuid": puuids.get(participant_id),
                        "timestamp_ms": timestamp,
                        "total_gold": _optional_int(participant_frame.get("totalGold")),
                        "current_gold": _optional_int(participant_frame.get("currentGold")),
                        "xp": _optional_int(participant_frame.get("xp")),
                        "level": _optional_int(participant_frame.get("level")),
                        "minions_killed": minions,
                        "jungle_minions_killed": jungle,
                        "cs_total": minions + jungle if minions is not None and jungle is not None else None,
                        "position_x": _optional_int(position.get("x")) if isinstance(position, dict) else None,
                        "position_y": _optional_int(position.get("y")) if isinstance(position, dict) else None,
                    }
                )

        source_events = source_frame.get("events")
        if not isinstance(source_events, list):
            continue
        for source_event in source_events:
            if not isinstance(source_event, dict):
                continue
            event_type = source_event.get("type")
            if event_type not in USEFUL_EVENT_TYPES:
                continue
            event_timestamp = _optional_int(source_event.get("timestamp"))
            if event_timestamp is None or not 0 <= event_timestamp <= 86_400_000:
                # Unknown event time is not the frame time or game start.
                if str(event_type).startswith('ITEM_'):
                    dropped_item_events += 1
                continue
            normalized_keys = {
                "timestamp", "type", "participantId", "killerId", "victimId",
                "creatorId", "itemId", "beforeId", "afterId", "teamId", "monsterType", "monsterSubType",
                "buildingType", "towerType", "laneType",
            }
            events.append(
                {
                    "event_index": event_index,
                    "timestamp_ms": event_timestamp,
                    "event_type": str(event_type),
                    "participant_id": _optional_int(source_event.get("participantId")),
                    "killer_id": _optional_int(source_event.get("killerId")),
                    "victim_id": _optional_int(source_event.get("victimId")),
                    "creator_id": _optional_int(source_event.get("creatorId")),
                    "item_id": _optional_int(source_event.get("itemId")),
                    "item_before_id": _optional_int(source_event.get("beforeId")),
                    "item_after_id": _optional_int(source_event.get("afterId")),
                    "team_id": _optional_int(source_event.get("teamId")),
                    "monster_type": source_event.get("monsterType"),
                    "monster_subtype": source_event.get("monsterSubType"),
                    "building_type": source_event.get("buildingType"),
                    "tower_type": source_event.get("towerType"),
                    "lane_type": source_event.get("laneType"),
                    "extra": {
                        key: value
                        for key, value in source_event.items()
                        if key not in normalized_keys
                    },
                }
            )
            event_index += 1
    return ParsedTimeline(match_id, frames, events, dropped_item_events)


class TimelineService:
    """Persist timelines one match at a time so interrupted runs can resume."""

    def __init__(self, api: TimelineAPI, database: Database) -> None:
        self.api = api
        self.database = database

    def enrich(
        self,
        puuid: str,
        limit: int | None = None,
        retry_errors: bool = False,
        progress: TimelineProgressCallback | None = None,
        match_ids: list[str] | None = None,
    ) -> TimelineEnrichmentResult:
        candidates = self.database.timeline_candidates(puuid, None if match_ids is not None else limit, retry_errors)
        if match_ids is not None:
            requested_ids = set(match_ids)
            candidates = [match_id for match_id in candidates if match_id in requested_ids]
            if limit is not None:
                if limit < 1:
                    raise ValueError("limit must be positive or None")
                candidates = candidates[:limit]
        available = unavailable = errors = 0
        stopped_early = False
        if progress:
            progress(TimelineProgress(0, len(candidates), 0, 0, 0))
        for current, match_id in enumerate(candidates, start=1):
            try:
                parsed = parse_timeline(self.api.get_timeline(match_id))
                if parsed.match_id != match_id:
                    raise ValueError("Timeline match ID does not match the request.")
                if len({frame["participant_id"] for frame in parsed.frames if frame.get("puuid") == puuid}) != 1:
                    raise ValueError("Timeline has no usable frames for the requested player.")
                self.database.save_timeline(match_id, parsed.frames, parsed.events,
                                            dropped_item_events=parsed.dropped_item_events)
                available += 1
            except RiotNotFoundError:
                self.database.set_timeline_status(match_id, "unavailable", "HTTP 404")
                unavailable += 1
            except Exception as error:
                self.database.set_timeline_status(match_id, "error", str(error)[:500])
                errors += 1
                if isinstance(
                    error,
                    (RiotAuthenticationError, RiotConfigurationError, RiotRateLimitError),
                ):
                    stopped_early = True
            if progress:
                progress(
                    TimelineProgress(
                        current, len(candidates), available, unavailable, errors, match_id
                    )
                )
            if stopped_early:
                break
        return TimelineEnrichmentResult(
            len(candidates), available, unavailable, errors, stopped_early
        )


def enrich_timelines_from_settings(
    settings: Settings,
    puuid: str,
    limit: int | None = None,
    retry_errors: bool = False,
    progress: TimelineProgressCallback | None = None,
    match_ids: list[str] | None = None,
) -> TimelineEnrichmentResult:
    """Create the Riot client only for an explicit enrichment action."""

    if not settings.riot_api_key:
        raise RiotConfigurationError("RIOT_API_KEY is missing.")
    database = Database(settings.database_path)
    database.initialize()
    with ImportLock(database.path), RiotAPIClient(
        settings.riot_api_key,
        settings.riot_routing_region,
        settings.riot_platform_region,
    ) as api:
        ensure_riot_ready(database.path)
        return TimelineService(api, database).enrich(
            puuid, limit, retry_errors, progress, match_ids
        )
