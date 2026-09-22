"""Build an isolated, deterministic demo using invented data and normal parsers.

Run ``python -m scripts.create_demo --output data/demo.db``. No Riot requests,
settings, environment files or existing databases are read by this command.
The output is a generated artifact and must not be committed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.db import Database
from core.models import RiotAccount, parse_riot_match
from core.timeline import parse_timeline


DEMO_GAME_NAME = "Demo Dragon"
DEMO_TAG_LINE = "DEMO"
DEMO_PUUID = "demo-player-dragon"
DEMO_SECOND_GAME_NAME = "Demo Atlas"
DEMO_SECOND_PUUID = "demo-player-atlas"
DEMO_MATCH_COUNT = 72
DEMO_START = datetime(2026, 8, 8, 17, 0, tzinfo=timezone.utc)

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ALLY_CHAMPIONS = ("Aatrox", "Sejuani", "Ahri", "Jinx", "Nami")
OPPONENT_CHAMPIONS = (
    ("Garen", "Darius", "Renekton", "Ornn"),
    ("Kayn", "Viego", "Graves", "LeeSin"),
    ("Viktor", "Syndra", "Yasuo", "Azir"),
    ("Caitlyn", "Jhin", "Ashe", "Ezreal"),
    ("Nautilus", "Thresh", "Lux", "Braum"),
)


def _inventory(champion: str, role: str, alternate: bool) -> list[int]:
    if champion == "Shyvana":
        core = [3115, 3089, 3137] if alternate else [3078, 3748, 3053]
    elif role == "UTILITY":
        core = [3190, 3109, 3075]
    elif role == "MIDDLE":
        core = [3118, 3089, 3157]
    elif role == "BOTTOM":
        core = [3031, 3085, 3036]
    else:
        core = [3078, 3053, 3071]
    return [core[0], 3047, core[1], core[2], 0, 0, 3340]


def _participants(index: int, won: bool) -> tuple[list[dict[str, Any]], int, int]:
    champion, role = (
        ("Shyvana", "JUNGLE") if index % 12 < 7 else
        ("Vi", "JUNGLE") if index % 12 < 9 else
        ("Leona", "UTILITY") if index % 12 < 11 else ("Jinx", "BOTTOM")
    )
    own_team = 100 if index % 2 == 0 else 200
    rows: list[dict[str, Any]] = []
    for team_id in (100, 200):
        for role_index, current_role in enumerate(ROLES):
            own = team_id == own_team
            is_player = own and current_role == role
            puuid = f"demo-guest-{index:03d}-{team_id}-{role_index}"
            first = ("Aster", "Nova", "Echo", "Orion", "Lumen")[role_index]
            second = ("Cobalt", "Ambre", "Silex", "Nacre")[(index + team_id // 100) % 4]
            name = f"{first} {second} {index + 1}"
            current_champion = (
                ALLY_CHAMPIONS[role_index] if own
                else OPPONENT_CHAMPIONS[role_index][(index // 3) % 4]
            )
            if is_player:
                puuid, name, current_champion = DEMO_PUUID, DEMO_GAME_NAME, champion
            elif own and current_role == "MIDDLE" and index % 6 < 4:
                puuid, name = DEMO_SECOND_PUUID, DEMO_SECOND_GAME_NAME
            elif own and current_role in ("TOP", "BOTTOM") and index % 6 < 3:
                puuid, name = f"demo-mate-{current_role.lower()}", {"TOP": "Bastion Bleu", "BOTTOM": "Nova Plume"}[current_role]
            inventory = _inventory(current_champion, current_role, bool(index % 2))
            row = {
                "participantId": len(rows) + 1,
                "puuid": puuid, "riotIdGameName": name, "riotIdTagline": DEMO_TAG_LINE,
                "championName": current_champion, "teamId": team_id,
                "teamPosition": current_role, "individualPosition": current_role,
                "win": won if own else not won,
                "kills": 0, "deaths": 0, "assists": 0,
                "visionScore": 18 + (index * 7 + role_index * 11) % 35,
                **{f"item{slot}": item for slot, item in enumerate(inventory)},
            }
            rows.append(row)
    player_id = next(row["participantId"] for row in rows if row["puuid"] == DEMO_PUUID)
    opponent_id = next(
        row["participantId"] for row in rows
        if row["teamId"] != own_team and row["teamPosition"] == role
    )
    return rows, player_id, opponent_id


def _combat_events(rows: list[dict[str, Any]], index: int, minutes: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    winning_team = rows[0]["teamId"] if rows[0]["win"] else rows[5]["teamId"]
    for event_index, second in enumerate(range(190, minutes * 60 - 45, 47)):
        killer_team = winning_team if (event_index + index) % 5 < 3 else 300 - winning_team
        killers = [row for row in rows if row["teamId"] == killer_team]
        victims = [row for row in rows if row["teamId"] != killer_team]
        killer_position = (event_index // 5 + event_index * 3 + index) % 5
        killer = killers[killer_position]
        victim = victims[(event_index // 5 + event_index * 2 + index) % 5]
        assistants = [killers[(killer_position + offset) % 5] for offset in range(1, 2 + event_index % 3)]
        killer["kills"] += 1
        victim["deaths"] += 1
        for assistant in assistants:
            assistant["assists"] += 1
        events.append({
            "type": "CHAMPION_KILL", "timestamp": second * 1000,
            "killerId": killer["participantId"], "victimId": victim["participantId"],
            "assistingParticipantIds": [row["participantId"] for row in assistants],
        })
    return events


def _gold_at(minute: int, row: dict[str, Any], index: int, player_id: int, opponent_id: int) -> int:
    # The last four-game review has one team-wide comeback, computed from all
    # ten actual synthetic frames (and used for the final match gold as well).
    if index == 70:
        own_team = 100 if index % 2 == 0 else 200
        own = row["teamId"] == own_team
        rate = (330 if own else 420) - (60 if row["teamPosition"] == "UTILITY" else 0)
        return 500 + minute * rate + (max(0, minute - 12) * 300 if own else 0)
    rate = 340 + (index * 13 + row["participantId"] * 17) % 65 + (35 if row["win"] else 0)
    if row["participantId"] in (player_id, opponent_id):
        player_won = row["win"] if row["participantId"] == player_id else not row["win"]
        leading = player_won != (index % 8 == 1)
        rate = 430 if leading == (row["participantId"] == player_id) else 335
        # A handful of invented comebacks reverse the early lead after minute 15.
        if index % 8 == 1:
            extra_rate = 170 if row["win"] else 0
            return 500 + minute * rate + max(0, minute - 15) * extra_rate
    if row["teamPosition"] == "UTILITY":
        rate -= 65
    return 500 + minute * rate


def _game(index: int, created_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    won = (index * 7 + index // 12) % 12 < 7
    minutes = 25 + index % 7 * 2
    rows, player_id, opponent_id = _participants(index, won)
    events = _combat_events(rows, index, minutes)
    leading_id = player_id if won != (index % 8 == 1) else opponent_id
    events.extend([
        {"type": "ELITE_MONSTER_KILL", "timestamp": 8 * 60_000 + 15_000,
         "killerId": leading_id, "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON"},
        {"type": "ELITE_MONSTER_KILL", "timestamp": 16 * 60_000 + 10_000,
         "killerId": leading_id, "monsterType": "RIFTHERALD"},
        {"type": "BUILDING_KILL", "timestamp": 14 * 60_000 + 25_000,
         "killerId": leading_id, "teamId": 300 - rows[leading_id - 1]["teamId"],
         "buildingType": "TOWER_BUILDING", "towerType": "OUTER_TURRET", "laneType": "MID_LANE"},
        {"type": "ELITE_MONSTER_KILL", "timestamp": (minutes - 3) * 60_000,
         "killerId": player_id if won else opponent_id, "monsterType": "BARON_NASHOR"},
    ])
    for row in rows:
        pid = row["participantId"]
        role = row["teamPosition"]
        cs_rate = 1.2 if role == "UTILITY" else 5.8 + ((index + pid) % 5) * 0.3
        row["goldEarned"] = _gold_at(minutes, row, index, player_id, opponent_id)
        cs = int(minutes * cs_rate)
        jungle = int(cs * 0.82) if role == "JUNGLE" else (index + pid) % 8
        row["neutralMinionsKilled"] = jungle
        row["totalMinionsKilled"] = cs - jungle
        row["totalDamageDealtToChampions"] = int(
            minutes * (240 if role == "UTILITY" else 450) + row["kills"] * 900 + index % 9 * 380
        )
        for item_slot, minute in ((0, 11), (1, 7), (2, 19), (3, 24)):
            events.append({"type": "ITEM_PURCHASED", "timestamp": minute * 60_000 + pid * 1000,
                           "participantId": pid, "itemId": row[f"item{item_slot}"]})
    events.extend([
        {"type": "ITEM_PURCHASED", "timestamp": 6 * 60_000,
         "participantId": player_id, "itemId": 1036},
        {"type": "ITEM_UNDO", "timestamp": 6 * 60_000 + 3000,
         "participantId": player_id, "beforeId": 1036, "afterId": 0},
    ])
    frames = []
    for minute in range(minutes + 1):
        participant_frames = {}
        for row in rows:
            pid = row["participantId"]
            total_cs = row["totalMinionsKilled"] + row["neutralMinionsKilled"]
            jungle = int(row["neutralMinionsKilled"] * minute / minutes)
            total_gold = _gold_at(minute, row, index, player_id, opponent_id)
            participant_frames[str(pid)] = {
                "participantId": pid, "totalGold": total_gold,
                "currentGold": 100 + (minute * 173 + pid * 31) % 1300,
                "xp": int(minute * (390 + (total_gold - 500) / max(1, minute) / 3)),
                "level": min(18, 1 + int(minute * 0.53)),
                "minionsKilled": int(total_cs * minute / minutes) - jungle,
                "jungleMinionsKilled": jungle,
                "position": {"x": 2500 + (minute * 271 + pid * 613) % 9000,
                             "y": 2300 + (minute * 353 + pid * 419) % 9000},
            }
        frames.append({
            "timestamp": minute * 60_000,
            "participantFrames": participant_frames,
            "events": sorted(
                [event for event in events if max(0, minute - 1) * 60_000 < event["timestamp"] <= minute * 60_000],
                key=lambda event: event["timestamp"],
            ),
        })
    match_id = f"DEMO_{index + 1:06d}"
    metadata = {"matchId": match_id, "participants": [row["puuid"] for row in rows]}
    match = {"metadata": metadata, "info": {
        "gameVersion": f"16.{16 + index // 36}.1.1", "queueId": 440 if index % 4 == 0 else 420,
        "gameDuration": minutes * 60, "gameCreation": int(created_at.timestamp() * 1000),
        "participants": rows,
    }}
    return match, {"metadata": metadata, "info": {"frames": frames}}


def create_demo_database(output: str | Path, *, journal_examples=False) -> Database:
    """Create 72 synthetic games at an explicit path; refuse any nonempty file.

    All identities begin with ``demo-``. Timestamps and data are constant across
    runs, so public screenshots and regression checks can be reproduced.
    """

    destination = Path(output).resolve()
    if destination.exists() and (not destination.is_file() or destination.stat().st_size):
        raise FileExistsError(f"Refusing to replace an existing nonempty path: {destination}")
    database = Database(destination)
    database.initialize()
    for puuid, name in ((DEMO_PUUID, DEMO_GAME_NAME), (DEMO_SECOND_PUUID, DEMO_SECOND_GAME_NAME)):
        database.upsert_player(RiotAccount(puuid=puuid, gameName=name, tagLine=DEMO_TAG_LINE), "euw1", "europe")
    created_at = DEMO_START
    session_index = -1
    remaining_in_session = 0
    session_lengths = (1, 2, 3, 8, 4)
    for index in range(DEMO_MATCH_COUNT):
        if remaining_in_session == 0:
            session_index += 1
            remaining_in_session = session_lengths[session_index % len(session_lengths)]
            created_at = DEMO_START + timedelta(days=session_index)
        match, timeline = _game(index, created_at)
        database.insert_match(parse_riot_match(match))
        parsed = parse_timeline(timeline)
        database.save_timeline(parsed.match_id, parsed.frames, parsed.events, fetched_at=1_788_307_200)
        created_at += timedelta(seconds=match["info"]["gameDuration"], minutes=8 + index % 5)
        remaining_in_session -= 1
    # The ordinary sync API writes wall time; fixed demo timestamps keep this
    # artifact deterministic while using its normal table contract.
    with database.connection() as connection:
        for puuid in (DEMO_PUUID, DEMO_SECOND_PUUID):
            connection.execute(
                "INSERT INTO sync_state (puuid, last_match_id_synced, last_sync_at) VALUES (?, ?, ?)",
                (puuid, database.player_matches(puuid, 1)[0]["match_id"], 1_788_307_200),
            )
    # Explicitly invented rank observations, not lobby-derived historical ranks.
    # Keep their origin visible and do not call Riot to populate a demo.
    with database.connection() as connection:
        for owner in (DEMO_PUUID, DEMO_SECOND_PUUID):
            for queue in (420, 440):
                for index, (tier, division, lp) in enumerate((
                    ('SILVER', 'II', 18), ('SILVER', 'II', 44), ('SILVER', 'I', 9),
                    ('SILVER', 'I', 35), ('SILVER', 'I', 22), ('GOLD', 'IV', 11),
                )):
                    stamp = (DEMO_START + timedelta(days=index * 4 + (1 if queue == 440 else 0))).timestamp()
                    connection.execute('''INSERT INTO profile_rank_observations
                        (owner_puuid, platform_region, queue_id, observed_at, status, tier, division, league_points, source)
                        VALUES (?, 'EUW1', ?, ?, 'ranked', ?, ?, ?, 'synthetic-demo')''',
                        (owner, queue, stamp, tier, division, lp))
    if journal_examples:
        _journal_examples(database)
    return database


def _journal_examples(database):
    """Invented annotations and a simulated goal created before later demo games.

    Only called during fresh fixture creation; never inject into an existing DB.
    The fixed marker models the fixture's past state, not a real retroactive goal.
    """
    from core.journal import save_annotation
    for owner in (DEMO_PUUID, DEMO_SECOND_PUUID):
        matches = database.player_matches(owner)
        for index, row in enumerate(matches[:2]):
            save_annotation(database, owner, row['match_id'],
                ('Exemple fictif : revoir le premier rappel et le chemin vers le deuxième objectif.' if index == 0 else
                 'Exemple fictif : nouvelle approche de build, à comparer sur plusieurs parties.'),
                ['objectif travaillé'] if index == 0 else ['nouveau build'], 0, clock=lambda: 1_788_400_000)
        anchor = matches[8]
        with database.connection() as connection:
            marker = connection.execute('SELECT id FROM participants WHERE puuid=? AND match_id=?', (owner, anchor['match_id'])).fetchone()[0]
            stamp = anchor['game_creation']/1000 + anchor['duration'] + 1
            connection.execute('''INSERT INTO personal_goals
                (goal_id,owner_puuid,title,metric_key,comparator,target_value,horizon,patch_policy,
                 baseline_participant_id,created_at,source) VALUES (?,?,'Exemple fictif · régularité du farm',
                 'cs_per_minute','gte',6.5,5,'mixed',?,?,'synthetic-demo')''',
                ('demo-journal-goal-' + owner, owner, marker, stamp))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a separate, fully synthetic LoL Analytics demo DB.")
    parser.add_argument("--output", type=Path, required=True, help="New database path, for example data/demo.db")
    parser.add_argument('--journal-examples', action='store_true', help='Include clearly invented notes and goals in this fresh demo')
    args = parser.parse_args()
    try:
        database = create_demo_database(args.output, journal_examples=args.journal_examples)
    except (FileExistsError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Created {database.count_matches()} synthetic matches at {database.path}")
    print(f"Demo profiles: {DEMO_GAME_NAME}#{DEMO_TAG_LINE} and {DEMO_SECOND_GAME_NAME}#{DEMO_TAG_LINE}")


if __name__ == "__main__":
    main()
