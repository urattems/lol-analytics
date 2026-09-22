"""A local post-session reading, from the result to each game's Timeline."""
from __future__ import annotations

from html import escape
import plotly.graph_objects as go
import streamlit as st

from analytics.exports import markdown_text
from analytics.session_export import build_session_package, session_package_filename
from analytics.session_review import build_session_review, session_catalog, resolve_session
from core.db import Database
from core.queues import queue_name
from core.static_data import get_static_data_service
from core.timeline import enrich_timelines_from_settings
from core.import_lock import import_is_running
from ui.components import empty_state, page_header, section_header, stat_strip
from ui.formatting import context_label, decimal_label, duration_label, kda_line, match_date_label, signed_label, playtime_label
from ui.page_helpers import database_revision, select_short_game_inclusion
from ui.profiles import get_active_settings, get_active_player
from ui.session_navigation import cached_review_history, open_review_timeline, session_label
from ui.theme import TEAL
from ui.charts import chart_style


METRIC_LABELS = {"kda_mean": "KDA moyen", "kda_median": "KDA médian", "cs_per_min": "CS / min",
                 "gold_per_min": "Gold / min", "damage_per_min": "Dégâts / min", "vision_per_min": "Vision / min",
                 "gold_diff_15": "GD @15 médian"}


def _cards(review: dict, puuid: str, gap: int) -> None:
    rows, summary = review["matches"], review["summary"]
    service = get_static_data_service()
    for offset in range(0, len(rows), 4):
        for column, row in zip(st.columns(min(4, len(rows) - offset)), rows[offset:offset + 4]):
            with column:
                asset = service.champion(str(row.get("champion") or "Inconnu"))
                portrait = f'<img src="{escape(asset.image_url)}" alt="" class="la-review-portrait">' if asset.image_url else '<span class="la-review-portrait la-portrait-fallback"></span>'
                result, tone = ("WIN", "win") if row.get("win") == 1 else ("LOSS", "loss") if row.get("win") == 0 else ("N/A", "muted")
                short_badge = '<span class="la-badge la-badge-gold">Partie courte &lt;5 min</span>' if isinstance(row.get("duration"), (int, float)) and 0 <= row['duration'] < 300 else ''
                pause = "Début de session" if row["session_game_number"] == 1 else f'Pause {decimal_label(row.get("minutes_since_previous_game"), 0)} min'
                focused = " la-review-focus" if st.session_state.get("review_focus_match") == row["match_id"] else ""
                single = " la-review-single" if len(rows) == 1 else ""
                trajectory = context_label(row.get("trajectory")) if row.get("timeline_available") and row.get("trajectory") else "Trajectoire N/A"
                gd = row.get("gold_diff_15") if row.get("timeline_available") else None
                st.markdown(
                    f'<article class="la-review-game la-result-{tone}{focused}{single}"><header><span>PARTIE {row["session_game_number"]}</span><b class="la-{tone}">{result}</b></header>'
                    f'<div class="la-review-champion">{portrait}<div><b>{escape(asset.display_name)}</b><small>{escape(context_label(row.get("role") or "N/A"))}</small></div></div>'
                    f'<div class="la-review-kda">{escape(kda_line(row.get("kills"), row.get("deaths"), row.get("assists")))}</div>{short_badge}'
                    f'<small>{duration_label(row.get("duration"))} en jeu · {match_date_label(row.get("game_creation"), True)[-5:]}</small>'
                    f'<footer><span>GD @15 <b>{signed_label(gd)}</b></span><small>{escape(trajectory)}</small><small>{escape(pause)}</small></footer></article>',
                    unsafe_allow_html=True,
                )
                if st.button(f"Revoir la partie {row['session_game_number']} →", key=f"review_game_{row['match_id']}", width="stretch"):
                    open_review_timeline(row["match_id"], puuid, summary["anchor_match_id"], gap)


def _comparisons(review: dict) -> None:
    section_header("Votre repère personnel", "Même champion, même rôle. Jusqu’à 20 parties strictement antérieures à cette session.")
    for comparison in review["comparisons"]:
        champion, role = comparison["champion"], comparison["role"]
        st.markdown(f"**{markdown_text(champion)} · {markdown_text(context_label(role))}** — N session = {comparison['session_n']} · N historique = {comparison['baseline_n']}")
        if not comparison["eligible"]:
            st.caption("Échantillon insuffisant : au moins 2 parties dans la session, 5 dans l’historique et un rôle identifié sont nécessaires. Aucun écart mis en avant.")
        # Role-aware ordering: Support does not inherit a Jungle farming headline.
        keys = ["kda_mean", "kda_median", "vision_per_min" if role == "UTILITY" else "cs_per_min", "gold_diff_15"]
        if comparison["eligible"]:
            metrics = []
            for key in keys:
                current, baseline = comparison["session"][key], comparison["baseline"][key]
                usable = current["n"] >= 2 and baseline["n"] >= 5
                fmt = signed_label if key == "gold_diff_15" else decimal_label
                metrics.append({"Mesure": METRIC_LABELS[key], "Session": fmt(current["value"]), "N session": current["n"],
                                "Historique": fmt(baseline["value"]), "N historique": baseline["n"],
                                "Lecture": "Descriptive" if usable else "Données insuffisantes"})
            st.dataframe(metrics, hide_index=True, width="stretch")
    with st.expander("Toutes les mesures · moyennes, médianes et effectifs"):
        st.caption("Les ratios sont calculés par partie, puis moyennés. KDA = (kills + assists) / max(deaths, 1). Les valeurs absentes sont exclues, jamais remplacées par zéro.")
        for comparison in review["comparisons"]:
            st.markdown(f"**{markdown_text(comparison['champion'])} · {markdown_text(context_label(comparison['role']))}**")
            st.dataframe([{"Mesure": METRIC_LABELS[key], "Session": decimal_label(current["value"]), "N session": current["n"],
                           "Historique": decimal_label(comparison["baseline"][key]["value"]), "N historique": comparison["baseline"][key]["n"]}
                          for key, current in comparison["session"].items()], hide_index=True, width="stretch")


