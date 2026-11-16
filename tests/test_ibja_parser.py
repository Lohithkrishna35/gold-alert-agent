import datetime as dt

import pytest

from goldbot.fetchers import ibja


def test_parses_real_page_layout(ibja_html):
    page = ibja.parse(ibja_html)
    assert page.today_am == (134380, 123092)
    assert page.today_pm is None  # PM not published at 12:49 PM
    am = [r for r in page.history if r.session == "AM"]
    pm = [r for r in page.history if r.session == "PM"]
    assert len(am) == 4 and len(pm) == 4
    latest_pm = max(pm, key=lambda r: r.date)
    assert latest_pm.date == dt.date(2026, 11, 13)
    assert (latest_pm.g24_10g, latest_pm.g22_10g) == (135809, 124401)


def test_pm_published(ibja_html):
    html = ibja_html.replace('id="lblGold999_PM"></span>', 'id="lblGold999_PM">133900</span>').replace(
        'id="lblGold916_PM"></span>', 'id="lblGold916_PM">122650</span>'
    )
    page = ibja.parse(html)
    assert page.today_pm == (133900, 122650)


def test_layout_change_is_reported(ibja_html):
    with pytest.raises(ibja.IbjaError, match="layout changed"):
        ibja.parse(ibja_html.replace("lblGold916_AM", "somethingElse"))


def test_partly_published_is_an_error(ibja_html):
    html = ibja_html.replace('id="lblGold999_PM"></span>', 'id="lblGold999_PM">133900</span>')
    with pytest.raises(ibja.IbjaError, match="partly"):
        ibja.parse(html)


def test_implausible_values_rejected(ibja_html):
    # 22K higher than 24K cannot be right
    with pytest.raises(ibja.IbjaError, match="ratio"):
        ibja.parse(ibja_html.replace(">123092<", ">153092<"))


def test_fetch_retries_then_gives_up(monkeypatch, cfg):
    import requests

    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(ibja.requests, "get", boom)
    with pytest.raises(ibja.IbjaError, match="unreachable"):
        ibja.fetch_html(cfg["source"], sleep=lambda s: None)
    assert len(calls) == len(cfg["source"]["network_retry_waits"]) + 1
