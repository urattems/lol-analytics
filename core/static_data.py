"""Cached, failure-tolerant French Riot Data Dragon metadata."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from core.network import environment_client
from core.exceptions import ProxyConfigurationError
from core.diagnostics import record_failure


DDRAGON_BASE_URL = "https://ddragon.leagueoflegends.com"
DEFAULT_LOCALE = "fr_FR"
FALLBACK_VERSION = "16.16.1"


@dataclass(frozen=True)
class ChampionStaticData:
    internal_name: str
    display_name: str
    image_url: str | None
    splash_url: str | None = None


@dataclass(frozen=True)
class ItemStaticData:
    item_id: int
    display_name: str
    image_url: str | None


class DataDragonService:
    """Load small static catalogs once and degrade to text when unavailable."""

    def __init__(
        self,
        locale: str = DEFAULT_LOCALE,
        fallback_version: str = FALLBACK_VERSION,
        timeout: float = 4.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.locale = locale
        self.fallback_version = fallback_version
        try:
            self._client = environment_client(
                timeout=httpx.Timeout(timeout), follow_redirects=True, transport=transport,
            )
        except ProxyConfigurationError as error:
            record_failure(error, phase='metadata')
            self._client = None
        self._version: str | None = None
        self._champions: dict[str, dict[str, Any]] | None = None
        self._items: dict[str, dict[str, Any]] | None = None
        self._fallbacks: set[str] = set()

    @property
    def fallback_count(self) -> int:
        """Count unique static-data lookups that fell back to text."""

        return len(self._fallbacks)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    @property
    def version(self) -> str:
        if self._version is None:
            payload = self._safe_json(f"{DDRAGON_BASE_URL}/api/versions.json")
            if isinstance(payload, list) and payload and isinstance(payload[0], str):
                self._version = payload[0]
            else:
                self._version = self.fallback_version
        return self._version

    def champion(self, internal_name: str) -> ChampionStaticData:
        """Return localized champion metadata or a safe text-only fallback."""

        data = self._champion_catalog().get(internal_name)
        if not isinstance(data, dict):
            self._fallbacks.add(f"champion:{internal_name}")
            return ChampionStaticData(internal_name, internal_name, None, None)
        display_name = data.get("name")
        image = data.get("image")
        image_name = image.get("full") if isinstance(image, dict) else None
        return ChampionStaticData(
            internal_name=internal_name,
            display_name=display_name if isinstance(display_name, str) else internal_name,
            image_url=self._asset_url("champion", image_name),
            splash_url=(
                f"{DDRAGON_BASE_URL}/cdn/img/champion/splash/{internal_name}_0.jpg"
            ),
        )

    def item(self, item_id: int) -> ItemStaticData:
        """Return localized item metadata or an ID-based fallback."""

        normalized_id = int(item_id)
        data = self._item_catalog().get(str(normalized_id))
        fallback_name = f"Item {normalized_id}"
        if not isinstance(data, dict):
            self._fallbacks.add(f"item:{normalized_id}")
            return ItemStaticData(normalized_id, fallback_name, None)
        display_name = data.get("name")
        image = data.get("image")
        image_name = image.get("full") if isinstance(image, dict) else None
        return ItemStaticData(
            item_id=normalized_id,
            display_name=display_name if isinstance(display_name, str) else fallback_name,
            image_url=self._asset_url("item", image_name),
        )

    def _champion_catalog(self) -> dict[str, dict[str, Any]]:
        if self._champions is None:
            self._champions = self._load_catalog("champion")
        return self._champions

    def _item_catalog(self) -> dict[str, dict[str, Any]]:
        if self._items is None:
            self._items = self._load_catalog("item")
        return self._items

    def _load_catalog(self, kind: str) -> dict[str, dict[str, Any]]:
        url = (
            f"{DDRAGON_BASE_URL}/cdn/{self.version}/data/"
            f"{self.locale}/{kind}.json"
        )
        payload = self._safe_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def _asset_url(self, kind: str, image_name: object) -> str | None:
        if not isinstance(image_name, str) or not image_name:
            return None
        return f"{DDRAGON_BASE_URL}/cdn/{self.version}/img/{kind}/{image_name}"

    def _safe_json(self, url: str) -> Any:
        if self._client is None:
            return None
        try:
            response = self._client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            record_failure(error, phase='metadata')
            return None


@lru_cache(maxsize=1)
def get_static_data_service() -> DataDragonService:
    """Share one lazy catalog cache across Streamlit reruns."""

    return DataDragonService()
