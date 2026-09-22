"""Environment-backed settings for the local application."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "lol_analytics.db"


class Settings(BaseModel):
    """Validated runtime settings without exposing secrets in representations."""

    riot_api_key: str | None = Field(default=None, repr=False)
    riot_game_name: str = "YourGameName"
    riot_tag_line: str = "TAG"
    database_path: Path = DEFAULT_DATABASE_PATH
    riot_platform_region: str = "EUW1"
    riot_routing_region: str = "EUROPE"
    demo_mode: bool = False

    @property
    def riot_id(self) -> str:
        """Return the configured display Riot ID."""

        return f"{self.riot_game_name}#{self.riot_tag_line}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from ``.env`` and the process environment once."""

    # Riot IDs are literal text, including dollar signs; never expand ${...}
    # from a player's name into another environment variable (possibly secret).
    load_dotenv(PROJECT_ROOT / ".env", interpolate=False)
    api_key = os.getenv("RIOT_API_KEY", "").strip() or None
    database_value = os.getenv("LOL_ANALYTICS_DB_PATH", "").strip()
    database_path = Path(database_value).expanduser() if database_value else DEFAULT_DATABASE_PATH

    return Settings(
        riot_api_key=api_key,
        riot_game_name=os.getenv("RIOT_GAME_NAME", "YourGameName").strip() or "YourGameName",
        riot_tag_line=os.getenv("RIOT_TAG_LINE", "TAG").strip() or "TAG",
        database_path=database_path,
        demo_mode=os.getenv("LOL_ANALYTICS_DEMO", "").strip() == "1",
        riot_platform_region=(
            os.getenv("RIOT_PLATFORM_REGION", "EUW1").strip().upper() or "EUW1"
        ),
        riot_routing_region=(
            os.getenv("RIOT_ROUTING_REGION", "EUROPE").strip().upper() or "EUROPE"
        ),
    )
