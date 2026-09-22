from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from analytics.ai_bundle import BUNDLE_FILES, ai_bundle_filename, build_ai_bundle
from analytics.overview import get_player_matches
from core.db import Database
from core.models import parse_riot_match


def test_complete_ai_bundle_contains_checked_full_library(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    participants = riot_match_payload["info"]["participants"]  # type: ignore[index]
    participants[1]["riotIdGameName"] = "PrivateMate"  # type: ignore[index]
    participants[1]["riotIdTagline"] = "HIDDEN"  # type: ignore[index]
    database.insert_match(parse_riot_match(riot_match_payload))
    database.save_timeline(
        "EUW1_123456",
        [
            {
                "participant_id": 1, "puuid": "player-puuid", "timestamp_ms": 600_000,
                "total_gold": 5000, "current_gold": 500, "xp": 4000, "level": 8,
                "minions_killed": 30, "jungle_minions_killed": 40, "cs_total": 70,
                "position_x": 5000, "position_y": 5000,
            }
        ],
        [
            {
                "event_index": 0, "timestamp_ms": 500_000, "event_type": "ITEM_PURCHASED",
                "participant_id": 1, "item_id": 3078, "extra": {},
            },
            {
                "event_index": 1, "timestamp_ms": 510_000, "event_type": "ITEM_UNDO",
                "participant_id": 1, "item_before_id": 3078, "item_after_id": 0,
                "extra": {},
            },
        ],
    )
    games = get_player_matches(database, "player-puuid", None)
    generated = datetime(2026, 9, 4, tzinfo=timezone.utc)
    content = build_ai_bundle(
        games, database, "player-puuid", "Synthetic Main#TEST", "EUW1", "EUROPE", generated
    )

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert set(archive.namelist()) == BUNDLE_FILES
        manifest = json.loads(archive.read("manifest.json"))
        matches = [json.loads(line) for line in archive.read("matches.jsonl").decode().splitlines()]
        checkpoints = archive.read("timeline_checkpoints.jsonl").decode().splitlines()
        events = archive.read("timeline_events.jsonl").decode().splitlines()
        readme = archive.read("README_AI.md").decode()
        all_text = "\n".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
        )
    assert manifest["schema_version"] == "2.1"
    assert manifest["local_games"] == manifest["exported_games"] == 1
    assert manifest["filters"] == {}
    assert manifest["timeline"] == {"available": 1, "missing": 0}
    assert len(matches) == 1
    assert matches[0]["timeline_available"] is True
    assert matches[0]["trinket"] == 3340
    assert len(checkpoints) == 1
    assert "player-puuid" not in content.decode("latin1", errors="ignore")
    assert "enemy-puuid" not in content.decode("latin1", errors="ignore")
    assert len(events) == 2
    assert json.loads(events[1])["item_before_id"] == 3078
    assert "corrélation" in readme
    assert "blue-ally" not in all_text
    assert "player-puuid" not in all_text
    assert "PrivateMate" not in all_text
    assert "HIDDEN" not in all_text
    assert "teammate_" in all_text


def test_bundle_filename_is_safe_and_explicitly_all() -> None:
    generated = datetime(2026, 9, 4, tzinfo=timezone.utc)
    assert ai_bundle_filename("Étoile / Test#EUW", generated) == (
        "LoL-Analytics-AI-Etoile-Test-ALL-2026-09-04.zip"
    )
