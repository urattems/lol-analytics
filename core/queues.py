"""Stable Riot queue labels shared by UI and export layers."""

from __future__ import annotations


QUEUE_NAMES = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
}


def queue_name(queue_id: int | None) -> str:
    """Return a readable label while preserving unknown queue IDs."""

    if queue_id is None:
        return "Unknown queue"
    return QUEUE_NAMES.get(int(queue_id), f"Queue {queue_id}")
