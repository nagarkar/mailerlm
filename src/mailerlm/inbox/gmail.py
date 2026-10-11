"""Gmail connection: the owner's own account, readonly, connected once by the owner.

`connect` runs the OAuth consent in the browser and stores the token in the workspace with mode
600. mailerlm's own client comes from `.env` (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET), never
another repo's. The `flow_factory` argument lets tests run without a browser or the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "gmail_token.json"


class GmailNotConfigured(RuntimeError):
    """The OAuth client is missing from .env."""


def token_path(workspace: Path) -> Path:
    return workspace / TOKEN_FILE


def client_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source: Mapping[str, str] = os.environ if env is None else env
    cid = source.get("GMAIL_CLIENT_ID", "").strip()
    secret = source.get("GMAIL_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise GmailNotConfigured(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are not set in .env (config names: "
            ".env.example); create a Desktop-app OAuth client with the Gmail API enabled"
        )
    return {
        "installed": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def _installed_app_flow(config: dict[str, Any]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_config(config, SCOPES)


def connect(
    workspace: Path,
    env: Mapping[str, str] | None = None,
    flow_factory: Callable[[dict[str, Any]], Any] = _installed_app_flow,
) -> Path:
    """Run the consent flow (opens a browser) and write the token; returns its path."""
    flow = flow_factory(client_config(env))
    creds = flow.run_local_server(port=0)
    path = token_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)
    return path
