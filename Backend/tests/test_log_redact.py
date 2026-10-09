"""Secrets that ride in URLs (OAuth code and state, tokens) never reach the access log."""
import logging

from tf_backend.log_redact import RedactSecrets, redact


def test_oauth_codes_and_tokens_are_blanked_in_logged_urls():
    url = "/api/socials/tiktok/callback?code=made-up-code_123%2Av&scopes=video.list&state=made-up-state"
    assert redact(url) == "/api/socials/tiktok/callback?code=[redacted]&scopes=video.list&state=[redacted]"
    assert redact("/x?access_token=IGAA-made-up") == "/x?access_token=[redacted]"
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s"', ("127.0.0.1", "GET", url), None)
    RedactSecrets().filter(record)
    assert "made-up-code" not in record.getMessage() and "made-up-state" not in record.getMessage()
