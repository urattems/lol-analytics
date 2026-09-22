from __future__ import annotations

from config.settings import Settings


def test_riot_id_platform_and_routing_regions_are_distinct() -> None:
    settings = Settings(riot_game_name="Example", riot_tag_line="EUW")

    assert settings.riot_id == "Example#EUW"
    assert settings.riot_tag_line == "EUW"
    assert settings.riot_platform_region == "EUW1"
    assert settings.riot_routing_region == "EUROPE"
