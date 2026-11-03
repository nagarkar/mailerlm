"""Sends: the record of what was sent to whom, and the loop that sends it.

A send is planned (who, what, a fingerprint of both), recorded (one `delivery` row per person,
queued), then run: each queued row is handed to the `MailSender` with an idempotency key, and the row
becomes `sent` or `failed`. A run that stops half-way is resumed from the queued rows; the key keeps a
person from being mailed twice. Later `poll` moves rows to delivered, bounced and so on.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from mailerlm.consent import states as consent_states
from mailerlm.contacts import repository as contacts_repository
from mailerlm.delivery.sender import MailSender, OutgoingMessage, SenderError
from mailerlm.people.client import People, PeopleError, PeopleUnavailable

TERMINAL = {"bounced", "complained", "failed"}


@dataclass(frozen=True, slots=True)
class Recipient:
    contact_id: int | None
    email: str
    first_name: str
    list_name: str | None = None  # the list it is sent for: the send guard's scope


def _stamp(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def live_sends_today(
    connection: sqlite3.Connection, now: Callable[[], datetime] = datetime.now
) -> int:
    day = now().date().isoformat()
    return connection.execute(
        "SELECT COUNT(*) FROM send WHERE kind = 'live' AND created_at >= ?", (day,)
    ).fetchone()[0]


def already_sent(connection: sqlite3.Connection, campaign_name: str, fingerprint: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM send WHERE campaign_name = ? AND kind = 'live' AND fingerprint = ?",
            (campaign_name, fingerprint),
        ).fetchone()
    )


def create_send(
    connection: sqlite3.Connection,
    campaign_name: str,
    kind: str,
    subject: str,
    fingerprint: str,
    recipients: list[Recipient],
    now: Callable[[], datetime] = datetime.now,
    excerpt: str = "",
) -> int:
    campaign = connection.execute(
        "SELECT id FROM campaign WHERE name = ?", (campaign_name,)
    ).fetchone()
    stamp = _stamp(now)
    cursor = connection.execute(
        "INSERT INTO send (campaign_id, campaign_name, kind, status, subject, fingerprint, planned, created_at, excerpt) "
        "VALUES (?, ?, ?, 'sending', ?, ?, ?, ?, ?)",
        (
            campaign[0] if campaign else None,
            campaign_name,
            kind,
            subject,
            fingerprint,
            len(recipients),
            stamp,
            excerpt[:1500],
        ),
    )
    send_id = int(cursor.lastrowid or 0)
    for recipient in recipients:
        connection.execute(
            "INSERT INTO delivery (send_id, contact_id, email, status, updated_at, scope) "
            "VALUES (?, ?, ?, 'queued', ?, ?)",
            (send_id, recipient.contact_id, recipient.email, stamp, recipient.list_name),
        )
    return send_id


def _fail(
    connection: sqlite3.Connection, delivery_id: int, error: str, now: Callable[[], datetime]
) -> None:
    connection.execute(
        "UPDATE delivery SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (error[:300], _stamp(now), delivery_id),
    )


def run_send(
    connection: sqlite3.Connection,
    send_id: int,
    sender: MailSender,
    build_message: Callable[[Recipient, str], OutgoingMessage],
    people: People | None = None,
    summary: str = "",
    pause_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, int]:
    """Send every queued row of this send; returns the counts by status. Safe to call again.

    A row for a contact asks peoplelm's send guard first (a row it holds back becomes `failed`
    with the guard's reasons) and is logged with `guard_send` once the provider took it. When
    peoplelm does not answer the run stops with `PeopleUnavailable`, the rest still queued. A row
    without a contact is a test send to the owner's own address and asks no one."""
    connection.execute(
        "UPDATE send SET started_at = COALESCE(started_at, ?) WHERE id = ?", (_stamp(now), send_id)
    )
    rows = connection.execute(
        "SELECT id, contact_id, email, scope FROM delivery WHERE send_id = ? AND status = 'queued' ORDER BY id",
        (send_id,),
    ).fetchall()
    for position, (delivery_id, contact_id, email, scope) in enumerate(rows):
        person = contacts_repository.person_id(connection, contact_id)
        if contact_id is not None:
            if person is None or people is None or not scope:
                _fail(
                    connection, delivery_id, "not linked to peoplelm: run mailerlm people link", now
                )
                continue
            verdict = people.check(person, scope)
            if not verdict["allowed"]:
                _fail(
                    connection,
                    delivery_id,
                    "held by the send guard: " + "; ".join(verdict["reasons"]),
                    now,
                )
                continue
        first_name = _first_name(connection, contact_id)
        key = f"mailerlm-send-{send_id}-{email}"
        try:
            provider_id = sender.send(
                build_message(Recipient(contact_id, email, first_name, scope), key)
            )
        except SenderError as error:
            _fail(connection, delivery_id, str(error), now)
        else:
            connection.execute(
                "UPDATE delivery SET status = 'sent', provider_id = ?, error = NULL, updated_at = ? WHERE id = ?",
                (provider_id, _stamp(now), delivery_id),
            )
            if person is not None and people is not None and scope:
                try:
                    people.record_send(person, scope, delivery_id, summary)
                except (PeopleError, PeopleUnavailable) as error:
                    connection.execute(
                        "UPDATE delivery SET error = ? WHERE id = ?",
                        (f"sent; peoplelm did not log it: {error}"[:300], delivery_id),
                    )
                    if isinstance(error, PeopleUnavailable):
                        raise
        if pause_seconds and position < len(rows) - 1:
            sleep(pause_seconds)
    if not connection.execute(
        "SELECT 1 FROM delivery WHERE send_id = ? AND status = 'queued'", (send_id,)
    ).fetchone():
        connection.execute(
            "UPDATE send SET status = 'done', finished_at = ? WHERE id = ?", (_stamp(now), send_id)
        )
    return counts(connection, send_id)


