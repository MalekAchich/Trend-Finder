import re

class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message


class AuthRequired(ProviderError):
    """Credentials missing, expired or rejected. Needs a human to log in again."""


class UsageLimited(ProviderError):
    """Subscription window exhausted. `reset_at` is a unix timestamp when known."""

    def __init__(self, provider: str, message: str, reset_at: float | None = None) -> None:
        super().__init__(provider, message)
        self.reset_at = reset_at


class TransientProviderError(ProviderError):
    """Network/5xx/timeout. Safe to retry."""


class InvalidRequest(ProviderError):
    """The provider rejected the request itself. Retrying won't help."""


class MalformedResponse(ProviderError):
    """The provider answered but the payload couldn't be parsed into the expected shape."""


class AllProvidersUnavailable(ProviderError):
    def __init__(self, role: str, earliest_reset: float | None) -> None:
        super().__init__("router", f"no provider available for role {role!r}")
        self.role = role
        self.earliest_reset = earliest_reset


class ContentRefused(ProviderError):
    """The provider's safety layer declined this message. The account is fine; the prompt needs fixing."""

    @property
    def request_id(self) -> str | None:
        m = re.search(r"Request ID: (req_[A-Za-z0-9]+)", self.message)
        return m.group(1) if m else None

    @property
    def detail(self) -> str | None:
        m = re.search(r"Details: `\[([a-z_]+)\]`", self.message)
        return m.group(1) if m else None


class ModelUnavailable(ProviderError):
    """This model can't be used on the current subscription (e.g. Claude answers "out of usage credits")."""
