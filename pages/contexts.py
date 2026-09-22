"""Three contextual views: opponents, recurring teammates and session rhythm."""

from __future__ import annotations

from datetime import datetime, time
from html import escape

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.matchups import matchup_detail, matchup_matrix, observed_extremes
from analytics.sessions import assign_sessions, session_analytics
from analytics.teammates import RECURRING_TEAMMATE_MIN_GAMES, champion_pairings, solo_vs_known, squad_summary, teammate_summary
from core.db import Database
from ui.charts import chart_style
from ui.components import empty_state, page_header, scope_bar, section_header, stat_strip
from ui.context_charts import comparison_figure, session_sequence_figure
from ui.formatting import context_label, decimal_label, duration_label, kda_line, match_date_label, signed_label
from ui.page_helpers import cached_context_dataset, database_revision, select_short_game_inclusion
from ui.profiles import get_active_settings, get_active_player
from ui.theme import TEAL


DATASETS = {f"{n} dernières": n for n in (20, 50, 100, 200, 500)} | {"Tout l’historique": None}
PLOT_CONFIG = {"displayModeBar": False}


def _select_scope(games: pl.DataFrame) -> pl.DataFrame:
    champion_volume = games.group_by("champion").len().sort("len", descending=True)["champion"].drop_nulls().to_list()
    champions = ["Tous", *champion_volume]
    requested = st.query_params.get("champion")
    if requested in champions and st.session_state.get("_contexts_query_applied") != requested:
        st.session_state["contexts_champion"] = requested
        st.session_state["_contexts_query_applied"] = requested
    index = champions.index(requested) if requested in champions else 1 if len(champions) > 1 else 0
    c1, c2, c3, c4, c5 = st.columns([1.3, 1, .9, 1.2, .7], vertical_alignment="bottom")
    champion = c1.selectbox("Champion", champions, index=index, key="contexts_champion")
    role = c2.selectbox("Rôle", ["Tous", *sorted(games["role"].drop_nulls().unique().to_list())], format_func=context_label, key="contexts_role")
    patch = c3.selectbox("Patch", ["Tous", *sorted(games["patch"].drop_nulls().unique().to_list(), reverse=True)], key="contexts_patch")
    dataset = c4.selectbox("Historique", list(DATASETS), index=2, key="contexts_dataset")
    selected = games.sort(["game_creation", "match_id"], descending=[True, True])
    limit = DATASETS[dataset]
    if limit is not None:
        selected = selected.head(limit)
    base_size = selected.height
    for column, value in (("champion", champion), ("role", role), ("patch", patch)):
        if value != "Tous":
            selected = selected.filter(pl.col(column) == value)
    with c5.popover("Période", width="stretch"):
        include_short = select_short_game_inclusion("contexts")
        if not selected["game_creation"].drop_nulls().is_empty():
            minimum = datetime.fromtimestamp(int(selected["game_creation"].min()) / 1000).date()
            maximum = datetime.fromtimestamp(int(selected["game_creation"].max()) / 1000).date()
            dates = st.date_input("Du / au", (minimum, maximum), min_value=minimum, max_value=maximum, key=f"contexts_dates_{champion}_{role}_{patch}_{minimum}_{maximum}")
            if isinstance(dates, tuple) and len(dates) == 2:
                selected = selected.filter(pl.col("game_creation").is_between(int(datetime.combine(dates[0], time.min).timestamp() * 1000), int(datetime.combine(dates[1], time.max).timestamp() * 1000)))
    if not include_short:
        from analytics.constants import SHORT_GAME_THRESHOLD_SECONDS
        selected = selected.filter(pl.col("duration").is_null() | (pl.col("duration") >= SHORT_GAME_THRESHOLD_SECONDS))
    scope_bar(base_size, selected.height, "Fenêtre récente, puis filtres champion / rôle / patch / dates")
    return selected


