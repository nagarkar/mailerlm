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


def credentials(workspace: Path) -> Any:
    """The stored token, refreshed when it has expired; `mailerlm gmail connect` makes it."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(workspace)
    if not path.exists():
        raise GmailNotConfigured(
            f"Gmail is not connected (no {path.name} in the workspace). Run: mailerlm gmail connect"
        )
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
        path.chmod(0o600)
    if not creds.valid:
        raise GmailNotConfigured("the Gmail token is not valid. Run: mailerlm gmail connect")
    return creds


class GmailReader:
    """The `MailReader` over the Gmail API, readonly. Only messages from the given senders are listed."""

    def __init__(self, creds: Any):
        from googleapiclient.discovery import build

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def search(self, from_addresses: list[str], after: str) -> list[str]:
        ids: list[str] = []
        for start in range(0, len(from_addresses), 20):
            chunk = from_addresses[start : start + 20]
            query = f"from:({' OR '.join(chunk)}) after:{after}"
            page = None
            while True:
                reply = (
                    self._service.users()
                    .messages()
                    .list(userId="me", q=query, pageToken=page, maxResults=100)
                    .execute()
                )
                ids += [m["id"] for m in reply.get("messages") or []]
                page = reply.get("nextPageToken")
                if not page:
                    break
        return list(dict.fromkeys(ids))

    def fetch(self, message_id: str) -> Any:
        import base64
        from email.utils import parseaddr, parsedate_to_datetime

        from mailerlm.inbox import text as text_tools
        from mailerlm.inbox.reader import RawMessage

        raw = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"] for h in (raw.get("payload") or {}).get("headers") or []
        }
        plain: list[str] = []
        html: list[str] = []

        def walk(part: dict) -> None:
            data = (part.get("body") or {}).get("data")
            if data and part.get("mimeType") == "text/plain":
                plain.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
            elif data and part.get("mimeType") == "text/html":
                html.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
            for child in part.get("parts") or []:
                walk(child)

        walk(raw.get("payload") or {})
        try:
            received = parsedate_to_datetime(headers["date"]).isoformat(timespec="seconds")
        except (KeyError, TypeError, ValueError):
            received = ""
        body = "\n".join(plain) or text_tools.html_to_text("\n".join(html))
        return RawMessage(
            message_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            from_email=parseaddr(headers.get("from", ""))[1].lower(),
            subject=headers.get("subject", ""),
            received_at=received,
            body=body,
            headers={
                k: headers[k]
                for k in ("auto-submitted", "precedence", "x-autoreply", "x-autorespond")
                if k in headers
            },
        )
