"""The scoped API fetches only this profile's eligible session match IDs."""
from copy import deepcopy

from core.exceptions import RiotNotFoundError
from core.models import parse_riot_match
from core.timeline import TimelineService
from tests.test_timeline import FakeTimelineAPI, timeline_payload


def test_scope_filters_foreign_unknown_duplicates_and_available(database, riot_match_payload):
    for index in range(6):
        payload = deepcopy(riot_match_payload)
        payload["metadata"]["matchId"] = f"SCOPED_{index}"
        payload["info"]["gameCreation"] += index * 60_000
        if index == 5:
            payload["info"]["participants"][0]["puuid"] = "foreign-player"
        database.insert_match(parse_riot_match(payload))
    api = FakeTimelineAPI({f"SCOPED_{i}": timeline_payload(f"SCOPED_{i}") for i in range(6)})
    service = TimelineService(api, database)
    assert service.enrich("player-puuid", match_ids=["SCOPED_0"]).available == 1
    api.calls.clear()
    result = service.enrich("player-puuid", match_ids=["SCOPED_0", "SCOPED_2", "SCOPED_2", "SCOPED_5", "UNKNOWN"])
    assert result.requested == result.available == 1
    assert api.calls == ["SCOPED_2"]
    assert service.enrich("player-puuid", match_ids=[]).requested == 0
    assert service.enrich("player-puuid", limit=1, match_ids=["SCOPED_1", "SCOPED_3"]).requested == 1
    assert api.calls[-1] == "SCOPED_3"
    assert "SCOPED_4" in database.timeline_candidates("player-puuid")


def test_scope_errors_retry_but_404_never_refetched(database, riot_match_payload):
    ids = ["SCOPED_ERROR", "SCOPED_404", "SCOPED_OUTSIDE"]
    for match_id in ids:
        payload = deepcopy(riot_match_payload)
        payload["metadata"]["matchId"] = match_id
        database.insert_match(parse_riot_match(payload))
    api = FakeTimelineAPI({ids[0]: RuntimeError("offline"), ids[1]: RiotNotFoundError("404")})
    service = TimelineService(api, database)
    result = service.enrich("player-puuid", match_ids=ids[:2])
    assert result.errors == result.unavailable == 1
    assert service.enrich("player-puuid", match_ids=ids[:2]).requested == 0
    api.responses[ids[0]] = timeline_payload(ids[0])
    api.calls.clear()
    assert service.enrich("player-puuid", retry_errors=True, match_ids=ids[:2]).available == 1
    assert api.calls == [ids[0]]
