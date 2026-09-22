"""Presentation helpers for optional Data Dragon names and icons."""

from __future__ import annotations

from html import escape
from collections.abc import Iterable

import streamlit as st

from core.static_data import DataDragonService


def item_names(item_ids: Iterable[int], static_data: DataDragonService) -> str:
    """Return localized names with reliable ID fallbacks."""

    return " · ".join(static_data.item(item_id).display_name for item_id in item_ids)


def render_item_strip(
    item_ids: Iterable[int], static_data: DataDragonService, *,
    icons_only: bool = False, ordered: bool = False,
) -> None:
    """Render compact item icons; text fallbacks remain visible offline."""

    blocks: list[str] = []
    for item_id in item_ids:
        item = static_data.item(item_id)
        label = escape(item.display_name)
        if item.image_url:
            blocks.append(
                f'<span class="la-item{" la-item-icon" if icons_only else ""}" title="{label}">'
                f'<img src="{escape(item.image_url)}" alt="{label}">'
                f'<span>{label}</span></span>'
            )
        else:
            blocks.append(f'<span class="la-item la-item-fallback">{label}</span>')
    separator = '<span class="la-muted" aria-hidden="true">→</span>' if ordered else ""
    st.markdown(
        f'<div class="la-item-strip">{separator.join(blocks) or "Inventaire vide"}</div>',
        unsafe_allow_html=True,
    )
