"""Discover patterns, then explore one descriptive comparison at a time."""

from __future__ import annotations

from datetime import datetime, time

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.insights import build_insight_bundle
from core.db import Database
from core.queues import QUEUE_NAMES
from core.static_data import get_static_data_service
from ui.charts import chart_style
from ui.components import discovery_card, empty_state, page_header, scope_bar, section_header, stat_strip
from ui.context_charts import comparison_figure, discovery_figure, distribution_figure
from ui.formatting import context_label, decimal_label, signed_label
from ui.page_helpers import cached_context_dataset, database_revision, select_short_game_inclusion
from ui.profiles import get_active_settings, get_active_player
from ui.theme import GOLD, MUTED, TEAL


DATASETS = {f"{n} dernières": n for n in (20, 50, 100, 200, 500)} | {"Tout l’historique": None}
PLOT_CONFIG = {"displayModeBar": False}


def _display_rows(rows: list[dict[str, object]], context: str = "") -> list[dict[str, object]]:
    return [{
        "Contexte": context_label(row.get("label"), context), "N": row.get("games", 0),
        "WR": decimal_label(row.get("winrate"), 1, " %"),
        "KDA": decimal_label(row.get("kda"), 2), "CS/min": decimal_label(row.get("cs_per_min"), 2),
        "Gold/min": decimal_label(row.get("gold_per_min"), 0), "DPM": decimal_label(row.get("damage_per_min"), 0),
        "Vision/min": decimal_label(row.get("vision_per_min"), 2), "Échantillon": row.get("sample_size"),
    } for row in rows]


def _timeline_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{
        "Contexte": context_label(row.get("label")), "N": row.get("games", 0),
        "WR": decimal_label(row.get("winrate"), 1, " %"), "GD @10": signed_label(row.get("gold_diff_10")),
        "GD @15": signed_label(row.get("gold_diff_15")), "Équipe GD @15": signed_label(row.get("team_gold_diff_15")),
        "Morts avant 10": decimal_label(row.get("deaths_before_10"), 2), "Échantillon": row.get("sample_size"),
    } for row in rows]


def _comparison(rows: list[dict[str, object]], key: str, timeline: bool = False) -> None:
    if not rows or not any(int(row.get("games", 0)) for row in rows):
        empty_state("Aucune observation disponible", "Cette comparaison nécessite des parties correspondant à ce contexte.")
        return
    metric_options = {"Winrate": "winrate", "Gold Diff @15": "gold_diff_15"} if timeline else {
        "Winrate": "winrate", "KDA": "kda", "CS/min": "cs_per_min", "DPM": "damage_per_min",
    }
    metric = st.segmented_control("Comparer", list(metric_options), default="Winrate", key=f"insights_metric_{key}") or "Winrate"
    st.plotly_chart(comparison_figure(rows, metric_options[metric], metric), width="stretch", config=PLOT_CONFIG)
    st.caption("N = nombre de parties. Les groupes de moins de 5 parties apparaissent en gris.")
    if len(rows) > 12:
        st.caption("Le graphique montre les 12 premiers contextes ; le détail ci-dessous conserve toute la sélection.")
    with st.expander("Toutes les métriques de cette comparaison"):
        st.dataframe(_timeline_rows(rows) if timeline else _display_rows(rows), width="stretch", hide_index=True)


def _discovery_tab(bundle: dict[str, object]) -> None:
    highlights = bundle["highlights"]
    if highlights:
        section_header("Ce qui ressort", "Les écarts les plus marqués de la sélection, avec au moins 10 parties dans chaque groupe.")
        for column, observation in zip(st.columns(2), highlights[:2]):
            with column:
                discovery_card(observation)
        st.plotly_chart(discovery_figure(highlights), width="stretch", config=PLOT_CONFIG)
        st.caption("Losange doré : WR le plus bas · Point turquoise : WR le plus haut. Les effectifs sont indiqués au survol et dans le détail.")
        if len(highlights) > 2:
            with st.expander(f"{len(highlights) - 2} autres observations mises en avant"):
                for observation in highlights[2:]:
                    discovery_card(observation)
    else:
        empty_state("L’historique n’a pas encore assez de recul", "Une découverte est mise en avant lorsque les deux groupes comparés comptent chacun au moins 10 parties. Vous pouvez déjà explorer les contextes dans les autres onglets.")
    discovery = bundle["discovery"]
    with st.expander(f"Examiner les {len(discovery)} comparaisons candidates · N ≥ 5 par groupe"):
        if discovery:
            st.dataframe([{
                "Contexte": context_label(row["context"]), "Groupe haut": context_label(row["high"]["label"], row["context"]),
                "N haut": row["high"]["games"], "WR haut": decimal_label(row["high"]["winrate"], 1, " %"),
                "Groupe bas": context_label(row["low"]["label"], row["context"]), "N bas": row["low"]["games"],
                "WR bas": decimal_label(row["low"]["winrate"], 1, " %"),
            } for row in discovery], hide_index=True, width="stretch")
        else:
            st.caption("Aucune comparaison n’atteint encore 5 parties dans chacun de ses groupes.")
        st.caption("Le classement combine l’écart de winrate et le plus petit effectif. Il ne mesure ni une probabilité ni une cause.")


