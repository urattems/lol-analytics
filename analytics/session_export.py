"""Small in-memory session package; the global AI bundle contract is unchanged."""
from __future__ import annotations

from datetime import datetime
import io
import json
import zipfile

import polars as pl

from analytics.exports import _match_payload, _local_iso, export_filename, markdown_text
from analytics.session_review import finite


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def session_export_payload(review: dict, riot_id: str) -> dict:
    """Explicit public fields only: never serialize arbitrary participant/context rows."""
    raw = review["matches"]
    source = pl.from_dicts(raw, infer_schema_length=None, strict=False)
    matches = _match_payload(source, timeline_match_ids={r["match_id"] for r in raw if r.get("timeline_available")})
    for match, row in zip(matches, raw):
        match["session_game_number"] = row["session_game_number"]
        match["gap_minutes"] = finite(row.get("minutes_since_previous_game"))
        match["checkpoints"] = [
            {"minute": minute, **{key: finite(row.get(f"{key}_{minute}")) if row.get("timeline_available") else None
               for key in ("gold", "cs", "xp", "gold_diff", "team_gold_diff")}}
            for minute in (10, 15, 20)
        ]
    summary = review["summary"]
    return {
        "schema_version": "session-review-1", "player": riot_id,
        "session": {key: summary[key] for key in ("games", "wins", "losses", "unknown_results", "winrate", "champions", "roles", "timeline_games")},
        "start": _local_iso(summary["start_ms"]), "end": _local_iso(summary["end_ms"]),
        "metrics": review["metrics"], "phases": review["phases"],
        "highlights": [{key: h[key] for key in ("kind", "text", "n", "match_id")} for h in review["highlights"]],
        "comparisons": [{key: c[key] for key in ("champion", "role", "session_n", "baseline_n", "scope", "baseline_label", "eligible", "session", "baseline")} for c in review["comparisons"]],
        "matches": matches,
    }


def session_markdown(payload: dict) -> str:
    summary = payload["session"]
    def label(value):
        return "N/A" if value is None else markdown_text(value)
    def metric(value):
        return "N/A" if value is None else f"{value:.2f}"
    lines = ["# SESSION REVIEW", "", f"Profil : {label(payload['player'])}",
             f"Période : {label(payload['start'])} → {label(payload['end'])}",
             f"{summary['games']} parties — {summary['wins']} V / {summary['losses']} D",
             f"Timeline : {summary['timeline_games']}/{summary['games']}",
             "Champions : " + " ; ".join(f"{label(champion)} ×{n}" for champion, n in summary["champions"].items()),
             "", "Observations descriptives uniquement. Aucune causalité ni recommandation. Les données manquantes restent N/A.",
             "", "## Mesures de la session", ""]
    lines += [f"- {label(name)} ({m['aggregation']}) : {metric(m['value'])} (N={m['n']})." for name, m in payload["metrics"].items()]
    lines += ["", "## Ce qui ressort", ""]
    lines += [f"- {label(h['text'])} (N={h['n']})" for h in payload["highlights"]]
    lines += ["", "## Comparaisons personnelles", "", "Baseline strictement antérieure, même champion et même rôle ; au plus 20 parties.", ""]
    for comparison in payload["comparisons"]:
        lines += [f"### {label(comparison['champion'])} · {label(comparison['role'])}", "",
                  f"N session={comparison['session_n']} ; N historique={comparison['baseline_n']}."]
        if not comparison["eligible"]:
            lines.append("Échantillon insuffisant pour promouvoir cette comparaison.")
        for name in comparison["session"]:
            current, baseline = comparison["session"][name], comparison["baseline"][name]
            lines.append(f"- {label(name)} ({current['aggregation']}) : {metric(current['value'])} (N={current['n']}) / historique {metric(baseline['value'])} (N={baseline['n']}).")
        lines.append("")
    lines += ["## Parties, dans l’ordre", ""]
    for match in payload["matches"]:
        result = "VICTOIRE" if match["win"] is True else "DÉFAITE" if match["win"] is False else "N/A"
        lines += [f"### Partie {match['session_game_number']} · {label(match['champion'])} · {result}", "",
                  f"- Match : {label(match['match_id'])}", f"- Date : {label(match['date'])}",
                  f"- Durée : {label(match['duration_formatted'])} ; K/D/A : {match['kills']}/{match['deaths']}/{match['assists']}",
                  f"- Pause avant la partie : {metric(match['gap_minutes'])} min",
                  f"- Trajectoire équipe : {label(match['trajectory'])}",
                  f"- Premier dragon / héraut / tour : {label(match['first_dragon'])} / {label(match['first_herald'])} / {label(match['first_tower'])}"]
        for point in match["checkpoints"]:
            lines.append(f"- @{point['minute']} min : Gold={metric(point['gold'])} ; CS={metric(point['cs'])} ; GD direct={metric(point['gold_diff'])} ; GD équipe={metric(point['team_gold_diff'])}.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_session_package(review: dict, riot_id: str) -> bytes:
    payload = session_export_payload(review, riot_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("session_review.md", session_markdown(payload).encode("utf-8"))
        archive.writestr("session_matches.jsonl", ("\n".join(_json(row) for row in payload["matches"]) + "\n").encode("utf-8"))
    return buffer.getvalue()


def session_package_filename(riot_id: str, game_count: int, generated_at: datetime | None = None) -> str:
    return "session_" + export_filename(riot_id, game_count, "zip", generated_at)
