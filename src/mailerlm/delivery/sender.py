"""The `MailSender` port, the message it carries, and a fake for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class SenderError(RuntimeError):
    """The provider did not accept a message; the text never echoes the message."""


@dataclass(frozen=True, slots=True)
class InlinePicture:
    content_id: str
    filename: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    sender_name: str
    sender_address: str
    to: str
    subject: str
    html: str
    text: str
    reply_to: str
    unsubscribe_mailto: str
    pictures: tuple[InlinePicture, ...] = field(default_factory=tuple)
    idempotency_key: str = ""
    tag: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    provider_id: str
    status: str  # one of the delivery statuses in schema.py


class MailSender(Protocol):
    name: str

    def send(self, message: OutgoingMessage) -> str:
        """Hand the message to the provider; returns the provider's id for it."""
        ...

    def status(self, provider_id: str) -> DeliveryEvent:
        """The latest known event for a message already sent."""
        ...


class FakeSender:
    """Records what would be sent; nothing leaves the process."""

    name = "fake"

    def __init__(self, fail_for: set[str] | None = None, statuses: dict[str, str] | None = None):
        self.sent: list[OutgoingMessage] = []
        self.fail_for = fail_for or set()
        self.statuses = statuses or {}

    def send(self, message: OutgoingMessage) -> str:
        if message.to in self.fail_for:
            raise SenderError(
                f"the provider rejected the message (HTTP 422) for {message.idempotency_key}"
            )
        self.sent.append(message)
        return f"fake_{len(self.sent)}"

    def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id, self.statuses.get(provider_id, "delivered"))
