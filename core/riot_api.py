"""Synchronous, rate-limit-aware client for Account V1 and Match V5."""

from __future__ import annotations

import logging
import math
import time
from email.utils import parsedate_to_datetime
from datetime import timezone
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from core.exceptions import (
    RiotAPIError,
    RiotAuthenticationError,
    RiotConfigurationError,
    RiotNetworkError,
    RiotNotFoundError,
    RiotRateLimitError,
    RiotServerError,
)
from core.models import RiotAccount
from core.network import environment_client


LOGGER = logging.getLogger(__name__)
ACCOUNT_HOST = "https://{routing}.api.riotgames.com"
MATCH_HOST = "https://{routing}.api.riotgames.com"


class RiotAPIClient:
    """Minimal Riot client with bounded retries and no secret logging."""

    def __init__(
        self,
        api_key: str,
        routing_region: str = "EUROPE",
        platform_region: str = "EUW1",
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
        defer_rate_limits: bool = False,
    ) -> None:
        if not api_key.strip():
            raise RiotConfigurationError("RIOT_API_KEY is missing.")
        self.routing_region = routing_region.lower()
        self.platform_region = platform_region.upper()
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self._sleep = sleep
        self.defer_rate_limits = defer_rate_limits
        self._client = environment_client(
            headers={"X-Riot-Token": api_key},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    def __enter__(self) -> RiotAPIClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""

        self._client.close()

    def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount:
        """Resolve a Riot ID to its stable PUUID through Account V1."""

        account_routing = "asia" if self.routing_region == "sea" else self.routing_region
        url = (
            f"{ACCOUNT_HOST.format(routing=account_routing)}"
            f"/riot/account/v1/accounts/by-riot-id/{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
        )
        return RiotAccount.model_validate(self._get_json(url))

    def get_summoner(self, puuid: str) -> dict[str, Any]:
        """Check that a PUUID has a LoL profile on the selected platform.

        Account V1 alone cannot validate a platform: EUW and EUNE, for
        example, use the same regional Account V1 endpoint.
        """
        url = (f"https://{self.platform_region.lower()}.api.riotgames.com"
               f"/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}")
        payload = self._get_json(url)
        if not isinstance(payload, dict) or payload.get("puuid") != puuid:
            raise RiotAPIError("Riot returned an invalid summoner identity.")
        return payload

    def get_league_entries(self, puuid: str) -> list[dict[str, Any]]:
        """Current official ranked entries, not historical rank or hidden MMR."""
        url = (f"https://{self.platform_region.lower()}.api.riotgames.com"
               f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}")
        payload = self._get_json(url)
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise RiotAPIError('Riot returned invalid ranked entries.')
        if any('puuid' in row and row['puuid'] != puuid for row in payload):
            raise RiotAPIError('Riot returned another ranked identity.')
        return payload

    def get_match_ids(
        self,
        puuid: str,
        start: int = 0,
        count: int = 20,
        queue: int | None = None,
    ) -> list[str]:
        """Retrieve one Match V5 ID page (Riot permits at most 100 IDs)."""

        if not 1 <= count <= 100:
            raise ValueError("count must be between 1 and 100")
        params: dict[str, int] = {"start": max(0, start), "count": count}
        if queue is not None:
            params["queue"] = queue
        url = (
            f"{MATCH_HOST.format(routing=self.routing_region)}"
            f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids"
        )
        payload = self._get_json(url, params=params)
        if not isinstance(payload, list) or not all(isinstance(value, str) for value in payload):
            raise RiotAPIError("Riot returned an invalid match ID response.")
        return payload

    def get_match(self, match_id: str) -> dict[str, Any]:
        """Retrieve one Match V5 detail payload."""

        url = (
            f"{MATCH_HOST.format(routing=self.routing_region)}"
            f"/lol/match/v5/matches/{quote(match_id, safe='')}"
        )
        payload = self._get_json(url)
        if not isinstance(payload, dict):
            raise RiotAPIError("Riot returned an invalid match response.")
        return payload

    def get_timeline(self, match_id: str) -> dict[str, Any]:
        """Retrieve the official Match V5 timeline payload for one match."""

        url = (
            f"{MATCH_HOST.format(routing=self.routing_region)}"
            f"/lol/match/v5/matches/{quote(match_id, safe='')}/timeline"
        )
        payload = self._get_json(url)
        if not isinstance(payload, dict):
            raise RiotAPIError("Riot returned an invalid timeline response.")
        return payload

    def _get_json(self, url: str, params: dict[str, int] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt >= self.max_retries:
                    raise RiotNetworkError("Unable to reach Riot after retries.") from exc
                self._wait_with_backoff(attempt)
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise RiotAPIError("Riot returned malformed JSON.") from exc
            if response.status_code in (401, 403):
                raise RiotAuthenticationError(
                    f"Riot rejected the request with HTTP {response.status_code}."
                )
            if response.status_code == 404:
                raise RiotNotFoundError("The requested Riot resource was not found.")
            if response.status_code == 429:
                if self.defer_rate_limits:
                    raise RiotRateLimitError('Riot requests a deferred retry.',
                                             retry_after=self._deferred_retry_after(response))
                if attempt >= self.max_retries:
                    raise RiotRateLimitError("Riot rate limit persisted after retries.")
                retry_after = self._retry_after_seconds(response, attempt)
                LOGGER.warning("Riot rate limit reached; retrying after %.1f seconds.", retry_after)
                self._sleep(retry_after)
                continue
            if 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    raise RiotServerError(
                        f"Riot server error HTTP {response.status_code} persisted after retries."
                    )
                self._wait_with_backoff(attempt)
                continue
            raise RiotAPIError(f"Unexpected Riot response: HTTP {response.status_code}.")

        raise RiotAPIError("Riot request ended unexpectedly.")

    def _retry_after_seconds(self, response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value is not None:
            try:
                seconds = float(value)
                if not math.isfinite(seconds) or not 0 <= seconds <= 60:
                    # Do not freeze a Streamlit session or retry before Riot permits it.
                    raise RiotRateLimitError("Riot requested an unsafe or long delay; retry later.")
                return seconds
            except ValueError:
                pass
        return self.backoff_base * (2**attempt)

    @staticmethod
    def _deferred_retry_after(response: httpx.Response) -> float | None:
        """Long delays belong to a cancellable job, never a UI-thread sleep.

        Missing header: conservatively wait two minutes. Invalid or >24h
        header: pause for user intervention; do not cap and retry too early.
        Numeric seconds and HTTP dates are both accepted.
        """
        value = response.headers.get('Retry-After')
        if value is None:
            return 120.0
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = max(0.0, date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError, OSError):
                return None
        return seconds if math.isfinite(seconds) and 0 <= seconds <= 86400 else None

    def _wait_with_backoff(self, attempt: int) -> None:
        self._sleep(self.backoff_base * (2**attempt))