def _first_name(connection: sqlite3.Connection, contact_id: int | None) -> str:
    if contact_id is None:
        return "there"
    row = connection.execute("SELECT name FROM contact WHERE id = ?", (contact_id,)).fetchone()
    name = (row[0] if row else "").strip()
    return name.split()[0] if name else "there"


def counts(connection: sqlite3.Connection, send_id: int) -> dict[str, int]:
    return dict(
        connection.execute(
            "SELECT status, COUNT(*) FROM delivery WHERE send_id = ? GROUP BY status", (send_id,)
        )
    )


def get_send(connection: sqlite3.Connection, send_id: int) -> dict:
    row = connection.execute(
        "SELECT id, campaign_name, kind, status, subject, planned, created_at, started_at, finished_at "
        "FROM send WHERE id = ?",
        (send_id,),
    ).fetchone()
    if row is None:
        from mailerlm.errors import NotFoundError

        raise NotFoundError(f"no send number {send_id}")
    keys = (
        "id",
        "campaign_name",
        "kind",
        "status",
        "subject",
        "planned",
        "created_at",
        "started_at",
        "finished_at",
    )
    return {**dict(zip(keys, row, strict=True)), "counts": counts(connection, send_id)}


def list_sends(
    connection: sqlite3.Connection, campaign_name: str | None = None, limit: int = 30
) -> list[dict]:
    where, parameters = ("WHERE campaign_name = ?", (campaign_name,)) if campaign_name else ("", ())
    ids = [
        r[0]
        for r in connection.execute(
            f"SELECT id FROM send {where} ORDER BY id DESC LIMIT {int(limit)}", parameters
        )
    ]
    return [get_send(connection, i) for i in ids]


def poll(
    connection: sqlite3.Connection,
    sender: MailSender,
    people: People,
    send_id: int | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, int]:
    """Ask the provider about every sent row not yet final; a bounce or complaint blocks the
    person's email in peoplelm and rejects their memberships (before the row changes, so a
    peoplelm outage leaves the row to be polled again)."""
    where = "AND send_id = ?" if send_id else ""
    rows = connection.execute(
        f"SELECT id, contact_id, provider_id, status FROM delivery WHERE provider_id IS NOT NULL "
        f"AND status NOT IN ('bounced', 'complained', 'failed') {where}",
        (send_id,) if send_id else (),
    ).fetchall()
    changed = {"updated": 0, "blocked": 0}
    for delivery_id, contact_id, provider_id, status in rows:
        event = sender.status(provider_id)
        if event.status == status:
            continue
        person = contacts_repository.person_id(connection, contact_id)
        if event.status in ("bounced", "complained") and person is not None:
            consent_states.suppress(
                people, person, f"{event.status} (mailerlm delivery {delivery_id})"
            )
            changed["blocked"] += 1
        connection.execute(
            "UPDATE delivery SET status = ?, updated_at = ? WHERE id = ?",
            (event.status, _stamp(now), delivery_id),
        )
        changed["updated"] += 1
    return changed
