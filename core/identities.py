"""Local display identities and explicit, identity-only Match V5 hydration.

These objects belong to the UI boundary, never to the export payload models.
The participant PUUID remains the key across renames. No lookup runs on read.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from collections.abc import Callable
import time

from analytics.privacy import stable_alias
from config.settings import Settings
from core.db import Database
from core.import_lock import ImportLock, ensure_riot_ready
from core.exceptions import RiotConfigurationError
from core.profiles import parse_riot_id
from core.riot_api import RiotAPIClient


def valid_riot_identity(name: object, tag: object) -> tuple[str, str] | None:
    if not isinstance(name, str) or not isinstance(tag, str):
        return None
    if name.strip().casefold() in {"", "none", "null", "unknown", "n/a"} or tag.strip().casefold() in {"", "none", "null", "unknown", "n/a"}:
        return None
    try:
        return parse_riot_id(f"{name}#{tag}")
    except ValueError:
        return None


@dataclass(frozen=True)
class PlayerIdentity:
    puuid: str
    game_name: str | None = None
    tag_line: str | None = None
    last_seen: int | None = None
    previous_names: tuple[str, ...] = ()
    registered: bool = False

    @property
    def riot_id(self) -> str | None:
        return f"{self.game_name}#{self.tag_line}" if self.game_name and self.tag_line else None

    @property
    def label(self) -> str:
        return self.riot_id or stable_alias(self.puuid, "teammate")

    @property
    def local_label(self) -> str:
        """Human UI fallback, independent of the stable export alias contract."""
        return self.riot_id or f"Joueur non identifié · #{stable_alias(self.puuid, 'teammate')[-4:].upper()}"


def local_identity_index(participants: list[dict], profiles: list[dict] = ()) -> dict[str, PlayerIdentity]:
    """Newest *known* name, not the first occurrence or the newest null field.

Registered Account V1 names are a fallback when no match name is known. They
have no observation timestamp, so cannot silently override a later rename.
"""
    observations: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    identifiers = {str(p["puuid"]) for p in participants if p.get("puuid")}
    registered = {str(p["puuid"]): p for p in profiles if p.get("puuid")}
    identifiers.update(registered)
    for row in participants:
        pair = valid_riot_identity(row.get("teammate_game_name"), row.get("teammate_tag_line"))
        if row.get("puuid") and pair:
            stamp = row.get("game_creation")
            stamp = int(stamp) if isinstance(stamp, (int, float)) and 0 <= stamp <= 253370764800000 else -1
            observations[str(row["puuid"])].append((stamp, str(row.get("match_id", "")), *pair))
    result = {}
    for puuid in identifiers:
        entries = sorted(observations.get(puuid, []), reverse=True)
        profile = registered.get(puuid, {})
        pair = (entries[0][2], entries[0][3]) if entries else valid_riot_identity(profile.get("game_name"), profile.get("tag_line"))
        label = f"{pair[0]}#{pair[1]}" if pair else None
        previous = tuple(dict.fromkeys(f"{r[2]}#{r[3]}" for r in entries if f"{r[2]}#{r[3]}" != label))
        result[puuid] = PlayerIdentity(puuid, *(pair or (None, None)),
            entries[0][0] if entries and entries[0][0] >= 0 else None, previous, puuid in registered)
    return result


@dataclass(frozen=True)
class HydrationProgress:
    total: int
    checked: int = 0
    recovered: int = 0
    already_known: int = 0
    skipped: int = 0
    remaining: int = 0


class IdentityHydrationService:
    def __init__(self, database: Database, api):
        self.database, self.api = database, api

    def run(self, puuid: str, progress: Callable[[HydrationProgress], None] | None = None,
            *, retry_unavailable: bool = False) -> HydrationProgress:
        """At most one detail request per relevant local match, newest first.

        Each successful response commits its identity updates and checkpoint
        together. Failures/interruptions propagate without marking completion;
        a later explicit run resumes. Successful responses with missing Riot
        fields are remembered, and only retried by explicit opt-in.
        """
        participants = self.database.participants_for_player_matches(puuid)
        identities = local_identity_index(participants, self.database.list_players())
        known = {key for key, value in identities.items() if value.riot_id}
        missing = set(identities) - known
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in participants:
            groups[str(row["match_id"])].append(row)
        with self.database.connection() as connection:
            completed = {str(r[0]) for r in connection.execute("SELECT match_id FROM identity_fetch_status")}
        candidates = [key for key, rows in groups.items()
                      if any(str(r["puuid"]) in missing for r in rows)
                      and (retry_unavailable or key not in completed)]
        candidates.sort(key=lambda key: (groups[key][0].get("game_creation") or -1, key), reverse=True)
        total, checked, recovered, skipped = len(candidates), 0, 0, 0
        def report():
            state = HydrationProgress(total, checked, recovered, len(known), skipped, len(missing))
            if progress:
                progress(state)
            return state
        report()
        for match_id in candidates:
            local = {str(r["puuid"]) for r in groups[match_id]}
            if not local.intersection(missing):
                skipped += 1
                report()
                continue
            payload = self.api.get_match(match_id)
            if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict) or payload["metadata"].get("matchId") != match_id:
                raise ValueError("Le détail reçu ne correspond pas à la partie locale.")
            rows = payload.get("info", {}).get("participants") if isinstance(payload.get("info"), dict) else None
            if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
                raise ValueError("Les participants du détail reçu sont invalides.")
            response_ids = [r.get("puuid") for r in rows]
            if any(not isinstance(key, str) or not key for key in response_ids) or len(set(response_ids)) != len(response_ids) or not local.issubset(response_ids):
                raise ValueError("L’identité des participants du détail reçu est ambiguë.")
            updates = []
            for row in rows:
                pair = valid_riot_identity(row.get("riotIdGameName"), row.get("riotIdTagline"))
                if row["puuid"] in local and pair:
                    updates.append((*pair, match_id, row["puuid"]))
            with self.database.connection() as connection:
                # The only writable participant columns at this boundary.
                connection.executemany("UPDATE participants SET teammate_game_name=?, teammate_tag_line=? WHERE match_id=? AND puuid=?", updates)
                connection.execute("INSERT INTO identity_fetch_status(match_id,checked_at) VALUES (?,?) ON CONFLICT(match_id) DO UPDATE SET checked_at=excluded.checked_at", (match_id, int(time.time())))
            found = {row[3] for row in updates}.intersection(missing)
            recovered += len(found)
            missing.difference_update(found)
            checked += 1
            report()
        return report()


def hydrate_identities(settings: Settings, puuid: str, progress=None, *, retry_unavailable=False):
    """Called only after the user presses the recovery button."""
    if not settings.riot_api_key or settings.demo_mode:
        raise RiotConfigurationError("La récupération nécessite une clé Riot hors mode démo.")
    with ImportLock(settings.database_path), RiotAPIClient(settings.riot_api_key, settings.riot_routing_region, settings.riot_platform_region) as api:
        ensure_riot_ready(settings.database_path)
        return IdentityHydrationService(Database(settings.database_path), api).run(puuid, progress, retry_unavailable=retry_unavailable)
