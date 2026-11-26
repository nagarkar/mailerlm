"""Person drafts: one editable copy of a campaign's letter (subject and Markdown body) per person.

Drafts are made from the campaign with each person's values already filled in (their preferred
name and the campaign's values), so the owner reads and edits the letter that person will get.
Storage only: who gets a draft, and whether a draft may still be sent, is decided by the caller
(`mailerlm.api`), which knows the audience and the sends.

Remaking drafts (`sync`) adds a draft for each new person, rewrites a draft the owner has not
edited from the current campaign, keeps every edited draft, and removes an unedited draft whose
person is no longer in the audience; an edited draft of such a person is kept and reported, never
dropped silently. Drafts of people who were already mailed are never touched.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from mailerlm.campaigns.repository import CampaignsError, _campaign_id

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class Made:
    """What the campaign says to one person: the subject and body with their values filled in."""

    contact_id: int
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    added: tuple[int, ...]
    updated: tuple[int, ...]
    kept_edited: tuple[int, ...]
    removed: tuple[int, ...]
    kept_not_in_audience: tuple[int, ...]


def _stamp(now: Clock) -> str:
    return now().isoformat(timespec="seconds")


def sync(
    connection: sqlite3.Connection,
    campaign_name: str,
    made: Iterable[Made],
    locked: set[int],
    now: Clock = datetime.now,
) -> SyncResult:
    """Bring the campaign's drafts in line with `made` (one per person in the audience now);
    `locked` are contact ids already mailed this campaign, whose drafts are left as they are."""
    campaign_id = _campaign_id(connection, campaign_name)
    stamp = _stamp(now)
    existing = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT contact_id, subject, body, made_subject, made_body FROM person_draft "
            "WHERE campaign_id = ?",
            (campaign_id,),
        )
    }
    added: list[int] = []
    updated: list[int] = []
    kept: list[int] = []
    wanted: set[int] = set()
    for item in made:
        wanted.add(item.contact_id)
        if item.contact_id in locked:
            continue
        old = existing.get(item.contact_id)
        if old is None:
            connection.execute(
                "INSERT INTO person_draft (campaign_id, contact_id, subject, body, made_subject, "
                "made_body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, item.contact_id, item.subject, item.body, item.subject, item.body)
                + (stamp, stamp),
            )
            added.append(item.contact_id)
        elif (old[0], old[1]) != (old[2], old[3]):
            kept.append(item.contact_id)
        elif (old[0], old[1]) != (item.subject, item.body):
            connection.execute(
                "UPDATE person_draft SET subject = ?, body = ?, made_subject = ?, made_body = ?, "
                "updated_at = ? WHERE campaign_id = ? AND contact_id = ?",
                (item.subject, item.body, item.subject, item.body, stamp, campaign_id)
                + (item.contact_id,),
            )
            updated.append(item.contact_id)
    removed: list[int] = []
    kept_gone: list[int] = []
    for contact_id, (subject, body, made_subject, made_body) in existing.items():
        if contact_id in wanted or contact_id in locked:
            continue
        if (subject, body) != (made_subject, made_body):
            kept_gone.append(contact_id)
            continue
        connection.execute(
            "DELETE FROM person_draft WHERE campaign_id = ? AND contact_id = ?",
            (campaign_id, contact_id),
        )
        removed.append(contact_id)
    return SyncResult(tuple(added), tuple(updated), tuple(kept), tuple(removed), tuple(kept_gone))


def all_for(connection: sqlite3.Connection, campaign_name: str) -> list[dict]:
    """Every draft of the campaign with its person, ordered by name then address."""
    campaign_id = _campaign_id(connection, campaign_name)
    rows = connection.execute(
        "SELECT d.contact_id, c.name, c.preferred_name, c.email, d.subject, d.body, d.made_subject, "
        "d.made_body, d.updated_at FROM person_draft d JOIN contact c ON c.id = d.contact_id "
        "WHERE d.campaign_id = ? ORDER BY lower(COALESCE(NULLIF(c.name, ''), c.email)), c.email",
        (campaign_id,),
    ).fetchall()
    return [_draft(row) for row in rows]


def get(connection: sqlite3.Connection, campaign_name: str, contact_id: int) -> dict:
    campaign_id = _campaign_id(connection, campaign_name)
    row = connection.execute(
        "SELECT d.contact_id, c.name, c.preferred_name, c.email, d.subject, d.body, d.made_subject, "
        "d.made_body, d.updated_at FROM person_draft d JOIN contact c ON c.id = d.contact_id "
        "WHERE d.campaign_id = ? AND d.contact_id = ?",
        (campaign_id, contact_id),
    ).fetchone()
    if row is None:
        raise CampaignsError(
            f"no draft for contact {contact_id} in '{campaign_name}'. "
            f"Run: mailerlm drafts make {campaign_name}"
        )
    return _draft(row)


def _draft(row: tuple) -> dict:
    contact_id, name, preferred, email, subject, body, made_subject, made_body, updated = row
    return {
        "contact": contact_id,
        "name": name or "",
        "preferred_name": preferred or "",
        "email": email or "",
        "subject": subject,
        "body": body,
        "edited": (subject, body) != (made_subject, made_body),
        "updated_at": updated,
    }


def save(
    connection: sqlite3.Connection,
    campaign_name: str,
    contact_id: int,
    subject: str,
    body: str,
    now: Clock = datetime.now,
) -> dict:
    get(connection, campaign_name, contact_id)  # names the fix when there is no draft
    connection.execute(
        "UPDATE person_draft SET subject = ?, body = ?, updated_at = ? "
        "WHERE campaign_id = ? AND contact_id = ?",
        (subject, body, _stamp(now), _campaign_id(connection, campaign_name), contact_id),
    )
    return get(connection, campaign_name, contact_id)


def delete(connection: sqlite3.Connection, campaign_name: str, contact_id: int) -> None:
    get(connection, campaign_name, contact_id)
    connection.execute(
        "DELETE FROM person_draft WHERE campaign_id = ? AND contact_id = ?",
        (_campaign_id(connection, campaign_name), contact_id),
    )
