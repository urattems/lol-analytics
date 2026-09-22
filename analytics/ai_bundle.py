"""In-memory V2.1 AI bundle containing the complete local match library."""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable
from datetime import datetime

import polars as pl

from analytics.builds import build_summary
from analytics.context import build_context_dataset
from analytics.discovery import discover_contexts
from analytics.exports import build_full_export_payload, export_markdown, markdown_text, to_csv
from analytics.overview import get_player_matches
from analytics.game_flow import trajectory_preset
from analytics.insights import build_insight_bundle
from analytics.matchups import matchup_matrix
from analytics.privacy import sanitize_for_ai
from analytics.sessions import session_analytics
from analytics.teammates import champion_pairings, squad_summary, teammate_summary
from analytics.timeline import load_timeline_analytics, timeline_frame
from core.db import Database
from core.static_data import ItemStaticData


BUNDLE_FILES = {
    "manifest.json",
    "README_AI.md",
    "summary.md",
    "matches.jsonl",
    "champions.csv",
    "builds.csv",
    "presets.json",
    "timeline_checkpoints.jsonl",
    "timeline_events.jsonl",
    "matchups.csv",
    "teammates.csv",
    "squads.csv",
    "sessions.csv",
    "context_insights.json",
    "trajectories.csv",
    "data_dictionary.md",
}


class BundleDatasetMismatch(ValueError):
    """The cached selection no longer represents the complete requested profile."""

README_AI = """# LoL Analytics — bundle IA complet

Ce bundle représente la totalité de la bibliothèque locale au moment indiqué dans `manifest.json`.

- Les statistiques sont descriptives : une corrélation n'établit pas une causalité.
- Les parties courtes, y compris celles de moins de cinq minutes, sont volontairement incluses.
- Une partie reste dans `matches.jsonl` même si sa Timeline Riot est indisponible.
- Les statistiques Timeline ne couvrent que les parties marquées `timeline_available=true`.
- `N` désigne toujours le nombre de parties du groupe observé.
- Les identifiants PUUID et Riot IDs des autres joueurs ne sortent jamais du processus local.
- Les coéquipiers sont représentés uniquement par des alias stables dans ce bundle.
"""

DATA_DICTIONARY = """# Dictionnaire des données

- `matches.jsonl` : une ligne par partie locale, inventaire principal et trinket séparés.
- `champions.csv` : agrégats descriptifs par champion avec N.
- `builds.csv` : inventaires finaux canoniques ; aucun ordre d'achat n'est inféré.
- `presets.json` : side, durée, queue, patch, récent/précédent et presets Timeline.
- `timeline_checkpoints.jsonl` : frames Riot réelles choisies près de 5/10/15/20 minutes, avec timestamps demandés et réels.
- `timeline_events.jsonl` : achats, ventes, kills et objectifs normalisés réellement reçus.
- `matchups.csv` : matchups personnels même-rôle avec N et métriques Timeline disponibles.
- `teammates.csv` : coéquipiers observés sous alias uniquement.
- `squads.csv` : groupes récurrents sous alias uniquement.
- `sessions.csv` : position et longueur de session par match.
- `context_insights.json` : écarts descriptifs détectés avec protections de volume.
- `trajectories.csv` : trajectoires Team Gold @10 → @15 → @20 complètes uniquement.
- `timeline_available` : indique si la partie possède une Timeline persistée et valide.
- `*_diff_*` : valeur du joueur/de son équipe moins celle de l'adversaire.
- `N` / `games` : taille du groupe observé.
- `interestingness_score` : classement interne `|écart de WR| × √(plus petit N)` ; ce n'est ni une probabilité ni un test de significativité.
"""


def _json(value: object, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent, default=str)


def _jsonl(rows: list[dict[str, object]]) -> str:
    return "".join(_json(row) + "\n" for row in rows)


