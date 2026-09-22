"""Explicit exceptions raised by the Riot integration."""


class RiotError(Exception):
    """Base class for Riot-related failures."""


class RiotConfigurationError(RiotError):
    """Raised when required Riot configuration is missing."""


class RiotAPIError(RiotError):
    """Raised for an unexpected Riot API response."""


class ProxyConfigurationError(RiotConfigurationError):
    """Environment proxy/certificate configuration cannot be used safely."""


class RiotAuthenticationError(RiotAPIError):
    """Raised for invalid, missing, or unauthorized credentials."""


class RiotNotFoundError(RiotAPIError):
    """Raised when a Riot resource cannot be found."""


class RiotRateLimitError(RiotAPIError):
    """Raised when retries cannot recover from rate limiting."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class RiotServerError(RiotAPIError):
    """Raised when Riot remains unavailable after retries."""


class RiotNetworkError(RiotAPIError):
    """Raised when network retries are exhausted."""