def _heatmap(rows: list[dict[str, object]], metric: str) -> go.Figure:
    champion_volume: dict[str, int] = {}
    opponent_volume: dict[str, int] = {}
    for row in rows:
        champion, opponent = str(row["champion"]), str(row["opponent_champion"])
        champion_volume[champion] = champion_volume.get(champion, 0) + int(row["games"])
        opponent_volume[opponent] = opponent_volume.get(opponent, 0) + int(row["games"])
    champions = sorted(champion_volume, key=lambda name: (-champion_volume[name], name))[:8]
    opponents = sorted(opponent_volume, key=lambda name: (-opponent_volume[name], name))[:12]
    lookup = {(str(row["champion"]), str(row["opponent_champion"])): row for row in rows}
    key = {"Winrate": "winrate", "Gold Diff @15": "gold_diff_15", "CS Diff @15": "cs_diff_15"}[metric]
    z, labels, details = [], [], []
    for champion in champions:
        values, texts, custom = [], [], []
        for opponent in opponents:
            row = lookup.get((champion, opponent))
            n = int(row["games"]) if row else 0
            value = row.get(key) if row else None
            values.append(value)
            texts.append(f"{decimal_label(value, 0, '%' if metric == 'Winrate' else '')}<br>N={n}{' · faible' if 0 < n < 5 else ''}" if row else "—")
            custom.append([escape(champion), escape(opponent), n, row.get("winrate") if row else None, row.get("kda") if row else None, row.get("gold_diff_15") if row else None])
        z.append(values)
        labels.append(texts)
        details.append(custom)
    figure = go.Figure(go.Heatmap(
        z=z, x=opponents, y=champions, text=labels, texttemplate="%{text}", customdata=details,
        colorscale=[[0, "#8b4651"], [.5, "#192433"], [1, "#327e70"]],
        zmid=50 if metric == "Winrate" else 0,
        zmin=0 if metric == "Winrate" else None, zmax=100 if metric == "Winrate" else None,
        showscale=False, xgap=4, ygap=4, textfont={"size": 11, "color": "#edf3fa"},
        hovertemplate="<b>%{customdata[0]} vs %{customdata[1]}</b><br>N = %{customdata[2]}<br>WR : %{customdata[3]:.1f} %<br>KDA : %{customdata[4]:.2f}<br>GD @15 : %{customdata[5]:+.0f}g<extra></extra>",
    ))
    chart_style(figure, max(215, 110 + len(champions) * 65))
    figure.update_layout(margin={"l": 12, "r": 12, "t": 10, "b": 70})
    figure.update_xaxes(tickangle=-30, showgrid=False)
    figure.update_yaxes(showgrid=False, autorange="reversed")
    return figure


def _matchup_table(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"Matchup": f"{row['champion']} vs {row['opponent_champion']}", "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"), "KDA": decimal_label(row["kda"], 2), "GD @15": signed_label(row["gold_diff_15"]), "Échantillon": row["sample_size"]} for row in rows]


