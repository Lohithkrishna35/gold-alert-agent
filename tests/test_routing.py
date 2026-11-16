"""The family must only ever receive price-drop alerts. Errors go to the admin alone."""

from goldbot.notifier.router import Router
from goldbot.notifier.senders import ConsoleSender


class FlakySender(ConsoleSender):
    def __init__(self, fail_names=(), fail_times=99):
        super().__init__()
        self.fail_names = set(fail_names)
        self.fail_times = fail_times
        self.attempts = {}

    def send(self, contact, text):
        self.attempts[contact.name] = self.attempts.get(contact.name, 0) + 1
        if contact.name in self.fail_names and self.attempts[contact.name] <= self.fail_times:
            return False
        self.sent.append((contact, text))
        return True


def _router(admin, family, sender):
    return Router(admin, family, sender, retry_waits=[0, 0, 0], gap_seconds=0, sleep=lambda s: None)


def test_admin_note_never_reaches_family(admin, family, capsys):
    s = ConsoleSender()
    _router(admin, family, s).send_admin("[Gold bot - admin] IBJA page down")
    assert [c.name for c, _ in s.sent] == ["Lohith"]


def test_drop_alert_reaches_everyone_admin_first(admin, family, capsys):
    s = ConsoleSender()
    missed = _router(admin, family, s).send_drop_alert("Gold Price Dropped")
    assert missed == []
    assert [c.name for c, _ in s.sent] == ["Lohith", "Amma", "Nanna"]


def test_retry_then_success(admin, family, capsys):
    s = FlakySender(fail_names={"Amma"}, fail_times=2)
    missed = _router(admin, family, s).send_drop_alert("x")
    assert missed == [] and s.attempts["Amma"] == 3


def test_missed_person_is_reported(admin, family, capsys):
    s = FlakySender(fail_names={"Nanna"})
    missed = _router(admin, family, s).send_drop_alert("x")
    assert missed == ["Nanna"] and s.attempts["Nanna"] == 4


def test_contact_repr_hides_number_and_key(admin):
    text = repr(admin) + str(admin)
    assert "9000000001" not in text and "key-admin" not in text


def test_logs_are_scrubbed():
    from goldbot.masking import register_secret, scrub

    register_secret("super-secret-key")
    out = scrub("sending to +919876543210 with super-secret-key at 2026-11-16 12:20:01")
    assert "9876543210" not in out and "super-secret-key" not in out
    assert "2026-11-16 12:20:01" in out
