"""One explicit social workflow reused by rosters and teammate cards."""
from html import escape
import logging
import streamlit as st

from analytics.exports import markdown_text
from core.identities import PlayerIdentity, hydrate_identities
from core.import_lock import import_is_running
from core.profiles import PLATFORM_ROUTING_REGIONS, import_profile
from ui.formatting import match_date_label
from ui.profiles import activate_profile

LOGGER = logging.getLogger(__name__)


def player_identity_dialog(identity: PlayerIdentity, settings, owner: str, *, shared_games: int = 0, opponent=False):
    """Keep the dialog request across full reruns as well as fragment reruns."""
    st.session_state["identity_dialog_request"] = (identity, owner, shared_games, opponent)
    st.rerun()


def _dismiss_identity_dialog():
    st.session_state.pop("identity_dialog_request", None)


def render_player_dialog(settings, owner):
    request = st.session_state.get("identity_dialog_request")
    if request and request[1] == owner:
        _render_identity_dialog(request[0], settings, owner, shared_games=request[2], opponent=request[3])
    elif request:
        _dismiss_identity_dialog()


@st.dialog("Le joueur derrière le champion", width="medium", on_dismiss=_dismiss_identity_dialog)
def _render_identity_dialog(identity: PlayerIdentity, settings, owner: str, *, shared_games=0, opponent=False):
    st.markdown(f'<div class="la-player-dialog"><div class="la-eyebrow">IDENTITÉ LOCALE</div><h2>{escape(identity.local_label)}</h2></div>', unsafe_allow_html=True)
    st.caption(f"{shared_games} rencontre(s) locale(s) dans ce camp · aucun historique distant chargé à l’ouverture.")
    if identity.last_seen:
        st.caption("Dernier nom connu : " + match_date_label(identity.last_seen))
    if identity.previous_names:
        st.caption("Ancien nom observé : " + markdown_text(identity.previous_names[0]))
    if st.button("Voir nos parties", key="identity_shared", disabled=not shared_games, icon=":material/history:"):
        from ui.history_navigation import open_history
        _dismiss_identity_dialog()
        open_history(opponent=identity.puuid) if opponent else open_history(teammate=identity.puuid)
    if identity.registered:
        if st.button("Ouvrir le profil", key="identity_open_profile", type="primary"):
            activate_profile(identity.puuid)
            from ui.history_navigation import open_history
            open_history(reset=True)
        return
    if not identity.riot_id:
        st.info("Riot ID indisponible dans les détails locaux. Utilisez « Retrouver les Riot IDs » dans l’Historique ou Coéquipiers. Les anciennes réponses Riot peuvent ne pas contenir ces champs.")
        return
    st.divider()
    st.markdown("**Analyser son historique récent**")
    left, right = st.columns(2)
    platform = left.selectbox("Serveur du joueur", list(PLATFORM_ROUTING_REGIONS),
                              index=list(PLATFORM_ROUTING_REGIONS).index(settings.riot_platform_region), key="identity_platform")
    count = right.selectbox("Parties à importer", [20, 50], key="identity_count")
    st.caption("Le serveur est indépendant du tag Riot. L’import réutilise les matchs partagés, sans Timeline ni backfill automatique.")
    if st.button("Importer et analyser", key="identity_import", type="primary", disabled=settings.demo_mode or not settings.riot_api_key or import_is_running(settings.database_path)):
        from ui.library import _riot_error_message
        try:
            with st.status("Import du profil…", expanded=True) as status:
                bar = st.progress(0)
                def progress(value):
                    total = max(1, value.total or value.current or 1)
                    bar.progress(min(1.0, value.current / total))
                result = import_profile(settings, identity.riot_id, platform, count, progress,
                                        expected_puuid=identity.puuid)
                status.update(label="Profil prêt", state="complete")
        except Exception as exc:
            LOGGER.exception("Explicit social profile import failed")
            st.error(str(exc) if isinstance(exc, ValueError) else _riot_error_message(exc))
        else:
            st.cache_data.clear()
            activate_profile(result.account.puuid)
            from ui.history_navigation import open_history
            open_history(reset=True)
    if settings.demo_mode or not settings.riot_api_key:
        st.caption("Import désactivé en démo ou sans clé Riot. Les parties locales restent consultables.")


def identity_recovery_panel(settings, owner: str, identities: dict[str, PlayerIdentity], *, compact=False):
    missing = sum(not i.riot_id for key, i in identities.items() if key != owner)
    if not missing:
        if not compact:
            st.caption("Les Riot IDs rencontrés sont connus localement. Ils restent anonymisés dans vos exports.")
        return
    panel = st.popover(f"Retrouver les Riot IDs · {missing} inconnus", icon=":material/person_search:") if compact else st.expander(f"Retrouver les Riot IDs · {missing} identités indisponibles")
    with panel:
        st.caption("Action explicite : relire uniquement les détails de parties locales nécessaires. Aucun match, statistique ou événement Timeline n’est ajouté ou remplacé.")
        retry = st.checkbox("Réessayer aussi les réponses sans noms", key="identity_retry")
        if st.button("Récupérer les Riot IDs manquants", key="identity_hydrate", disabled=settings.demo_mode or not settings.riot_api_key or import_is_running(settings.database_path)):
            from ui.library import _riot_error_message
            try:
                with st.status("Récupération des identités…", expanded=True) as status:
                    bar, detail = st.progress(0), st.empty()
                    def progress(value):
                        bar.progress(min(1.0, (value.checked + value.skipped) / max(1, value.total)))
                        detail.caption(f"{value.checked + value.skipped}/{value.total} détails traités · {value.recovered} noms retrouvés · {value.already_known} déjà connus · {value.remaining} encore indisponibles")
                    result = hydrate_identities(settings, owner, progress, retry_unavailable=retry)
                    status.update(label="Récupération terminée", state="complete")
            except Exception as exc:
                LOGGER.exception("Explicit identity hydration failed")
                st.cache_data.clear()
                st.error(_riot_error_message(exc))
                st.caption("Les détails déjà validés sont conservés. Relancez l’action pour reprendre.")
            else:
                st.session_state["identity_flash"] = f"{result.recovered} identité(s) retrouvée(s) ; {result.remaining} indisponible(s). Les statistiques sont inchangées."
                st.cache_data.clear()
                st.rerun()
        if not settings.riot_api_key or settings.demo_mode:
            st.caption("Une clé Riot valide, hors démo, est nécessaire pour cette action.")
