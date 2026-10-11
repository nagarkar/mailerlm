"""Who a campaign would reach: the people on its lists who may be mailed right now."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Audience:
    emails: tuple[str, ...]
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


def resolve(
    connection: sqlite3.Connection, lists: tuple[str, ...], tags: tuple[str, ...], kind: str
) -> Audience:
    """A campaign reaches `subscribed` members; a confirmation reaches `imported` members.
    Do-not-mail contacts, contacts without an email and people lacking a tag are skipped."""
    wanted = "imported" if kind == "confirmation" else "subscribed"
    if not lists:
        return Audience((), {"campaign names no list": 1})
    marks = ",".join("?" for _ in lists)
    rows = connection.execute(
        "SELECT DISTINCT c.id, c.email, c.do_not_mail, m.status FROM contact c "
        "JOIN membership m ON m.contact_id = c.id JOIN mailing_list l ON l.id = m.list_id "
        f"WHERE l.name IN ({marks})",
        lists,
    ).fetchall()
    skipped: dict[str, int] = {}
    emails: dict[int, str] = {}
    for contact_id, email, do_not_mail, status in rows:
        reason = None
        if do_not_mail:
            reason = "do-not-mail"
        elif not email:
            reason = "no email address"
        elif status != wanted:
            reason = f"status {status}"
        elif tags and not all(
            connection.execute(
                "SELECT 1 FROM tag WHERE contact_id = ? AND name = ?", (contact_id, tag)
            ).fetchone()
            for tag in tags
        ):
            reason = "missing a tag"
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        else:
            emails[contact_id] = email
    return Audience(tuple(sorted(emails.values())), skipped)
