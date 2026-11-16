"""Telegram + Whatabot channels, per-contact channel choice, and secret handling."""

import json
from types import SimpleNamespace

import pytest

from goldbot.config import ConfigError, Contact, load_secrets
from goldbot.notifier import senders as S
from goldbot.notifier.router import Router


class FakeResp(SimpleNamespace):
    def json(self):
        return self.payload


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("ADMIN_CONTACT", "FAMILY_CONTACTS", "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "GROQ_API_KEY",
              "API_NINJAS_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_mixed_channels_parse(clean_env, cfg):
    clean_env.setenv("ADMIN_CONTACT", json.dumps({"name": "Lohith", "channel": "telegram", "chat_id": "111222333"}))
    clean_env.setenv("FAMILY_CONTACTS", json.dumps([
        {"name": "Amma", "channel": "callmebot", "phone": "+91 90000 00002", "apikey": "k2"},
        {"name": "Nanna", "channel": "whatabot", "phone": "+919000000003", "apikey": "k3"},
    ]))
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    s = load_secrets(cfg)
    assert s.admin.channel == "telegram" and s.admin.chat_id == "111222333"
    assert [c.channel for c in s.family] == ["callmebot", "whatabot"]
    assert s.family[0].phone == "+919000000002"  # spaces removed
    assert "111222333" not in repr(s.admin)


def test_default_channel_is_callmebot(clean_env, cfg):
    clean_env.setenv("ADMIN_CONTACT", json.dumps({"name": "Lohith", "phone": "+919000000001", "apikey": "k"}))
    assert load_secrets(cfg).admin.channel == "callmebot"


def test_telegram_needs_token(clean_env, cfg):
    clean_env.setenv("ADMIN_CONTACT", json.dumps({"name": "Lohith", "channel": "telegram", "chat_id": "1"}))
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_secrets(cfg)


@pytest.mark.parametrize("raw, msg", [
    ({"name": "X", "channel": "sms", "phone": "+91", "apikey": "k"}, "channel must be"),
    ({"name": "X", "channel": "telegram"}, "chat_id"),
    ({"name": "X", "channel": "telegram", "chat_id": "abc"}, "must be a number"),
    ({"name": "X", "channel": "whatabot", "phone": "9000000001", "apikey": "k"}, "country code"),
])
def test_bad_contacts_rejected(clean_env, cfg, raw, msg):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "t")
    clean_env.setenv("ADMIN_CONTACT", json.dumps(raw))
    with pytest.raises(ConfigError, match=msg):
        load_secrets(cfg)


def test_router_uses_each_contacts_channel(capsys):
    tg, wa = S.ConsoleSender(), S.ConsoleSender()
    admin = Contact("Lohith", channel="telegram", chat_id="1")
    fam = [Contact("Amma", "+919000000002", "k", channel="callmebot")]
    r = Router(admin, fam, {"telegram": tg, "callmebot": wa}, retry_waits=[], gap_seconds=0, sleep=lambda s: None)
    r.send_drop_alert("drop")
    r.send_admin("error")
    assert [(c.name, t) for c, t in tg.sent] == [("Lohith", "drop"), ("Lohith", "error")]
    assert [(c.name, t) for c, t in wa.sent] == [("Amma", "drop")]  # family never gets the error


def test_telegram_sender_markdown_then_plain_fallback(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        if "parse_mode" in json:
            return FakeResp(status_code=400, payload={"ok": False})
        return FakeResp(status_code=200, payload={"ok": True})

    monkeypatch.setattr(S.requests, "post", fake_post)
    ok = S.TelegramSender("123:abc").send(Contact("L", channel="telegram", chat_id="42"), "*Gold*")
    assert ok and calls[0]["parse_mode"] == "Markdown" and "parse_mode" not in calls[1]
    assert calls[1]["chat_id"] == "42"


def test_telegram_sender_reports_failure(monkeypatch):
    monkeypatch.setattr(S.requests, "post", lambda url, json, timeout: FakeResp(status_code=403, payload={"ok": False}))
    assert S.TelegramSender("t").send(Contact("L", channel="telegram", chat_id="42"), "x") is False


def test_whatabot_sends_phone_text_apikey(monkeypatch):
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params)
        return FakeResp(status_code=200, text="Message sent")

    monkeypatch.setattr(S.requests, "get", fake_get)
    c = Contact("L", "+919000000001", "key9", channel="whatabot")
    assert S.WhatabotSender("https://api.whatabot.net/whatsapp/sendMessage").send(c, "hi")
    assert seen["params"] == {"phone": "+919000000001", "text": "hi", "apikey": "key9"}


