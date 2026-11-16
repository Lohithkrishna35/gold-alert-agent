"""Loads config.yaml (settings) and secrets (environment variables only)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .masking import mask_phone, register_secret


class ConfigError(Exception):
    pass


CHANNELS = ("callmebot", "whatabot", "telegram")
LANGUAGES = ("en", "te")  # English, Telugu
WHATSAPP_CHANNELS = ("callmebot", "whatabot")


@dataclass(frozen=True)
class Contact:
    """One person. WhatsApp channels use phone + apikey; Telegram uses chat_id."""

    name: str
    phone: str = ""
    apikey: str = field(default="", repr=False)
    channel: str = "callmebot"
    chat_id: str = ""
    language: str = "en"

    @property
    def address(self) -> str:
        return self.chat_id if self.channel == "telegram" else self.phone

    def __repr__(self) -> str:  # never print the number, chat id or key
        return f"Contact(name={self.name!r}, channel={self.channel}, to={mask_phone(self.address)})"

    __str__ = __repr__


@dataclass
class Secrets:
    admin: Contact | None
    family: list[Contact]
    api_ninjas_key: str | None
    llm_keys: dict[str, str]
    telegram_token: str | None = None


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml is empty or invalid")
    return cfg


def load_dotenv(path: str | Path = ".env") -> None:
    """Tiny .env reader for local runs. Real environment variables always win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _parse_contact(raw: Any, label: str, default_channel: str) -> Contact:
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} must be a JSON object")
    channel = str(raw.get("channel", default_channel)).strip().lower()
    if channel not in CHANNELS:
        raise ConfigError(f"{label}: channel must be one of {', '.join(CHANNELS)}")
    needed = ("name", "chat_id") if channel == "telegram" else ("name", "phone", "apikey")
    missing = [k for k in needed if not str(raw.get(k, "")).strip()]
    if missing:
        raise ConfigError(f"{label} ({channel}) is missing: {', '.join(missing)}")
    name = str(raw["name"]).strip()
    language = str(raw.get("language", "en")).strip().lower()
    if language not in LANGUAGES:
        raise ConfigError(f"{label}: language must be one of {', '.join(LANGUAGES)} (en = English, te = Telugu)")
    if channel == "telegram":
        chat_id = str(raw["chat_id"]).strip()
        if not chat_id.lstrip("-").isdigit():
            raise ConfigError(f"{label}: chat_id must be a number (get it with --telegram-setup)")
        contact = Contact(name=name, channel=channel, chat_id=chat_id, language=language)
        register_secret(chat_id)
        return contact
    phone = str(raw["phone"]).strip().replace(" ", "")
    if not phone.startswith("+"):
        raise ConfigError(f"{label}: phone must include the country code, like +91...")
    contact = Contact(name=name, phone=phone, apikey=str(raw["apikey"]).strip(), channel=channel, language=language)
    register_secret(contact.apikey)
    register_secret(contact.phone)
    return contact


def load_secrets(cfg: dict[str, Any]) -> Secrets:
    default_channel = cfg.get("notifier", {}).get("default_channel", "callmebot")
    admin = None
    raw_admin = os.environ.get("ADMIN_CONTACT", "").strip()
    if raw_admin:
        try:
            admin = _parse_contact(json.loads(raw_admin), "ADMIN_CONTACT", default_channel)
        except json.JSONDecodeError as e:
            raise ConfigError("ADMIN_CONTACT is not valid JSON") from e

    family: list[Contact] = []
    raw_family = os.environ.get("FAMILY_CONTACTS", "").strip()
    if raw_family:
        try:
            items = json.loads(raw_family)
        except json.JSONDecodeError as e:
            raise ConfigError("FAMILY_CONTACTS is not valid JSON") from e
        if not isinstance(items, list):
            raise ConfigError("FAMILY_CONTACTS must be a JSON list")
        family = [_parse_contact(item, f"FAMILY_CONTACTS[{i}]", default_channel) for i, item in enumerate(items)]

    if admin and any((c.channel, c.address) == (admin.channel, admin.address) for c in family):
        raise ConfigError("The admin must not also be in FAMILY_CONTACTS (the admin already gets every alert)")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    register_secret(telegram_token)
    everyone = ([admin] if admin else []) + family
    if any(c.channel == "telegram" for c in everyone) and not telegram_token:
        raise ConfigError("A contact uses Telegram but TELEGRAM_BOT_TOKEN is not set")

    ninjas = os.environ.get("API_NINJAS_KEY", "").strip() or None
    register_secret(ninjas)

    llm_keys: dict[str, str] = {}
    for p in cfg.get("agent", {}).get("providers", []):
        env_name = p.get("api_key_env", "")
        value = os.environ.get(env_name, "").strip()
        if value:
            llm_keys[p["name"]] = value
            register_secret(value)

    return Secrets(
        admin=admin, family=family, api_ninjas_key=ninjas, llm_keys=llm_keys, telegram_token=telegram_token
    )
