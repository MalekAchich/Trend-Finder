"""Access logs never keep a secret that rides in a URL (an OAuth code or state, a token)."""
import logging
import re

SECRET_PARAMS = re.compile(r"\b(code|state|access_token|refresh_token|token|client_secret)=[^&\s\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    return SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=[redacted]", text)


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install() -> None:
    for name in ("uvicorn.access", "uvicorn.error", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(RedactSecrets())
