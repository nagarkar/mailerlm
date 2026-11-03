"""Reads and writes of the contacts tables; every function takes an open connection.

Who a contact is, which lists they are on and whether they may be mailed live in peoplelm; the
`contact` row is mailerlm's handle on a peoplelm person (`person_id`) and carries the tags and a
copy of the email and name for deliveries and greetings. Statuses other than `imported` are
written only by the consent area.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mailerlm.contacts.parsing import Candidate
from mailerlm.people.client import People

Clock = Callable[[], datetime]

# peoplelm's task state -> mailerlm's word. The consent area owns the mapping; contacts may not
# import it, so the words are repeated here and tests/test_people.py checks that they agree.
STATUS_WORDS = {
    "proposed": "imported",
    "requested": "pending",
    "accepted": "subscribed",
    "rejected": "unsubscribed",
}


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


def person_id(connection: sqlite3.Connection, contact_id: int | None) -> int | None:
    """The peoplelm person this contact is, or None when it was never linked."""
    if contact_id is None:
        return None
    row = connection.execute("SELECT person_id FROM contact WHERE id = ?", (contact_id,)).fetchone()
    return row[0] if row else None


def contact_for_person(
    connection: sqlite3.Connection,
    person: int,
    email: str | None,
    name: str = "",
    linkedin_url: str | None = None,
    now: Clock = datetime.now,
) -> int:
    """The local contact row for a peoplelm person, created when missing; peoplelm's email and
    name overwrite the local copy."""
    row = connection.execute(
        "SELECT id FROM contact WHERE person_id = ? ORDER BY id LIMIT 1", (person,)
    ).fetchone()
    if row is None and email:
        row = connection.execute(
            "SELECT id FROM contact WHERE email = ? AND person_id IS NULL", (email,)
        ).fetchone()
    if row is None and linkedin_url:
        row = connection.execute(
            "SELECT id FROM contact WHERE linkedin_url = ? AND person_id IS NULL", (linkedin_url,)
        ).fetchone()
    if row is None:
        if not email and not linkedin_url:
            raise ContactsError(f"peoplelm person {person} has no email or LinkedIn URL")
        cursor = connection.execute(
            "INSERT INTO contact (email, name, linkedin_url, created_at, person_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, name, linkedin_url, _stamp(now), person),
        )
        return int(cursor.lastrowid or 0)
    contact_id = row[0]
    connection.execute("UPDATE contact SET person_id = ? WHERE id = ?", (person, contact_id))
    with contextlib.suppress(sqlite3.IntegrityError):  # an old unlinked row holds that copy
        connection.execute(
            "UPDATE contact SET email = COALESCE(?, email), "
            "name = CASE WHEN ? = '' THEN name ELSE ? END, "
            "linkedin_url = COALESCE(linkedin_url, ?) WHERE id = ?",
            (email, name, name, linkedin_url, contact_id),
        )
    return contact_id


def primary_email(person: dict) -> str | None:
    emails = [
        i["value"]
        for i in person.get("identifiers", [])
        if i["channel"] == "email" and i["is_primary"]
    ]
    return emails[0] if emails else None


def add_tag(connection: sqlite3.Connection, contact_id: int, tag: str) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO tag (contact_id, name) VALUES (?, ?)", (contact_id, tag.strip())
    )
    return cursor.rowcount == 1


def import_candidates(
    connection: sqlite3.Connection,
    people: People,
    candidates: list[Candidate],
    list_name: str | None,
    tags: tuple[str, ...] = (),
    dry_run: bool = False,
    now: Clock = datetime.now,
) -> ImportCounts:
    """Each candidate is resolved in peoplelm (who they are) and joins the list there as a
    proposed mailing-list task; tags stay here. A dry run only reads peoplelm and counts. Local
    writes run inside the caller's transaction."""
    if list_name:
        list_id(connection, list_name)
    counts = {"added": 0, "already_present": 0, "memberships_added": 0, "tags_added": 0}
    for candidate in candidates:
        if dry_run:
            known = people.lookup_email(candidate.email) if candidate.email else None
            counts["added" if known is None else "already_present"] += 1
            if list_name and (
                known is None or people.find_task(int(known["id"]), list_name) is None
            ):
                counts["memberships_added"] += 1
            counts["tags_added"] += len({*tags, *candidate.tags})
            continue
        person, created = people.resolve(candidate.email, candidate.linkedin_url, candidate.name)
        counts["added" if created else "already_present"] += 1
        contact_id = contact_for_person(
            connection,
            int(person["id"]),
            primary_email(person) or candidate.email,
            person.get("display_name") or candidate.name,
            candidate.linkedin_url,
            now,
        )
        if list_name and people.propose(int(person["id"]), list_name)[1]:
            counts["memberships_added"] += 1
        for tag in (*tags, *candidate.tags):
            counts["tags_added"] += add_tag(connection, contact_id, tag)
    return ImportCounts(**counts)


