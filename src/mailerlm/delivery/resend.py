"""The Resend adapter: the only module that speaks HTTP to Resend.

One POST per message with an `Idempotency-Key`, so a retry or a resumed send never mails a person twice.
Only a 429 is retried (honouring `retry-after`) and each retry says so on stderr; a 5xx or any other
failure raises `SenderError` naming the status and key, never the message. `post`, `get` and `sleep`
are injectable so the rules are tested offline.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from mailerlm.delivery.sender import DeliveryEvent, OutgoingMessage, SenderError

KEY_ENV = "RESEND_API_KEY"
ENDPOINT = "https://api.resend.com"
MAX_ATTEMPTS = 4
RETRY_SLEEP_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 30.0
SECONDS_BETWEEN_MESSAGES = 0.6  # Resend allows two requests a second by default
EVENT_TO_STATUS = {
    "sent": "sent",
    "delivered": "delivered",
    "delivery_delayed": "delivery_delayed",
    "bounced": "bounced",
    "complained": "complained",
    "opened": "opened",
    "clicked": "clicked",
    "failed": "failed",
    "canceled": "failed",
    "scheduled": "sent",
    "queued": "sent",
}


def _reason(response: Any) -> str:
    """Resend's own explanation of a refusal (its `name` and `message`), never our message."""
    try:
        body = response.json()
        name, message = str(body.get("name", "")), str(body.get("message", ""))
    except (ValueError, AttributeError):
        return ""
    reason = " ".join(part for part in (name, message) if part)[:160]
    return f": {reason}" if reason else ""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get(KEY_ENV, '')}",
        "Content-Type": "application/json",
    }


class ResendSender:
    name = "resend"

    def __init__(
        self,
        post: Callable[..., Any] | None = None,
        get: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        import httpx

        self._post = post or httpx.post
        self._get = get or httpx.get
        self._sleep = sleep
        self._network_errors: tuple[type[BaseException], ...] = (httpx.HTTPError, OSError)

    def _body(self, message: OutgoingMessage) -> dict[str, Any]:
        body: dict[str, Any] = {
            "from": f"{message.sender_name} <{message.sender_address}>",
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
            "headers": {},
        }
        if message.reply_to:
            body["reply_to"] = message.reply_to
        if message.unsubscribe_mailto:
            body["headers"]["List-Unsubscribe"] = f"<{message.unsubscribe_mailto}>"
        if message.pictures:
            body["attachments"] = [
                {
                    "filename": p.filename,
                    "content": base64.b64encode(p.data).decode(),
                    "content_id": p.content_id,
                    "content_type": p.mime,
                }
                for p in message.pictures
            ]
        if message.tag:
            body["tags"] = [{"name": "campaign", "value": message.tag}]
        return body

    def _request(
        self, call: Callable[..., Any], url: str, label: str, **kwargs: Any
    ) -> dict[str, Any]:
        extra_headers = kwargs.pop("extra_headers", {})
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = call(
                    url,
                    headers={**_headers(), **extra_headers},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    **kwargs,
                )
            except self._network_errors as error:
                raise SenderError(f"resend: {type(error).__name__} for {label}") from error
            if response.status_code == 429:
                if attempt == MAX_ATTEMPTS:
                    raise SenderError(f"resend: HTTP 429 for {label}")
                header = str(response.headers.get("retry-after", ""))
                wait = (
                    float(header)
                    if header.replace(".", "", 1).isdigit()
                    else RETRY_SLEEP_SECONDS * attempt
                )
                print(
                    f"resend: HTTP 429 for {label}; retry {attempt} after {wait:g}s",
                    file=sys.stderr,
                )
                self._sleep(wait)
                continue
            if response.status_code != 200:
                raise SenderError(
                    f"resend: HTTP {response.status_code} for {label}{_reason(response)}"
                )
            try:
                return response.json()
            except ValueError as error:
                raise SenderError(f"resend: the reply for {label} is not JSON") from error
        raise SenderError(f"resend: no reply for {label}")

    def send(self, message: OutgoingMessage) -> str:
        label = message.idempotency_key or "message"
        extra = {"Idempotency-Key": message.idempotency_key} if message.idempotency_key else {}
        reply = self._request(
            self._post, f"{ENDPOINT}/emails", label, json=self._body(message), extra_headers=extra
        )
        provider_id = reply.get("id")
        if not isinstance(provider_id, str):
            raise SenderError(f"resend: the reply for {label} had no id")
        return provider_id

    def status(self, provider_id: str) -> DeliveryEvent:
        reply = self._request(self._get, f"{ENDPOINT}/emails/{provider_id}", provider_id)
        return DeliveryEvent(provider_id, EVENT_TO_STATUS.get(str(reply.get("last_event")), "sent"))