def _matchup_tab(games: pl.DataFrame) -> None:
    identifiable = games.filter(pl.col("matchup_available") == True).height  # noqa: E712
    matrix = matchup_matrix(games)
    st.caption(f"{identifiable}/{games.height} adversaires identifiés · {len(matrix)} paires observées · {games.height - identifiable} ambigus ou absents")
    if not matrix:
        empty_state("Pas encore de matchup identifiable", "Le matchup nécessite un adversaire unique sur le même rôle. Élargissez la sélection pour retrouver d’autres parties.")
        return
    view_area, metric_area, sort_area = st.columns([1, 2, 1])
    view = view_area.segmented_control("Vue", ["Cartes", "Matrice"], default="Cartes", key="contexts_matchup_view") or "Cartes"
    metric = metric_area.segmented_control("Mesure", ["Winrate", "Gold Diff @15", "CS Diff @15"], default="Winrate", key="contexts_matchup_metric") or "Winrate"
    sort = sort_area.selectbox("Trier par", ["Volume", "Winrate", "Gold Diff @15", "CS Diff @15"], key="contexts_matchup_sort")
    if view == "Cartes":
        from ui.matchup_cards import matchup_cards, render_matchup_cards
        keys = {"Volume": "games", "Winrate": "winrate", "Gold Diff @15": "gold_diff_15", "CS Diff @15": "cs_diff_15"}
        render_matchup_cards(matchup_cards(games, keys[metric], keys[sort]))
    else:
        from ui.history_cards import champion_portrait
        portraits = ''.join(champion_portrait(name, "small") for name in sorted({str(row['opponent_champion']) for row in matrix})[:12])
        st.markdown(f'<div class="la-matchup-legend">{portraits}</div>', unsafe_allow_html=True)
        st.plotly_chart(_heatmap(matrix, metric), width="stretch", config=PLOT_CONFIG)
    st.caption("Sur votre historique uniquement. Moins de 5 parties : faible ; 5–9 : limité ; 10–24 : modéré ; 25+ : solide descriptivement. Aucun « counter » déduit d’un petit effectif.")
    if len({row["opponent_champion"] for row in matrix}) > 12 or len({row["champion"] for row in matrix}) > 8:
        st.caption("Vue compacte : 8 champions × 12 adversaires les plus fréquents au maximum. Toutes les paires restent accessibles ci-dessous.")
    options = [(str(row["champion"]), str(row["opponent_champion"])) for row in matrix]
    selected = st.selectbox("Explorer un matchup", options, format_func=lambda pair: f"{pair[0]} vs {pair[1]}", key="contexts_matchup_detail")
    detail = matchup_detail(games, *selected)
    stat_strip([("Parties", str(detail["games"])), ("Winrate", decimal_label(detail["winrate"], 1, " %")), ("KDA", decimal_label(detail.get("kda"), 2)), ("Gold Diff @15", signed_label(detail.get("gold_diff_15")))])
    st.caption(f"Échantillon {detail['sample_size'].lower()} · {detail['timeline_games']} parties avec Timeline")
    with st.expander("Détail du matchup · early game, patches et parties"):
        stat_strip([("GD @10", signed_label(detail.get("gold_diff_10"))), ("CS Diff @15", decimal_label(detail.get("cs_diff_15"), 1)), ("XP Diff @15", signed_label(detail.get("xp_diff_15"), " XP")), ("Durée médiane", duration_label(detail.get("median_duration")))])
        st.dataframe([{"Patch": row["patch"], "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"), "GD @15": signed_label(row["gold_diff_15"])} for row in detail["patches"]], hide_index=True, width="stretch")
        st.dataframe([{"Match": row["match_id"], "Date": match_date_label(row.get("game_creation")), "Résultat": "WIN" if row.get("win") == 1 else "LOSS", "Côté": context_label(row.get("side")), "Patch": row.get("patch")} for row in detail["matches"]], hide_index=True, width="stretch")
    favorable, difficult = observed_extremes(matrix)
    with st.expander("Classements observés et matrice complète"):
        if favorable:
            left, right = st.columns(2)
            with left:
                section_header("Favorables observés")
                st.dataframe(_matchup_table(favorable), hide_index=True, width="stretch")
            with right:
                section_header("Difficiles observés")
                st.dataframe(_matchup_table(difficult), hide_index=True, width="stretch")
        else:
            st.caption("Aucune paire n’atteint encore le seuil de 5 parties pour un classement.")
        st.dataframe(_matchup_table(matrix), hide_index=True, width="stretch")


