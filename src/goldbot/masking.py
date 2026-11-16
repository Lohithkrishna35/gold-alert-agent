"""Logging helpers that never print phone numbers or API keys."""

from __future__ import annotations

import logging
import re

# International numbers (+91...) or a bare 10-13 digit run; dates and times are left alone
_PHONE = re.compile(r"\+\d[\d\s-]{8,}\d|(?<![\d.:-])\d{10,13}(?![\d.:-])")
_SECRET_VALUES: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret so it is scrubbed from every log line."""
    if value and len(value) >= 4:
        _SECRET_VALUES.add(value)


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "****"
    return "+" + "*" * max(len(digits) - 4, 2) + digits[-4:]


def scrub(text: str) -> str:
    for secret in _SECRET_VALUES:
        text = text.replace(secret, "[secret]")
    return _PHONE.sub(lambda m: mask_phone(m.group(0)), text)


class ScrubbingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Third-party HTTP libraries can log full URLs (which include the CallMeBot key); keep them quiet.
    for noisy in ("urllib3", "httpx", "httpcore", "httpx2", "httpcore2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
