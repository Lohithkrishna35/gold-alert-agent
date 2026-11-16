import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.config import Contact, load_config  # noqa: E402
from goldbot.models import Rate  # noqa: E402
from goldbot.storage import Storage  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
FIXTURE = ROOT / "tests" / "fixtures" / "ibja_2026-11-16_am.html"


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.yaml")


@pytest.fixture
def storage():
    s = Storage(":memory:")
    yield s
    s.conn.close()


@pytest.fixture
def ibja_html():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def admin():
    return Contact("Lohith", "+919000000001", "key-admin")


@pytest.fixture
def family():
    return [Contact("Amma", "+919000000002", "key-f1"), Contact("Nanna", "+919000000003", "key-f2")]


def rate(day: str, session: str, g22_10g: int, g24_10g: int | None = None) -> Rate:
    if g24_10g is None:
        g24_10g = round(g22_10g / 0.916)
    return Rate(date=dt.date.fromisoformat(day), session=session, g24_10g=g24_10g, g22_10g=g22_10g)


def ist(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).replace(tzinfo=IST)