def _meta_tab(bundle: dict[str, object]) -> None:
    personal = bundle["personal_meta"]
    champion_rows = personal["champion_frequency"]
    role_rows = personal["role_distribution"]
    if not champion_rows:
        empty_state("Pas encore de répartition", "Les champions et rôles apparaîtront après la synchronisation de parties.")
        return
    section_header("Votre pool, au fil des patches", "La fréquence d’utilisation décrit vos choix dans cette sélection.")
    periods = sorted({str(row["period"]) for row in champion_rows}, reverse=True)
    patch = st.selectbox("Patch à explorer", periods, key="insights_meta_patch")
    selected_champions = [dict(row, label=row["champion"]) for row in champion_rows if str(row["period"]) == patch]
    selected_roles = [row for row in role_rows if str(row["period"]) == patch]
    left, right = st.columns([1.4, 1])
    with left:
        st.plotly_chart(comparison_figure(selected_champions, "pick_rate", "Part des parties (%)", limit=8), width="stretch", config=PLOT_CONFIG)
        if len(selected_champions) > 8:
            st.caption("Les 8 champions les plus joués sont affichés. Le détail conserve le pool complet.")
    with right:
        figure = go.Figure(go.Pie(
            labels=[context_label(row["role"]) for row in selected_roles], values=[row["games"] for row in selected_roles],
            hole=.72, sort=False, marker={"colors": [TEAL, GOLD, "#7792c8", "#aa91ba", MUTED]},
            textinfo="label+percent", textposition="outside", hovertemplate="%{label}<br>%{value} parties · %{percent}<extra></extra>",
        ))
        chart_style(figure, 300)
        figure.update_layout(showlegend=False, annotations=[{"text": "RÔLES", "showarrow": False, "font": {"size": 13}}])
        st.plotly_chart(figure, width="stretch", config=PLOT_CONFIG)
    with st.expander("Détail complet par patch, champion et rôle"):
        st.dataframe([{"Patch": row["period"], "Champion": row["champion"], "N": row["games"], "Part des parties": decimal_label(row["pick_rate"], 1, " %")} for row in champion_rows], hide_index=True, width="stretch")
        st.dataframe([{"Patch": row["period"], "Rôle": context_label(row["role"]), "N": row["games"], "Part des parties": decimal_label(row["role_rate"], 1, " %")} for row in role_rows], hide_index=True, width="stretch")


def _consistency_tab(rows: list[dict[str, object]]) -> None:
    section_header("Au-delà de la moyenne", "L’intervalle P25–P75 contient la moitié centrale de vos performances. Ce n’est pas un intervalle de confiance.")
    if not rows:
        empty_state("Distribution indisponible", "Aucune métrique exploitable dans la sélection.")
        return
    metric = st.segmented_control("Métrique", [row["metric"] for row in rows], default=rows[0]["metric"], key="insights_consistency_metric") or rows[0]["metric"]
    selected = next(row for row in rows if row["metric"] == metric)
    if not selected["games"]:
        empty_state("Métrique indisponible", "Cette métrique n’est renseignée dans aucune partie sélectionnée.")
    else:
        stat_strip([("Médiane", decimal_label(selected["median"], 2)), ("Moyenne", decimal_label(selected["mean"], 2)), ("Écart P75−P25", decimal_label(selected["iqr"], 2)), ("Parties renseignées", str(selected["games"]))])
        st.plotly_chart(distribution_figure(selected), width="stretch", config=PLOT_CONFIG)
    with st.expander("Toutes les distributions"):
        st.dataframe([{"Métrique": row["metric"], "N": row["games"], **{label: decimal_label(row[key], 2) for key, label in (("mean", "Moyenne"), ("median", "Médiane"), ("p25", "P25"), ("p75", "P75"), ("iqr", "IQR"))}} for row in rows], hide_index=True, width="stretch")


