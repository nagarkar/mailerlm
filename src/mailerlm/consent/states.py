"""Consent statuses and the transitions that change them; each returns what it changed so it can be undone.

`imported` -> `pending` (a confirmation letter went out), `pending` -> `subscribed` (they confirmed),
anything not yet `unsubscribed` or `suppressed` -> `unsubscribed` (they asked to stop). A change is recorded
as `{"list_id", "previous", "new"}` and `restore` puts the previous statuses back.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

STATUSES = ("imported", "pending", "subscribed", "unsubscribed", "suppressed")


def _stamp(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def _move(
    connection: sqlite3.Connection,
    contact_id: int,
    allowed_from: tuple[str, ...],
    new: str,
    now: Callable[[], datetime],
    list_ids: list[int] | None = None,
) -> list[dict]:
    marks = ",".join("?" for _ in allowed_from)
    rows = connection.execute(
        f"SELECT list_id, status FROM membership WHERE contact_id = ? AND status IN ({marks})",
        (contact_id, *allowed_from),
    ).fetchall()
    changes = []
    for list_id, previous in rows:
        if list_ids is not None and list_id not in list_ids:
            continue
        connection.execute(
            "UPDATE membership SET status = ?, status_changed_at = ? WHERE contact_id = ? AND list_id = ?",
            (new, _stamp(now), contact_id, list_id),
        )
        changes.append({"list_id": list_id, "previous": previous, "new": new})
    return changes


def mark_pending(
    connection: sqlite3.Connection,
    contact_ids: list[int],
    list_ids: list[int],
    now: Callable[[], datetime] = datetime.now,
) -> int:
    """Move `imported` memberships of these contacts on these lists to `pending`; returns how many moved."""
    return sum(
        len(_move(connection, c, ("imported",), "pending", now, list_ids)) for c in contact_ids
    )


def confirm(
    connection: sqlite3.Connection, contact_id: int, now: Callable[[], datetime] = datetime.now
) -> list[dict]:
    """They said yes: every `pending` membership becomes `subscribed`."""
    return _move(connection, contact_id, ("pending",), "subscribed", now)


def unsubscribe(
    connection: sqlite3.Connection, contact_id: int, now: Callable[[], datetime] = datetime.now
) -> list[dict]:
    """They asked to stop: every membership that is not already ended becomes `unsubscribed`."""
    return _move(connection, contact_id, ("imported", "pending", "subscribed"), "unsubscribed", now)


def restore(
    connection: sqlite3.Connection,
    contact_id: int,
    changes: list[dict],
    now: Callable[[], datetime] = datetime.now,
) -> None:
    for change in changes:
        connection.execute(
            "UPDATE membership SET status = ?, status_changed_at = ? WHERE contact_id = ? AND list_id = ? AND status = ?",
            (change["previous"], _stamp(now), contact_id, change["list_id"], change["new"]),
        )