def _teammate_tab(games: pl.DataFrame, participants: list[dict[str, object]], puuid: str) -> None:
    from ui.history_navigation import cached_history_library, open_history
    from ui.player_actions import identity_recovery_panel, player_identity_dialog
    from ui.history_cards import champion_portrait
    from collections import Counter
    settings = get_active_settings()
    library = cached_history_library(str(settings.database_path), puuid, database_revision(settings.database_path))
    if flash := st.session_state.pop("identity_flash", None):
        st.success(flash)
    teammates = [row for row in teammate_summary(games, participants, puuid) if int(row["games"]) > 0]
    local_names = {identity.label: identity.local_label for identity in library.identities.values()}
    from analytics.privacy import stable_alias
    local_names.update({stable_alias(key, "teammate"): identity.local_label for key, identity in library.identities.items()})
    for mate in teammates:
        mate["display_name"] = local_names.get(mate["display_name"], mate["display_name"])
    recurring = [row for row in teammates if int(row["games"]) >= RECURRING_TEAMMATE_MIN_GAMES]
    squads = [row for row in squad_summary(games, participants, puuid) if int(row["games"]) >= RECURRING_TEAMMATE_MIN_GAMES]
    for squad in squads:
        squad["display_names"] = [local_names.get(name, name) for name in squad["display_names"]]
    st.caption(f"{len(recurring)} coéquipiers récurrents · {len(teammates)} personnes croisées dans la sélection · {len(squads)} combinaisons récurrentes")
    selected_ids = set(games['match_id'].to_list())
    champion_counts = {}
    for participant in participants:
        if participant.get('match_id') in selected_ids:
            champion_counts.setdefault(str(participant['puuid']), Counter()).update([str(participant.get('champion') or 'Inconnu')])
    for offset in range(0, min(len(recurring), 9), 3):
        for area, mate in zip(st.columns(3), recurring[offset:offset + 3]):
            identity = library.identities[str(mate['teammate_puuid'])]
            champions = champion_counts.get(identity.puuid, Counter())
            portraits = ''.join(champion_portrait(c, "small") for c, n in champions.most_common(3))
            with area, st.container(border=True, key=f"contexts_mate_card_{mate['teammate_alias']}"):
                st.markdown(f'<article class="la-mate-card"><div class="la-eyebrow">COÉQUIPIER RÉCURRENT</div><h3 title="{escape(identity.local_label)}">{escape(identity.local_label)}</h3>'
                    f'<div class="la-mate-portraits">{portraits}</div><div class="la-mate-stats"><strong>{mate["games"]}<small>parties ensemble</small></strong><strong>{decimal_label(mate["winrate"], 0)} %<small>victoires observées</small></strong></div>'
                    f'<small>Dernière rencontre · {match_date_label(mate["last_game"])}</small></article>', unsafe_allow_html=True)
                if st.button("Voir nos parties", key=f"contexts_shared_{mate['teammate_alias']}", width="stretch", icon=":material/history:"):
                    open_history(teammate=identity.puuid)
                if st.button("Ouvrir le profil" if identity.registered else "Analyser le profil", key=f"contexts_analyze_{mate['teammate_alias']}", width="stretch", type="tertiary"):
                    player_identity_dialog(identity, settings, puuid, shared_games=len(library.shared.get(("ally", identity.puuid), [])))
    st.caption("Récurrent = au moins 3 parties partagées. Une présence répétée ne permet pas d’affirmer que vous étiez en groupe prémade.")
    identity_recovery_panel(settings, puuid, library.identities)
    if recurring:
        chosen = st.selectbox("Retrouver un coéquipier", range(len(recurring)), format_func=lambda i: f"{recurring[i]['display_name']} · {recurring[i]['games']} parties", key="contexts_teammate")
        mate = recurring[chosen]
        stat_strip([("Parties ensemble", str(mate["games"])), ("Winrate ensemble", decimal_label(mate["winrate"], 1, " %")), ("Votre KDA", decimal_label(mate["kda"], 2)), ("Votre GD @15", signed_label(mate["gold_diff_15"]))])
        st.caption(f"{mate['sample_size']} · Première rencontre locale : {match_date_label(mate['first_game'])} · Dernière : {match_date_label(mate['last_game'])}")
        activity = games.filter(pl.col("recurring_teammates").list.contains(mate["teammate_alias"])).sort("game_creation")
        figure = go.Figure(go.Histogram(
            x=[datetime.fromtimestamp(int(value) / 1000).astimezone() for value in activity["game_creation"].drop_nulls()],
            xbins={"size": "M1"}, marker_color=TEAL,
            hovertemplate="%{x|%m/%Y}<br>%{y} parties partagées<extra></extra>",
        ))
        chart_style(figure, 240)
        figure.update_layout(bargap=.35, showlegend=False)
        figure.update_yaxes(title="Parties partagées", dtick=1, rangemode="tozero")
        figure.update_xaxes(title="Activité dans la sélection", tickformat="%m/%Y", dtick="M1", ticklabelmode="period")
        st.plotly_chart(figure, width="stretch", config=PLOT_CONFIG)
        with st.expander("Tous les coéquipiers récurrents"):
            st.dataframe([{"Coéquipier": row["display_name"], "N": row["games"], "Première": match_date_label(row["first_game"]), "Dernière": match_date_label(row["last_game"]), "WR": decimal_label(row["winrate"], 1, " %"), "KDA": decimal_label(row["kda"], 2), "GD @15": signed_label(row["gold_diff_15"]), "Équipe GD @15": signed_label(row["team_gold_diff_15"]), "Échantillon": row["sample_size"]} for row in recurring], hide_index=True, width="stretch")
    else:
        empty_state("Pas encore de coéquipier récurrent ici", "Élargissez l’historique ou choisissez « Tous » les champions pour retrouver les personnes croisées au moins trois fois.")
    comparison = solo_vs_known(games)
    section_header("Avec ou sans coéquipier récurrent", "La récurrence est identifiée sur l’historique local complet du profil.")
    st.plotly_chart(comparison_figure(comparison), width="stretch", config=PLOT_CONFIG)
    with st.expander("Comparer les métriques et explorer les groupes"):
        st.dataframe([{"Contexte": context_label(row["label"]), "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"), "KDA": decimal_label(row["kda"], 2), "GD @15": signed_label(row["gold_diff_15"]), "CS Diff @15": decimal_label(row["cs_diff_15"], 1), "DPM": decimal_label(row["damage_per_min"], 0)} for row in comparison], hide_index=True, width="stretch")
        section_header("Combinaisons récurrentes")
        st.caption("Les combinaisons affichées comptent au moins 3 parties dans la sélection. La taille compte les coéquipiers en plus de vous. Une partie peut appartenir à plusieurs combinaisons.")
        if squads:
            st.dataframe([{"Coéquipiers": " + ".join(row["display_names"]), "Taille": row["size"], "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"), "KDA": decimal_label(row["kda"], 2)} for row in squads], hide_index=True, width="stretch")
        else:
            st.caption("Aucune combinaison récurrente dans la sélection.")
        section_header("Associations de champions")
        pairings = [row for row in champion_pairings(games, participants, puuid) if int(row["games"]) >= 2]
        for pairing in pairings:
            pairing["display_name"] = local_names.get(pairing["display_name"], pairing["display_name"])
        if pairings:
            st.dataframe([{"Coéquipier": row["display_name"], "Champions": f"{row['player_champion']} + {row['teammate_champion']}", "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %")} for row in pairings], hide_index=True, width="stretch")
        else:
            st.caption("Aucune association observée au moins deux fois.")


