from __future__ import annotations

from copy import deepcopy

from core.db import Database
from core.exceptions import RiotNotFoundError
from core.models import parse_riot_match
from core.timeline import TimelineService, parse_timeline
from analytics.timeline import (
    analyze_match_timeline,
    checkpoint,
    event_item_id,
    resolve_direct_opponent,
)


def timeline_payload(match_id: str = "EUW1_123456") -> dict[str, object]:
    participant_frames = {
        "1": {
            "participantId": 1,
            "totalGold": 5000,
            "currentGold": 600,
            "xp": 4200,
            "level": 8,
            "minionsKilled": 30,
            "jungleMinionsKilled": 45,
            "position": {"x": 5100, "y": 6100},
        },
        "2": {
            "participantId": 2,
            "totalGold": 4600,
            "currentGold": 300,
            "xp": 3900,
            "level": 7,
            "minionsKilled": 22,
            "jungleMinionsKilled": 48,
            "position": {"x": 9200, "y": 8600},
        },
    }
    later = deepcopy(participant_frames)
    later["1"]["totalGold"] = 7600
    later["2"]["totalGold"] = 7000
    return {
        "metadata": {
            "matchId": match_id,
            "participants": ["player-puuid", "enemy-puuid"],
        },
        "info": {
            "frameInterval": 60000,
            "frames": [
                {"timestamp": 600_000, "participantFrames": participant_frames, "events": [
                    {"timestamp": 522_000, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3078},
                    {"timestamp": 540_000, "type": "CHAMPION_KILL", "killerId": 1, "victimId": 2, "assistingParticipantIds": [], "killType": "KILL_FIRST_BLOOD"},
                    {"timestamp": 580_000, "type": "CHAMPION_KILL", "killerId": 2, "victimId": 1},
                    {"timestamp": 550_000, "type": "WARD_PLACED", "creatorId": 1},
                ]},
                {"timestamp": 900_000, "participantFrames": later, "events": [
                    {"timestamp": 840_000, "type": "ELITE_MONSTER_KILL", "killerId": 1, "teamId": 100, "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON"},
                    {"timestamp": 850_000, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3161},
                    {"timestamp": 860_000, "type": "ITEM_SOLD", "participantId": 1, "itemId": 1001},
                ]},
            ],
        },
    }


def test_timeline_parsing_normalizes_frames_and_useful_events() -> None:
    parsed = parse_timeline(timeline_payload())
    assert parsed.match_id == "EUW1_123456"
    assert len(parsed.frames) == 4
    assert parsed.frames[0] == {
        "participant_id": 1,
        "puuid": "player-puuid",
        "timestamp_ms": 600_000,
        "total_gold": 5000,
        "current_gold": 600,
        "xp": 4200,
        "level": 8,
        "minions_killed": 30,
        "jungle_minions_killed": 45,
        "cs_total": 75,
        "position_x": 5100,
        "position_y": 6100,
    }
    assert [event["event_type"] for event in parsed.events] == [
        "ITEM_PURCHASED", "CHAMPION_KILL", "CHAMPION_KILL",
        "ELITE_MONSTER_KILL", "ITEM_PURCHASED", "ITEM_SOLD",
    ]
    assert parsed.events[1]["extra"] == {
        "assistingParticipantIds": [], "killType": "KILL_FIRST_BLOOD"
    }


def test_item_undo_keeps_before_and_after_ids_without_item_zero() -> None:
    payload = timeline_payload()
    payload["info"]["frames"][1]["events"].append(  # type: ignore[index]
        {
            "timestamp": 870_000,
            "type": "ITEM_UNDO",
            "participantId": 1,
            "beforeId": 3078,
            "afterId": 0,
        }
    )
    event = parse_timeline(payload).events[-1]
    assert event["event_type"] == "ITEM_UNDO"
    assert event["item_id"] is None
    assert event["item_before_id"] == 3078
    assert event["item_after_id"] == 0
    assert event["extra"] == {}
    assert event_item_id(event) == 3078
    assert event_item_id({"event_type": "ITEM_UNDO", "item_before_id": 0}) is None
    assert event_item_id({"event_type": "ITEM_PURCHASED", "item_id": "unknown"}) is None


def test_frame_and_event_persistence_is_atomic_and_idempotent(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    parsed = parse_timeline(timeline_payload())
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events, fetched_at=123)
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events, fetched_at=124)
    assert len(database.timeline_frames(parsed.match_id)) == 4
    assert len(database.timeline_events(parsed.match_id)) == 6
    coverage = database.timeline_coverage("player-puuid")
    assert coverage == {
        "available": 1, "unavailable": 0, "error": 0, "missing": 0,
        "local": 1, "frames": 4, "events": 6,
    }


