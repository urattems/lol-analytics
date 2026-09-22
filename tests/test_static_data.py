from __future__ import annotations

import httpx

from core.static_data import DataDragonService


def test_french_champion_and_item_metadata_with_cached_catalogs() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/versions.json":
            return httpx.Response(200, json=["16.17.1", "16.16.1"])
        if request.url.path.endswith("/champion.json"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "MonkeyKing": {
                            "id": "MonkeyKing",
                            "name": "Wukong",
                            "image": {"full": "MonkeyKing.png"},
                        }
                    }
                },
            )
        if request.url.path.endswith("/item.json"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "3078": {
                            "name": "Force de la trinité",
                            "image": {"full": "3078.png"},
                        }
                    }
                },
            )
        return httpx.Response(404)

    service = DataDragonService(transport=httpx.MockTransport(handler))
    try:
        champion = service.champion("MonkeyKing")
        item = service.item(3078)
        service.champion("MonkeyKing")
        service.item(3078)
    finally:
        service.close()

    assert champion.display_name == "Wukong"
    assert champion.image_url == (
        "https://ddragon.leagueoflegends.com/cdn/16.17.1/img/champion/MonkeyKing.png"
    )
    assert champion.splash_url == (
        "https://ddragon.leagueoflegends.com/cdn/img/champion/splash/MonkeyKing_0.jpg"
    )
    assert item.display_name == "Force de la trinité"
    assert item.image_url == (
        "https://ddragon.leagueoflegends.com/cdn/16.17.1/img/item/3078.png"
    )
    assert calls.count("/api/versions.json") == 1
    assert sum(path.endswith("/champion.json") for path in calls) == 1
    assert sum(path.endswith("/item.json") for path in calls) == 1


def test_unknown_ids_and_network_failure_use_text_fallbacks() -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("offline", request=request)
        )
    )
    service = DataDragonService(transport=transport)
    try:
        champion = service.champion("Shyvana")
        item = service.item(999_999)
    finally:
        service.close()

    assert champion.display_name == "Shyvana"
    assert champion.image_url is None
    assert item.display_name == "Item 999999"
    assert item.image_url is None


def test_unknown_entries_in_valid_catalogs_fall_back_without_crashing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/versions.json":
            return httpx.Response(200, json=["16.17.1"])
        return httpx.Response(200, json={"data": {}})

    service = DataDragonService(transport=httpx.MockTransport(handler))
    try:
        assert service.champion("UnknownChampion").display_name == "UnknownChampion"
        assert service.item(1234).display_name == "Item 1234"
    finally:
        service.close()
