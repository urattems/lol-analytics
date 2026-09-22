"""Escaped match-card rendering and native, keyboard-accessible actions."""
from datetime import datetime
from html import escape

import streamlit as st

from analytics.exports import markdown_text
from analytics.history import HistoryMatch, HistoryItem
from core.queues import queue_name
from core.static_data import get_static_data_service
from ui.components import stat_strip
from ui.formatting import context_label, decimal_label, duration_label, kda_line, match_date_label, signed_label
from ui.history_navigation import open_history_timeline
from ui.session_navigation import open_session_review
from ui.player_actions import player_identity_dialog


def champion_portrait(champion: str | None, size="normal") -> str:
    asset = get_static_data_service().champion(champion or "Inconnu")
    label = escape(asset.display_name)
    content = f'<img src="{escape(asset.image_url)}" alt="{label}" loading="lazy">' if asset.image_url else f'<span>{label[:3]}</span>'
    return f'<span class="la-portrait la-portrait-{size}" title="{label}">{content}</span>'


def item_icons(items: tuple[HistoryItem, ...]) -> str:
    values = []
    for item in items:
        asset = get_static_data_service().item(item.item_id) if item.item_id else None
        label = asset.display_name if asset else "Emplacement vide" if item.item_id == 0 else "Objet indisponible"
        content = f'<img src="{escape(asset.image_url)}" alt="{escape(label)}" loading="lazy">' if asset and asset.image_url else f'<span>{item.item_id if item.item_id else "·" if item.item_id == 0 else "?"}</span>'
        values.append(f'<span class="la-inventory-slot{" la-trinket" if item.trinket else ""}" title="{escape(label)}{" · Trinket" if item.trinket else ""}">{content}</span>')
    return '<div class="la-inventory">' + ''.join(values) + '</div>'


def relative_date(timestamp) -> str:
    exact = match_date_label(timestamp)
    if exact == "N/A":
        return "Date indisponible"
    day = datetime.fromtimestamp(timestamp / 1000).astimezone().date()
    delta = (datetime.now().astimezone().date() - day).days
    return "Aujourd’hui" if delta == 0 else "Hier" if delta == 1 else exact


def card_html(card: HistoryMatch) -> str:
    row = card.facts
    result, tone = ("WIN", "win") if row.get("win") == 1 else ("LOSS", "loss") if row.get("win") == 0 else ("N/A", "muted")
    badge = '<span class="la-badge la-badge-gold">Partie courte &lt;5 min</span>' if card.short else ''
    gd = signed_label(row.get("gold_diff_15")) if card.context.timeline_available else "N/A"
    return (
        f'<article class="la-history-row la-history-{tone}">'
        f'<div class="la-history-result"><b class="la-{tone}">{result}</b><small title="{escape(match_date_label(row.get("game_creation"), True))}">{escape(relative_date(row.get("game_creation")))}</small><small>{escape(queue_name(row.get("queue_id")))}</small>{badge}</div>'
        f'<div class="la-history-champion">{champion_portrait(row.get("champion"))}<div><b>{escape(str(row.get("champion") or "Inconnu"))}</b><small>{escape(context_label(row.get("role")))}</small></div></div>'
        f'<div class="la-history-kda"><strong>{escape(kda_line(row.get("kills"), row.get("deaths"), row.get("assists")))}</strong><small>{decimal_label(row.get("review_kda"), 2)} KDA · {duration_label(row.get("duration"))}</small></div>'
        f'<div class="la-history-build">{item_icons(card.items)}<small>{decimal_label(row.get("cs_total"), 0)} CS · {decimal_label(row.get("review_cs"), 1)} / min</small></div>'
        f'<div class="la-history-encounter"><small>GD @15 <b>{gd}</b></small></div></article>'
    )


def _expand(match_id):
    st.session_state["history_expanded_match"] = None if st.session_state.get("history_expanded_match") == match_id else match_id


def _render_roster(card, library, settings, expanded):
    own = next((p for p in card.players if p.own), None)
    sides = (own.side if own else None, "red" if own and own.side == "blue" else "blue")
    with st.container(key=f"history_roster_{card.match_id}", gap=None):
        for column, side, title in zip(st.columns(2, gap="small", wrap=False), sides, ("Votre équipe", "Équipe adverse")):
            with column, st.container(gap=None):
                st.markdown(f'<div class="la-roster-heading">{title}</div>', unsafe_allow_html=True)
                players = [p for p in card.players if p.side == side] if side in ("blue", "red") else []
                if len(players) != 5:
                    st.caption(f"Composition partielle : {len(players)} participants connus.")
                for index, player in enumerate(players):
                    label = player.identity.local_label
                    score = kda_line(player.kills, player.deaths, player.assists)
                    detail = f"{label} · {player.champion or 'Champion inconnu'} · {score}"
                    if not player.identity.riot_id:
                        detail += " · Riot ID absent des données locales ; récupération disponible dans l’Historique."
                    if player.own:
                        st.markdown(f'<div class="la-roster-own" title="{escape(detail)}">{champion_portrait(player.champion, "mini")}<span><b>Vous</b> · {escape(label)}</span></div>', unsafe_allow_html=True)
                    else:
                        asset = get_static_data_service().champion(player.champion or "Inconnu")
                        portrait = f"![{markdown_text(asset.display_name)}]({asset.image_url}) " if asset.image_url else ""
                        if st.button(portrait + markdown_text(label), key=f"history_player_{card.match_id}_{side}_{index}",
                                     type="tertiary", help=markdown_text(detail), width="stretch", wrap=False):
                            relationship = "ally" if side == sides[0] else "enemy"
                            player_identity_dialog(player.identity, settings, library.owner,
                                shared_games=len(library.shared.get((relationship, player.identity.puuid), [])), opponent=relationship == "enemy")
                    if expanded:
                        st.markdown(f'<div class="la-roster-score">{escape(score)} · {escape(context_label(player.role))}</div>', unsafe_allow_html=True)


