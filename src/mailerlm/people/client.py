"""The peoplelm port, its HTTP adapter and the typed client mailerlm calls.

`Transport.call(method, params)` returns peoplelm's `result`; a refused call (4xx) raises
`PeopleError` with peoplelm's message, and no answer at all (connection refused, timeout, 5xx)
raises `PeopleUnavailable`, which names `peoplelm doctor`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from mailerlm.errors import MailerlmError
from mailerlm.people import CHANNEL, DOCTOR, PRODUCER, PURPOSE


class PeopleError(MailerlmError):
    """peoplelm answered and declined the call; the message is peoplelm's."""


class PeopleUnavailable(MailerlmError):
    """peoplelm did not answer; nothing that depends on consent may go ahead."""


class Transport(Protocol):
    def call(self, method: str, params: dict[str, Any]) -> Any: ...


class HttpTransport:
    """POST {base}/api/peoplelm with `{method, params}` and the write-check headers."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.url = base_url.rstrip("/") + "/api/peoplelm"
        self.timeout = timeout

    def call(self, method: str, params: dict[str, Any]) -> Any:
        body = json.dumps({"method": method, "params": params}).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Peoplelm": "1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())["result"]
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get("error", "")
            except (ValueError, AttributeError):
                message = ""
            if error.code < 500:
                raise PeopleError(f"peoplelm: {message or error.code}") from error
            raise PeopleUnavailable(
                f"peoplelm failed ({error.code} {message}). {DOCTOR}"
            ) from error
        except (OSError, ValueError, KeyError) as error:
            raise PeopleUnavailable(f"peoplelm does not answer at {self.url}. {DOCTOR}") from error


class People:
    """The peoplelm calls mailerlm makes, each with mailerlm's purpose and channel filled in."""

    def __init__(self, transport: Transport):
        self.transport = transport

    def _call(self, method: str, **params: Any) -> Any:
        return self.transport.call(method, params)

    # persons
    def resolve(self, email: str | None, linkedin_url: str | None, name: str) -> tuple[dict, bool]:
        identifiers = [[c, v] for c, v in (("email", email), ("linkedin", linkedin_url)) if v]
        out = self._call("person_resolve", identifiers=identifiers, name=name, source=PRODUCER)
        return out["person"], bool(out["created"])

    def get(self, person_id: int) -> dict:
        return self._call("person_get", person_id=person_id)

    def lookup_email(self, email: str) -> dict | None:
        return self._call("person_lookup", channel=CHANNEL, value=email)

    def update_name(self, person_id: int, name: str) -> None:
        self._call("person_update", person_id=person_id, display_name=name)

    def identify_email(self, person_id: int, email: str) -> None:
        self._call(
            "person_identify", person_id=person_id, channel=CHANNEL, value=email, source=PRODUCER
        )
        self._call("person_primary", person_id=person_id, channel=CHANNEL, value=email)

    def block_email(self, person_id: int, reason: str, source: str = "owner") -> None:
        self._call(
            "person_block_channel",
            person_id=person_id,
            channel=CHANNEL,
            reason=reason,
            source=source,
        )

    def unblock_email(self, person_id: int) -> None:
        self._call("person_unblock_channel", person_id=person_id, channel=CHANNEL)

    # list memberships (mailing-list tasks)
    def propose(self, person_id: int, list_name: str, note: str = "") -> tuple[dict, bool]:
        out = self._call(
            "task_propose",
            person_id=person_id,
            purpose=PURPOSE,
            scope=list_name,
            by=PRODUCER,
            note=note,
        )
        return out["task"], bool(out["created"])

    def find_task(self, person_id: int, list_name: str) -> dict | None:
        return self._call("task_find", person_id=person_id, purpose=PURPOSE, scope=list_name)

    def move(self, task_id: int, to_state: str, by: str, note: str = "") -> dict:
        return self._call("task_move", task_id=task_id, to_state=to_state, by=by, note=note)

    def advance(self, task_id: int, to_state: str, by: str, note: str = "") -> dict:
        return self._call("task_advance", task_id=task_id, to_state=to_state, by=by, note=note)

    def tasks_of(self, person_id: int) -> list[dict]:
        """This person's mailing-list tasks (one per list)."""
        return [t for t in self.get(person_id).get("tasks", []) if t["purpose"] == PURPOSE]

    def audience(self, list_name: str | None = None, states: list[str] | None = None) -> list[dict]:
        return self._call("audience", purpose=PURPOSE, scope=list_name, states=states)

    def rename_list(self, old: str, new: str) -> None:
        self._call("scope_rename", purpose=PURPOSE, old=old, new=new)

    # the send guard and the touch log
    def check(self, person_id: int, list_name: str) -> dict:
        return self._call(
            "guard_check", person_id=person_id, purpose=PURPOSE, scope=list_name, channel=CHANNEL
        )

    def record_send(self, person_id: int, list_name: str, delivery_id: int, summary: str) -> dict:
        return self._call(
            "guard_send",
            person_id=person_id,
            channel=CHANNEL,
            producer=PRODUCER,
            summary=summary,
            purpose=PURPOSE,
            scope=list_name,
            ref=f"delivery:{delivery_id}",
        )

    def log_reply(self, person_id: int, gmail_id: str, summary: str = "") -> None:
        self._call(
            "touch_log",
            person_id=person_id,
            channel=CHANNEL,
            direction="in",
            kind="reply",
            producer=PRODUCER,
            summary=summary,
            ref=gmail_id,
        )
