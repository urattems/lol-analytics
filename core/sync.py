"""Incremental Riot-to-SQLite synchronization and command-line validation."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from config.settings import Settings, get_settings
from core.db import Database
from core.import_lock import ImportLock, ensure_riot_ready
from core.exceptions import RiotConfigurationError, RiotError
from core.models import RiotAccount, parse_riot_match
from core.riot_api import RiotAPIClient


LOGGER = logging.getLogger(__name__)


class RiotAPI(Protocol):
    """Interface used by synchronization, allowing deterministic test doubles."""

    def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount: ...

    def get_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, queue: int | None = None
    ) -> list[str]: ...

    def get_match(self, match_id: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class SyncResult:
    """Observable outcome of one sync run."""

    account: RiotAccount
    discovered: int
    inserted: int
    already_known: int

    @property
    def up_to_date(self) -> bool:
        return self.inserted == 0


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of filling the local library toward a requested total."""

    account: RiotAccount
    target: int | None
    local_before: int
    discovered: int
    inserted: int
    already_known: int
    local_after: int
    history_exhausted: bool

    @property
    def already_sufficient(self) -> bool:
        return self.target is not None and self.local_before >= self.target


@dataclass(frozen=True)
class ImportProgress:
    """Framework-independent progress update for Streamlit or CLI callers."""

    phase: Literal["discovering", "importing"]
    current: int
    total: int | None
    discovered: int


ProgressCallback = Callable[[ImportProgress], None]


