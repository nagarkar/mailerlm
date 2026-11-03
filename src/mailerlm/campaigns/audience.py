"""Who a campaign would reach: the people on its lists who may be mailed right now, read from peoplelm."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mailerlm.contacts import repository as contacts_repository
from mailerlm.people.client import People


@dataclass(frozen=True, slots=True)
class Audience:
    emails: tuple[str, ...]
    contact_ids: tuple[int, ...] = ()
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    person_ids: tuple[int, ...] = ()
    lists: tuple[str, ...] = ()  # the list each recipient is mailed for (the send guard's scope)
    names: tuple[str, ...] = ()


def resolve(
    connection: sqlite3.Connection,
    people: People,
    lists: tuple[str, ...],
    tags: tuple[str, ...],
    kind: str,
) -> Audience:
    """A campaign reaches members whose mailing-list task is accepted (subscribed); a confirmation
    reaches proposed (imported) ones. People whose email is blocked (do-not-mail) or who are
    do-not-contact, people without an email, people lacking a tag and people the send guard
    holds back are skipped and counted by reason."""
    wanted = "proposed" if kind == "confirmation" else "accepted"
    if not lists:
        return Audience((), (), {"campaign names no list": 1})
    skipped: dict[str, int] = {}
    chosen: dict[int, tuple[str, str, int, str]] = {}  # person -> (email, list, contact, name)
    refused: dict[int, str] = {}
    for list_name in lists:
        for entry in people.audience(list_name):
            person = int(entry["person_id"])
            if person in chosen:
                continue
            reason = None
            if entry["do_not_contact"]:
                reason = "do-not-contact"
            elif "email" in entry["blocked_channels"]:
                reason = "do-not-mail"
            elif not entry["email"]:
                reason = "no email address"
            elif entry["state"] != wanted:
                word = contacts_repository.STATUS_WORDS.get(entry["state"], entry["state"])
                reason = f"status {word}"
            if reason is None:
                contact_id = contacts_repository.contact_for_person(
                    connection, person, entry["email"], entry["name"] or ""
                )
                have = {
                    r[0]
                    for r in connection.execute(
                        "SELECT name FROM tag WHERE contact_id = ?", (contact_id,)
                    )
                }
                if tags and not set(tags) <= have:
                    reason = "missing a tag"
                else:
                    verdict = people.check(person, list_name)
                    if not verdict["allowed"]:
                        reason = "held by the send guard"
                    else:
                        chosen[person] = (
                            entry["email"],
                            list_name,
                            contact_id,
                            entry["name"] or "",
                        )
                        refused.pop(person, None)
                        continue
            refused.setdefault(person, reason)
    for reason in refused.values():
        skipped[reason] = skipped.get(reason, 0) + 1
    ordered = sorted(chosen.items(), key=lambda item: item[1][0])
    return Audience(
        tuple(v[0] for _, v in ordered),
        tuple(v[2] for _, v in ordered),
        skipped,
        tuple(p for p, _ in ordered),
        tuple(v[1] for _, v in ordered),
        tuple(v[3] for _, v in ordered),
    )