def test_item_undo_migration_backfills_existing_extra_json(database: Database) -> None:
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO matches VALUES ('UNDO', '16.17', 420, 1200, 1)"
        )
        connection.execute(
            """
            INSERT INTO timeline_events (
                match_id, event_index, timestamp_ms, event_type, participant_id, extra_json
            ) VALUES ('UNDO', 0, 1000, 'ITEM_UNDO', 1, '{"beforeId":3078,"afterId":0}')
            """
        )
    database.initialize()
    event = database.timeline_events("UNDO")[0]
    assert event["item_before_id"] == 3078
    assert event["item_after_id"] == 0


class FakeTimelineAPI:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_timeline(self, match_id: str) -> dict[str, object]:
        self.calls.append(match_id)
        response = self.responses[match_id]
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]


def test_enrichment_records_404_and_resumes_without_refetching(
    database: Database,
    riot_match_payload: dict[str, object],
    second_riot_match_payload: dict[str, object],
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    database.insert_match(parse_riot_match(second_riot_match_payload))
    api = FakeTimelineAPI({
        "EUW1_123457": RiotNotFoundError("missing"),
        "EUW1_123456": timeline_payload(),
    })
    first = TimelineService(api, database).enrich("player-puuid")
    second = TimelineService(api, database).enrich("player-puuid")
    assert (first.available, first.unavailable, first.errors) == (1, 1, 0)
    assert second.requested == 0
    assert api.calls == ["EUW1_123457", "EUW1_123456"]


def test_errors_require_explicit_retry(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    failing = FakeTimelineAPI({"EUW1_123456": RuntimeError("temporary")})
    assert TimelineService(failing, database).enrich("player-puuid").errors == 1
    assert TimelineService(failing, database).enrich("player-puuid").requested == 0

    recovered = FakeTimelineAPI({"EUW1_123456": timeline_payload()})
    result = TimelineService(recovered, database).enrich("player-puuid", retry_errors=True)
    assert result.available == 1
    assert recovered.calls == ["EUW1_123456"]


def _participants() -> list[dict[str, object]]:
    return [
        {"puuid": "player-puuid", "role": "JUNGLE", "side": "blue"},
        {"puuid": "enemy-puuid", "role": "JUNGLE", "side": "red"},
    ]


def test_checkpoint_uses_real_nearest_frame_and_never_final_substitution() -> None:
    frames = parse_timeline(timeline_payload()).frames
    point = checkpoint(frames, 1, 10, duration_seconds=1_200)
    assert point == {
        "requested_timestamp_ms": 600_000,
        "actual_frame_timestamp_ms": 600_000,
        "gold": 5000,
        "cs": 75,
        "xp": 4200,
        "level": 8,
    }
    assert checkpoint(frames, 1, 20, duration_seconds=1_100) is None
    assert checkpoint(frames, 1, 5, duration_seconds=1_200, tolerance_ms=10_000) is None


def test_direct_opponent_requires_one_unambiguous_same_role() -> None:
    assert resolve_direct_opponent("player-puuid", _participants()) == "enemy-puuid"
    ambiguous = [*_participants(), {"puuid": "enemy-two", "role": "JUNGLE", "side": "red"}]
    assert resolve_direct_opponent("player-puuid", ambiguous) is None
    missing_role = [dict(_participants()[0], role="UNKNOWN"), _participants()[1]]
    assert resolve_direct_opponent("player-puuid", missing_role) is None


def test_match_analytics_derives_diffs_items_kills_deaths_and_objectives() -> None:
    parsed = parse_timeline(timeline_payload())
    result = analyze_match_timeline(
        {"match_id": parsed.match_id, "duration": 1_200, "win": 1},
        _participants(),
        parsed.frames,
        [dict(event, extra_json=__import__("json").dumps(event["extra"])) for event in parsed.events],
        "player-puuid",
    )
    assert result["gold_diff_10"] == 400
    assert result["cs_diff_10"] == 5
    assert result["xp_diff_10"] == 300
    assert result["team_gold_diff_10"] == 400
    assert result["gold_diff_15"] == 600
    assert result["gold_20"] is None
    assert result["kills_before_10"] == 1
    assert result["deaths_before_10"] == 1
    assert result["first_death_before_10"] is True
    assert result["first_blood_participation"] is True
    assert [purchase["event_type"] for purchase in result["purchases"]] == [  # type: ignore[index]
        "ITEM_PURCHASED", "ITEM_PURCHASED", "ITEM_SOLD"
    ]
    assert result["objectives"][0]["monster_type"] == "DRAGON"  # type: ignore[index]
