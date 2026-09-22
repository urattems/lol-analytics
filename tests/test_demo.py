from __future__ import annotations

from pathlib import Path
import io
import json
import zipfile

import httpx
import pytest

from analytics.context import build_context_dataset
from analytics.ai_bundle import build_ai_bundle
from analytics.discovery import promoted_insights
from analytics.overview import get_player_matches
from scripts.create_demo import (
    DEMO_GAME_NAME,
    DEMO_MATCH_COUNT,
    DEMO_PUUID,
    DEMO_SECOND_GAME_NAME,
    DEMO_SECOND_PUUID,
    DEMO_TAG_LINE,
    create_demo_database,
)
from scripts.start_demo import prepare_demo
from core.db import Database


def test_demo_is_offline_coherent_and_useful_for_both_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Demo creation must never request Riot or other network services")

    monkeypatch.setattr(httpx.Client, "send", forbid_network)
    database = create_demo_database(tmp_path / "synthetic.db")
    assert database.count_matches() == DEMO_MATCH_COUNT
    assert database.count_participants() == DEMO_MATCH_COUNT * 10
    assert database.find_player(DEMO_GAME_NAME, DEMO_TAG_LINE)["puuid"] == DEMO_PUUID
    assert database.find_player(DEMO_SECOND_GAME_NAME, DEMO_TAG_LINE)["puuid"] == DEMO_SECOND_PUUID
    with database.connection() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT COUNT(*) FROM participants WHERE puuid NOT LIKE 'demo-%'").fetchone()[0] == 0
        teams = connection.execute(
            "SELECT match_id, side, COUNT(*) AS n, SUM(kills) AS kills, SUM(deaths) AS deaths "
            "FROM participants GROUP BY match_id, side ORDER BY match_id, side"
        ).fetchall()
        for blue, red in zip(teams[::2], teams[1::2]):
            assert blue["n"] == red["n"] == 5
            assert blue["kills"] == red["deaths"]
            assert red["kills"] == blue["deaths"]
        assert connection.execute(
            "SELECT COUNT(*) FROM timeline_events WHERE event_type='ITEM_UNDO' AND item_before_id=1036"
        ).fetchone()[0] == DEMO_MATCH_COUNT

    main = build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID)
    mate = build_context_dataset(get_player_matches(database, DEMO_SECOND_PUUID, None), database, DEMO_SECOND_PUUID)
    assert main.height == DEMO_MATCH_COUNT
    assert mate.height == 48
    assert set(mate["match_id"]) < set(main["match_id"])
    assert main["timeline_available"].all() and main["matchup_available"].all()
    assert mate["timeline_available"].all() and mate["matchup_available"].all()
    assert main["gold_diff_15"].null_count() == 0
    assert main["gold_diff_15"].min() < 0 < main["gold_diff_15"].max()
    assert main["with_recurring_teammate"].n_unique() == 2
    assert len(promoted_insights(main)) >= 3
    bundle = build_ai_bundle(mate, database, DEMO_SECOND_PUUID, f"{DEMO_SECOND_GAME_NAME}#{DEMO_TAG_LINE}", "EUW1", "EUROPE")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["local_games"] == manifest["exported_games"] == 48
        exported = [json.loads(line) for line in archive.read("matches.jsonl").decode().splitlines()]
        assert {row["match_id"] for row in exported} == set(mate["match_id"])
        text = "\n".join(archive.read(name).decode() for name in archive.namelist())
        assert DEMO_GAME_NAME not in text
        assert DEMO_PUUID not in text
        assert DEMO_SECOND_PUUID not in text


def test_demo_never_overwrites_and_has_deterministic_content(tmp_path: Path) -> None:
    first = create_demo_database(tmp_path / "first.db")
    before = first.path.read_bytes()
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        create_demo_database(first.path)
    assert first.path.read_bytes() == before

    second = create_demo_database(tmp_path / "second.db")
    assert first.player_matches(DEMO_PUUID) == second.player_matches(DEMO_PUUID)
    assert first.timeline_frames("DEMO_000001") == second.timeline_frames("DEMO_000001")
    assert first.timeline_events("DEMO_000001") == second.timeline_events("DEMO_000001")


def test_demo_launcher_refuses_non_demo_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "existing.db")
    database.initialize()
    before = database.path.read_bytes()
    with pytest.raises(ValueError, match="non synthétiques"):
        prepare_demo(database.path)
    assert before == database.path.read_bytes()


def test_demo_validation_closes_its_readonly_connection(tmp_path: Path) -> None:
    database = create_demo_database(tmp_path / "demo.db")
    prepare_demo(database.path)
    # Windows refuses renaming a file whose connection remains open.
    target = tmp_path / "validated.db"
    database.path.rename(target)
    assert target.exists()
