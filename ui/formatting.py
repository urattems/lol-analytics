"""Presentation-only formatting utilities."""

from __future__ import annotations

from datetime import datetime
import math

from core.queues import queue_name


def integer_label(value: int) -> str:
    """Format a non-sensitive integer for the local UI."""

    return f"{value:,}".replace(",", " ")


def decimal_label(value: float | int | None, decimals: int = 2, suffix: str = "") -> str:
    """Format an optional metric without inventing unavailable values."""

    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{float(value):,.{decimals}f}".replace(",", " ") + suffix


def signed_label(value: float | int | None, suffix: str = "g") -> str:
    """Keep a difference's sign and thousands readable, including missing data."""

    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{float(value):+,.0f}".replace(",", " ") + suffix


CONTEXT_LABELS = {
    "Early @10": "Avance à 10 min", "Early @15": "Avance à 15 min",
    "First death": "Première mort", "First dragon": "Premier dragon",
    "Session position": "Position dans la session", "Squad context": "Coéquipiers récurrents",
    "Recent vs Previous": "Évolution récente", "Side": "Côté de la carte",
    "Queue": "File de jeu", "Duration": "Durée", "Patch": "Patch",
    "AHEAD": "Devant", "BEHIND": "Derrière", "EVEN": "À l’équilibre",
    "ours": "Notre équipe", "theirs": "Équipe adverse", "none": "Aucun",
    "ambiguous": "Indéterminé", "Recent": "Période récente", "Previous": "Période précédente",
    "blue": "Blue Side", "red": "Red Side", "win": "Après une victoire",
    "loss": "Après une défaite", "first": "Première partie",
    "0 deaths before 10": "Aucune mort avant 10 min", "1 death before 10": "Une mort avant 10 min",
    "2+ deaths before 10": "2+ morts avant 10 min",
    "Ahead": "Devant", "Even": "À l’équilibre", "Behind": "Derrière",
    "TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support",
    "BLUE SIDE": "Blue Side", "RED SIDE": "Red Side",
    "Solo / aucun coéquipier récurrent": "Aucun récurrent identifié",
    "Avec ≥1 coéquipier récurrent": "Avec un récurrent",
}


def context_label(value: object, context: str = "") -> str:
    """Translate presentation labels without changing their analytical values."""

    if value is None:
        return "N/A"
    label = str(value)
    if context == "champion_switch" and label in ("True", "False"):
        return "Champion changé" if label == "True" else "Champion conservé"
    if context == "First death" and label in ("True", "False"):
        return "Mort avant 10 min" if label == "True" else "Aucune mort avant 10 min"
    if context == "Squad context" and label in ("True", "False"):
        return "Avec un récurrent" if label == "True" else "Aucun récurrent"
    if context == "Queue" and label.isdigit():
        return queue_name(int(label))
    if " → " in label:
        return " → ".join(CONTEXT_LABELS.get(part, part) for part in label.split(" → "))
    return CONTEXT_LABELS.get(label, label)


def duration_label(seconds: int | None) -> str:
    """Format a duration stored in seconds."""

    if seconds is None:
        return "N/A"
    minutes, remaining_seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def selection_filter_label(key: str, value: object) -> str:
    """Readable selection chips; the serialized export contract stays unchanged."""
    labels = {
        "champion": "Champion", "role": "Rôle", "queue_id": "File", "patch": "Patch",
        "side": "Côté", "win": "Résultat", "min_duration": "Durée minimum",
        "max_duration": "Durée maximum", "include_short_games": "Parties courtes",
        "date_from_ms": "Depuis", "date_to_ms": "Jusqu’au", "timeline_requirement": "Timeline",
        "first_death_before_10": "Mort avant 10 min", "opponent_champion": "Adversaire",
        "session_game_number": "Position dans la session", "recurring_teammate": "Coéquipier",
        "first_dragon": "Premier dragon", "trajectory": "Trajectoire",
    }
    if key == "win":
        formatted = "Victoire" if value else "Défaite"
    elif key == "queue_id":
        formatted = queue_name(int(value))
    elif key in ("min_duration", "max_duration"):
        formatted = duration_label(int(value))
    elif key in ("date_from_ms", "date_to_ms"):
        formatted = match_date_label(int(value))
    elif isinstance(value, bool):
        formatted = "Incluses" if value else "Exclues" if key == "include_short_games" else "Non"
        if key != "include_short_games":
            formatted = "Oui" if value else "Non"
    elif key.startswith("gold_diff_"):
        _, _, minute, bound = key.split("_")
        return f"GD @{minute} {'≥' if bound == 'min' else '≤'} {signed_label(value)}"
    else:
        formatted = {"available": "Disponible", "missing": "Manquante"}.get(str(value), context_label(value))
    return f"{labels.get(key, key)} : {formatted}"


def playtime_label(seconds: int | float | None) -> str:
    """Human-readable cumulative playtime, without changing match/export clocks."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "N/A"
    minutes = int(seconds) // 60
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


def match_date_label(timestamp_ms: int | None, include_time: bool = False) -> str:
    """Format a Riot UTC millisecond timestamp in the computer's local timezone."""

    if timestamp_ms is None:
        return "N/A"
    try:
        local_date = datetime.fromtimestamp(timestamp_ms / 1000).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        return "N/A"
    return local_date.strftime("%d/%m/%Y %H:%M" if include_time else "%d/%m/%Y")


def sync_date_label(timestamp_seconds: int | None) -> str:
    """Format a sync timestamp or a clear unavailable label."""

    if timestamp_seconds is None:
        return "Jamais"
    try:
        return datetime.fromtimestamp(timestamp_seconds).astimezone().strftime("%d/%m/%Y à %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "N/A"


def kda_line(kills: int | None, deaths: int | None, assists: int | None) -> str:
    """Format a K/D/A triplet, keeping missing values explicit."""

    values = (kills, deaths, assists)
    return "/".join("N/A" if value is None else str(value) for value in values)