def _csv(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    flat = [
        {
            key: _json(value) if isinstance(value, (list, dict)) else value
            for key, value in row.items()
        }
        for row in rows
    ]
    return to_csv(pl.from_dicts(flat, strict=False, infer_schema_length=None))


def _builds_csv(games: pl.DataFrame) -> str:
    summary = build_summary(games)
    if summary.is_empty():
        return ""
    rows = []
    for row in summary.to_dicts():
        row["items"] = _json(row.get("items", []))
        rows.append(row)
    return _csv(rows)


def _serializable_presets(bundle: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in bundle.items() if key != "games"}


def _summary_sections(payload: dict[str, object], presets: dict[str, object]) -> str:
    lines = [export_markdown(payload), "\n# Preset summaries\n"]
    for key in ("role", "side", "duration", "wins_losses", "queue", "patch", "recent_previous", "ahead_10", "ahead_15"):
        rows = presets.get(key)
        if not isinstance(rows, list):
            continue
        lines.extend([f"\n## {key.replace('_', ' ').title()}\n", "| Groupe | N | WR | KDA |\n|---|---:|---:|---:|\n"])
        for row in rows:
            if isinstance(row, dict):
                lines.append(
                    f"| {markdown_text(row.get('label', 'N/A'))} | {row.get('games', 0)} | "
                    f"{float(row.get('winrate') or 0):.1f} % | "
                    f"{float(row.get('kda') or 0):.2f} |\n"
                )
    return "".join(lines)


def build_ai_bundle(
    games: pl.DataFrame,
    database: Database,
    puuid: str,
    riot_id: str,
    platform_region: str,
    routing_region: str,
    generated_at: datetime | None = None,
    item_resolver: Callable[[int], ItemStaticData] | None = None,
) -> bytes:
    """Create a complete, internally checked ZIP without writing personal data to disk."""

    canonical = get_player_matches(database, puuid, None)
    if not set(canonical.columns).issubset(games.columns) or not games.select(
        canonical.columns
    ).sort("match_id").equals(canonical.sort("match_id")):
        raise BundleDatasetMismatch("AI bundle refused: dataset is not the complete active profile library.")
    available_ids = database.timeline_available_match_ids(puuid)
    context_games = build_context_dataset(games, database, puuid)
    payload = build_full_export_payload(
        context_games, riot_id, platform_region, routing_region, generated_at,
        item_resolver, available_ids,
    )
    timeline_rows = load_timeline_analytics(database, puuid)
    timeline = timeline_frame(timeline_rows)
    presets = _serializable_presets(build_insight_bundle(context_games, timeline))
    _, _, _, events = database.timeline_analysis_source(puuid)
    participants = database.participants_for_player_matches(puuid)
    teammate_rows = teammate_summary(context_games, participants, puuid)
    safe_teammates = [
        {
            "teammate_alias": row["teammate_alias"],
            "games": row["games"], "first_game": row["first_game"],
            "last_game": row["last_game"], "wins": row["wins"],
            "winrate": row["winrate"], "kda": row["kda"],
            "gold_diff_15": row["gold_diff_15"],
            "team_gold_diff_15": row["team_gold_diff_15"],
        }
        for row in teammate_rows
    ]
    squad_rows = squad_summary(context_games, participants, puuid)
    safe_squads = [
        {
            "member_aliases": row["member_aliases"], "size": row["size"],
            "games": row["games"], "wins": row["wins"],
            "winrate": row["winrate"], "kda": row["kda"],
        }
        for row in squad_rows
    ]
    session_columns = [
        column for column in (
            "match_id", "session_id", "session_game_number", "session_length",
            "minutes_since_previous_game", "previous_result", "previous_champion",
            "champion_switch",
        ) if column in context_games.columns
    ]
    session_rows = context_games.select(session_columns).to_dicts()
    selection = payload["selection"]
    assert isinstance(selection, dict)
    manifest = {
        "schema_version": "2.1",
        "player": riot_id,
        "generated_at": payload["generated_at"],
        "local_games": games.height,
        "exported_games": len(payload["matches"]),  # type: ignore[arg-type]
        "filters": {},
        "timeline": {
            "available": len(available_ids.intersection(set(games["match_id"].to_list()))),
            "missing": games.height - len(available_ids.intersection(set(games["match_id"].to_list()))),
        },
    }
    if manifest["local_games"] != manifest["exported_games"]:
        raise ValueError("AI bundle refused: full match count mismatch.")

    champion_rows = payload["champions"]
    assert isinstance(champion_rows, list)
    files = {
        "manifest.json": _json(manifest, indent=2) + "\n",
        "README_AI.md": README_AI,
        "summary.md": _summary_sections(payload, presets),
        "matches.jsonl": _jsonl(payload["matches"]),  # type: ignore[arg-type]
        "champions.csv": _csv(champion_rows),  # type: ignore[arg-type]
        "builds.csv": _builds_csv(games),
        "presets.json": _json(presets, indent=2) + "\n",
        "timeline_checkpoints.jsonl": _jsonl(sanitize_for_ai(timeline_rows, puuid)),
        "timeline_events.jsonl": _jsonl(sanitize_for_ai(events, puuid)),
        "matchups.csv": _csv(matchup_matrix(context_games)),
        "teammates.csv": _csv(safe_teammates),
        "squads.csv": _csv(safe_squads),
        "sessions.csv": _csv(session_rows),
        "context_insights.json": _json(
            sanitize_for_ai(discover_contexts(context_games), puuid), indent=2
        ) + "\n",
        "trajectories.csv": _csv(trajectory_preset(context_games)),
        "data_dictionary.md": DATA_DICTIONARY,
    }
    if set(files) != BUNDLE_FILES:
        raise AssertionError("AI bundle file contract is incomplete.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


def ai_bundle_filename(riot_id: str, generated_at: datetime | None = None) -> str:
    """Build a safe, recognizable full-history ZIP filename."""

    player = riot_id.split("#", 1)[0]
    normalized = unicodedata.normalize("NFKD", player).encode("ascii", "ignore").decode("ascii")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-") or "Player")[:64]
    date_value = (generated_at or datetime.now().astimezone()).date().isoformat()
    return f"LoL-Analytics-AI-{slug}-ALL-{date_value}.zip"
