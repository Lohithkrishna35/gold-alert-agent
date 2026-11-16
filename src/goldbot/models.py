"""Plain data objects shared across the project."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

SESSIONS = ("AM", "PM")
# IBJA publishes around 12:05 PM (AM rate) and 5:05 PM (PM rate) IST
SESSION_PUBLISH_TIME = {"AM": dt.time(12, 5), "PM": dt.time(17, 5)}


@dataclass(frozen=True)
class Rate:
    """One IBJA rate. Values are rupees per 10 grams, exactly as IBJA publishes them."""

    date: dt.date
    session: str  # "AM" or "PM"
    g24_10g: int  # Gold 999
    g22_10g: int  # Gold 916

    def __post_init__(self) -> None:
        if self.session not in SESSIONS:
            raise ValueError(f"session must be AM or PM, got {self.session!r}")

    @property
    def g24(self) -> float:
        """24K rupees per gram."""
        return self.g24_10g / 10

    @property
    def g22(self) -> float:
        """22K rupees per gram."""
        return self.g22_10g / 10

    @property
    def sort_key(self) -> tuple[dt.date, int]:
        return (self.date, SESSIONS.index(self.session))

    def same_values(self, other: "Rate") -> bool:
        return self.g24_10g == other.g24_10g and self.g22_10g == other.g22_10g


@dataclass
class IbjaPage:
    """What we read from the IBJA home page."""

    today_am: tuple[int, int] | None  # (g24_10g, g22_10g) or None if not published yet
    today_pm: tuple[int, int] | None
    history: list[Rate] = field(default_factory=list)  # previous business days, AM and PM


@dataclass(frozen=True)
class GlobalQuote:
    """International gold price (last daily settlement) used only as a cross-check."""

    fetched_at: dt.datetime
    inr_per_g: float
    usd_per_oz: float
    updated: int  # provider's unix timestamp of the settlement

    @property
    def usd_inr(self) -> float:
        return self.inr_per_g * 31.1034768 / self.usd_per_oz


@dataclass
class CrossCheck:
    available: bool
    ibja_change_pct: float | None = None
    global_change_pct: float | None = None
    global_usd_change_pct: float | None = None
    usd_inr_change_pct: float | None = None
    disagreement_pp: float | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "ibja_change_pct": _r(self.ibja_change_pct),
            "global_gold_inr_change_pct": _r(self.global_change_pct),
            "global_gold_usd_change_pct": _r(self.global_usd_change_pct),
            "usd_inr_change_pct": _r(self.usd_inr_change_pct),
            "disagreement_pp": _r(self.disagreement_pp),
            "note": self.note,
        }


@dataclass
class Evaluation:
    """Result of the drop rules for one new rate."""

    current: Rate
    previous: Rate | None
    change_pct: float | None = None
    triggers: list[str] = field(default_factory=list)
    window_low: Rate | None = None
    window_high: Rate | None = None
    window_points: int = 0
    below_high_pct: float | None = None
    bad_data: bool = False
    should_alert: bool = False
    skip_reason: str = ""


@dataclass
class AgentResult:
    send: bool
    reason: str
    message: str
    provider: str = ""
    tool_calls: int = 0
    errors: list[str] = field(default_factory=list)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)
