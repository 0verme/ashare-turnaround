"""Redaction helpers shared by provider errors and local state."""

from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s'\"<>\]\[)]+", re.IGNORECASE)
_AUTHORIZATION = re.compile(
    r"(?i)\b(?:proxy-)?authorization\s*[:=]\s*(?:bearer\s+)?\S+"
)


def redact_text(value: object, secret: str | None = None) -> str:
    """Return text safe for logs and persisted operational state.

    URLs and authorization values are not useful in a run log and can contain
    credentials or private proxy endpoints, so they are removed in addition to
    the configured token.
    """

    text = str(value)
    if secret:
        text = text.replace(secret, "<redacted>")
    text = _AUTHORIZATION.sub("<redacted-authorization>", text)
    return _URL.sub("<redacted-url>", text)
