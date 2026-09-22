"""Explicit profile-owned Journal entry; browsing never writes an annotation."""
import streamlit as st


def open_journal(owner, match_id):
    st.session_state['journal_requested_match'] = {'owner': owner, 'match_id': match_id}
    st.session_state['journal_section'] = 'Notes & tags'
    st.query_params.clear()
    from pages.journal import show_journal
    st.switch_page(st.Page(show_journal, title='Journal', url_path='journal'))


def journal_button(owner, match_id, key):
    # Switch before rendering the outgoing page. Mid-render navigation can
    # leave duplicated tab blocks in the real Streamlit browser after return.
    st.button('Note & tags', key=key, icon=':material/edit_note:', type='tertiary',
              on_click=open_journal, args=(owner, match_id))