class SyncService:
    """Synchronize new matches and backfill older local history safely."""

    def __init__(
        self,
        api: RiotAPI,
        database: Database,
        platform_region: str = "EUW1",
        routing_region: str = "EUROPE",
    ) -> None:
        self.api = api
        self.database = database
        self.platform_region = platform_region.upper()
        self.routing_region = routing_region.upper()

    def sync_player(
        self,
        game_name: str,
        tag_line: str,
        count: int | None = None,
        progress: ProgressCallback | None = None,
        *, expected_puuid: str | None = None,
    ) -> SyncResult:
        """Import every new match, or a caller-supplied maximum, newest first."""

        if count is not None and count < 1:
            raise ValueError("count must be at least 1")

        account = self._resolve_player(game_name, tag_line, expected_puuid)

        previous_anchor = self.database.get_sync_state(account.puuid)
        local_count = self.database.count_player_matches(account.puuid)
        effective_limit = 20 if local_count == 0 and count is None else count
        match_ids, newest_id, can_advance_anchor, known_count = self._discover_new_match_ids(
            account.puuid, effective_limit, previous_anchor, local_count, progress
        )
        LOGGER.info("Found %d new match ID(s).", len(match_ids))

        inserted, newly_known = self._import_match_ids(match_ids, progress, account.puuid)
        known_count += newly_known

        anchor = newest_id if can_advance_anchor and newest_id else previous_anchor
        self.database.update_sync_state(account.puuid, anchor)
        LOGGER.info("Inserted %d new match(es).", inserted)
        LOGGER.info("Sync completed.")
        return SyncResult(account, len(match_ids), inserted, known_count)

    def import_recent_player(
        self,
        game_name: str,
        tag_line: str,
        count: int = 20,
        progress: ProgressCallback | None = None,
        *,
        expected_puuid: str | None = None,
    ) -> SyncResult:
        """Import an explicit recent snapshot, counting shared matches in its limit.

        Existing incremental anchors only advance if the snapshot reaches them;
        otherwise a later full sync must still fill the intervening gap.
        """

        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError("count must be an integer between 1 and 100")
        account = self._resolve_player(game_name, tag_line, expected_puuid)
        previous_anchor = self.database.get_sync_state(account.puuid)
        recent = list(dict.fromkeys(self.api.get_match_ids(account.puuid, count=count)))[:count]
        known = self.database.existing_match_ids(recent)
        unseen = [match_id for match_id in recent if match_id not in known]
        if progress:
            progress(ImportProgress("discovering", 1, 1, len(unseen)))
        inserted, newly_known = self._import_match_ids(unseen, progress, account.puuid)
        can_advance = previous_anchor is None or previous_anchor in recent
        anchor = recent[0] if recent and can_advance else previous_anchor
        self.database.update_sync_state(account.puuid, anchor)
        return SyncResult(account, len(unseen), inserted, len(known) + newly_known)

    def backfill_history(
        self,
        game_name: str,
        tag_line: str,
        target: int | None,
        progress: ProgressCallback | None = None,
        *, expected_puuid: str | None = None,
    ) -> BackfillResult:
        """Fill older history until a local total target or Riot's available end."""

        if target is not None and target < 1:
            raise ValueError("target must be positive or None")
        account = self._resolve_player(game_name, tag_line, expected_puuid)
        local_before = self.database.count_player_matches(account.puuid)
        if target is not None and local_before >= target:
            return BackfillResult(
                account, target, local_before, 0, 0, 0, local_before, False
            )

        needed = None if target is None else target - local_before
        match_ids, known_count, exhausted = self._discover_backfill_match_ids(
            account.puuid, needed, progress
        )
        inserted, newly_known = self._import_match_ids(match_ids, progress, account.puuid)
        local_after = self.database.count_player_matches(account.puuid)
        return BackfillResult(
            account=account,
            target=target,
            local_before=local_before,
            discovered=len(match_ids),
            inserted=inserted,
            already_known=known_count + newly_known,
            local_after=local_after,
            history_exhausted=exhausted,
        )

    def _resolve_player(self, game_name: str, tag_line: str, expected_puuid: str | None = None) -> RiotAccount:
        LOGGER.info("Resolving Riot ID...")
        account = self.api.resolve_account(game_name, tag_line)
        if expected_puuid is not None and account.puuid != expected_puuid:
            raise ValueError("Ce Riot ID ne correspond plus au joueur rencontré. Recherchez son nouveau Riot ID depuis Profils.")
        LOGGER.info("PUUID resolved.")
        self.database.initialize()
        self.database.upsert_player(
            account, self.platform_region, self.routing_region
        )
        return account

    def _import_match_ids(
        self, match_ids: list[str], progress: ProgressCallback | None, puuid: str
    ) -> tuple[int, int]:
        inserted = 0
        already_known = 0
        for position, match_id in enumerate(match_ids, start=1):
            LOGGER.info("Fetching %d/%d...", position, len(match_ids))
            if progress:
                progress(ImportProgress("importing", position - 1, len(match_ids), len(match_ids)))
            parsed_match = parse_riot_match(self.api.get_match(match_id))
            if parsed_match.match_id != match_id:
                raise ValueError("Match ID does not match the request.")
            if sum(row.puuid == puuid for row in parsed_match.participants) != 1:
                raise ValueError("Match does not contain the requested player exactly once.")
            if self.database.insert_match(parsed_match):
                inserted += 1
            else:
                already_known += 1
            if progress:
                progress(ImportProgress("importing", position, len(match_ids), len(match_ids)))
        return inserted, already_known

    def _discover_new_match_ids(
        self,
        puuid: str,
        limit: int | None,
        previous_anchor: str | None,
        local_count: int,
        progress: ProgressCallback | None,
    ) -> tuple[list[str], str | None, bool, int]:
        page_size = 100
        max_pages = 1_000
        start = 0
        newest_id: str | None = None
        unseen: list[str] = []
        seen_ids: set[str] = set()
        already_known = 0
        anchor_reached = False
        exhausted = False

        for page_number in range(max_pages):
            page = self.api.get_match_ids(puuid, start=start, count=page_size)
            if not page:
                exhausted = True
                break
            if newest_id is None:
                newest_id = page[0]
            known_on_page = self.database.existing_match_ids(page)
            made_progress = False

            for match_id in page:
                if match_id in seen_ids:
                    continue
                made_progress = True
                seen_ids.add(match_id)
                if previous_anchor is not None and match_id == previous_anchor:
                    already_known += 1
                    anchor_reached = True
                    break
                if match_id in known_on_page:
                    already_known += 1
                    continue
                unseen.append(match_id)
                if limit is not None and len(unseen) >= limit:
                    break

            if progress:
                progress(ImportProgress("discovering", page_number + 1, None, len(unseen)))
            if anchor_reached or (limit is not None and len(unseen) >= limit):
                break
            if len(page) < page_size:
                exhausted = True
                break
            if not made_progress:
                LOGGER.warning("Riot pagination repeated IDs; stopping safely.")
                break
            start += len(page)

        can_advance_anchor = anchor_reached or exhausted or local_count == 0
        return unseen, newest_id, can_advance_anchor, already_known

    def _discover_backfill_match_ids(
        self,
        puuid: str,
        needed: int | None,
        progress: ProgressCallback | None,
    ) -> tuple[list[str], int, bool]:
        page_size = 100
        max_pages = 1_000
        start = 0
        unseen: list[str] = []
        seen_ids: set[str] = set()
        already_known = 0
        exhausted = False

        for page_number in range(max_pages):
            page = self.api.get_match_ids(puuid, start=start, count=page_size)
            if not page:
                exhausted = True
                break
            known_on_page = self.database.existing_match_ids(page)
            made_progress = False
            for match_id in page:
                if match_id in seen_ids:
                    continue
                made_progress = True
                seen_ids.add(match_id)
                if match_id in known_on_page:
                    already_known += 1
                    continue
                unseen.append(match_id)
                if needed is not None and len(unseen) >= needed:
                    break
            if progress:
                progress(ImportProgress("discovering", page_number + 1, None, len(unseen)))
            if needed is not None and len(unseen) >= needed:
                break
            if len(page) < page_size:
                exhausted = True
                break
            if not made_progress:
                LOGGER.warning("Riot pagination repeated IDs; stopping backfill safely.")
                break
            start += len(page)
        else:
            LOGGER.warning("Backfill pagination safety limit reached.")

        return unseen, already_known, exhausted


