"""Batched, local match-history preparation; independent of Streamlit/HTTP.

Session membership is prepared on the whole library before any filter. Only
visible cards are materialized. Player identities must never enter AI exports.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
import json

import polars as pl

from analytics.constants import SHORT_GAME_THRESHOLD_SECONDS
from analytics.explorer import ExplorerSelection, build_analysis_dataset
from analytics.session_review import prepare_review_history
from core.identities import PlayerIdentity, local_identity_index


@dataclass(frozen=True)
class HistoryItem:
    item_id: int | None
    slot: int
    trinket: bool = False


def inventory(value: object) -> tuple[HistoryItem, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = None
    if not isinstance(value, list):
        value = []
    return tuple(HistoryItem(v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None, i, i == 6)
                 for i, v in enumerate((value + [None] * 7)[:7]))


@dataclass(frozen=True)
class HistoryPlayer:
    identity: PlayerIdentity
    champion: str | None
    role: str | None
    side: str | None
    own: bool
    kills: int | None
    deaths: int | None
    assists: int | None
    items: tuple[HistoryItem, ...]


@dataclass(frozen=True)
class HistoryContext:
    session_anchor: str | None
    session_number: int | None
    timeline_available: bool


@dataclass(frozen=True)
class HistoryMatch:
    match_id: str
    facts: dict[str, Any]
    players: tuple[HistoryPlayer, ...]
    context: HistoryContext
    items: tuple[HistoryItem, ...]

    @property
    def short(self) -> bool:
        value = self.facts.get("duration")
        return isinstance(value, (int, float)) and 0 <= value < SHORT_GAME_THRESHOLD_SECONDS


@dataclass
class HistoryLibrary:
    owner: str
    games: pl.DataFrame
    rosters: dict[str, list[dict]]
    identities: dict[str, PlayerIdentity]
    anchors: dict[str, str]
    shared: dict[tuple[str, str], set[str]]

    def select(self, filters: ExplorerSelection, teammate: str | None = None,
               opponent: str | None = None) -> pl.DataFrame:
        selected = build_analysis_dataset(self.games, filters).filtered
        for relationship, identifier in (("ally", teammate), ("enemy", opponent)):
            if identifier:
                selected = selected.filter(pl.col("match_id").is_in(self.shared.get((relationship, identifier), set())))
        return selected

    def batch(self, selected: pl.DataFrame, offset: int = 0, size: int = 20) -> list[HistoryMatch]:
        if isinstance(offset, bool) or isinstance(size, bool) or not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or not 1 <= size <= 100:
            raise ValueError("Fenêtre de cartes invalide.")
        allowed = set(self.games["match_id"].to_list())
        cards = []
        for row in selected.slice(offset, size).to_dicts():
            match_id = str(row["match_id"])
            if match_id not in allowed:
                raise ValueError("La partie ne fait pas partie de ce profil.")
            players = tuple(HistoryPlayer(self.identities[str(r["puuid"])], r.get("champion"), r.get("role"),
                            r.get("side"), r.get("puuid") == self.owner, r.get("kills"), r.get("deaths"), r.get("assists"), inventory(r.get("items")))
                            for r in self.rosters.get(match_id, []) if str(r.get("puuid")) in self.identities)
            context = HistoryContext(self.anchors.get(str(row.get("session_id"))), row.get("session_game_number"), bool(row.get("timeline_available")))
            cards.append(HistoryMatch(match_id, row, players, context, inventory(row.get("items"))))
        return cards


def prepare_history(games: pl.DataFrame, participants: list[dict], owner: str,
                    profiles: list[dict] = ()) -> HistoryLibrary:
    prepared = prepare_review_history(games)
    rosters: dict[str, list[dict]] = defaultdict(list)
    shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in participants:
        rosters[str(row["match_id"])].append(row)
    for match_id, rows in rosters.items():
        own = [r for r in rows if r.get("puuid") == owner]
        if len(own) != 1 or own[0].get("side") not in ("blue", "red"):
            continue
        for row in rows:
            if row.get("puuid") != owner and row.get("side") in ("blue", "red"):
                relationship = "ally" if row["side"] == own[0]["side"] else "enemy"
                shared[(relationship, str(row["puuid"]))].add(match_id)
    anchors = {}
    if not prepared.is_empty():
        for row in prepared.drop_nulls("session_id").sort(["game_creation", "match_id"]).select("session_id", "match_id").to_dicts():
            anchors.setdefault(str(row["session_id"]), str(row["match_id"]))
    return HistoryLibrary(owner, prepared, dict(rosters), local_identity_index(participants, profiles), anchors, dict(shared))
