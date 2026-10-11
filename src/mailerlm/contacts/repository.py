"""Reads and writes of the contacts tables; every function takes an open connection.

Consent statuses other than `imported` are written only by the consent area; this module sets
`imported` when a contact joins a list.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mailerlm.contacts.parsing import Candidate

Clock = Callable[[], datetime]


def _stamp(now: Clock) -> str:
    return now().isoformat(timespec="seconds")


class ContactsError(ValueError):
    """A request the contacts area cannot satisfy; the message names the fix."""


class ContactsNotFound(ContactsError):
    """The named list or contact does not exist."""


@dataclass(frozen=True, slots=True)
class ImportCounts:
    added: int = 0
    already_present: int = 0
    memberships_added: int = 0
    tags_added: int = 0


def list_id(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute("SELECT id FROM mailing_list WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ContactsNotFound(f"no list named '{name}'. Run: mailerlm lists add {name}")
    return row[0]


def add_list(
    connection: sqlite3.Connection,
    name: str,
    sender_name: str | None,
    sender_address: str | None,
    now: Clock = datetime.now,
) -> bool:
    """Create a list; False when it already exists."""
    name = name.strip()
    if not name:
        raise ContactsError("a list needs a name. Run: mailerlm lists add <name>")
    cursor = connection.execute(
        "INSERT OR IGNORE INTO mailing_list (name, sender_name, sender_address, created_at) "
        "VALUES (?, ?, ?, ?)",
        (name, sender_name, sender_address, _stamp(now)),
    )
    return cursor.rowcount == 1


def find_contact_id(connection: sqlite3.Connection, candidate: Candidate) -> int | None:
    if candidate.email:
        row = connection.execute(
            "SELECT id FROM contact WHERE email = ?", (candidate.email,)
        ).fetchone()
        if row:
            return row[0]
    if candidate.linkedin_url:
        row = connection.execute(
            "SELECT id FROM contact WHERE linkedin_url = ?", (candidate.linkedin_url,)
        ).fetchone()
        if row:
            return row[0]
    return None


def upsert_contact(
    connection: sqlite3.Connection, candidate: Candidate, now: Clock = datetime.now
) -> tuple[int, bool]:
    """The contact's id and whether it was new. A match fills a blank name, email or LinkedIn
    URL and never overwrites what the owner already has."""
    existing = find_contact_id(connection, candidate)
    if existing is None:
        cursor = connection.execute(
            "INSERT INTO contact (email, name, linkedin_url, created_at) VALUES (?, ?, ?, ?)",
            (candidate.email, candidate.name, candidate.linkedin_url, _stamp(now)),
        )
        return int(cursor.lastrowid or 0), True
    connection.execute(
        "UPDATE contact SET name = CASE WHEN name = '' THEN ? ELSE name END, "
        "email = COALESCE(email, ?), linkedin_url = COALESCE(linkedin_url, ?) WHERE id = ?",
        (candidate.name, candidate.email, candidate.linkedin_url, existing),
    )
    return existing, False


def join_list(
    connection: sqlite3.Connection, contact_id: int, list_identifier: int, now: Clock = datetime.now
) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO membership (contact_id, list_id, status, status_changed_at) "
        "VALUES (?, ?, 'imported', ?)",
        (contact_id, list_identifier, _stamp(now)),
    )
    return cursor.rowcount == 1


def add_tag(connection: sqlite3.Connection, contact_id: int, tag: str) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO tag (contact_id, name) VALUES (?, ?)", (contact_id, tag.strip())
    )
    return cursor.rowcount == 1


def import_candidates(
    connection: sqlite3.Connection,
    candidates: list[Candidate],
    list_name: str | None,
    tags: tuple[str, ...] = (),
    now: Clock = datetime.now,
) -> ImportCounts:
    """Runs inside the caller's transaction, so everything is added or nothing is."""
    list_identifier = list_id(connection, list_name) if list_name else None
    counts = {"added": 0, "already_present": 0, "memberships_added": 0, "tags_added": 0}
    for candidate in candidates:
        contact_id, is_new = upsert_contact(connection, candidate, now)
        counts["added" if is_new else "already_present"] += 1
        if list_identifier is not None and join_list(connection, contact_id, list_identifier, now):
            counts["memberships_added"] += 1
        for tag in (*tags, *candidate.tags):
            counts["tags_added"] += add_tag(connection, contact_id, tag)
    return ImportCounts(**counts)


def contact_by_ref(connection: sqlite3.Connection, ref: str | int) -> int:
    """A contact's id from its email address, or from its id (an int, or digits as text)."""
    if isinstance(ref, int) or str(ref).isdigit():
        row = connection.execute("SELECT id FROM contact WHERE id = ?", (int(ref),)).fetchone()
    else:
        row = connection.execute(
            "SELECT id FROM contact WHERE email = ?", (str(ref).strip().lower(),)
        ).fetchone()
    if row is None:
        raise ContactsNotFound("no such contact. Run: mailerlm contacts find")
    return row[0]


