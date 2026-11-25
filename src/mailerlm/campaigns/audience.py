"""Who a campaign would reach: the people on its lists who may be mailed right now, and whether
that audience is a test list (then the subject says so) and whether it hides the footer."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

TEST_SUBJECT_PREFIX = "Testing: "


def is_test(connection: sqlite3.Connection, lists: tuple[str, ...]) -> bool:
    """True when the campaign names at least one list and every list it names is a test list.
    One real list among them makes it a real send, which must look exactly like one."""
    if not lists:
        return False
    marks = ",".join("?" for _ in lists)
    flags = [
        row[0]
        for row in connection.execute(
            f"SELECT is_test FROM mailing_list WHERE name IN ({marks})", lists
        )
    ]
    return bool(flags) and all(flags)


def footer_hidden(connection: sqlite3.Connection, lists: tuple[str, ...]) -> bool:
    """True when the campaign names at least one list and every list it names hides the footer.
    One list that shows it keeps the footer for everyone."""
    if not lists:
        return False
    marks = ",".join("?" for _ in lists)
    shown = [
        row[0]
        for row in connection.execute(
            f"SELECT show_footer FROM mailing_list WHERE name IN ({marks})", lists
        )
    ]
    return bool(shown) and not any(shown)


def test_subject(subject: str) -> str:
    """`subject` as a test list receives it: "Testing: " in front, once; an empty subject stays empty."""
    stripped = subject.strip()
    if not stripped or stripped.lower().startswith(TEST_SUBJECT_PREFIX.strip().lower()):
        return subject
    return TEST_SUBJECT_PREFIX + stripped


@dataclass(frozen=True, slots=True)
class Audience:
    emails: tuple[str, ...]
    contact_ids: tuple[int, ...] = ()
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


def resolve(
    connection: sqlite3.Connection, lists: tuple[str, ...], tags: tuple[str, ...], kind: str
) -> Audience:
    """A campaign reaches `subscribed` members; a confirmation reaches `imported` members.
    Do-not-mail contacts, contacts without an email and people lacking a tag are skipped."""
    wanted = "imported" if kind == "confirmation" else "subscribed"
    if not lists:
        return Audience((), (), {"campaign names no list": 1})
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
    ordered = sorted(emails.items(), key=lambda item: item[1])
    return Audience(tuple(e for _, e in ordered), tuple(i for i, _ in ordered), skipped)
