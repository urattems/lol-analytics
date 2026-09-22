"""Deterministic local aliases and export-boundary privacy guards."""

from __future__ import annotations

import hashlib
from typing import Any


def stable_alias(identifier: str, kind: str = "player") -> str:
    """Return a stable, non-reversible label without publishing the source ID."""

    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:10]
    return f"{kind}_{digest}"


def sanitize_for_ai(value: Any, primary_puuid: str | None = None) -> Any:
    """Recursively remove raw external identities at the AI export boundary."""

    if isinstance(value, list):
        return [sanitize_for_ai(item, primary_puuid) for item in value]
    if not isinstance(value, dict):
        return value

    clean: dict[str, Any] = {}
    raw_identity_keys = {
        "teammate_game_name", "teammate_tag_line", "riotIdGameName",
        "riotIdTagline", "riot_id_game_name", "riot_id_tag_line",
    }
    for key, item in value.items():
        # Opaque Riot extensions have no privacy contract (including encoded JSON).
        # Keep them locally, but never forward arbitrary nested values to exports.
        if key in {"extra", "extra_json"}:
            continue
        if key in raw_identity_keys:
            continue
        if key == "opponent_puuid":
            clean["opponent_alias"] = (
                stable_alias(str(item), "opponent") if item else None
            )
            continue
        if key == "teammate_puuid":
            clean["teammate_alias"] = (
                stable_alias(str(item), "teammate") if item else None
            )
            continue
        if key == "puuid":
            if item and str(item) != primary_puuid:
                clean["participant_alias"] = stable_alias(str(item), "participant")
            continue
        clean[key] = sanitize_for_ai(item, primary_puuid)
    return clean
