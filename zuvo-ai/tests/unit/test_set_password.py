"""Operator password tool (services/migrator/set_password.py): validation and argv safety, no database needed."""
import importlib.util
import io
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "services" / "migrator" / "set_password.py"
_spec = importlib.util.spec_from_file_location("set_password", _PATH)
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


@pytest.mark.parametrize("pw, ok", [
    ("short", False),
    ("aaaaaaaaaaaaaaaa", False),          # too few distinct characters
    (" leading-space-pw", False),
    ("owner@example.com", False),         # the email itself
    ("OWNER", False),
    ("correct-horse-battery", True),
])
def test_check_password(pw, ok):
    assert (sp.check_password("owner@example.com", pw) is None) is ok


def test_password_in_argv_is_refused(capsys):
    assert sp.main(["set_password.py", "owner@example.com=Secret12345!"]) == 2
    assert sp.main(["set_password.py"]) == 2
    assert "Secret12345" not in capsys.readouterr().err


def test_weak_password_from_stdin_rejected_before_db(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("tooshort\n"))
    monkeypatch.setattr(sp, "apply", lambda *a: pytest.fail("must not touch the database"))
    assert sp.main(["set_password.py", "owner@example.com"]) == 2
    err = capsys.readouterr().err
    assert "at least" in err and "tooshort" not in err


def test_email_hash_is_stable_and_not_the_email():
    h = sp.email_sha(" Owner@Example.com ")
    assert h == sp.email_sha("owner@example.com") and "@" not in h and len(h) == 12