def _session_tab(games: pl.DataFrame, full_history: pl.DataFrame | None = None) -> None:
    gap = st.segmented_control("Pause maximale entre deux parties", [30, 45, 60, 90], default=45, format_func=lambda value: f"{value} min", key="contexts_session_gap") or 45
    # Preserve chronology through short/excluded games, then select analytics.
    sessions = assign_sessions(full_history if full_history is not None else games, int(gap)).filter(pl.col("match_id").is_in(games["match_id"].to_list()))
    bundle = session_analytics(sessions)
    overview = bundle["overview"]
    stat_strip([("Sessions", str(overview["sessions"])), ("Parties / session", decimal_label(overview["average_games"], 2)), ("La plus longue", f"{overview['longest']} parties")])
    st.caption("Sessions définies sur l’historique complet, de la fin d’une partie au début de la suivante. Les filtres réduisent les indicateurs, jamais la chronologie.")
    sessions = sessions.drop_nulls("session_id")
    if sessions.is_empty():
        empty_state("Sessions indisponibles", "Les dates de ces parties sont absentes ou invalides.")
        return
    if overview["sessions"] == sessions.height:
        st.caption("Cette sélection ne contient que des sessions d’une partie.")
    actual = sessions.sort(["game_creation", "match_id"], descending=[True, True]).partition_by("session_id", maintain_order=True)
    chosen = st.selectbox("Revoir une session", range(len(actual)), format_func=lambda i: f"{match_date_label(actual[i]['game_creation'].min(), include_time=True)} · {actual[i].height} parties · {int(actual[i]['win'].sum())} victoires", key="contexts_session_selected")
    sequence = actual[chosen].sort("session_game_number")
    if st.button("Ouvrir Session Review →", key="contexts_review"):
        from ui.session_navigation import open_session_review
        open_session_review(str(sequence["match_id"][0]), int(gap))
    st.caption("Session Review ouvre la session complète du profil ; les filtres ci-dessus peuvent n’en montrer qu’une partie.")
    st.plotly_chart(session_sequence_figure(sequence.to_dicts()), width="stretch", config=PLOT_CONFIG)
    with st.expander("Détail des parties de cette session"):
        st.dataframe([{"Position": row["session_game_number"], "Date": match_date_label(row.get("game_creation"), include_time=True), "Champion": row.get("champion"), "Résultat": "WIN" if row.get("win") == 1 else "LOSS", "K/D/A": kda_line(row.get("kills"), row.get("deaths"), row.get("assists")), "Durée": duration_label(row.get("duration")), "Pause avant": decimal_label(row.get("minutes_since_previous_game"), 1, " min") if row["session_game_number"] > 1 else "Début", "Match": row["match_id"]} for row in sequence.to_dicts()], hide_index=True, width="stretch")
    section_header("Le rythme de vos sessions")
    options = {"Position de la partie": "by_game_number", "Après une victoire / défaite": "previous_result", "Longueur de session": "by_session_length", "Conserver / changer de champion": "champion_switch"}
    choice = st.selectbox("Comparer les sessions par", list(options), key="contexts_session_comparison")
    key = options[choice]
    rows = sorted(bundle[key], key=lambda row: str(row["label"]))
    if rows:
        st.plotly_chart(comparison_figure(rows, context=key), width="stretch", config=PLOT_CONFIG)
        with st.expander("Toutes les métriques de cette comparaison"):
            st.dataframe([{"Contexte": context_label(row["label"], key), "N": row["games"], "WR": decimal_label(row["winrate"], 1, " %"), "KDA": decimal_label(row["kda"], 2), "CS/min": decimal_label(row["cs_per_min"], 2), "DPM": decimal_label(row["damage_per_min"], 0), "GD @15": signed_label(row["gold_diff_15"]), "Morts <10": decimal_label(row["deaths_before_10"], 2)} for row in rows], hide_index=True, width="stretch")
    else:
        st.caption("Ce contexte nécessite plusieurs parties au sein d’une même session.")
    with st.expander("Séries observées au sein des sessions"):
        if bundle["streaks"]:
            st.dataframe([{"Série": row["label"], "Occurrences": row["occurrences"]} for row in bundle["streaks"]], hide_index=True, width="stretch")
        else:
            st.caption("Aucune série d’au moins deux résultats identiques dans la sélection.")
    st.caption("Les variations entre positions de session ne permettent pas d’en identifier la cause.")


