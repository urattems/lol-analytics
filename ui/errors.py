"""Rendering boundaries with safe default diagnostics, opt-in developer detail."""
from contextlib import contextmanager
import logging
import os
import sqlite3

import streamlit as st
from core.diagnostics import record_failure, export_diagnostic

LOGGER = logging.getLogger(__name__)


def developer_errors() -> bool:
    return os.getenv("LOL_ANALYTICS_DEV_ERRORS", "").strip().lower() in {"1", "true", "yes"}


def configure_error_display() -> None:
    st.set_option("client.showErrorDetails", "full" if developer_errors() else "none")


def show_diagnostic(error: Exception, *, phase: str) -> None:
    payload = record_failure(error, phase=phase)
    with st.expander('Diagnostic expurgé'):
        st.code(export_diagnostic(payload), language='json')
        st.caption('Copiable sans clé, identité ou chemin local. Intégrité SQLite non vérifiée ici.')


@contextmanager
def chart_boundary(key: str):
    """Keep dataset loading/invariants outside this rendering-only boundary."""
    try:
        yield
    except (sqlite3.DatabaseError, AssertionError):
        raise
    except Exception as exc:
        record_failure(exc, phase='chart')
        st.warning("Impossible d’afficher ce graphique. Les autres statistiques restent disponibles.")
        if developer_errors():
            LOGGER.exception("Chart rendering failed: %s", key)
            st.exception(exc)
        if st.button("Réessayer le graphique", key="chart_retry_" + key, type="tertiary"):
            st.rerun()
