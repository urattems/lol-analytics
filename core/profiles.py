"""Explicit local profile selection and bounded Riot lookups, without UI state."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from config.settings import Settings
from core.db import Database, ProfileRemovalResult
from core.import_lock import ImportLock, ensure_riot_ready
from core.exceptions import RiotConfigurationError
from core.models import RiotAccount
from core.riot_api import RiotAPIClient
from core.sync import ProgressCallback, RiotAPI, SyncResult, SyncService


# Platform is selected independently from the Riot ID tag (which is user chosen).
PLATFORM_ROUTING_REGIONS = {
    "EUW1": "EUROPE", "EUN1": "EUROPE", "TR1": "EUROPE", "RU": "EUROPE",
    "NA1": "AMERICAS", "BR1": "AMERICAS", "LA1": "AMERICAS", "LA2": "AMERICAS",
    "KR": "ASIA", "JP1": "ASIA",
    "OC1": "SEA", "SG2": "SEA", "PH2": "SEA", "TH2": "SEA",
    "TW2": "SEA", "VN2": "SEA",
}


def parse_riot_id(value: str) -> tuple[str, str]:
    """Validate a usable Name#TAG while allowing Unicode names and internal spaces."""

    if not isinstance(value, str) or value.count("#") != 1:
        raise ValueError("Saisissez un Riot ID au format Nom#TAG.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Le Riot ID contient des caractères de contrôle invalides.")
    game_name, tag_line = (part.strip() for part in value.split("#", 1))
    if not game_name or not tag_line or len(game_name) > 64 or len(tag_line) > 16:
        raise ValueError("Le nom et le tag du Riot ID doivent être renseignés et valides.")
    if any(char.isspace() for char in tag_line):
        raise ValueError("Le tag du Riot ID ne peut pas contenir d'espace.")
    return game_name, tag_line


def routing_for_platform(platform_region: str) -> str:
    """Resolve a supported platform explicitly; never guess from a Riot ID tag."""

    if not isinstance(platform_region, str):
        raise ValueError("Sélectionnez un serveur Riot valide.")
    platform = platform_region.strip().upper()
    if platform not in PLATFORM_ROUTING_REGIONS:
        raise ValueError("Serveur Riot non pris en charge. Choisissez un serveur de la liste.")
    return PLATFORM_ROUTING_REGIONS[platform]


def settings_for_lookup(base: Settings, riot_id: str, platform_region: str) -> Settings:
    """Copy profile fields without mutating the primary settings or its API key."""

    game_name, tag_line = parse_riot_id(riot_id)
    routing = routing_for_platform(platform_region)
    return base.model_copy(update={
        "riot_game_name": game_name,
        "riot_tag_line": tag_line,
        "riot_platform_region": platform_region.strip().upper(),
        "riot_routing_region": routing,
    })


def settings_for_profile(base: Settings, profile: Mapping[str, object]) -> Settings:
    """Select a registered profile; legacy rows can use the configured platform."""

    game_name, tag_line = profile.get("game_name"), profile.get("tag_line")
    if not isinstance(game_name, str) or not isinstance(tag_line, str):
        raise ValueError("Ce profil local ne possède pas de Riot ID utilisable.")
    platform = profile.get("platform_region") or base.riot_platform_region
    return settings_for_lookup(base, f"{game_name}#{tag_line}", str(platform))


def import_profile(
    base: Settings,
    riot_id: str,
    platform_region: str,
    count: int = 20,
    progress: ProgressCallback | None = None,
    *,
    api: RiotAPI | None = None,
    expected_puuid: str | None = None,
) -> SyncResult:
    """Resolve and import only the requested recent window on explicit submission.

    Shared matches are reused. No timeline, backfill or background network work
    is triggered. ``api`` allows deterministic callers/tests without a live key.
    """

    settings = settings_for_lookup(base, riot_id, platform_region)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError("Choisissez entre 1 et 100 parties récentes.")
    database = Database(settings.database_path)

    def run(client: RiotAPI) -> SyncResult:
        with ImportLock(database.path):
            ensure_riot_ready(database.path)
            return SyncService(
                client, database, settings.riot_platform_region, settings.riot_routing_region
            ).import_recent_player(
                settings.riot_game_name, settings.riot_tag_line, count, progress, expected_puuid=expected_puuid
            )

    if api is not None:
        return run(api)
    if not settings.riot_api_key:
        raise RiotConfigurationError("RIOT_API_KEY is missing.")
    with RiotAPIClient(
        settings.riot_api_key, settings.riot_routing_region, settings.riot_platform_region
    ) as client:
        return run(client)


def remove_profile_from_library(base: Settings, puuid: str) -> ProfileRemovalResult:
    """Delete a secondary profile's exclusive data; settings identify the primary."""
    return Database(base.database_path).remove_library_profile(
        puuid, base.riot_game_name, base.riot_tag_line,
    )


def register_local_profile(
    database: Database, puuid: str, platform_region: str
) -> dict[str, object]:
    """Explicitly save one locally observed identity, without contacting Riot.

    Merely listing teammates never registers them. A participant without a real
    stored Riot ID cannot be turned into a searchable profile using an alias.
    """

    if (
        not isinstance(puuid, str)
        or not puuid
        or puuid.casefold() in {"none", "null", "unknown", "n/a"}
        or not all(char.isascii() and (char.isalnum() or char in "-_") for char in puuid)
    ):
        raise ValueError("Identité de joueur locale invalide.")
    routing = routing_for_platform(platform_region)
    existing = database.get_player(puuid)
    if existing is not None:
        return existing
    identity = database.participant_identity(puuid)
    if identity is None:
        raise ValueError("Aucun Riot ID réel disponible localement pour ce joueur.")
    game_name, tag_line = parse_riot_id(f"{identity['game_name']}#{identity['tag_line']}")
    database.upsert_player(
        RiotAccount(puuid=puuid, game_name=game_name, tag_line=tag_line),
        platform_region.strip().upper(), routing,
    )
    player = database.get_player(puuid)
    assert player is not None
    return player
