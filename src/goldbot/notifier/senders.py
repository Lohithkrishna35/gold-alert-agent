"""Low-level senders. A sender delivers one text to one contact; routing lives in router.py.

Channels:
- callmebot: free personal WhatsApp gateway (phone + apikey)
- whatabot:  free personal WhatsApp gateway (phone + apikey)
- telegram:  official Telegram Bot API (bot token + chat_id), most reliable
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import requests

from ..config import Contact, ConfigError, Secrets
from ..masking import mask_phone

log = logging.getLogger(__name__)

# WhatsApp gateways answer HTTP 200 with a short page. Only clear refusals count as failure, because a
# false "failed" would trigger a retry and the family would get the same alert twice.
_FAILURE_HINTS = (
    "apikey is invalid",
    "invalid apikey",
    "not activated",
    "not allowed",
    "you need to get the apikey",
    "wrong apikey",
)


class Sender(Protocol):
    def send(self, contact: Contact, text: str) -> bool: ...


class _WhatsAppGatewaySender:
    name = "whatsapp"

    def __init__(self, url: str, timeout: int = 30):
        self.url = url
        self.timeout = timeout

    def _params(self, contact: Contact, text: str) -> dict[str, str]:
        return {"phone": contact.phone, "text": text, "apikey": contact.apikey}

    def send(self, contact: Contact, text: str) -> bool:
        try:
            resp = requests.get(self.url, params=self._params(contact, text), timeout=self.timeout)
        except requests.RequestException as e:
            # never log the exception text: it can contain the full URL with the key
            log.warning("%s send to %s failed: %s", self.name, contact.name, type(e).__name__)
            return False
        body = resp.text.lower()
        ok = resp.status_code == 200 and not any(h in body for h in _FAILURE_HINTS)
        if ok:
            log.info("%s: sent to %s (HTTP 200)", self.name, contact.name)
        else:
            log.warning("%s: send to %s rejected (HTTP %s)", self.name, contact.name, resp.status_code)
        return ok


class CallMeBotSender(_WhatsAppGatewaySender):
    name = "callmebot"


class WhatabotSender(_WhatsAppGatewaySender):
    name = "whatabot"


class TelegramSender:
    name = "telegram"

    def __init__(self, token: str, api_base: str = "https://api.telegram.org", timeout: int = 30,
                 alert_sticker: str = ""):
        self._base = f"{api_base.rstrip('/')}/bot{token}"
        self._url = f"{self._base}/sendMessage"
        self.timeout = timeout
        self.alert_sticker = (alert_sticker or "").strip()

    def before_alert(self, contact: Contact) -> None:
        """Sends the optional sticker just before a drop alert. Never used for admin notes."""
        if not self.alert_sticker:
            return
        resp = self._post({"chat_id": contact.chat_id, "sticker": self.alert_sticker}, method="sendSticker")
        if resp is None or resp.status_code != 200:
            log.warning("telegram: sticker not sent to %s (alert still goes out)", contact.name)

    def _post(self, payload: dict[str, Any], method: str = "sendMessage") -> requests.Response | None:
        try:
            return requests.post(f"{self._base}/{method}", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("telegram send failed: %s", type(e).__name__)  # URL holds the token: never log it
            return None

    def send(self, contact: Contact, text: str) -> bool:
        # Telegram's legacy Markdown uses *bold* like WhatsApp; if the text trips the parser, send it plain
        # In Telegram's Markdown, [ and _ have meaning; escape them so "[Gold bot - admin]" shows as written
        md_text = text.replace("_", "\\_").replace("[", "\\[")
        resp = self._post({"chat_id": contact.chat_id, "text": md_text, "parse_mode": "Markdown"})
        if resp is not None and resp.status_code == 400:
            resp = self._post({"chat_id": contact.chat_id, "text": text})
        ok = resp is not None and resp.status_code == 200 and bool(_json(resp).get("ok"))
        if ok:
            log.info("telegram: sent to %s", contact.name)
        else:
            code = resp.status_code if resp is not None else "no response"
            log.warning("telegram: send to %s rejected (%s)", contact.name, code)
        return ok


def _json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


class ConsoleSender:
    """Dry-run sender: prints instead of sending. Keeps a list for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[Contact, str]] = []

    def send(self, contact: Contact, text: str) -> bool:
        self.sent.append((contact, text))
        print(
            f"\n----- [DRY RUN] {contact.channel} to {contact.name} ({mask_phone(contact.address)}) -----\n{text}\n"
        )
        return True


def build_senders(notifier_cfg: dict[str, Any], secrets: Secrets) -> dict[str, Sender]:
    """One sender per channel that is actually used by a contact."""
    everyone = ([secrets.admin] if secrets.admin else []) + secrets.family
    used = {c.channel for c in everyone}
    senders: dict[str, Sender] = {}
    if "callmebot" in used:
        senders["callmebot"] = CallMeBotSender(notifier_cfg["callmebot"]["url"])
    if "whatabot" in used:
        senders["whatabot"] = WhatabotSender(notifier_cfg["whatabot"]["url"])
    if "telegram" in used:
        if not secrets.telegram_token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is not set")
        tg = notifier_cfg["telegram"]
        senders["telegram"] = TelegramSender(
            secrets.telegram_token, tg["api_base"], alert_sticker=tg.get("alert_sticker") or ""
        )
    return senders


def telegram_chat_ids(token: str, api_base: str = "https://api.telegram.org") -> list[tuple[str, str]]:
    """People who sent /start (or any message) to the bot recently: [(name, chat_id)]."""
    return telegram_updates(token, api_base)[0]


def telegram_updates(
    token: str, api_base: str = "https://api.telegram.org"
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """([(name, chat_id)], [(sticker emoji, sticker file_id)]) from messages sent to the bot recently."""
    if any(ch in token for ch in " \"'<>") or ":" not in token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN looks wrong: it should look like 123456789:AAH... with no spaces or quotes"
        )
    resp = requests.get(f"{api_base.rstrip('/')}/bot{token}/getUpdates", timeout=30)
    data = _json(resp)
    if resp.status_code != 200 or not data.get("ok"):
        reason = data.get("description") or f"HTTP {resp.status_code}"
        hint = {
            401: "the token is wrong; copy it again from @BotFather",
            404: "the token is wrong or incomplete; copy it again from @BotFather",
            409: "the bot has a webhook set; this script needs it removed",
        }.get(resp.status_code, "check the token")
        raise ConfigError(f"Telegram said: {reason} ({hint})")
    found: dict[str, str] = {}
    stickers: dict[str, str] = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("title", "?")
            found[str(chat["id"])] = name
        sticker = msg.get("sticker") or {}
        if sticker.get("file_id"):
            stickers[sticker["file_id"]] = sticker.get("emoji", "")
    return [(name, cid) for cid, name in found.items()], [(emo, fid) for fid, emo in stickers.items()]
