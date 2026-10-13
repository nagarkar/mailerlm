"""Consent statuses and the transitions other areas may request.

Only `imported` -> `pending` is built here (a confirmation letter went out); the reply-driven
transitions arrive with the inbox work.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

STATUSES = ("imported", "pending", "subscribed", "unsubscribed", "suppressed")


def mark_pending(
    connection: sqlite3.Connection,
    contact_ids: list[int],
    list_ids: list[int],
    now: Callable[[], datetime] = datetime.now,
) -> int:
    """Move `imported` memberships of these contacts on these lists to `pending`; returns how many moved."""
    moved = 0
    stamp = now().isoformat(timespec="seconds")
    for contact_id in contact_ids:
        for list_id in list_ids:
            moved += connection.execute(
                "UPDATE membership SET status = 'pending', status_changed_at = ? "
                "WHERE contact_id = ? AND list_id = ? AND status = 'imported'",
                (stamp, contact_id, list_id),
            ).rowcount
    return moved
