import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """No test reads the owner's .env, workspace or keys: pointers go to a temp directory."""
    for key in (
        "MAILERLM_WORKSPACE",
        "RESEND_API_KEY",
        "TYPESAFE_API_KEY",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAILERLM_ENV", str(tmp_path / "env" / ".env"))
