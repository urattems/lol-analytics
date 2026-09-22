"""Hostile inputs and interrupted transactions; never accesses configured data."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import csv
import io
import json
import sqlite3
import threading
import time

import httpx
import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

from analytics.exports import _local_iso, to_csv
from analytics.overview import get_player_matches
from analytics.timeline import analyze_match_timeline, timeline_frame
from core.exceptions import (RiotAPIError, RiotAuthenticationError, RiotNetworkError,
                             RiotNotFoundError, RiotRateLimitError, RiotServerError)
from core.models import parse_riot_match
from core.riot_api import RiotAPIClient
from core.sync import SyncService
from core.timeline import TimelineService, parse_timeline
from tests.test_redteam_regressions import SnapshotAPI
from tests.test_timeline import timeline_payload


@pytest.mark.parametrize("status,expected,attempts", [
    (401, RiotAuthenticationError, 1), (403, RiotAuthenticationError, 1),
    (404, RiotNotFoundError, 1), (429, RiotRateLimitError, 3),
    (500, RiotServerError, 3), (502, RiotServerError, 3), (503, RiotServerError, 3),
])
def test_http_errors_are_bounded_and_do_not_import(database, status, expected, attempts):
    calls, waits = [], []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(status)
    with RiotAPIClient("fake-secret", transport=httpx.MockTransport(handler),
                       max_retries=2, sleep=waits.append) as api:
        with pytest.raises(expected) as error:
            SyncService(api, database).sync_player("QA", "TEST")
    assert len(calls) == attempts and len(waits) == attempts - 1
    assert "fake-secret" not in str(error.value)
    assert database.count_matches() == database.count_participants() == 0


@pytest.mark.parametrize("fault", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_transport_failure_then_recovery(fault):
    calls, waits = [], []
    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise fault("synthetic disconnect", request=request)
        return httpx.Response(200, json=["EUW1_1"])
    with RiotAPIClient("fake", transport=httpx.MockTransport(handler), sleep=waits.append) as api:
        assert api.get_match_ids("player") == ["EUW1_1"]
    assert waits == [1, 2]
    assert calls[0].extensions["timeout"]["read"] == 15


def test_slow_response_and_exhausted_timeout():
    def slow(request):
        time.sleep(.01)  # MockTransport itself does not enforce socket timeouts.
        return httpx.Response(200, json=[])
    with RiotAPIClient("fake", transport=httpx.MockTransport(slow)) as api:
        assert api.get_match_ids("player") == []
    waits = []
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)
    with RiotAPIClient("fake", transport=httpx.MockTransport(timeout), sleep=waits.append) as api:
        with pytest.raises(RiotNetworkError):
            api.get_match("EUW1_1")
    assert waits == [1, 2, 4]


@pytest.mark.parametrize("content", [b"", b"<html>bad gateway</html>", b"null", b"[]"])
def test_malformed_or_empty_detail_response(content):
    with RiotAPIClient("fake", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))) as api:
        with pytest.raises(RiotAPIError):
            api.get_match("EUW1_1")


@pytest.mark.parametrize("header", ["inf", "NaN", "999999999", "-4"])
def test_hostile_retry_after_never_sleeps_or_retries_early(header):
    waits, calls = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": header})
    with RiotAPIClient("fake", transport=httpx.MockTransport(handler), sleep=waits.append) as api:
        with pytest.raises(RiotRateLimitError):
            api.get_match("EUW1_1")
    assert calls and len(calls) == 1
    assert waits == []


@pytest.mark.parametrize("phase", ["ids", "detail", "three_commits", "last_commit"])
def test_import_interruption_resumes_twenty_exactly(database, riot_match_payload, phase):
    payloads = {}
    for i in range(20):
        payload = deepcopy(riot_match_payload)
        payload["metadata"]["matchId"] = f"EUW1_QA_{i:02}"
        payloads[payload["metadata"]["matchId"]] = payload
    class API(SnapshotAPI):
        fail = True
        fetched = []
        def get_match_ids(self, *args, **kwargs):
            return list(payloads)
        def get_match(self, match_id):
            if self.fail and phase == "detail":
                raise KeyboardInterrupt("during detail")
            self.fetched.append(match_id)
            return payloads[match_id]
    api = API(None)
    def interrupt(progress):
        if ((phase == "ids" and progress.phase == "discovering") or
            (progress.phase == "importing" and progress.current ==
             {"three_commits": 3, "last_commit": 20}.get(phase, -1))):
            raise KeyboardInterrupt("interrupted by QA")
    service = SyncService(api, database)
    with pytest.raises(KeyboardInterrupt):
        service.import_recent_player("QA", "TEST", progress=interrupt)
    committed = {"ids": 0, "detail": 0, "three_commits": 3, "last_commit": 20}[phase]
    assert database.count_matches() == committed
    assert database.count_participants() == committed * 4
    assert database.get_sync_state("player-puuid") is None
    api.fail = False
    result = service.import_recent_player("QA", "TEST")
    assert result.inserted == 20 - committed
    assert database.count_matches() == 20 and database.count_participants() == 80
    assert len(api.fetched) == len(set(api.fetched)) == 20
    assert database.get_sync_state("player-puuid") == "EUW1_QA_00"


@pytest.mark.parametrize("existing", [False, True])
def test_timeline_interruption_after_frames_rolls_back_everything(database, riot_match_payload, existing):
    database.insert_match(parse_riot_match(riot_match_payload))
    parsed = parse_timeline(timeline_payload())
    if existing:
        database.save_timeline(parsed.match_id, parsed.frames, parsed.events, fetched_at=1)
    before_frames = database.timeline_frames(parsed.match_id)
    before_events = database.timeline_events(parsed.match_id)
    class InterruptedEvents(list):
        def __iter__(self):
            raise KeyboardInterrupt("after frame INSERTs, before event INSERTs")
    with pytest.raises(KeyboardInterrupt):
        database.save_timeline(parsed.match_id, parsed.frames, InterruptedEvents(parsed.events))
    assert database.timeline_frames(parsed.match_id) == before_frames
    assert database.timeline_events(parsed.match_id) == before_events
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events)
    assert len(database.timeline_frames(parsed.match_id)) == 4
    assert len(database.timeline_events(parsed.match_id)) == 6


def test_timeline_fetch_interrupt_is_retryable(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    class API:
        def get_timeline(self, match_id):
            raise KeyboardInterrupt("during HTTP")
    with pytest.raises(KeyboardInterrupt):
        TimelineService(API(), database).enrich("player-puuid")
    assert database.timeline_candidates("player-puuid") == ["EUW1_123456"]


def test_duplicate_participant_rolls_back_match(database, riot_match_payload):
    riot_match_payload["info"]["participants"].append(deepcopy(riot_match_payload["info"]["participants"][0]))
    with pytest.raises(sqlite3.IntegrityError):
        database.insert_match(parse_riot_match(riot_match_payload))
    assert database.count_matches() == database.count_participants() == 0


def test_orphan_match_can_be_reimported(database, riot_match_payload):
    with database.connection() as connection:
        connection.execute("INSERT INTO matches VALUES ('EUW1_123456', 'old', 0, 1, 1)")
    assert database.count_player_matches("player-puuid") == 0
    result = SyncService(SnapshotAPI(riot_match_payload), database).import_recent_player("QA", "TEST")
    assert result.inserted == 1 and database.count_matches() == 1
    assert database.count_participants() == 4
    assert database.player_matches("player-puuid")[0]["duration"] == 1902


@pytest.mark.parametrize("events", [False, True])
def test_legacy_available_without_frames_is_missing_and_retryable(database, riot_match_payload, events):
    database.insert_match(parse_riot_match(riot_match_payload))
    with database.connection() as connection:
        connection.execute("INSERT INTO timeline_status (match_id,status) VALUES ('EUW1_123456','available')")
        if events:
            connection.execute("INSERT INTO timeline_events (match_id,event_index,timestamp_ms,event_type) VALUES ('EUW1_123456',0,0,'ITEM_PURCHASED')")
    assert database.timeline_available_match_ids("player-puuid") == set()
    assert database.timeline_coverage("player-puuid")["missing"] == 1
    assert database.timeline_candidates("player-puuid") == ["EUW1_123456"]
    assert all(not rows for rows in database.timeline_analysis_source("player-puuid"))


def test_failed_concurrent_fetch_does_not_hide_committed_timeline(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    parsed = parse_timeline(timeline_payload())
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events)
    database.set_timeline_status(parsed.match_id, "error", "other worker timed out")
    assert database.timeline_coverage("player-puuid")["available"] == 1


def test_database_temporary_write_lock_releases_without_partial_import(database, riot_match_payload):
    ready = threading.Event()
    def lock():
        with sqlite3.connect(database.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(.15)
    worker = threading.Thread(target=lock)
    worker.start()
    assert ready.wait(2)
    assert database.insert_match(parse_riot_match(riot_match_payload))
    worker.join(2)
    assert not worker.is_alive()
    assert database.count_matches() == 1 and database.count_participants() == 4


def test_readonly_database_keeps_data_readable_and_unchanged(database, riot_match_payload, monkeypatch):
    database.insert_match(parse_riot_match(riot_match_payload))
    @contextmanager
    def readonly():
        connection = sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    monkeypatch.setattr(database, "connection", readonly)
    assert get_player_matches(database, "player-puuid", None).height == 1
    with pytest.raises(sqlite3.OperationalError):
        database.set_timeline_status("EUW1_123456", "error")
    assert database.count_matches() == 1


def test_incomplete_team_never_invents_gold_diff():
    parsed = parse_timeline(timeline_payload())
    participants = [{"puuid": "player-puuid", "side": "blue", "role": "JUNGLE"},
                    {"puuid": "enemy-puuid", "side": "red", "role": "JUNGLE"},
                    {"puuid": "missing-ally", "side": "blue", "role": "TOP"}]
    result = analyze_match_timeline({"match_id": parsed.match_id, "duration": 1200},
                                    participants, parsed.frames, [], "player-puuid")
    assert result["gold_diff_10"] == 400
    assert result["team_gold_diff_10"] is None and result["trajectory"] is None
    from pages.timeline import _team_gold_figure
    assert all(value is None for value in _team_gold_figure(parsed.frames, participants, "blue").data[0].y)


def test_timeline_schema_handles_late_nonnull_values():
    rows = [{"match_id": str(i), "gold_diff_15": None if i < 120 else 500} for i in range(121)]
    assert timeline_frame(rows)["gold_diff_15"].to_list() == [None] * 120 + [500]


@pytest.mark.parametrize("timestamp", [float("inf"), 10**30, -10**30, "not-a-date"])
def test_invalid_export_timestamp_is_unavailable(timestamp):
    assert _local_iso(timestamp) is None


def test_csv_formulas_are_inert_but_numeric_negatives_remain_numeric():
    source = pl.DataFrame({"label": ["=1+1", "  @SUM(1)", "+cmd", "-cmd", "\tcmd", "ordinary"], "delta": [-5] * 6})
    rows = list(csv.DictReader(io.StringIO(to_csv(source))))
    assert all(row["label"].startswith("'") for row in rows[:5])
    assert rows[-1]["label"] == "ordinary"
    assert all(row["delta"] == "-5" for row in rows)


def test_ui_database_failure_after_initialization_has_actionable_error(database, monkeypatch):
    from config.settings import Settings
    from core.db import Database
    from tests.test_ui_smoke import _offline_settings
    settings = Settings(database_path=database.path, riot_game_name="QA", riot_tag_line="TEST", demo_mode=True)
    _offline_settings(monkeypatch, settings)
    def fail(self):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(Database, "list_players", fail)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not app.exception and app.error


def test_invalid_legacy_timestamps_do_not_crash_pages(database, riot_match_payload, monkeypatch):
    from config.settings import Settings
    from core.models import RiotAccount
    from tests.test_ui_smoke import _offline_settings
    database.insert_match(parse_riot_match(riot_match_payload))
    database.upsert_player(RiotAccount(puuid="player-puuid", game_name="QA", tag_line="TEST"), "EUW1", "EUROPE")
    with database.connection() as connection:
        connection.execute("UPDATE matches SET game_creation = 9000000000000000000")
    _offline_settings(monkeypatch, Settings(database_path=database.path, riot_game_name="QA", riot_tag_line="TEST", demo_mode=True))
    for page in ("overview", "insights", "contexts", "explorer", "ai_export"):
        app = AppTest.from_string(f"from pages.{page} import show_{page}\nshow_{page}()").run(timeout=60)
        assert not app.exception, (page, [e.message for e in app.exception])


def test_unknown_timestamp_does_not_create_a_fake_session(database, riot_match_payload):
    from analytics.context import build_context_dataset
    database.insert_match(parse_riot_match(riot_match_payload))
    with database.connection() as connection:
        connection.execute("UPDATE matches SET game_creation = NULL")
    games = build_context_dataset(get_player_matches(database, "player-puuid", None), database, "player-puuid")
    assert games["session_id"][0] is None


def test_ambiguous_timeline_identity_is_retryable(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    payload = timeline_payload()
    payload["metadata"]["participants"] = ["player-puuid", "player-puuid"]
    class API:
        def get_timeline(self, match_id):
            return payload
    assert TimelineService(API(), database).enrich("player-puuid").errors == 1
    # Also exercise a legacy ambiguous timeline already on disk.
    parsed = parse_timeline(payload)
    database.save_timeline(parsed.match_id, parsed.frames, parsed.events)
    assert database.timeline_available_match_ids("player-puuid") == set()
    assert database.timeline_coverage("player-puuid")["missing"] == 1
    assert database.timeline_candidates("player-puuid") == [parsed.match_id]
    assert all(not rows for rows in database.timeline_analysis_source("player-puuid"))


def test_real_legacy_column_migration_is_repeatable(database, riot_match_payload):
    from core.models import RiotAccount
    database.insert_match(parse_riot_match(riot_match_payload))
    database.upsert_player(RiotAccount(puuid="player-puuid", game_name="Legacy", tag_line="TEST"), "EUW1", "EUROPE")
    with database.connection() as connection:
        for table, columns in {"players": ["platform_region", "routing_region"],
                               "participants": ["teammate_game_name", "teammate_tag_line"],
                               "timeline_events": ["item_before_id", "item_after_id"]}.items():
            for column in columns:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.execute("INSERT INTO timeline_events (match_id,event_index,timestamp_ms,event_type,extra_json) VALUES ('EUW1_123456',0,0,'ITEM_UNDO',?)",
                           (json.dumps({"beforeId": 3078, "afterId": 0}),))
    for _ in range(3):
        database.initialize()
        assert database.count_matches() == 1 and database.count_participants() == 4
        assert database.find_player("Legacy", "TEST")["puuid"] == "player-puuid"
        event = database.timeline_events("EUW1_123456")[0]
        assert (event["item_before_id"], event["item_after_id"]) == (3078, 0)
    with database.connection() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("item", [float("inf"), float("nan"), 10**30])
def test_hostile_legacy_extra_does_not_break_migration(database, riot_match_payload, item):
    database.insert_match(parse_riot_match(riot_match_payload))
    with database.connection() as connection:
        connection.execute("INSERT INTO timeline_events (match_id,event_index,timestamp_ms,event_type,extra_json) VALUES ('EUW1_123456',0,0,'ITEM_UNDO',?)",
                           (json.dumps({"beforeId": item, "afterId": 0}),))
    database.initialize()
    database.initialize()
    event = database.timeline_events("EUW1_123456")[0]
    assert event["item_before_id"] is None and event["item_after_id"] == 0


@pytest.mark.parametrize("timestamp", [None, -1, float("inf"), 10**30])
def test_invalid_event_timestamp_is_not_invented_or_counted(timestamp):
    payload = timeline_payload()
    payload["info"]["frames"][0]["events"][0]["timestamp"] = timestamp
    parsed = parse_timeline(payload)
    assert len(parsed.events) == 5
    assert all(0 <= event["timestamp_ms"] <= 86_400_000 for event in parsed.events)
