"""SQLite storage: IBJA rate history, global cross-check quotes, sent alerts, run log.

Holds only public prices and run status. No names, phone numbers or keys are ever stored.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .models import GlobalQuote, Rate

SCHEMA = """
CREATE TABLE IF NOT EXISTS rates (
    date TEXT NOT NULL,
    session TEXT NOT NULL CHECK (session IN ('AM', 'PM')),
    g24_10g INTEGER NOT NULL,
    g22_10g INTEGER NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (date, session)
);
CREATE TABLE IF NOT EXISTS global_quotes (
    fetched_at TEXT PRIMARY KEY,
    inr_per_g REAL NOT NULL,
    usd_per_oz REAL NOT NULL,
    updated INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    date TEXT NOT NULL,
    session TEXT NOT NULL,
    g22_10g INTEGER NOT NULL,
    triggers TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (date, session)
);
CREATE TABLE IF NOT EXISTS hints (
    date TEXT PRIMARY KEY,
    global_change_pct REAL NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def _row_to_rate(row: sqlite3.Row) -> Rate:
    return Rate(
        date=dt.date.fromisoformat(row["date"]),
        session=row["session"],
        g24_10g=row["g24_10g"],
        g22_10g=row["g22_10g"],
    )


class Storage:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---- rates ----
    def add_history(self, rates: list[Rate], now: dt.datetime) -> int:
        """Insert past rates from the IBJA page. Existing rows are never overwritten."""
        added = 0
        for r in rates:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO rates VALUES (?, ?, ?, ?, 'ibja_history', ?)",
                (r.date.isoformat(), r.session, r.g24_10g, r.g22_10g, now.isoformat()),
            )
            added += cur.rowcount
        self.conn.commit()
        return added

    def save_today(self, rate: Rate, now: dt.datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rates VALUES (?, ?, ?, ?, 'ibja_today', ?)",
            (rate.date.isoformat(), rate.session, rate.g24_10g, rate.g22_10g, now.isoformat()),
        )
        self.conn.commit()

    def get_rate(self, date: dt.date, session: str) -> Rate | None:
        row = self.conn.execute(
            "SELECT * FROM rates WHERE date = ? AND session = ?", (date.isoformat(), session)
        ).fetchone()
        return _row_to_rate(row) if row else None

    def all_rates(self) -> list[Rate]:
        rows = self.conn.execute("SELECT * FROM rates").fetchall()
        return sorted((_row_to_rate(r) for r in rows), key=lambda r: r.sort_key)

    def previous_rate(self, rate: Rate) -> Rate | None:
        earlier = [r for r in self.all_rates() if r.sort_key < rate.sort_key]
        return earlier[-1] if earlier else None

    def window(self, rate: Rate, days: int) -> list[Rate]:
        """Rates in the `days` calendar days before `rate`, excluding `rate` itself."""
        start = rate.date - dt.timedelta(days=days)
        return [r for r in self.all_rates() if r.date >= start and r.sort_key < rate.sort_key]

    def history(self, days: int, until: Rate) -> list[Rate]:
        start = until.date - dt.timedelta(days=days)
        return [r for r in self.all_rates() if r.date >= start and r.sort_key <= until.sort_key]

    # ---- global cross-check quotes ----
    def save_quote(self, q: GlobalQuote) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO global_quotes VALUES (?, ?, ?, ?)",
            (q.fetched_at.isoformat(), q.inr_per_g, q.usd_per_oz, q.updated),
        )
        self.conn.commit()

    def quote_at_or_before(self, when: dt.datetime) -> GlobalQuote | None:
        row = self.conn.execute(
            "SELECT * FROM global_quotes WHERE fetched_at <= ? ORDER BY fetched_at DESC LIMIT 1",
            (when.isoformat(),),
        ).fetchone()
        if not row:
            return None
        return GlobalQuote(
            fetched_at=dt.datetime.fromisoformat(row["fetched_at"]),
            inr_per_g=row["inr_per_g"],
            usd_per_oz=row["usd_per_oz"],
            updated=row["updated"],
        )

    def previous_settlement(self, latest: GlobalQuote) -> GlobalQuote | None:
        """The most recent stored quote from an earlier daily settlement than `latest`."""
        if latest.updated:
            row = self.conn.execute(
                "SELECT * FROM global_quotes WHERE updated > 0 AND updated < ? ORDER BY updated DESC, fetched_at DESC "
                "LIMIT 1",
                (latest.updated,),
            ).fetchone()
        else:  # provider gave no settlement time: use the last quote from before today
            day_start = latest.fetched_at.replace(hour=0, minute=0, second=0, microsecond=0)
            row = self.conn.execute(
                "SELECT * FROM global_quotes WHERE fetched_at < ? ORDER BY fetched_at DESC LIMIT 1",
                (day_start.isoformat(),),
            ).fetchone()
        if not row:
            return None
        return GlobalQuote(
            fetched_at=dt.datetime.fromisoformat(row["fetched_at"]),
            inr_per_g=row["inr_per_g"],
            usd_per_oz=row["usd_per_oz"],
            updated=row["updated"],
        )

    # ---- morning hints ----
    def hint_sent(self, date: dt.date) -> bool:
        return self.conn.execute("SELECT 1 FROM hints WHERE date = ?", (date.isoformat(),)).fetchone() is not None

    def record_hint(self, date: dt.date, change_pct: float, now: dt.datetime) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO hints VALUES (?, ?, ?)", (date.isoformat(), change_pct, now.isoformat())
        )
        self.conn.commit()

    # ---- alerts ----
    def alerts_on(self, date: dt.date) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM alerts WHERE date = ? ORDER BY sent_at", (date.isoformat(),)
        ).fetchall()

    def last_alert(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM alerts ORDER BY sent_at DESC LIMIT 1").fetchone()

    def alert_exists(self, rate: Rate) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM alerts WHERE date = ? AND session = ?", (rate.date.isoformat(), rate.session)
            ).fetchone()
            is not None
        )

    def record_alert(self, rate: Rate, triggers: list[str], now: dt.datetime) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO alerts VALUES (?, ?, ?, ?, ?)",
            (rate.date.isoformat(), rate.session, rate.g22_10g, ",".join(triggers), now.isoformat()),
        )
        self.conn.commit()

    def alerts_between(self, start: dt.date, end: dt.date) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat())
        ).fetchone()
        return int(row["n"])

    # ---- run log ----
    def log_run(self, now: dt.datetime, status: str, detail: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?)", (now.isoformat(), status, detail[:500]))
        # keep the log small: last 200 runs
        self.conn.execute(
            "DELETE FROM runs WHERE started_at NOT IN (SELECT started_at FROM runs ORDER BY started_at DESC LIMIT 200)"
        )
        self.conn.commit()