def _format_duration(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _print_recent_games(database: Database, account: RiotAccount, limit: int) -> None:
    print(f"\nPlayer: {account.game_name}#{account.tag_line}\n")
    games = database.recent_games_for_player(account.puuid, limit)
    print(f"{len(games)} match(es) available locally.\n")
    for index, game in enumerate(games, start=1):
        outcome = "WIN " if game["win"] else "LOSS"
        print(
            f"{index:02d}. {game['champion']} | {outcome} | "
            f"{game['kills']}/{game['deaths']}/{game['assists']} | "
            f"{_format_duration(int(game['duration']))}"
        )


def _service_from_settings(settings: Settings) -> tuple[RiotAPIClient, SyncService]:
    if not settings.riot_api_key:
        raise RiotConfigurationError("RIOT_API_KEY is missing.")
    database = Database(settings.database_path)
    api = RiotAPIClient(
        settings.riot_api_key,
        settings.riot_routing_region,
        settings.riot_platform_region,
    )
    return api, SyncService(
        api,
        database,
        settings.riot_platform_region,
        settings.riot_routing_region,
    )


def sync_from_settings(
    settings: Settings,
    count: int | None = None,
    progress: ProgressCallback | None = None,
) -> SyncResult:
    """Run the existing sync service from validated application settings."""

    api, service = _service_from_settings(settings)
    with api, ImportLock(settings.database_path):
        ensure_riot_ready(settings.database_path)
        return service.sync_player(
            settings.riot_game_name, settings.riot_tag_line, count, progress
        )


def backfill_from_settings(
    settings: Settings,
    target: int | None,
    progress: ProgressCallback | None = None,
) -> BackfillResult:
    """Backfill the configured player's historical local library."""

    api, service = _service_from_settings(settings)
    with api, ImportLock(settings.database_path):
        ensure_riot_ready(settings.database_path)
        return service.backfill_history(
            settings.riot_game_name, settings.riot_tag_line, target, progress
        )


def main() -> int:
    """Run a real, bounded synchronization from ``python -m core.sync``."""

    parser = argparse.ArgumentParser(description="Sync recent Riot matches into local SQLite.")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Optional safety maximum; by default all newly played matches are synchronized",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    database = Database(settings.database_path)
    result = sync_from_settings(settings, args.count)
    if result.up_to_date:
        print("\n0 nouveau match. Base déjà à jour.")
    else:
        print(f"\n{result.inserted} match(es) synchronisé(s).")
    _print_recent_games(database, result.account, args.count or 20)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RiotError as error:
        LOGGER.error("Synchronization failed: %s", error)
        raise SystemExit(2) from error
