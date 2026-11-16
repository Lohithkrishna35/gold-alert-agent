"""International gold price, used only to cross-check that an IBJA drop is real.

API Ninjas free tier returns the last completed daily settlement of COMEX gold.
It is never shown to the family; only the official IBJA rate is.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import requests

from ..models import GlobalQuote

log = logging.getLogger(__name__)


class GlobalPriceError(Exception):
    pass


def _get(url: str, api_key: str, params: dict[str, str], timeout: int) -> dict[str, Any]:
    resp = requests.get(url, headers={"X-Api-Key": api_key}, params=params, timeout=timeout)
    if resp.status_code != 200:
        raise GlobalPriceError(f"global price API returned HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict) or "price" not in data:
        raise GlobalPriceError("global price API response has no price")
    return data


def fetch_quote(cross_cfg: dict[str, Any], api_key: str, now: dt.datetime, timeout: int = 20) -> GlobalQuote:
    url = cross_cfg["url"]
    inr = _get(url, api_key, {"currency": "INR", "unit": "g"}, timeout)
    usd = _get(url, api_key, {"currency": "USD", "unit": "troy_ounce"}, timeout)
    inr_per_g = float(inr["price"])
    usd_per_oz = float(usd["price"])
    if inr_per_g <= 0 or usd_per_oz <= 0:
        raise GlobalPriceError("global price is zero or negative")
    return GlobalQuote(
        fetched_at=now,
        inr_per_g=inr_per_g,
        usd_per_oz=usd_per_oz,
        updated=int(inr.get("updated") or 0),
    )
