"""Reads the official IBJA benchmark rates from ibjarates.com.

The page is server-rendered HTML:
- Today's rates sit in spans with ids like lblGold999_AM / lblGold916_PM (rupees per 10 g).
  The PM span is empty until IBJA publishes the PM rate.
- Previous business days sit in two tab panes, #tab-am and #tab-pm, one row per date.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..models import IbjaPage, Rate

log = logging.getLogger(__name__)


class IbjaError(Exception):
    """The IBJA page could not be fetched or no longer looks as expected."""


def fetch_html(source_cfg: dict[str, Any], sleep=time.sleep) -> str:
    url = source_cfg["ibja_url"]
    headers = {"User-Agent": source_cfg.get("user_agent", "gold-alert-agent/1.0")}
    timeout = source_cfg.get("timeout_seconds", 20)
    waits = list(source_cfg.get("network_retry_waits", [10, 30, 60]))
    last_error: Exception | None = None
    for attempt in range(len(waits) + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_error = e
        else:
            if resp.status_code == 200:
                return resp.text
            if resp.status_code < 500:
                # 4xx will not fix itself by retrying
                raise IbjaError(f"IBJA returned HTTP {resp.status_code}")
            last_error = IbjaError(f"IBJA server error {resp.status_code}")
        if attempt < len(waits):
            log.warning("IBJA fetch failed (%s); retrying in %ss", type(last_error).__name__, waits[attempt])
            sleep(waits[attempt])
    raise IbjaError(f"IBJA page unreachable after {len(waits) + 1} tries: {last_error}")


def _int_or_none(text: str | None) -> int | None:
    if text is None:
        return None
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise IbjaError(f"Unexpected rate value {text!r}")
    return int(cleaned)


def _span_value(soup: BeautifulSoup, element_id: str) -> int | None:
    el = soup.find(id=element_id)  # the page repeats some ids; the first is the main table
    if el is None:
        raise IbjaError(f"IBJA page layout changed: #{element_id} not found")
    return _int_or_none(el.get_text())


def _today(soup: BeautifulSoup, session: str) -> tuple[int, int] | None:
    g24 = _span_value(soup, f"lblGold999_{session}")
    g22 = _span_value(soup, f"lblGold916_{session}")
    if g24 is None and g22 is None:
        return None
    if g24 is None or g22 is None:
        raise IbjaError(f"IBJA {session} rate is only partly published")
    return (g24, g22)


def _history(soup: BeautifulSoup, pane_id: str, session: str) -> list[Rate]:
    pane = soup.find(id=pane_id)
    if pane is None:
        raise IbjaError(f"IBJA page layout changed: #{pane_id} not found")
    rates: list[Rate] = []
    for row in pane.select("tbody tr"):
        first = row.find("td")
        if first is None:
            continue
        date_text = first.get_text(strip=True)
        try:
            day = dt.datetime.strptime(date_text, "%d/%m/%Y").date()
        except ValueError:
            continue
        cells = {td.get("data-label", "").strip(): td.get_text(strip=True) for td in row.find_all("td")}
        g24 = _int_or_none(cells.get("Gold 999"))
        g22 = _int_or_none(cells.get("Gold 916"))
        if g24 and g22:
            rates.append(Rate(date=day, session=session, g24_10g=g24, g22_10g=g22))
    return rates


def parse(html: str) -> IbjaPage:
    soup = BeautifulSoup(html, "html.parser")
    page = IbjaPage(
        today_am=_today(soup, "AM"),
        today_pm=_today(soup, "PM"),
        history=_history(soup, "tab-am", "AM") + _history(soup, "tab-pm", "PM"),
    )
    for values in (page.today_am, page.today_pm):
        if values:
            _check_plausible(*values)
    for r in page.history:
        _check_plausible(r.g24_10g, r.g22_10g)
    return page


def _check_plausible(g24_10g: int, g22_10g: int) -> None:
    """Basic sanity: 22K (91.6% pure) must be about 0.916 of 24K (99.9% pure)."""
    if g24_10g <= 0 or g22_10g <= 0:
        raise IbjaError("IBJA rate is zero or negative")
    ratio = g22_10g / g24_10g
    if not 0.90 <= ratio <= 0.93:
        raise IbjaError(f"IBJA 22K/24K ratio looks wrong ({ratio:.3f})")
