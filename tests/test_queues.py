from __future__ import annotations

from core.queues import queue_name


def test_common_queue_names_are_centralized() -> None:
    assert queue_name(420) == "Ranked Solo/Duo"
    assert queue_name(440) == "Ranked Flex"
    assert queue_name(450) == "ARAM"
    assert queue_name(480) == "Swiftplay"


def test_unknown_queue_has_stable_fallback() -> None:
    assert queue_name(12_345) == "Queue 12345"
    assert queue_name(None) == "Unknown queue"