def render_history_card(card: HistoryMatch, library, settings, rank_context=None):
    row = card.facts
    expanded = st.session_state.get("history_expanded_match") == card.match_id
    with st.container(key=f"history_card_{card.match_id}", border=True):
        facts, roster = st.columns([1, 1.3], gap="medium", vertical_alignment="center")
        with facts:
            st.markdown(card_html(card), unsafe_allow_html=True)
            if not expanded:
                from ui.ranks import render_rank_summary
                render_rank_summary(rank_context)
        with roster:
            _render_roster(card, library, settings, expanded)
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.button("Masquer" if expanded else "Équipes & détails", icon=":material/groups:",
                      key=f"history_expand_{card.match_id}", on_click=_expand, args=(card.match_id,), type="tertiary")
            if st.button("Timeline", icon=":material/timeline:", key=f"history_timeline_{card.match_id}",
                         help="Ouvrir la Timeline de cette partie" if card.context.timeline_available else "Timeline non enrichie : ouvrir l’état de disponibilité", type="tertiary"):
                open_history_timeline(card.match_id, library.owner)
            if st.button("Session", icon=":material/view_carousel:", key=f"history_session_{card.match_id}",
                         disabled=not card.context.session_anchor, type="tertiary"):
                open_session_review(card.context.session_anchor)
            from ui.journal_navigation import journal_button
            journal_button(library.owner, card.match_id, f'history_note_{card.match_id}')
            st.caption(f"{'Timeline disponible' if card.context.timeline_available else 'Timeline absente'} · {f'Partie {card.context.session_number} de la session' if card.context.session_number else 'Session indisponible'}")
        if not expanded:
            return
        st.divider()
        from ui.ranks import render_rank_panel
        render_rank_panel(rank_context, settings, library.owner)
        stat_strip([("Gold / min", decimal_label(row.get("review_gold"), 0)), ("Dégâts / min", decimal_label(row.get("review_damage"), 0)),
                    ("Vision", decimal_label(row.get("vision_score"), 0)), ("GD @10", signed_label(row.get("gold_diff_10")) if card.context.timeline_available else "N/A"),
                    ("GD @15", signed_label(row.get("gold_diff_15")) if card.context.timeline_available else "N/A")])
        st.caption(" · ".join(f"Équipe GD @{minute} : {signed_label(row.get(f'team_gold_diff_{minute}')) if card.context.timeline_available else 'N/A'}" for minute in (10, 15, 20)))
        st.caption(" · ".join(f"{label} : {context_label(row.get(key)) if card.context.timeline_available else 'N/A'}" for key, label in
                             (("first_dragon", "Premier dragon"), ("first_herald", "Premier Héraut"), ("first_tower", "Première tour"), ("trajectory", "Trajectoire"))))
        st.caption("Inventaire final : les emplacements ne décrivent pas l’ordre d’achat. La Timeline donne la chronologie réelle des objets.")
        if card.context.timeline_available:
            from ui.history_navigation import cached_history_item_events
            from ui.page_helpers import database_revision
            from analytics.timeline import event_item_id
            events = cached_history_item_events(str(settings.database_path), library.owner, card.match_id, database_revision(settings.database_path))
            if events:
                tiles = []
                for event in events[:12]:
                    identifier = event_item_id(event)
                    asset = get_static_data_service().item(identifier) if identifier else None
                    label = {"ITEM_PURCHASED": "Achat", "ITEM_SOLD": "Vente", "ITEM_UNDO": "Annulation"}[event['event_type']]
                    icon = f'<img src="{escape(asset.image_url)}" alt="">' if asset and asset.image_url else ''
                    name = asset.display_name if asset else "Objet indisponible"
                    tiles.append(f'<div class="la-purchase">{icon}<div><b>{duration_label(event["timestamp_ms"] // 1000)} · {label}</b><span>{escape(name)}</span></div></div>')
                st.markdown('<div class="la-purchase-grid">' + ''.join(tiles) + '</div>', unsafe_allow_html=True)
                st.caption(f"{min(12, len(events))}/{len(events)} opérations d’objet, dans leur ordre enregistré. La Timeline conserve le détail complet.")
            else:
                st.caption("Aucune opération d’objet datée disponible pour ce joueur.")
