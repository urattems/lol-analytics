"""Explore observed inventories, compare their results, then follow purchases."""

from __future__ import annotations

import polars as pl
import streamlit as st

from analytics.builds import build_summary, compare_builds, filter_build_dataset, purchase_sequence_summary
from analytics.samples import sample_size_label
from analytics.timeline import load_timeline_analytics
from core.db import Database
from core.static_data import DataDragonService, get_static_data_service
from ui.components import empty_state, page_header, section_header, stat_strip
from ui.formatting import context_label, decimal_label, duration_label
from ui.page_helpers import load_selected_dataset
from ui.profiles import get_active_settings, get_active_player
from ui.static_assets import item_names, render_item_strip


def _render_build_card(title: str, row: dict[str, object], static_data: DataDragonService) -> None:
    section_header(title)
    render_item_strip(row["items"], static_data, icons_only=True)
    stat_strip([("Parties", str(row["games"])), ("Winrate", decimal_label(row["winrate"], 1, " %")), ("KDA", decimal_label(row["kda"], 2))])
    stat_strip([("CS/min", decimal_label(row["cs_per_minute"], 2)), ("Gold/min", decimal_label(row["gold_per_minute"], 0)), ("DPM", decimal_label(row["damage_per_minute"], 0))])
    st.caption(f"Échantillon : {sample_size_label(int(row['games'])).lower()}.")
    with st.expander("Objets de cet inventaire"):
        st.write(item_names(row["items"], static_data) or "Inventaire vide")


def _inventory_rows(rows: list[dict[str, object]], static_data: DataDragonService) -> list[dict[str, object]]:
    return [{
        "Inventaire": f"#{index + 1}", "Objets": item_names(row["items"], static_data) or "Inventaire vide",
        "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"),
        "KDA": decimal_label(row["kda"], 2), "CS/min": decimal_label(row["cs_per_minute"], 2),
        "Gold/min": decimal_label(row["gold_per_minute"], 0), "DPM": decimal_label(row["damage_per_minute"], 0),
    } for index, row in enumerate(rows)]


def _build_table(summary: pl.DataFrame, static_data: DataDragonService, total: int) -> None:
    rows = summary.to_dicts()
    for offset in range(0, min(6, len(rows)), 2):
        for index, column in zip(range(offset, min(offset + 2, len(rows))), st.columns(2)):
            row = rows[index]
            with column.container(border=True, key=f"build_inventory_card_{index}"):
                st.caption(f"INVENTAIRE #{index + 1} · {decimal_label(int(row['games']) * 100 / total, 0, ' %')} de la sélection")
                render_item_strip(row["items"], static_data, icons_only=True)
                st.markdown(
                    '<div class="la-build-stats">'
                    f'<span><small>Parties</small><b>{row["games"]}</b></span>'
                    f'<span><small>Winrate</small><b>{decimal_label(row["winrate"], 1, " %")}</b></span>'
                    f'<span><small>KDA</small><b>{decimal_label(row["kda"], 2)}</b></span>'
                    f'<span><small>DPM</small><b>{decimal_label(row["damage_per_minute"], 0)}</b></span>'
                    '</div>', unsafe_allow_html=True,
                )
                st.caption(f"Échantillon {sample_size_label(int(row['games'])).lower()}")
    with st.expander(f"Les {len(rows)} inventaires · noms des objets et toutes les métriques"):
        st.dataframe(_inventory_rows(rows, static_data), width="stretch", hide_index=True)


def _compare_tab(summary: pl.DataFrame, static_data: DataDragonService) -> None:
    section_header("Deux inventaires, deux contextes", "Ces résultats décrivent les parties jouées avec ces objets ; ils ne mesurent pas leur effet sur une victoire.")
    if summary.height < 2:
        empty_state("Un seul inventaire observé", "Élargissez l’historique pour comparer deux inventaires distincts.")
        return
    labels = {
        str(row["build_key"]): f"#{index + 1} · {row['games']} parties · {item_names(row['items'], static_data) or 'Inventaire vide'}"
        for index, row in enumerate(summary.to_dicts())
    }
    left, right = st.columns(2)
    selected_a = left.selectbox("Inventaire A", list(labels), format_func=labels.get, key="build_a")
    selected_b = right.selectbox("Inventaire B", list(labels), index=1, format_func=labels.get, key="build_b")
    build_a, build_b = compare_builds(summary, selected_a, selected_b)
    if selected_a == selected_b:
        st.caption("Le même inventaire est sélectionné des deux côtés.")
    left, right = st.columns(2)
    with left.container(border=True):
        if build_a:
            _render_build_card("Inventaire A", build_a, static_data)
    with right.container(border=True):
        if build_b:
            _render_build_card("Inventaire B", build_b, static_data)


