"""Who a send would reach: the people on the chosen lists who may be mailed right now, each reached
through one list whose settings shape their letter (its footer, its "Testing: " prefix, its sender).

A person who would be mailed through two or more of the chosen lists is an overlap: the send stops
until the owner fixes it (see the decision in `mailerlm.campaigns`), so each person who is mailed is
reached through exactly one chosen list. A person on two chosen lists who would be mailed through at
most one of them (say pending on the other) is only a note."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mailerlm.errors import NotFoundError

TEST_SUBJECT_PREFIX = "Testing: "


def test_subject(subject: str) -> str:
    """`subject` as a test list receives it: "Testing: " in front, once; an empty subject stays empty."""
    stripped = subject.strip()
    if not stripped or stripped.lower().startswith(TEST_SUBJECT_PREFIX.strip().lower()):
        return subject
    return TEST_SUBJECT_PREFIX + stripped


@dataclass(frozen=True, slots=True)
class ListSettings:
    name: str
    is_test: bool
    show_footer: bool
    sender_name: str = ""
    sender_address: str = ""


@dataclass(frozen=True, slots=True)
class Reached:
    """One person and the list they are reached through, with that list's prefix and footer."""

    contact_id: int
    email: str
    list_name: str
    test_prefix: bool
    show_footer: bool


@dataclass(frozen=True, slots=True)
class Overlap:
    """A person on two or more of the chosen lists: `mailed_through` are the lists that would mail
    them, `on` every chosen list they belong to (with their status there)."""

    contact_id: int
    email: str
    mailed_through: tuple[str, ...]
    on: dict[str, str]


@dataclass(frozen=True, slots=True)
class ListReach:
    settings: ListSettings
    recipients: int
    skipped_by_reason: dict[str, int]


@dataclass(frozen=True, slots=True)
class SendAudience:
    lists: tuple[ListReach, ...]
    recipients: tuple[Reached, ...]
    overlaps: tuple[Overlap, ...] = ()

    @property
    def emails(self) -> tuple[str, ...]:
        return tuple(r.email for r in self.recipients)

    @property
    def contact_ids(self) -> tuple[int, ...]:
        return tuple(r.contact_id for r in self.recipients)

    @property
    def blocking(self) -> tuple[Overlap, ...]:
        """People who would be mailed through more than one chosen list."""
        return tuple(o for o in self.overlaps if len(o.mailed_through) > 1)

    @property
    def notes(self) -> tuple[Overlap, ...]:
        """People on several chosen lists who would be mailed through at most one of them."""
        return tuple(o for o in self.overlaps if len(o.mailed_through) <= 1)

    @property
    def skipped_by_reason(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for reach in self.lists:
            for reason, count in reach.skipped_by_reason.items():
                total[reason] = total.get(reason, 0) + count
        return total


def list_settings(connection: sqlite3.Connection, name: str) -> ListSettings:
    """A list's settings; an unknown name names the fix."""
    row = connection.execute(
        "SELECT name, is_test, show_footer, sender_name, sender_address FROM mailing_list WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no list named '{name}'. Run: mailerlm lists add {name}")
    return ListSettings(row[0], bool(row[1]), bool(row[2]), row[3] or "", row[4] or "")


def per_list(
    connection: sqlite3.Connection, lists: tuple[str, ...], tags: tuple[str, ...], kind: str
) -> SendAudience:
    """Everyone the chosen lists reach, each through the first chosen list that would mail them, with
    that list's settings; per list, how many it reaches and why others are skipped; and the people on
    more than one chosen list (see this module's docstring)."""
    settings = [list_settings(connection, name) for name in dict.fromkeys(lists)]
    status_on: dict[int, dict[str, str]] = {}
    emails: dict[int, str] = {}
    mailed_through: dict[int, list[str]] = {}
    reached: dict[int, Reached] = {}
    reaches: list[ListReach] = []
    for setting in settings:
        for contact_id, email, status in connection.execute(
            "SELECT c.id, c.email, m.status FROM membership m JOIN mailing_list l ON l.id = m.list_id "
            "JOIN contact c ON c.id = m.contact_id WHERE l.name = ?",
            (setting.name,),
        ):
            status_on.setdefault(contact_id, {})[setting.name] = status
            emails[contact_id] = email or ""
        found = resolve(connection, (setting.name,), tags, kind)
        for contact_id, email in zip(found.contact_ids, found.emails, strict=True):
            mailed_through.setdefault(contact_id, []).append(setting.name)
            reached.setdefault(
                contact_id,
                Reached(contact_id, email, setting.name, setting.is_test, setting.show_footer),
            )
        reaches.append(ListReach(setting, len(found.emails), found.skipped_by_reason))
    overlaps = tuple(
        Overlap(contact_id, emails[contact_id], tuple(mailed_through.get(contact_id, [])), on)
        for contact_id, on in sorted(status_on.items(), key=lambda item: emails[item[0]])
        if len(on) > 1
    )
    ordered = sorted(reached.values(), key=lambda r: r.email)
    return SendAudience(tuple(reaches), tuple(ordered), overlaps)


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
