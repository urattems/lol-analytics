from __future__ import annotations

import pytest

from core.models import calculate_cs, extract_patch, parse_riot_match, side_from_team_id


def test_patch_is_normalized_once() -> None:
    assert extract_patch("16.17.123.456") == "16.17"
    with pytest.raises(ValueError):
        extract_patch("invalid")


def test_team_id_maps_to_blue_and_red() -> None:
    assert side_from_team_id(100) == "blue"
    assert side_from_team_id(200) == "red"
    assert side_from_team_id(300) is None
    assert side_from_team_id(None) is None


def test_cs_requires_both_riot_components() -> None:
    assert calculate_cs(42, 120) == 162
    assert calculate_cs(42, None) is None


def test_match_payload_parses_cs_items_and_damage_share(
    riot_match_payload: dict[str, object]
) -> None:
    match = parse_riot_match(riot_match_payload)
    player = match.participants[0]

    assert match.match_id == "EUW1_123456"
    assert match.patch == "16.17"
    assert match.duration == 1902
    assert player.cs_total == 162
    assert player.items == [1001, 3078, 3153, 3053, 3047, 0, 3340]
    assert player.side == "blue"
    assert player.damage_share == pytest.approx(2 / 3)


def test_damage_share_is_null_if_team_damage_is_incomplete(
    riot_match_payload: dict[str, object]
) -> None:
    riot_match_payload["info"]["participants"][1].pop("totalDamageDealtToChampions")  # type: ignore[index]
    match = parse_riot_match(riot_match_payload)
    assert match.participants[0].damage_share is None
    assert match.participants[1].damage_dealt is None