def show_contexts() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Contexts", "Vos adversaires, vos coéquipiers et votre rythme de jeu : trois angles sur le même historique.")
    if not player:
        empty_state("Les contextes attendent vos premières parties", "Synchronisez ce profil depuis la barre latérale pour retrouver ses adversaires et ses sessions.")
        return
    puuid = str(player["puuid"])
    context = cached_context_dataset(str(settings.database_path), puuid, database_revision(settings.database_path))
    if context.is_empty():
        empty_state("Aucune partie locale pour ce profil", "Synchronisez des parties pour explorer les contextes.")
        return
    selected = _select_scope(context)
    if selected.is_empty():
        empty_state("Aucune partie ne correspond à ces filtres", "Choisissez un autre champion, élargissez l’historique ou réinitialisez la période.")
        return
    participants = database.participants_for_player_matches(puuid)
    requested_tab = st.session_state.pop("contexts_open_tab", None)
    matchup_tab, teammate_tab, session_tab = st.tabs(["Matchups", "Coéquipiers", "Sessions"], default=requested_tab)
    with matchup_tab:
        _matchup_tab(selected)
    with teammate_tab:
        _teammate_tab(selected, participants, puuid)
    with session_tab:
        _session_tab(selected, context)
    st.caption("Toutes ces statistiques décrivent cet historique. Elles n’établissent aucune causalité.")


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