def _phases(review: dict) -> None:
    section_header("Le tempo de cette session", "Écart de gold d’équipe médian · chaque point indique son effectif réel.")
    stat_strip([(f"À {p['minute']} min · N={p['n']}", signed_label(p["median"])) for p in review["phases"]])
    if not any(p["n"] for p in review["phases"]):
        st.caption("Checkpoints indisponibles. Une partie trop courte ou une frame incomplète ne vaut pas un écart nul.")
    rows = review["matches"]
    if len(rows) >= 3 and any(row.get("timeline_available") and row.get("gold_diff_15") is not None for row in rows):
        with st.expander("Écart individuel à 15 min, partie après partie"):
            figure = go.Figure(go.Scatter(x=[r["session_game_number"] for r in rows],
                y=[r.get("gold_diff_15") if r.get("timeline_available") else None for r in rows],
                mode="lines+markers", connectgaps=False, line={"color": TEAL},
                hovertemplate="Partie %{x}<br>GD @15 : %{y:+.0f}g<extra></extra>"))
            chart_style(figure, 230)
            figure.update_xaxes(title="Ordre des parties", dtick=1)
            figure.update_yaxes(title="Gold vs adversaire direct")
            figure.add_hline(y=0, line_dash="dot", line_color="#9aaec2")
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
            st.caption("Les rôles peuvent varier entre les parties. Une absence de point signifie donnée indisponible ; cette courbe n’est pas une tendance générale.")


def _enrichment(review: dict, database: Database, settings, puuid: str) -> None:
    summary = review["summary"]
    missing = summary["games"] - summary["timeline_games"]
    st.caption(f"Timelines disponibles : {summary['timeline_games']}/{summary['games']}. Les indicateurs early game portent uniquement sur les parties observées.")
    if not missing:
        return
    ids = set(summary["match_ids"])
    eligible = [match_id for match_id in database.timeline_candidates(puuid, retry_errors=True) if match_id in ids]
    st.info(f"{missing} Timeline(s) manquante(s). Les statistiques de fin de partie restent lisibles. {len(eligible)} enrichissement(s) relançable(s) dans cette session.")
    if st.button("Enrichir les Timelines de cette session", key="review_enrich", disabled=not eligible or settings.demo_mode or not settings.riot_api_key or import_is_running(settings.database_path)):
        from ui.library import _riot_error_message
        try:
            with st.spinner("Enrichissement de cette session uniquement…"):
                result = enrich_timelines_from_settings(settings, puuid, retry_errors=True, match_ids=eligible)
            st.session_state["review_flash"] = f"Timelines : {result.available} disponible(s), {result.unavailable} indisponible(s), {result.errors} erreur(s)."
        except Exception as exc:
            st.error(_riot_error_message(exc))
        else:
            st.cache_data.clear()
            st.rerun()
    if not settings.riot_api_key and not settings.demo_mode:
        st.caption("Une clé Riot valide est nécessaire uniquement pour l’enrichissement explicite.")


