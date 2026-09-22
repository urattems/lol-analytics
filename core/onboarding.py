"""First-run validation and conservative local configuration persistence.

No dotenv loading into the process, database writes or network calls at import.
The GUI must explicitly confirm before save_configuration is called.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
import re
import tempfile

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import ValidationError

from config.settings import Settings
from core.exceptions import (
    RiotAPIError, RiotAuthenticationError, RiotNetworkError, RiotNotFoundError,
    RiotRateLimitError, RiotServerError,
)
from core.models import RiotAccount
from core.exceptions import ProxyConfigurationError
from core.diagnostics import safe_message
from core.profiles import parse_riot_id, routing_for_platform
from core.riot_api import RiotAPIClient


class SetupError(ValueError):
    """An actionable message safe to display without raw exceptions or tokens."""


@dataclass(frozen=True)
class SetupInput:
    game_name: str
    tag_line: str
    platform: str
    routing: str
    api_key: str = field(repr=False)

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


@dataclass(frozen=True)
class VerifiedAccount:
    settings: SetupInput
    account: RiotAccount
    has_history: bool


@dataclass(frozen=True)
class ConfigSnapshot:
    content: bytes | None = field(repr=False)
    values: dict[str, str | None] = field(repr=False)


def validate_input(riot_id: str, platform: str, api_key: str) -> SetupInput:
    name, tag = parse_riot_id(riot_id)
    platform = platform.strip().upper()
    routing = routing_for_platform(platform)
    key = api_key.strip()
    if not re.fullmatch(r"RGAPI-[A-Za-z0-9-]{16,128}", key):
        raise SetupError("Collez la clé API complète commençant par RGAPI-, sans guillemets. Ce n’est pas votre mot de passe Riot.")
    return SetupInput(name, tag, platform, routing, key)


def verify_account(
    settings: SetupInput, *, progress: Callable[[str], None] = lambda _: None,
    client_factory: Callable[..., RiotAPIClient] = RiotAPIClient,
) -> VerifiedAccount:
    """Validate account, actual LoL platform and Match V5 access, read-only."""
    phase = "account"
    try:
        # No automatic retry here: show a prompt immediately on throttling.
        with client_factory(settings.api_key, routing_region=settings.routing,
                            platform_region=settings.platform, max_retries=0) as api:
            progress("1/3 · Vérification de la clé et du Riot ID…")
            account = api.resolve_account(settings.game_name, settings.tag_line)
            parse_riot_id(f"{account.game_name}#{account.tag_line}")
            if not account.puuid.strip():
                raise RiotAPIError("Empty identity")
            phase = "platform"
            progress("2/3 · Compte trouvé. Vérification du serveur LoL…")
            api.get_summoner(account.puuid)
            phase = "history"
            progress("3/3 · Serveur confirmé. Vérification de l’accès aux parties…")
            history = api.get_match_ids(account.puuid, count=1)
            return VerifiedAccount(settings, account, bool(history))
    except ProxyConfigurationError as error:
        raise SetupError(safe_message(error)) from None
    except RiotAuthenticationError:
        raise SetupError("Riot refuse cette clé (401/403) : elle peut être expirée, invalide ou non autorisée. Régénérez-la sur le portail Riot, puis réessayez.") from None
    except RiotNotFoundError:
        messages = {
            "account": "Riot ID introuvable. Copiez le nom ET le tag depuis le client Riot (Nom#TAG).",
            "platform": "Compte trouvé, mais aucun profil LoL sur ce serveur. Vérifiez le serveur choisi et que ce compte a bien un profil League of Legends.",
            "history": "L’historique n’est pas accessible sur cette région. Vérifiez le serveur ou réessayez plus tard.",
        }
        raise SetupError(messages[phase]) from None
    except RiotRateLimitError:
        raise SetupError("Riot limite les requêtes (429). Patientez une à deux minutes avant de réessayer ; rien n’a été enregistré.") from None
    except RiotNetworkError:
        raise SetupError("Connexion à Riot impossible. Vérifiez Internet, le proxy ou le pare-feu, puis réessayez.") from None
    except RiotServerError:
        raise SetupError("Riot est temporairement indisponible. Réessayez plus tard ; votre configuration n’a pas changé.") from None
    except (RiotAPIError, ValidationError, ValueError):
        raise SetupError("Réponse Riot inexploitable. Rien n’a été enregistré ; réessayez plus tard.") from None


def read_configuration(root: Path) -> ConfigSnapshot:
    path = root / ".env"
    if path.is_symlink():
        raise SetupError("Le fichier .env est un lien symbolique. Configurez-le manuellement ; l’assistant ne le remplacera pas.")
    try:
        content = path.read_bytes() if path.exists() else None
        if content is not None and len(content) > 1_048_576:
            raise SetupError("Le fichier .env est trop volumineux pour l’assistant. Il est conservé.")
        source = content.decode("utf-8-sig") if content is not None else ""
        if any(binding.error for binding in parse_stream(StringIO(source))):
            raise SetupError("Le fichier .env contient une ligne invalide. Corrigez-le manuellement avant de relancer ; il est conservé.")
        return ConfigSnapshot(content, dict(dotenv_values(stream=StringIO(source), interpolate=False)))
    except (OSError, UnicodeError):
        raise SetupError("Impossible de lire .env. Vérifiez les droits du dossier et son encodage UTF-8 ; aucun fichier n’est remplacé.") from None


def needs_setup(root: Path) -> bool:
    snapshot = read_configuration(root)
    name, tag = snapshot.values.get("RIOT_GAME_NAME"), snapshot.values.get("RIOT_TAG_LINE")
    # An existing offline library does not require a currently valid API key.
    return not name or name == "YourGameName" or not tag


def render_configuration(snapshot: ConfigSnapshot, verified: VerifiedAccount) -> str:
    account, inputs = verified.account, verified.settings
    updates = {
        "RIOT_API_KEY": inputs.api_key, "RIOT_GAME_NAME": account.game_name,
        "RIOT_TAG_LINE": account.tag_line, "RIOT_PLATFORM_REGION": inputs.platform,
        "RIOT_ROUTING_REGION": inputs.routing, "LOL_ANALYTICS_DEMO": "0",
    }
    if not snapshot.values.get("LOL_ANALYTICS_DB_PATH"):
        updates["LOL_ANALYTICS_DB_PATH"] = "data/lol_analytics.db"
    source = (snapshot.content or b"").decode("utf-8-sig")
    # Parse full bindings: regex line replacement corrupts quoted multiline values.
    preserved = "".join(binding.original.string for binding in parse_stream(StringIO(source))
                        if binding.key not in updates)
    if preserved and not preserved.endswith("\n"):
        preserved += "\n"
    def quote(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return preserved + "".join(f"{key}={quote(value)}\n" for key, value in updates.items())


def save_configuration(root: Path, snapshot: ConfigSnapshot, verified: VerifiedAccount) -> Path | None:
    """Atomically replace .env, retaining the exact previous bytes in an ignored backup.

    Refuse changes since the GUI opened. No global transaction with editors is
    claimed; two cooperating assistants are serialized by an exclusive lock.
    """
    path, lock = root / ".env", root / ".env.setup.lock"
    temporary: Path | None = None
    backup: Path | None = None
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SetupError("Un autre assistant est ouvert. Fermez-le puis réessayez. Après un arrêt forcé, supprimez uniquement .env.setup.lock si aucun assistant ne tourne.") from None
    except OSError:
        raise SetupError("Ce dossier n’est pas accessible en écriture. Extrayez le projet dans Documents, puis relancez.") from None
    os.close(descriptor)
    try:
        if read_configuration(root).content != snapshot.content:
            raise SetupError("Le fichier .env a changé depuis l’ouverture. Fermez puis relancez l’assistant pour ne pas écraser ces modifications.")
        data = render_configuration(snapshot, verified).encode("utf-8")
        descriptor, name = tempfile.mkstemp(prefix=".env.setup-", dir=root)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if snapshot.content is not None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            descriptor, name = tempfile.mkstemp(prefix=f".env.backup-{stamp}-", dir=root)
            backup = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(snapshot.content)
                stream.flush()
                os.fsync(stream.fileno())
        if read_configuration(root).content != snapshot.content:
            raise SetupError("Le fichier .env a changé pendant l’enregistrement. Relancez l’assistant ; les modifications sont conservées.")
        os.replace(temporary, path)
        temporary = None
        return backup
    except OSError:
        raise SetupError("Enregistrement impossible (droits, antivirus ou disque). L’ancien .env reste en place. Fermez l’autre opération, puis réessayez.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def settings_for_import(root: Path, verified: VerifiedAccount) -> Settings:
    values = read_configuration(root).values
    path = Path(values.get("LOL_ANALYTICS_DB_PATH") or "data/lol_analytics.db").expanduser()
    if not path.is_absolute():
        path = root / path
    return Settings(riot_api_key=verified.settings.api_key,
                    riot_game_name=verified.account.game_name, riot_tag_line=verified.account.tag_line,
                    riot_platform_region=verified.settings.platform,
                    riot_routing_region=verified.settings.routing, database_path=path)
