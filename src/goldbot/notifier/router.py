"""Decides who gets what.

- send_drop_alert: admin + family. The ONLY path that reaches family members.
- send_admin: admin only. Errors, warnings and system notes always use this.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Mapping

from ..config import Contact
from .senders import Sender

log = logging.getLogger(__name__)


class Router:
    def __init__(
        self,
        admin: Contact,
        family: list[Contact],
        sender: Sender | dict[str, Sender],
        retry_waits: list[int] | None = None,
        gap_seconds: float = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._admin = admin
        self._family = list(family)
        self._sender = sender
        self._retry_waits = list(retry_waits or [5, 20, 60])
        self._gap = gap_seconds
        self._sleep = sleep

    @property
    def recipient_count(self) -> int:
        """How many people get a drop alert (admin + family)."""
        return 1 + len(self._family)

    def _sender_for(self, contact: Contact) -> Sender:
        """A single sender (dry run, tests) or one sender per channel (real runs)."""
        if isinstance(self._sender, dict):
            return self._sender[contact.channel]
        return self._sender

    def _deliver(self, contact: Contact, text: str) -> bool:
        sender = self._sender_for(contact)
        for attempt in range(len(self._retry_waits) + 1):
            if sender.send(contact, text):
                return True
            if attempt < len(self._retry_waits):
                self._sleep(self._retry_waits[attempt])
        return False

    @staticmethod
    def _text_for(contact: Contact, text: str | Mapping[str, str]) -> str:
        """One text for everyone, or {"en": ..., "te": ...} picked by each person's language."""
        if isinstance(text, str):
            return text
        return text.get(contact.language) or text["en"]

    def send_drop_alert(self, text: str | Mapping[str, str]) -> list[str]:
        """Send the price-drop alert to the admin first, then each family member.
        Returns the names of people who could not be reached."""
        missed: list[str] = []
        for i, contact in enumerate([self._admin, *self._family]):
            if i:
                self._sleep(self._gap)
            hook = getattr(self._sender_for(contact), "before_alert", None)
            if hook is not None:  # e.g. a Telegram sticker; a failure here never blocks the alert
                try:
                    hook(contact)
                except Exception:
                    log.warning("pre-alert extra failed for %s", contact.name)
            if not self._deliver(contact, self._text_for(contact, text)):
                missed.append(contact.name)
        return missed

    def send_hint(self, text: str | Mapping[str, str], include_family: bool) -> list[str]:
        """Morning early hint: admin, plus family if enabled. No sticker. Returns names not reached."""
        people = [self._admin, *self._family] if include_family else [self._admin]
        missed: list[str] = []
        for i, contact in enumerate(people):
            if i:
                self._sleep(self._gap)
            if not self._deliver(contact, self._text_for(contact, text)):
                missed.append(contact.name)
        return missed

    def send_admin(self, text: str) -> bool:
        """Admin-only note. Family contacts are never touched here."""
        ok = self._deliver(self._admin, text)
        if not ok:
            log.error("could not deliver admin note")
        return ok
