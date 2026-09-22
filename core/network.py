"""Explicit shared proxy policy: honor HTTPX environment settings, never bypass."""
import httpx

from core.exceptions import ProxyConfigurationError


def environment_client(**kwargs) -> httpx.Client:
    # HTTPX owns HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY and SSL_CERT_* handling.
    # Injected test transports remain local and bypass environment proxy mounts.
    try:
        return httpx.Client(trust_env=True, **kwargs)
    except (ImportError, ValueError, OSError):
        # Constructor errors can contain proxy credentials or local certificate paths.
        raise ProxyConfigurationError('Proxy ou certificats réseau non utilisables.') from None