def _purchases_tab(filtered: pl.DataFrame, database: Database, puuid: str, champion: str, role: str, static_data: DataDragonService) -> None:
    section_header("Les premiers achats, dans leur ordre réel", "Les 8 premiers achats enregistrés par partie, y compris composants et consommables. Les ventes et annulations ne sont pas des achats.")
    selected_ids = set(filtered["match_id"].to_list())
    timelines = [row for row in load_timeline_analytics(database, puuid)
                 if row.get("match_id") in selected_ids
                 and (champion == "Tous" or row.get("champion") == champion)
                 and (role == "Tous" or row.get("role") == role)]
    sequences = purchase_sequence_summary(timelines)
    stat_strip([("Parties avec Timeline", f"{len(timelines)}/{filtered.height}"), ("Séquences observées", str(len(sequences)))])
    if not sequences:
        empty_state("Aucun achat Timeline dans cette sélection", "Récupérez les Timelines depuis la barre latérale pour retrouver l’ordre des achats. Les inventaires finaux restent disponibles.")
        return
    for row in sequences[:8]:
        with st.container(border=True):
            st.caption(f"{row['games']} parties · {decimal_label(row['winrate'], 1, ' %')} WR · échantillon {sample_size_label(int(row['games'])).lower()}")
            render_item_strip(row["sequence"], static_data, icons_only=True, ordered=True)
            timings = [duration_label(int(value) // 1000) if value is not None else "N/A" for value in row["average_timings_ms"]]
            st.caption(f"Temps moyens des achats : {' → '.join(timings)}")
    with st.expander("Toutes les séquences · objets et temps d’achat"):
        st.dataframe([{
            "Achats successifs": " → ".join(static_data.item(item).display_name for item in row["sequence"]),
            "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"),
            "Temps moyens": " → ".join(duration_label(int(value) // 1000) if value is not None else "N/A" for value in row["average_timings_ms"]),
        } for row in sequences], width="stretch", hide_index=True)


def show_build_lab() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    static_data = get_static_data_service()
    player = get_active_player(database, settings)
    page_header("Build Lab", "Explorez les objets de vos parties, leurs résultats observés et le moment de leurs achats.")
    if not player:
        empty_state("Les objets racontent aussi vos parties", "Synchronisez ce profil depuis la barre latérale pour retrouver ses premiers inventaires.")
        return
    games, _ = load_selected_dataset(database, settings.database_path, str(player["puuid"]), "build_lab")
    if games.is_empty():
        empty_state("Aucune partie dans cette sélection", "Élargissez l’historique ou incluez les parties courtes pour retrouver des inventaires.")
        return
    left, right = st.columns(2)
    champion = left.selectbox("Champion", ["Tous", *sorted(games["champion"].drop_nulls().unique().to_list())], key="build_champion")
    role = right.selectbox("Rôle", ["Tous", *sorted(games["role"].drop_nulls().unique().to_list())], format_func=context_label, key="build_role")
    filtered = filter_build_dataset(games, champion=None if champion == "Tous" else champion, role=None if role == "Tous" else role)
    if filtered.is_empty():
        empty_state("Aucun inventaire pour ces filtres", "Choisissez un autre champion ou un autre rôle dans la fenêtre actuelle.")
        return
    summary = build_summary(filtered)
    st.caption(f"{filtered.height} parties après filtres · {summary.height} inventaires distincts")
    inventories, comparison, purchases = st.tabs(["Inventaires", "Comparer", "Achats Timeline"])
    with inventories:
        section_header("À la fin de la partie", "Les mêmes objets sont regroupés quel que soit leur emplacement, sans le trinket. L’inventaire final ne donne pas l’ordre des achats.")
        _build_table(summary, static_data, filtered.height)
    with comparison:
        _compare_tab(summary, static_data)
    with purchases:
        _purchases_tab(filtered, database, str(player["puuid"]), champion, role, static_data)


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
