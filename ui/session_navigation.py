"""Native navigation using profile-scoped match anchors, never stale ordinal IDs."""
from __future__ import annotations

import streamlit as st

from analytics.session_review import prepare_review_history, session_catalog, resolve_session
from ui.formatting import match_date_label
from ui.page_helpers import cached_context_dataset, database_revision


@st.cache_data(show_spinner=False, max_entries=12)
def cached_review_history(path: str, puuid: str, revision: int, gap: int = 45):
    return prepare_review_history(cached_context_dataset(path, puuid, revision), gap)


def session_label(session: dict) -> str:
    date = match_date_label(session["start_ms"], include_time=True)
    end = match_date_label(session["end_ms"], include_time=True)
    end = end[-5:] if end[:10] == date[:10] else end
    return f"{date} → {end} · {session['games']} partie{'s' if session['games'] != 1 else ''} · {session['wins']} V / {session['losses']} D"


def open_session_review(anchor_match_id: str | None = None, gap: int = 45) -> None:
    st.session_state["review_requested_match"] = anchor_match_id
    st.session_state["review_gap"] = gap
    st.query_params.clear()
    from pages.session_review import show_session_review
    st.switch_page(st.Page(show_session_review, title="Session Review", url_path="session-review"))


def open_review_timeline(match_id: str, puuid: str, anchor: str, gap: int) -> None:
    st.session_state.pop("history_timeline_context", None)
    st.session_state["review_timeline_context"] = {"owner": puuid, "anchor": anchor, "gap": gap, "match": match_id}
    st.session_state["timeline_requested_match"] = match_id
    st.query_params.clear()
    from pages.timeline import show_timeline
    st.switch_page(st.Page(show_timeline, title="Timeline", url_path="timeline"))


def timeline_session_navigation(database, puuid: str) -> str | None:
    context = st.session_state.get("review_timeline_context")
    if not context or context.get("owner") != puuid:
        st.session_state.pop("review_timeline_context", None)
        return None
    history = cached_review_history(str(database.path), puuid, database_revision(database.path), context["gap"])
    summary = resolve_session(session_catalog(history), context["anchor"])
    requested = st.session_state.get("timeline_requested_match") or st.session_state.get("timeline_selected_match") or context["match"]
    if not summary or requested not in summary["match_ids"] or context["anchor"] not in summary["match_ids"]:
        st.session_state.pop("review_timeline_context", None)
        return None
    index = summary["match_ids"].index(requested)
    st.caption(f"SESSION DU {match_date_label(summary['start_ms'])} · Partie {index + 1}/{summary['games']}")
    back, previous, following = st.columns([2, 1, 1])
    if back.button("← Retour à la session", key="review_back", width="stretch"):
        st.session_state["review_focus_match"] = requested
        open_session_review(context["anchor"], context["gap"])
    for column, label, offset, disabled, key in (
        (previous, "← Partie précédente", -1, index == 0, "review_previous"),
        (following, "Partie suivante →", 1, index == len(summary["match_ids"]) - 1, "review_next"),
    ):
        target = summary["match_ids"][index + offset] if not disabled else requested
        column.button(label, key=key, disabled=disabled, width="stretch", on_click=_select_timeline, args=(target,))
    return requested


def _select_timeline(match_id: str) -> None:
    st.session_state["timeline_requested_match"] = match_id
    st.session_state["review_timeline_context"]["match"] = match_id
