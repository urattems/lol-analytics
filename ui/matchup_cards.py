"""Visual matchup view models with honest metric-specific sample sizes."""
from html import escape

import streamlit as st

from analytics.matchup_cards import MatchupCard, matchup_cards, sample_label
from ui.formatting import decimal_label, signed_label
from ui.history_cards import champion_portrait


def render_matchup_cards(cards: list[MatchupCard]):
    for offset in range(0, min(12, len(cards)), 3):
        for area, card in zip(st.columns(3), cards[offset:offset + 3]):
            value = decimal_label(card.value, 0, " %") if card.metric == "winrate" else signed_label(card.value) if card.metric == "gold_diff_15" else decimal_label(card.value, 1, " CS")
            label = {"winrate": "Winrate observé", "gold_diff_15": "Gold diff @15", "cs_diff_15": "CS diff @15"}[card.metric]
            with area:
                st.markdown(
                    '<article class="la-matchup-card">'
                    f'<header><div><small>{escape(card.champion)} face à</small><h3>{escape(card.opponent)}</h3></div>'
                    f'<div class="la-matchup-versus">{champion_portrait(card.champion, "small")}<span>VS</span>{champion_portrait(card.opponent, "opponent")}</div></header>'
                    f'<div class="la-matchup-value">{value}</div><small>{label} · N observé={card.observed}</small>'
                    f'<footer><b>{card.games} parties</b><span class="la-badge" title="Effectif descriptif, pas une preuve de matchup favorable ou défavorable">{card.sample}</span></footer></article>', unsafe_allow_html=True)
    if len(cards) > 12:
        st.caption("Les 12 premières paires selon le tri. Toutes restent accessibles dans le sélecteur et la matrice.")