def show_session_review() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Votre session, en perspective.", "Le bilan, les écarts et les parties à revoir. Tout vient de votre bibliothèque locale.", "SESSION REVIEW")
    if not player:
        empty_state("Votre prochaine session commence ici", "Synchronisez un profil pour retrouver ses parties, ou lancez la démo synthétique.")
        return
    puuid = str(player["puuid"])
    gap = int(st.session_state.get("review_gap", st.session_state.get("review_saved_gap", 45)))
    if gap not in (30, 45, 60, 90):
        gap = 45
    st.session_state["review_gap"] = gap
    selector_area, definition_area = st.columns([3, 1])
    with definition_area:
        gap = st.selectbox("Pause maximale", [30, 45, 60, 90], key="review_gap", format_func=lambda minutes: f"{minutes} min")
    st.session_state["review_saved_gap"] = gap
    history = cached_review_history(str(database.path), puuid, database_revision(database.path), gap)
    catalog = session_catalog(history)
    if not catalog:
        empty_state("Aucune session datée pour ce profil", "Importez des parties. Les dates absentes ou invalides ne créent pas de fausse session.")
        return
    requested = st.session_state.pop("review_requested_match", st.session_state.get("review_anchor_match"))
    summary = resolve_session(catalog, requested)
    labels = {s["anchor_match_id"]: session_label(s) for s in catalog}
    if requested and requested not in {m for s in catalog for m in s["match_ids"]}:
        st.caption("La session demandée n’est plus disponible pour ce profil. La plus récente est affichée.")
    if st.session_state.get("review_selector") != summary["anchor_match_id"]:
        st.session_state["review_selector"] = summary["anchor_match_id"]
    def remember_selection():
        st.session_state["review_anchor_match"] = st.session_state["review_selector"]
        st.session_state.pop("review_focus_match", None)
    anchor = selector_area.selectbox("Session à revoir", list(labels), format_func=labels.get, key="review_selector", on_change=remember_selection)
    st.session_state["review_anchor_match"] = anchor
    include_short = select_short_game_inclusion("review")
    review = build_session_review(history, anchor, include_short_games=include_short)
    summary = review["summary"]
    analyzed = review["analysis_summary"]
    if review["excluded_short_games"]:
        st.caption(f"{analyzed['games']} parties analysées · {review['excluded_short_games']} partie(s) <5 min exclue(s) des indicateurs, conservée(s) dans le fil et l’export de session.")
    flash = st.session_state.pop("review_flash", None)
    if flash:
        st.success(flash)
    mix = " · ".join(f"{champion} ×{n}" for champion, n in summary["champions"].items())
    queues = " · ".join(queue_name(q) for q in summary["queues"])
    st.markdown(f'<section class="la-review-hero"><div><div class="la-eyebrow">SUR CETTE SESSION · {summary["games"]} PARTIE{"S" if summary["games"] != 1 else ""}</div>'
        f'<div class="la-review-date">{match_date_label(summary["start_ms"], True)} <span>→ {match_date_label(summary["end_ms"], True)[-5:] if match_date_label(summary["start_ms"]) == match_date_label(summary["end_ms"]) else match_date_label(summary["end_ms"], True)}</span></div>'
        f'<p>{escape(mix)}</p><small>{escape(queues)} · {playtime_label(summary["play_seconds"])} en jeu</small></div>'
        f'<div class="la-review-score"><strong><span class="la-win">{analyzed["wins"]} V</span> <span class="la-muted">/</span> <span class="la-loss">{analyzed["losses"]} D</span></strong>'
        f'<small>{decimal_label(analyzed["winrate"], 0)} % sur {analyzed["games"]} parties analysées · {summary["timeline_games"]}/{summary["games"]} Timelines</small></div></section>', unsafe_allow_html=True)
    if summary["unknown_results"]:
        st.caption(f"{summary['unknown_results']} résultat(s) indisponible(s), exclus du winrate.")
    section_header("Le fil de la session", "Dans l’ordre de jeu · ouvrez une partie pour retrouver sa Timeline.")
    _cards(review, puuid, gap)
    if review["highlights"]:
        section_header("Ce qui ressort", "Des observations sur cette session, pas un diagnostic de votre jeu.")
        for highlight in review["highlights"]:
            st.markdown(f'<div class="la-review-observation"><span>↗</span><div>{escape(highlight["text"])} <small>N={highlight["n"]}</small></div></div>', unsafe_allow_html=True)
    remarkable = review["remarkable_match_id"]
    if remarkable:
        number = summary["match_ids"].index(remarkable) + 1
        if st.button(f"Partie à examiner · #{number} → Timeline", help=review["remarkable_reason"], key="review_remarkable"):
            open_review_timeline(remarkable, puuid, anchor, gap)
        st.caption(review["remarkable_reason"] + " Ce n’est pas un classement de vos meilleures ou pires parties.")
    _enrichment(review, database, settings, puuid)
    _comparisons(review)
    _phases(review)
    section_header("Garder une trace", "Cette session uniquement · résumé Markdown et parties JSONL, sans identités externes. AI Export conserve son export global.")
    export_review = review if include_short else build_session_review(history, anchor, include_short_games=True)
    st.download_button("Exporter cette session · ZIP", build_session_package(export_review, settings.riot_id),
                       session_package_filename(settings.riot_id, summary["games"]), "application/zip", key="review_download")
    st.caption("Votre Riot ID et les IDs des matchs sont conservés. L’export n’est pas une garantie d’anonymat. Les petits effectifs ne permettent pas de conclure à une tendance durable.")
    with st.expander("Méthode et définition de la session"):
        st.caption("Même moteur que Contexts : intervalle entre la fin d’une partie et le début de la suivante, sur l’historique complet du profil. Aucun appel Riot au recalcul. Si la durée d’une partie est inconnue, la suivante ouvre une nouvelle session, sans pause inventée.")


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
