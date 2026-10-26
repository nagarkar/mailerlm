"""Consent statuses and the transitions that change them, kept in peoplelm as mailing-list tasks.

mailerlm's words map onto peoplelm's task states (owner, 2026-10-25): `imported` is proposed,
`pending` is requested, `subscribed` is accepted, `unsubscribed` (and a bounce or complaint,
`suppressed`) is rejected. A task that left a list is dropped and shows no status. Each transition
returns what it changed, `{"list", "previous", "new"}` in mailerlm's words.
"""

from __future__ import annotations

from mailerlm.people import PRODUCER
from mailerlm.people.client import People

STATUSES = ("imported", "pending", "subscribed", "unsubscribed", "suppressed")
STATE_FOR_STATUS = {
    "imported": "proposed",
    "pending": "requested",
    "subscribed": "accepted",
    "unsubscribed": "rejected",
    "suppressed": "rejected",
}
STATUS_FOR_STATE = {
    "proposed": "imported",
    "requested": "pending",
    "accepted": "subscribed",
    "rejected": "unsubscribed",
}


def status_of(state: str) -> str | None:
    """mailerlm's word for a task state; None for a task that left the list (dropped)."""
    return STATUS_FOR_STATE.get(state)


def statuses(people: People, person_id: int) -> dict[str, str]:
    """List name -> status for every list this person is on."""
    out = {}
    for task in people.tasks_of(person_id):
        status = status_of(task["state"])
        if status:
            out[task["scope"]] = status
    return out


def _move(
    people: People,
    person_id: int,
    allowed_from: tuple[str, ...],
    new: str,
    by: str,
    note: str,
    lists: list[str] | None = None,
) -> list[dict]:
    changes = []
    target = STATE_FOR_STATUS[new]
    for task in people.tasks_of(person_id):
        previous = status_of(task["state"])
        if previous not in allowed_from or (lists is not None and task["scope"] not in lists):
            continue
        people.advance(int(task["id"]), target, by, note)
        changes.append({"list": task["scope"], "previous": previous, "new": new})
    return changes


def mark_pending(people: People, person_id: int, lists: list[str]) -> int:
    """A confirmation letter went out: `imported` on these lists becomes `pending` (the send guard
    already moved the list it was logged under)."""
    return len(
        _move(people, person_id, ("imported",), "pending", PRODUCER, "confirmation letter", lists)
    )


def confirm(people: People, person_id: int, by: str = "jev") -> list[dict]:
    """They said yes: every `pending` membership becomes `subscribed`."""
    return _move(people, person_id, ("pending",), "subscribed", by, "confirmed by reply")


def unsubscribe(people: People, person_id: int, by: str = "jev") -> list[dict]:
    """They asked to stop: every membership not already ended becomes `unsubscribed`."""
    return _move(
        people,
        person_id,
        ("imported", "pending", "subscribed"),
        "unsubscribed",
        by,
        "asked to stop by reply",
    )


def suppress(people: People, person_id: int, reason: str) -> list[dict]:
    """A bounce or complaint: email is blocked and every open membership is rejected, the reason in the note."""
    people.block_email(person_id, reason, PRODUCER)
    return _move(
        people,
        person_id,
        ("imported", "pending", "subscribed"),
        "suppressed",
        PRODUCER,
        reason,
    )


def leave(people: People, person_id: int, list_name: str, by: str = "owner") -> bool:
    """Take this person off one list: a proposed task is dropped, a later one rejected."""
    task = people.find_task(person_id, list_name)
    if task is None or task["state"] in ("dropped", "rejected"):
        return False
    if task["state"] == "proposed":
        people.move(int(task["id"]), "dropped", by, "left the list")
    else:
        people.move(int(task["id"]), "rejected", by, "left the list")
    return True
