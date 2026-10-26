import pytest

from mailerlm import api
from mailerlm.people.fake import FakePeople


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """No test reads the owner's .env, workspace or keys: pointers go to a temp directory."""
    for key in (
        "MAILERLM_WORKSPACE",
        "RESEND_API_KEY",
        "TYPESAFE_API_KEY",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "PEOPLELM_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAILERLM_ENV", str(tmp_path / "env" / ".env"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # `.env` backups


@pytest.fixture(autouse=True)
def people(monkeypatch):
    """Every test talks to an in-memory peoplelm; nothing reaches the real service."""
    fake = FakePeople()
    monkeypatch.setattr(api, "people_for_tests", fake)
    return fake