def test_gateway_refusal_is_failure(monkeypatch):
    monkeypatch.setattr(S.requests, "get",
                        lambda url, params, timeout: FakeResp(status_code=200, text="APIKey is invalid"))
    assert not S.CallMeBotSender("u").send(Contact("L", "+919000000001", "k"), "hi")


def test_build_senders_only_for_used_channels(cfg):
    from goldbot.config import Secrets

    s = Secrets(admin=Contact("L", channel="telegram", chat_id="1"),
                family=[Contact("A", "+919000000002", "k", channel="whatabot")],
                api_ninjas_key=None, llm_keys={}, telegram_token="t")
    built = S.build_senders(cfg["notifier"], s)
    assert set(built) == {"telegram", "whatabot"}


def test_telegram_chat_ids(monkeypatch):
    payload = {"ok": True, "result": [
        {"message": {"chat": {"id": 111, "first_name": "Lohith"}}},
        {"message": {"chat": {"id": 222, "first_name": "Amma", "last_name": "K"}}},
        {"message": {"chat": {"id": 111, "first_name": "Lohith"}}},
    ]}
    monkeypatch.setattr(S.requests, "get", lambda url, timeout: FakeResp(status_code=200, payload=payload))
    assert S.telegram_chat_ids("123:abc") == [("Lohith", "111"), ("Amma K", "222")]


def test_telegram_setup_explains_bad_token(monkeypatch):
    with pytest.raises(ConfigError, match="no spaces or quotes"):
        S.telegram_chat_ids('"123:abc"')
    monkeypatch.setattr(S.requests, "get", lambda url, timeout: FakeResp(
        status_code=401, payload={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(ConfigError, match="Unauthorized.*copy it again"):
        S.telegram_chat_ids("123:abc")


def test_telegram_escapes_markdown_brackets(monkeypatch):
    sent = []
    monkeypatch.setattr(S.requests, "post",
                        lambda url, json, timeout: sent.append(json) or FakeResp(status_code=200, payload={"ok": True}))
    S.TelegramSender("123:abc").send(Contact("L", channel="telegram", chat_id="42"), "[Gold bot - admin]\n*Gold* rate_x")
    assert sent[0]["text"] == "\\[Gold bot - admin]\n*Gold* rate\\_x"



def test_sticker_goes_before_alerts_only(monkeypatch):
    calls = []
    monkeypatch.setattr(S.requests, "post", lambda url, json, timeout: calls.append(url.rsplit("/", 1)[1])
                        or FakeResp(status_code=200, payload={"ok": True}))
    tg = S.TelegramSender("123:abc", alert_sticker="STICKER_ID")
    admin = Contact("L", channel="telegram", chat_id="1")
    r = Router(admin, [], {"telegram": tg}, retry_waits=[], gap_seconds=0, sleep=lambda s: None)
    r.send_drop_alert("drop")
    r.send_admin("error")
    assert calls == ["sendSticker", "sendMessage", "sendMessage"]  # no sticker on the admin note


def test_sticker_failure_never_blocks_alert(monkeypatch):
    def post(url, json, timeout):
        if url.endswith("sendSticker"):
            raise S.requests.ConnectionError("x")
        return FakeResp(status_code=200, payload={"ok": True})
    monkeypatch.setattr(S.requests, "post", post)
    tg = S.TelegramSender("123:abc", alert_sticker="STICKER_ID")
    r = Router(Contact("L", channel="telegram", chat_id="1"), [], {"telegram": tg},
               retry_waits=[], gap_seconds=0, sleep=lambda s: None)
    assert r.send_drop_alert("drop") == []


def test_setup_lists_stickers(monkeypatch):
    payload = {"ok": True, "result": [
        {"message": {"chat": {"id": 111, "first_name": "Lohith"}, "sticker": {"file_id": "CAACAgUAAx", "emoji": "🥳"}}},
    ]}
    monkeypatch.setattr(S.requests, "get", lambda url, timeout: FakeResp(status_code=200, payload=payload))
    people, stickers = S.telegram_updates("123:abc")
    assert people == [("Lohith", "111")] and stickers == [("🥳", "CAACAgUAAx")]
