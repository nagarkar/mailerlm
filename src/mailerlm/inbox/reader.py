"""The `MailReader` port: find and read messages in the owner's mailbox, readonly; plus a fake for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RawMessage:
    message_id: str
    thread_id: str
    from_email: str
    subject: str
    received_at: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class MailReader(Protocol):
    def search(self, from_addresses: list[str], after: str, subjects: list[str]) -> list[str]:
        """Message ids from these senders, on or after `after` (YYYY/MM/DD), whose subject names one of `subjects`."""
        ...

    def fetch(self, message_id: str) -> RawMessage: ...


class FakeReader:
    def __init__(self, messages: list[RawMessage]):
        self.messages = {m.message_id: m for m in messages}
        self.searches: list[tuple[list[str], str, list[str]]] = []

    def search(self, from_addresses: list[str], after: str, subjects: list[str]) -> list[str]:
        self.searches.append((from_addresses, after, subjects))
        wanted = {a.lower() for a in from_addresses}
        return [i for i, m in self.messages.items() if m.from_email.lower() in wanted]

    def fetch(self, message_id: str) -> RawMessage:
        return self.messages[message_id]
