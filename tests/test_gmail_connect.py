"""gmail connect: its own client from .env, a readonly token stored with mode 600."""

import stat
from types import SimpleNamespace

import pytest

from mailerlm.inbox import gmail


def test_missing_client_names_the_settings():
    with pytest.raises(gmail.GmailNotConfigured, match="GMAIL_CLIENT_ID"):
        gmail.client_config({})


def test_connect_writes_a_private_readonly_token(tmp_path):
    seen = {}

    def factory(config):
        seen["id"] = config["installed"]["client_id"]
        return SimpleNamespace(
            run_local_server=lambda port: SimpleNamespace(to_json=lambda: '{"t": 1}')
        )

    path = gmail.connect(
        tmp_path / "ws", {"GMAIL_CLIENT_ID": "cid", "GMAIL_CLIENT_SECRET": "s"}, factory
    )
    assert seen["id"] == "cid"
    assert path.read_text() == '{"t": 1}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert gmail.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]
