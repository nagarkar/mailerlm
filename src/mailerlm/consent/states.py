"""Consent statuses and the transitions that change them; each returns what it changed so it can be undone.

`imported` -> `pending` (a confirmation letter went out), `pending` -> `subscribed` (they confirmed),
`imported` or `pending` -> `subscribed` on one list (the owner vouched, with a reason), anything not yet
`unsubscribed` or `suppressed` -> `unsubscribed` (they asked to stop). Every change is also written to
`membership_event` with how it happened. A change is recorded
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
    how: str = "",
    confirmed_by: str | None = None,
    reason: str | None = None,
) -> list[dict]:
    marks = ",".join("?" for _ in allowed_from)
    rows = connection.execute(
        f"SELECT m.list_id, m.status, l.name FROM membership m JOIN mailing_list l ON l.id = m.list_id "
        f"WHERE m.contact_id = ? AND m.status IN ({marks})",
        (contact_id, *allowed_from),
    ).fetchall()
    changes = []
    for list_id, previous, list_name in rows:
        if list_ids is not None and list_id not in list_ids:
            continue
        stamp = _stamp(now)
        connection.execute(
            # How it was confirmed is kept (and read only while subscribed), so undoing an
            # unsubscribe restores it; a new confirmation overwrites it.
            "UPDATE membership SET status = ?, status_changed_at = ?, "
            "confirmed_by = COALESCE(?, confirmed_by), confirm_reason = CASE WHEN ? IS NULL "
            "THEN confirm_reason ELSE ? END WHERE contact_id = ? AND list_id = ?",
            (new, stamp, confirmed_by, confirmed_by, reason, contact_id, list_id),
        )
        connection.execute(
            "INSERT INTO membership_event (contact_id, list_id, list_name, previous, new, how, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (contact_id, list_id, list_name, previous, new, how, stamp),
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
        len(
            _move(
                connection, c, ("imported",), "pending", now, list_ids, "confirmation letter sent"
            )
        )
        for c in contact_ids
    )


def confirm(
    connection: sqlite3.Connection, contact_id: int, now: Callable[[], datetime] = datetime.now
) -> list[dict]:
    """They said yes: every `pending` membership becomes `subscribed`."""
    return _move(
        connection,
        contact_id,
        ("pending",),
        "subscribed",
        now,
        how="confirmed by reply",
        confirmed_by="reply",
    )


class NotConfirmable(ValueError):
    """The owner cannot vouch for this membership; the message says why."""


def confirm_by_owner(
    connection: sqlite3.Connection,
    contact_id: int,
    list_id: int,
    reason: str,
    now: Callable[[], datetime] = datetime.now,
) -> list[dict]:
    """The owner vouches for one membership (`imported` or `pending` -> `subscribed`) with a reason.
    Returns the change, or [] when it is already subscribed. Raises `NotConfirmable` for someone
    marked do-not-mail, unsubscribed or suppressed: those are their own choices."""
    reason = " ".join(reason.split())
    if not reason:
        raise NotConfirmable("a reason is required, e.g. 'known personally'")
    if len(reason) > 200:
        raise NotConfirmable("keep the reason under 200 characters")
    row = connection.execute(
        "SELECT m.status, c.do_not_mail FROM membership m JOIN contact c ON c.id = m.contact_id "
        "WHERE m.contact_id = ? AND m.list_id = ?",
        (contact_id, list_id),
    ).fetchone()
    if row is None:
        raise NotConfirmable("they are not on the list; add them first (mailerlm contacts join)")
    status, do_not_mail = row
    if do_not_mail:
        raise NotConfirmable("they are marked do-not-mail")
    if status == "unsubscribed":
        raise NotConfirmable("they unsubscribed")
    if status == "suppressed":
        raise NotConfirmable("they are suppressed (a bounce or complaint)")
    return _move(
        connection,
        contact_id,
        ("imported", "pending"),
        "subscribed",
        now,
        [list_id],
        f"confirmed by owner: {reason}",
        "owner",
        reason,
    )


def unsubscribe(
    connection: sqlite3.Connection, contact_id: int, now: Callable[[], datetime] = datetime.now
) -> list[dict]:
    """They asked to stop: every membership that is not already ended becomes `unsubscribed`."""
    return _move(
        connection,
        contact_id,
        ("imported", "pending", "subscribed"),
        "unsubscribed",
        now,
        how="asked to stop",
    )


def restore(
    connection: sqlite3.Connection,
    contact_id: int,
    changes: list[dict],
    now: Callable[[], datetime] = datetime.now,
) -> None:
    for change in changes:
        _move(
            connection,
            contact_id,
            (change["new"],),
            change["previous"],
            now,
            [change["list_id"]],
            "undone",
        )
