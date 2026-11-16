"""One full check: fetch IBJA -> store -> rules -> (agent) -> validate -> WhatsApp.

Run:  python -m goldbot                  (real run, uses secrets from env / .env)
      python -m goldbot --dry-run        (prints messages instead of sending)
      python -m goldbot --test-message   (sends one test note to the admin only)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import messages as M
from .agent.agent import AgentError, run_agent
from .agent.tools import ToolContext, make_executor
from .config import ConfigError, Contact, Secrets, load_config, load_dotenv, load_secrets
from .fetchers import global_price, ibja
from .masking import scrub, setup_logging
from .models import Rate
from .notifier.router import Router
from .notifier.senders import ConsoleSender, build_senders, telegram_updates
from .rules import cross_check, evaluate, pct_change
from .storage import Storage
from .validator import validate

log = logging.getLogger("goldbot")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="goldbot", description="Gold price drop alert agent")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--db", default="data/prices.db")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--dry-run", action="store_true", help="print messages instead of sending them")
    p.add_argument("--html-file", help="read the IBJA page from a saved file (testing)")
    p.add_argument("--now", help="pretend the current IST time is this, e.g. 2026-11-16T12:20 (testing)")
    p.add_argument("--no-agent", action="store_true", help="skip the AI agent and use the template")
    p.add_argument("--test-message", action="store_true", help="send one test note to the admin only")
    p.add_argument("--preview", choices=["en", "te"],
                   help="send yourself (admin only) a sample alert + morning hint in this language, marked TEST PREVIEW")
    p.add_argument("--telegram-setup", action="store_true",
                   help="list the chat ids of people who sent /start to your Telegram bot")
    return p.parse_args(argv)


def _now(args: argparse.Namespace, tz: ZoneInfo) -> dt.datetime:
    if args.now:
        t = dt.datetime.fromisoformat(args.now)
        return t if t.tzinfo else t.replace(tzinfo=tz)
    return dt.datetime.now(tz)


def _dry_run_contacts(secrets: Secrets) -> tuple[Contact, list[Contact]]:
    admin = secrets.admin or Contact("Lohith (dry run)", "+910000000001", "dry-run")
    family = secrets.family or [
        Contact("Family 1 (dry run)", "+910000000002", "dry-run"),
        Contact("Family 2 (dry run)", "+910000000003", "dry-run"),
    ]
    return admin, family


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _parse_args(argv)
    load_dotenv(args.env_file)

    if args.telegram_setup:
        return _telegram_setup(args)

    try:
        cfg = load_config(args.config)
        secrets = load_secrets(cfg)
    except (ConfigError, OSError) as e:
        log.error("configuration problem: %s", e)
        return 2

    tz = ZoneInfo(cfg.get("timezone", "Asia/Kolkata"))
    now = _now(args, tz)
    n_cfg = cfg["notifier"]

    if args.dry_run:
        admin, family = _dry_run_contacts(secrets)
        sender: Any = ConsoleSender()
        sleep = lambda _s: None  # noqa: E731
    else:
        if secrets.admin is None:
            log.error("ADMIN_CONTACT is not set; nobody can be notified. Stopping.")
            return 2
        admin, family = secrets.admin, secrets.family
        try:
            sender = build_senders(n_cfg, secrets)
        except ConfigError as e:
            log.error("configuration problem: %s", e)
            return 2
        sleep = time.sleep

    router = Router(
        admin,
        family,
        sender,
        retry_waits=n_cfg.get("send_retry_waits"),
        gap_seconds=n_cfg.get("seconds_between_sends", 5),
        sleep=sleep,
    )

    if args.test_message:
        ok = router.send_admin(M.admin_info(now, "Test note: the gold bot can reach you."))
        return 0 if ok else 1

    if args.preview:
        return _send_preview(cfg, router, tz, args.preview)

    storage = Storage(args.db)
    try:
        return run_check(cfg, secrets, router, storage, now, tz, args, sleep)
    except Exception as e:  # last line of defence: tell the admin, never the family
        log.exception("unexpected error")
        router.send_admin(
            M.admin_note(now, f"Unexpected error: {type(e).__name__}: {scrub(str(e))[:200]}", "not sent",
                         M.next_check_label(now))
        )
        storage.log_run(now, "error", f"{type(e).__name__}: {scrub(str(e))}")
        return 1
    finally:
        storage.close()


def run_check(
    cfg: dict,
    secrets: Secrets,
    router: Router,
    storage: Storage,
    now: dt.datetime,
    tz: ZoneInfo,
    args: argparse.Namespace,
    sleep,
) -> int:
    today = now.date()
    holidays = {str(h) for h in cfg.get("holidays") or []}
    if now.weekday() >= 5 or today.isoformat() in holidays:
        log.info("weekend or IBJA holiday: nothing to do")
        storage.log_run(now, "skipped", "weekend or holiday")
        return 0

    if now.time() < dt.time(11, 0):  # the 9:30 AM run: early hint from the global price
        return run_morning_hint(cfg, secrets, router, storage, now)

    expected = "AM" if now.time() < dt.time(16, 0) else "PM"
    src = cfg["source"]

    # ---- 1. read the official IBJA page ----
    page = None
    tries = 1 if args.html_file else int(src.get("not_published_max_tries", 3))
    try:
        for attempt in range(tries):
            html = Path(args.html_file).read_text(encoding="utf-8") if args.html_file else ibja.fetch_html(src, sleep)
            page = ibja.parse(html)
            if (page.today_am if expected == "AM" else page.today_pm) is not None:
                break
            if attempt < tries - 1:
                wait = int(src.get("not_published_wait_minutes", 15))
                log.info("IBJA %s rate not published yet; waiting %s min", expected, wait)
                sleep(wait * 60)
    except ibja.IbjaError as e:
        router.send_admin(M.admin_note(now, f"IBJA page problem: {e}", "not sent", M.next_check_label(now)))
        storage.log_run(now, "error", f"ibja: {e}")
        return 1
    assert page is not None

    storage.add_history(page.history, now)

    # ---- 2. today's rates: keep only genuinely new ones ----
    current: Rate | None = None
    for session, values in (("AM", page.today_am), ("PM", page.today_pm)):
        if values is None:
            continue
        rate = Rate(date=today, session=session, g24_10g=values[0], g22_10g=values[1])
        earlier = [r for r in storage.all_rates() if r.session == session and r.date < today]
        if earlier and earlier[-1].same_values(rate):
            log.info("IBJA %s values repeat %s; not a new rate", session, earlier[-1].date)
            continue
        storage.save_today(rate, now)
        if session == expected:
            current = rate

    if current is None:
        published = (page.today_am if expected == "AM" else page.today_pm) is not None
        problem = (
            f"No new IBJA {expected} rate today (values repeat an older day; a holiday?). "
            "If it is a holiday, add the date to config.yaml holidays."
            if published
            else f"IBJA {expected} rate not published after {tries} tries."
        )
        router.send_admin(M.admin_note(now, problem, "not sent", M.next_check_label(now)))
        storage.log_run(now, "no_rate", problem)
        return 0

    log.info("new IBJA %s rate: 22K Rs %s/g, 24K Rs %s/g", current.session, M.inr(current.g22), M.inr(current.g24))

    # ---- 3. global cross-check price (never shown to the family) ----
    cc_cfg = cfg.get("cross_check", {})
    quote = None
    if cc_cfg.get("enabled") and secrets.api_ninjas_key:
        try:
            quote = global_price.fetch_quote(cc_cfg, secrets.api_ninjas_key, now)
            storage.save_quote(quote)
        except Exception as e:
            log.warning("global price unavailable: %s", type(e).__name__)

    # ---- 4. drop rules (plain Python) ----
    ev = evaluate(current, storage, cfg["rules"])
    if ev.bad_data:
        router.send_admin(M.admin_note(now, ev.skip_reason, "not sent", M.next_check_label(now)))
        storage.log_run(now, "bad_data", ev.skip_reason)
        return 0
    if not ev.should_alert:
        log.info("no alert: %s", ev.skip_reason)
        storage.log_run(now, "no_alert", ev.skip_reason)
        _maybe_weekly_note(cfg, router, storage, now, expected, current)
        return 0

    max_pp = float(cc_cfg.get("max_disagreement_pp", 2.0))
    cc = cross_check(current, ev.previous, quote, storage, tz, max_pp)
    facts = M.build_facts(ev, cc, storage.all_rates())
    template = M.template_alert(facts)
    notes: list[str] = []

    # ---- 5. hard guard: the global price must not contradict IBJA ----
    if cc.available and (cc.disagreement_pp or 0) > max_pp:
        problem = (
            f"Held the alert: IBJA 22K moved {cc.ibja_change_pct:+.2f}% but global gold moved "
            f"{cc.global_change_pct:+.2f}% (difference {cc.disagreement_pp:.2f} points)."
        )
        router.send_admin(M.admin_note(now, problem, "not sent (held for safety)"))
        storage.log_run(now, "held", problem)
        return 0
    if not cc.available:
        notes.append(f"Not cross-checked: {cc.note}.")

    # ---- 6. the AI agent: verify + write (validated before sending) ----
    message, source = template, "template"
    agent_cfg = cfg.get("agent", {})
    if agent_cfg.get("enabled") and not args.no_agent:
        if not secrets.llm_keys:
            notes.append("No LLM key set; template message sent.")
        else:
            ctx = ToolContext(storage=storage, current=current, previous=ev.previous, is_new=True, cross_check=cc)
            try:
                res = run_agent(facts, template, make_executor(ctx), agent_cfg, secrets.llm_keys, max_pp)
                if res.errors:
                    notes.append("Backup LLM used. " + scrub("; ".join(res.errors))[:200])
                if not res.send:
                    problem = f"Agent held the alert: {scrub(res.reason)[:200]}"
                    router.send_admin(M.admin_note(now, problem, "not sent (agent decision)"))
                    storage.log_run(now, "held", problem)
                    return 0
                problems = validate(res.message, facts, cfg["messages"], M.FOOTER)
                if problems:
                    notes.append("Agent text failed checks (" + "; ".join(problems)[:200] + "); template sent.")
                else:
                    message, source = res.message, f"agent/{res.provider}"
                log.info("agent finished with %s (%d tool calls)", res.provider, res.tool_calls)
            except AgentError as e:
                notes.append(f"LLM unavailable ({scrub(str(e))[:200]}); template sent.")

    # ---- 7. send: admin + family ----
    # English: the AI's validated text (or the template). Telugu: the reviewed template, same numbers.
    missed = router.send_drop_alert({"en": message, "te": M.template_alert_te(facts)})
    if len(missed) < router.recipient_count:  # at least one person got it: never send this rate again
        storage.record_alert(current, ev.triggers, now)
    if missed:
        notes.append("Could not reach: " + ", ".join(missed) + ".")
    if notes:
        router.send_admin(M.admin_info(now, f"Alert sent ({source}).\n- " + "\n- ".join(notes)))
    storage.log_run(now, "alert_sent", f"{source}; triggers={','.join(ev.triggers)}; missed={len(missed)}")
    _maybe_weekly_note(cfg, router, storage, now, expected, current)
    return 0


def run_morning_hint(cfg: dict, secrets: Secrets, router: Router, storage: Storage, now: dt.datetime) -> int:
    """9:30 AM: if global gold fell enough in its latest session, send a clearly-labelled early hint."""
    h = cfg.get("morning_hint", {})
    if not h.get("enabled"):
        log.info("morning hint is disabled")
        return 0
    if storage.hint_sent(now.date()):
        log.info("morning hint already sent today")
        return 0
    if not secrets.api_ninjas_key:
        log.info("no API_NINJAS_KEY: morning hint needs the global price")
        storage.log_run(now, "hint_skipped", "no API_NINJAS_KEY")
        return 0
    try:
        quote = global_price.fetch_quote(cfg["cross_check"], secrets.api_ninjas_key, now)
        storage.save_quote(quote)
    except Exception as e:
        problem = f"Global price unavailable for the 9:30 AM hint ({type(e).__name__})."
        router.send_admin(M.admin_note(now, problem, "no hint sent", "12:20 PM today"))
        storage.log_run(now, "hint_error", problem)
        return 0
    max_age = float(h.get("max_age_hours", 96)) * 3600
    if quote.updated and now.timestamp() - quote.updated > max_age:
        log.info("global price is stale; no hint")
        storage.log_run(now, "hint_skipped", "global price stale")
        return 0
    prev = storage.previous_settlement(quote)
    if prev is None:
        log.info("no earlier global settlement stored yet; hints start once there are two")
        storage.log_run(now, "hint_skipped", "need one more day of global data")
        return 0

    gl_inr = pct_change(quote.inr_per_g, prev.inr_per_g)
    gl_usd = pct_change(quote.usd_per_oz, prev.usd_per_oz)
    fx = pct_change(quote.usd_inr, prev.usd_inr)
    if gl_inr > -float(h.get("min_global_drop_pct", 1.0)):
        log.info("global gold moved %+.2f%% (in rupees): no hint", gl_inr)
        storage.log_run(now, "no_hint", f"global {gl_inr:+.2f}%")
        return 0

    rates = storage.all_rates()
    last = rates[-1] if rates else None
    texts = {"en": M.morning_hint(gl_inr, gl_usd, fx, last), "te": M.morning_hint_te(gl_inr, last)}
    missed = router.send_hint(texts, include_family=bool(h.get("send_to_family", True)))
    storage.record_hint(now.date(), gl_inr, now)
    if missed:
        router.send_admin(M.admin_info(now, "Morning hint sent. Could not reach: " + ", ".join(missed) + "."))
    storage.log_run(now, "hint_sent", f"global {gl_inr:+.2f}%; missed={len(missed)}")
    return 0


PREVIEW_PAGE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ibja_2026-11-16_am.html"


def _send_preview(cfg: dict, router: Router, tz: ZoneInfo, lang: str) -> int:
    """Sample messages (real IBJA data from 16 Nov 2026 + a sample reason), to the admin only."""
    from .models import CrossCheck

    when = dt.datetime(2026, 11, 16, 12, 20, tzinfo=tz)
    storage = Storage(":memory:")
    page = ibja.parse(PREVIEW_PAGE.read_text(encoding="utf-8"))
    storage.add_history(page.history, when)
    current = Rate(date=when.date(), session="AM", g24_10g=page.today_am[0], g22_10g=page.today_am[1])
    ev = evaluate(current, storage, cfg["rules"])
    sample_cc = CrossCheck(True, -1.05, -1.0, -0.8, -0.2, 0.05, "sample")
    facts = M.build_facts(ev, sample_cc, storage.all_rates())
    if lang == "te":
        alert, hint = M.template_alert_te(facts), M.morning_hint_te(-1.25, current)
    else:
        alert, hint = M.template_alert(facts), M.morning_hint(-1.25, -1.16, -0.09, current)
    banner = "🧪 TEST PREVIEW (sample data, not a real alert)\n\n"
    ok = router.send_admin(banner + alert) and router.send_admin(banner + hint)
    storage.conn.close()
    print("Preview sent to the admin only." if ok else "Could not send the preview.")
    return 0 if ok else 1


def _telegram_setup(args: argparse.Namespace) -> int:
    """Prints name + chat_id for everyone who messaged the bot, so you can fill in the contacts."""
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Put TELEGRAM_BOT_TOKEN=... in your .env first (from @BotFather).")
        return 2
    try:
        cfg = load_config(args.config)
        people, stickers = telegram_updates(token, cfg["notifier"]["telegram"]["api_base"])
    except ConfigError as e:
        print(scrub(str(e)))
        return 1
    except Exception as e:
        print(f"Could not reach Telegram ({type(e).__name__}). Check your internet connection.")
        return 1
    if not people:
        print("Nobody has messaged the bot yet. Open your bot in Telegram, tap Start (or send hi), then run this again.")
        return 0
    print("People who messaged your bot (use these chat_id values in .env):")
    for name, chat_id in people:
        print(f"  {name}: chat_id = {chat_id}")
    if stickers:
        print("\nStickers sent to your bot (paste one file_id into config.yaml > notifier > telegram > alert_sticker):")
        for emoji, file_id in stickers:
            print(f"  {emoji}  {file_id}")
    else:
        print("\nTip: send a sticker to your bot and run this again to get a sticker for the alerts.")
    return 0


def _maybe_weekly_note(cfg: dict, router: Router, storage: Storage, now: dt.datetime, session: str, latest: Rate) -> None:
    if not cfg.get("health", {}).get("weekly_note") or now.weekday() != 4 or session != "PM":
        return
    week_start = now.date() - dt.timedelta(days=now.weekday())
    n_alerts = storage.alerts_between(week_start, now.date())
    text = (
        "Weekly check: the bot is running.\n"
        f"Rates stored: {len(storage.all_rates())}\n"
        f"Alerts sent this week: {n_alerts}\n"
        f"Latest IBJA 22K: Rs {M.inr(latest.g22)}/g ({M.nice_date(latest.date)} {latest.session})"
    )
    router.send_admin(M.admin_info(now, text))


if __name__ == "__main__":
    sys.exit(main())
