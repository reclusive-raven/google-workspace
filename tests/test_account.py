"""Account enforcement policy in gws._assert_account (no network)."""
import types

import pytest

import gws
from gws_http import GwsError


def _args(**k):
    d = {"account": None, "yes": False}
    d.update(k)
    return types.SimpleNamespace(**d)


@pytest.fixture(autouse=True)
def _me(monkeypatch):
    monkeypatch.setattr(gws, "account_email", lambda creds=None: "me@x.com")


def test_account_flag_mismatch_is_exit_2():
    with pytest.raises(GwsError) as e:
        gws._assert_account(_args(account="other@x.com"), None, None, is_write=False)
    assert e.value.exit_code == 2


def test_stub_write_mismatch_blocks_without_yes():
    with pytest.raises(GwsError) as e:
        gws._assert_account(_args(), "owner@x.com", None, is_write=True)
    assert e.value.exit_code == 3


def test_stub_write_mismatch_allowed_with_yes():
    gws._assert_account(_args(yes=True), "owner@x.com", None, is_write=True)  # no raise


def test_stub_read_mismatch_only_warns(capsys):
    gws._assert_account(_args(), "owner@x.com", None, is_write=False)  # no raise
    assert "warning" in capsys.readouterr().err.lower()


def test_matching_identity_is_case_insensitive_ok():
    gws._assert_account(_args(account="ME@X.com"), "me@x.COM", None, is_write=True)  # no raise


def test_unknown_identity_is_permissive(monkeypatch):
    # if the email can't be determined (offline), don't manufacture a failure
    monkeypatch.setattr(gws, "account_email", lambda creds=None: None)
    gws._assert_account(_args(account="anyone@x.com"), "owner@x.com", None, is_write=True)  # no raise