def show_insights() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Insights", "Repérez ce qui varie dans vos parties, puis explorez le contexte derrière les chiffres.")
    if not player:
        empty_state("Votre histoire commence avec vos parties", "Synchronisez ce profil depuis la barre latérale pour découvrir ses premières tendances.")
        return
    all_games = cached_context_dataset(str(settings.database_path), str(player["puuid"]), database_revision(settings.database_path))
    if all_games.is_empty():
        empty_state("Aucune partie locale pour ce profil", "Synchronisez des parties pour ouvrir les comparaisons.")
        return
    left, middle, right = st.columns([1.4, 1, .7], vertical_alignment="bottom")
    champion = left.selectbox("Champion", ["Tous", *sorted(all_games["champion"].drop_nulls().unique().to_list())], key="insights_champion")
    dataset_label = middle.selectbox("Historique", list(DATASETS), index=1, key="insights_dataset")
    selected = all_games if champion == "Tous" else all_games.filter(pl.col("champion") == champion)
    selected = selected.sort(["game_creation", "match_id"], descending=[True, True])
    limit = DATASETS[dataset_label]
    if limit is not None:
        selected = selected.head(limit)
    base_size = selected.height
    with right.popover("Période", width="stretch"):
        include_short = select_short_game_inclusion("insights")
        if not selected["game_creation"].drop_nulls().is_empty():
            minimum = datetime.fromtimestamp(int(selected["game_creation"].min()) / 1000).date()
            maximum = datetime.fromtimestamp(int(selected["game_creation"].max()) / 1000).date()
            dates = st.date_input("Du / au", (minimum, maximum), min_value=minimum, max_value=maximum, key=f"insights_dates_{champion}_{minimum}_{maximum}")
            if isinstance(dates, tuple) and len(dates) == 2:
                selected = selected.filter(pl.col("game_creation").is_between(int(datetime.combine(dates[0], time.min).timestamp() * 1000), int(datetime.combine(dates[1], time.max).timestamp() * 1000)))
    if not include_short:
        from analytics.constants import SHORT_GAME_THRESHOLD_SECONDS
        selected = selected.filter(pl.col("duration").is_null() | (pl.col("duration") >= SHORT_GAME_THRESHOLD_SECONDS))
    coverage_count = selected.filter(pl.col("timeline_available") == True).height  # noqa: E712
    scope_bar(base_size, selected.height, f"Champion filtré avant la fenêtre · Timeline {coverage_count}/{selected.height}")
    if selected.is_empty():
        empty_state("Aucune partie sur cette période", "Élargissez la période ou sélectionnez un autre champion.")
        return
    bundle = build_insight_bundle(selected, pl.DataFrame())
    discovery_tab, context_tab, flow_tab, meta_tab, consistency_tab = st.tabs(["Découvertes", "Comparer les contextes", "Déroulement", "Pool personnel", "Régularité"])
    with discovery_tab:
        _discovery_tab(bundle)
    with context_tab:
        options = {"Côté de la carte": "side", "Rôle": "role", "Durée": "duration", "File de jeu": "queue", "Patch": "patch", "Victoires et défaites": "wins_losses", "20 dernières et 20 précédentes": "recent_previous"}
        choice = st.selectbox("Quel contexte explorer ?", list(options), key="insights_compare_context")
        _comparison(bundle[options[choice]], "context", timeline=options[choice] in {"wins_losses", "recent_previous"})
    with flow_tab:
        fast = bundle["fast_wins"]
        stat_strip([("Victoires en moins de 20 min", str(fast["games"])), ("Parties avec Timeline", f"{coverage_count}/{selected.height}")])
        with st.expander("Détail des victoires rapides"):
            st.dataframe(_display_rows([fast]), hide_index=True, width="stretch")
        if not coverage_count:
            empty_state("Les Timelines complètent cette lecture", "Récupérez les Timelines depuis la barre latérale pour explorer l’early game, les objectifs et les trajectoires.")
        else:
            options = {"Avance personnelle à 15 min": "ahead_15", "Avance personnelle à 10 min": "ahead_10", "Conversion et comeback": "conversion_comeback", "Première mort": "first_death", "Trajectoires à 10 → 15 → 20 min": "trajectories", "Premier dragon": "first_dragon", "Premier Herald": "first_herald", "Première tour": "first_tower", "Morts avant 10 min": "death_profile"}
            choice = st.selectbox("Moment de la partie", list(options), key="insights_flow_context")
            key = options[choice]
            rows = [bundle["conversion"], bundle["comeback"]] if key == "conversion_comeback" else bundle[key]
            _comparison(rows, "flow", timeline=True)
            st.caption("Chaque contexte utilise les parties où l’événement ou le checkpoint est renseigné ; son N peut être inférieur à la couverture Timeline.")
    with meta_tab:
        _meta_tab(bundle)
    with consistency_tab:
        _consistency_tab(bundle["consistency"])
    with st.expander("Couverture des données"):
        identifiable = selected.filter(pl.col("matchup_available") == True).height  # noqa: E712
        stat_strip([("Parties", str(selected.height)), ("Timelines", f"{coverage_count}/{selected.height}"), ("Adversaires identifiés", f"{identifiable}/{selected.height}"), ("Matchups ambigus", str(selected.height - identifiable))])
        st.caption(f"Parties <5 min : {selected.filter(pl.col('duration') < 300).height} · Files inconnues : {selected.filter(~pl.col('queue_id').is_in(list(QUEUE_NAMES))).height} · Assets de remplacement : {get_static_data_service().fallback_count}")
    st.caption("Ces observations décrivent cet historique. Elles n’établissent pas qu’un contexte cause un résultat.")


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