def edit_contact(
    connection: sqlite3.Connection,
    contact_id: int,
    email: str | None,
    name: str,
    linkedin_url: str | None,
) -> None:
    email = (email or "").strip().lower() or None
    linkedin_url = (linkedin_url or "").strip() or None
    if not email and not linkedin_url:
        raise ContactsError("a contact needs an email or a LinkedIn URL")
    try:
        connection.execute(
            "UPDATE contact SET email = ?, name = ?, linkedin_url = ? WHERE id = ?",
            (email, name.strip(), linkedin_url, contact_id),
        )
    except sqlite3.IntegrityError as error:
        raise ContactsError("another contact already has that email or LinkedIn URL") from error


def delete_contact(connection: sqlite3.Connection, contact_id: int) -> None:
    connection.execute("DELETE FROM contact WHERE id = ?", (contact_id,))


def rename_list(
    connection: sqlite3.Connection,
    name: str,
    new_name: str,
    sender_name: str | None,
    sender_address: str | None,
) -> None:
    identifier = list_id(connection, name)
    new_name = new_name.strip()
    if not new_name:
        raise ContactsError("a list needs a name")
    try:
        connection.execute(
            "UPDATE mailing_list SET name = ?, sender_name = ?, sender_address = ? WHERE id = ?",
            (
                new_name,
                (sender_name or "").strip() or None,
                (sender_address or "").strip() or None,
                identifier,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise ContactsError(f"a list named '{new_name}' already exists") from error


def delete_list(connection: sqlite3.Connection, name: str) -> None:
    connection.execute("DELETE FROM mailing_list WHERE id = ?", (list_id(connection, name),))


def leave_list(connection: sqlite3.Connection, contact_id: int, list_identifier: int) -> bool:
    return (
        connection.execute(
            "DELETE FROM membership WHERE contact_id = ? AND list_id = ?",
            (contact_id, list_identifier),
        ).rowcount
        == 1
    )


def remove_tag(connection: sqlite3.Connection, contact_id: int, tag: str) -> bool:
    return (
        connection.execute(
            "DELETE FROM tag WHERE contact_id = ? AND name = ?", (contact_id, tag.strip())
        ).rowcount
        == 1
    )


def set_do_not_mail(connection: sqlite3.Connection, contact_id: int, value: bool) -> None:
    connection.execute("UPDATE contact SET do_not_mail = ? WHERE id = ?", (int(value), contact_id))


def find(
    connection: sqlite3.Connection,
    list_name: str | None = None,
    tag: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Contacts matching every given filter, each with its lists and tags."""
    clauses, parameters = [], []
    if list_name:
        clauses.append("m.list_id = (SELECT id FROM mailing_list WHERE name = ?)")
        parameters.append(list_name)
    if status:
        clauses.append("m.status = ?")
        parameters.append(status)
    if tag:
        clauses.append("EXISTS (SELECT 1 FROM tag t WHERE t.contact_id = c.id AND t.name = ?)")
        parameters.append(tag)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = connection.execute(
        "SELECT DISTINCT c.id, c.email, c.name, c.linkedin_url, c.do_not_mail FROM contact c "
        f"LEFT JOIN membership m ON m.contact_id = c.id {where} ORDER BY c.id",
        parameters,
    ).fetchall()
    out = []
    for contact_id, email, name, linkedin_url, do_not_mail in rows:
        memberships = connection.execute(
            "SELECT l.name, m.status FROM membership m JOIN mailing_list l ON l.id = m.list_id "
            "WHERE m.contact_id = ? ORDER BY l.name",
            (contact_id,),
        ).fetchall()
        tags = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM tag WHERE contact_id = ? ORDER BY name", (contact_id,)
            )
        ]
        out.append(
            {
                "id": contact_id,
                "email": email,
                "name": name,
                "linkedin_url": linkedin_url,
                "do_not_mail": bool(do_not_mail),
                "lists": {name_: status_ for name_, status_ in memberships},
                "tags": tags,
            }
        )
    return out


def lists_summary(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT l.name, l.sender_name, l.sender_address, "
        "(SELECT COUNT(*) FROM membership m WHERE m.list_id = l.id) FROM mailing_list l "
        "ORDER BY l.name"
    ).fetchall()
    out = []
    for name, sender_name, sender_address, members in rows:
        by_status = dict(
            connection.execute(
                "SELECT m.status, COUNT(*) FROM membership m JOIN mailing_list l ON l.id = m.list_id "
                "WHERE l.name = ? GROUP BY m.status",
                (name,),
            )
        )
        out.append(
            {
                "name": name,
                "members": members,
                "by_status": by_status,
                "sender_name": sender_name or "",
                "sender_address": sender_address or "",
                "sender_override": bool(sender_name or sender_address),
            }
        )
    return out
