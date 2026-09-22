"""Focused Pydantic models and normalization for Match V5 data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RiotAccount(BaseModel):
    """Account V1 fields used by the application."""

    model_config = ConfigDict(populate_by_name=True)

    puuid: str
    game_name: str = Field(alias="gameName")
    tag_line: str = Field(alias="tagLine")


class RiotMatchMetadata(BaseModel):
    """Match metadata used to identify a Match V5 response."""

    model_config = ConfigDict(populate_by_name=True)

    match_id: str = Field(alias="matchId")


class RiotParticipant(BaseModel):
    """Subset of participant fields required by the first analytics layer."""

    model_config = ConfigDict(populate_by_name=True)

    puuid: str
    riot_id_game_name: str | None = Field(default=None, alias="riotIdGameName")
    riot_id_tag_line: str | None = Field(default=None, alias="riotIdTagline")
    champion_name: str = Field(alias="championName")
    team_id: int = Field(alias="teamId")
    team_position: str = Field(default="", alias="teamPosition")
    individual_position: str = Field(default="", alias="individualPosition")
    win: bool
    kills: int
    deaths: int
    assists: int
    gold_earned: int | None = Field(default=None, alias="goldEarned")
    total_minions_killed: int | None = Field(default=None, alias="totalMinionsKilled")
    neutral_minions_killed: int | None = Field(default=None, alias="neutralMinionsKilled")
    damage_to_champions: int | None = Field(default=None, alias="totalDamageDealtToChampions")
    vision_score: int | None = Field(default=None, alias="visionScore")
    item0: int | None = 0
    item1: int | None = 0
    item2: int | None = 0
    item3: int | None = 0
    item4: int | None = 0
    item5: int | None = 0
    item6: int | None = 0

    @property
    def item_ids(self) -> list[int]:
        """Return the seven final inventory slots, including the trinket slot."""

        return [
            self.item0 or 0,
            self.item1 or 0,
            self.item2 or 0,
            self.item3 or 0,
            self.item4 or 0,
            self.item5 or 0,
            self.item6 or 0,
        ]


class RiotMatchInfo(BaseModel):
    """Subset of Match V5 info needed for storage."""

    model_config = ConfigDict(populate_by_name=True)

    game_version: str = Field(alias="gameVersion")
    queue_id: int = Field(alias="queueId")
    duration: int = Field(alias="gameDuration")
    game_creation: int = Field(alias="gameCreation", ge=0, le=253370764800000)
    participants: list[RiotParticipant] = Field(min_length=1)


class RiotMatch(BaseModel):
    """Validated Match V5 response."""

    metadata: RiotMatchMetadata
    info: RiotMatchInfo


class ParsedParticipant(BaseModel):
    """Normalized participant row ready for SQLite persistence."""

    puuid: str
    teammate_game_name: str | None = None
    teammate_tag_line: str | None = None
    champion: str
    role: str
    win: bool
    kills: int
    deaths: int
    assists: int
    gold_earned: int | None
    cs_total: int | None
    damage_dealt: int | None
    damage_share: float | None
    vision_score: int | None
    items: list[int]
    side: str | None


class ParsedMatch(BaseModel):
    """Normalized match and its participants."""

    match_id: str
    patch: str
    queue_id: int
    duration: int
    game_creation: int
    participants: list[ParsedParticipant]


def extract_patch(game_version: str) -> str:
    """Convert a Riot game version such as ``16.17.123.456`` to ``16.17``."""

    parts = game_version.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"Invalid Riot game version: {game_version!r}")
    return f"{parts[0]}.{parts[1]}"


def side_from_team_id(team_id: int | None) -> str | None:
    """Map known Riot team IDs to a side, or return ``None`` when unavailable."""

    if team_id == 100:
        return "blue"
    if team_id == 200:
        return "red"
    return None


def calculate_cs(
    total_minions_killed: int | None, neutral_minions_killed: int | None
) -> int | None:
    """Add lane and neutral minions, returning null if either component is missing."""

    if total_minions_killed is None or neutral_minions_killed is None:
        return None
    return total_minions_killed + neutral_minions_killed


def parse_riot_match(payload: RiotMatch | dict[str, Any]) -> ParsedMatch:
    """Validate and normalize a Match V5 payload without inventing missing values."""

    match = payload if isinstance(payload, RiotMatch) else RiotMatch.model_validate(payload)
    team_damage: dict[int, int | None] = {}
    grouped_damage: dict[int, list[int | None]] = defaultdict(list)
    for participant in match.info.participants:
        grouped_damage[participant.team_id].append(participant.damage_to_champions)

    for team_id, damage_values in grouped_damage.items():
        if any(value is None for value in damage_values):
            team_damage[team_id] = None
        else:
            total = sum(value for value in damage_values if value is not None)
            team_damage[team_id] = total if total > 0 else None

    parsed_participants: list[ParsedParticipant] = []
    for participant in match.info.participants:
        damage_total = team_damage[participant.team_id]
        damage_share = None
        if participant.damage_to_champions is not None and damage_total is not None:
            damage_share = participant.damage_to_champions / damage_total

        parsed_participants.append(
            ParsedParticipant(
                puuid=participant.puuid,
                teammate_game_name=participant.riot_id_game_name,
                teammate_tag_line=participant.riot_id_tag_line,
                champion=participant.champion_name,
                role=participant.team_position or participant.individual_position or "UNKNOWN",
                win=participant.win,
                kills=participant.kills,
                deaths=participant.deaths,
                assists=participant.assists,
                gold_earned=participant.gold_earned,
                cs_total=calculate_cs(
                    participant.total_minions_killed, participant.neutral_minions_killed
                ),
                damage_dealt=participant.damage_to_champions,
                damage_share=damage_share,
                vision_score=participant.vision_score,
                items=participant.item_ids,
                side=side_from_team_id(participant.team_id),
            )
        )

    return ParsedMatch(
        match_id=match.metadata.match_id,
        patch=extract_patch(match.info.game_version),
        queue_id=match.info.queue_id,
        duration=match.info.duration,
        game_creation=match.info.game_creation,
        participants=parsed_participants,
    )