def contact_by_ref(
    connection: sqlite3.Connection, people: People, ref: str | int
) -> tuple[int, int]:
    """A linked contact's id and peoplelm person id, from its contact id (an int, or digits as
    text) or from an email address peoplelm knows."""
    if isinstance(ref, int) or str(ref).isdigit():
        row = connection.execute(
            "SELECT id, person_id FROM contact WHERE id = ?", (int(ref),)
        ).fetchone()
        if row is None:
            raise ContactsNotFound("no such contact. Run: mailerlm contacts find")
        if row[1] is None:
            raise ContactsError("that contact is not linked to peoplelm. Run: mailerlm people link")
        return row[0], row[1]
    person = people.lookup_email(str(ref).strip().lower())
    if person is None:
        raise ContactsNotFound("no such contact. Run: mailerlm contacts find")
    contact_id = contact_for_person(
        connection, int(person["id"]), primary_email(person), person.get("display_name") or ""
    )
    return contact_id, int(person["id"])


def edit_contact(
    connection: sqlite3.Connection,
    people: People,
    contact_id: int,
    person: int,
    email: str | None,
    name: str,
    linkedin_url: str | None,
) -> None:
    """The name and primary email change in peoplelm; the local copy follows."""
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
    people.update_name(person, name.strip())
    if email:
        people.identify_email(person, email)


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


def remove_tag(connection: sqlite3.Connection, contact_id: int, tag: str) -> bool:
    return (
        connection.execute(
            "DELETE FROM tag WHERE contact_id = ? AND name = ?", (contact_id, tag.strip())
        ).rowcount
        == 1
    )


def _blocked(entry: dict) -> bool:
    return "email" in entry["blocked_channels"] or bool(entry["do_not_contact"])


def find(
    connection: sqlite3.Connection,
    people: People,
    list_name: str | None = None,
    tag: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Linked contacts matching every filter, each with its lists (peoplelm's tasks in mailerlm's
    words), tags and do-not-mail (peoplelm's block on email)."""
    members: dict[int, dict] = {}
    for entry in people.audience(list_name):
        word = STATUS_WORDS.get(entry["state"])
        if word is None:
            continue
        record = members.setdefault(
            int(entry["person_id"]), {"lists": {}, "do_not_mail": _blocked(entry)}
        )
        record["lists"][entry["scope"]] = word
    rows = connection.execute(
        "SELECT id, email, name, linkedin_url, person_id FROM contact "
        "WHERE person_id IS NOT NULL ORDER BY id"
    ).fetchall()
    out = []
    seen: set[int] = set()
    for contact_id, email, name, linkedin_url, person in rows:
        if person in seen:
            continue
        seen.add(person)
        member = members.get(person)
        if member is None:
            if list_name or status:
                continue
            record = people.get(person)
            member = {"lists": {}, "do_not_mail": _blocked(record)}
        if status and status not in member["lists"].values():
            continue
        tags = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM tag WHERE contact_id = ? ORDER BY name", (contact_id,)
            )
        ]
        if tag and tag not in tags:
            continue
        out.append(
            {
                "id": contact_id,
                "person_id": person,
                "email": email,
                "name": name,
                "linkedin_url": linkedin_url,
                "do_not_mail": bool(member["do_not_mail"]),
                "lists": dict(sorted(member["lists"].items())),
                "tags": tags,
            }
        )
    return out


def lists_summary(connection: sqlite3.Connection, people: People) -> list[dict]:
    rows = connection.execute(
        "SELECT name, sender_name, sender_address FROM mailing_list ORDER BY name"
    ).fetchall()
    out = []
    for name, sender_name, sender_address in rows:
        by_status: dict[str, int] = {}
        for entry in people.audience(name):
            word = STATUS_WORDS.get(entry["state"])
            if word:
                by_status[word] = by_status.get(word, 0) + 1
        out.append(
            {
                "name": name,
                "members": sum(by_status.values()),
                "by_status": by_status,
                "sender_name": sender_name or "",
                "sender_address": sender_address or "",
                "sender_override": bool(sender_name or sender_address),
            }
        )
    return out


def link(connection: sqlite3.Connection, mapping: dict[int, int]) -> dict[str, int]:
    """Record peoplelm's person id for each contact id in the mapping (the cutover)."""
    counts = {"linked": 0, "already_linked": 0, "unknown_contacts": 0}
    for contact_id, person in sorted(mapping.items()):
        row = connection.execute(
            "SELECT person_id FROM contact WHERE id = ?", (contact_id,)
        ).fetchone()
        if row is None:
            counts["unknown_contacts"] += 1
        elif row[0] == person:
            counts["already_linked"] += 1
        else:
            connection.execute(
                "UPDATE contact SET person_id = ? WHERE id = ?", (person, contact_id)
            )
            counts["linked"] += 1
    counts["not_linked"] = connection.execute(
        "SELECT COUNT(*) FROM contact WHERE person_id IS NULL"
    ).fetchone()[0]
    return counts
