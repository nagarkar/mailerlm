"""Read replies to list mail, classify each with Jev, and apply what is safe; queue the rest for the owner.

Only replies about a letter mailerlm really sent (the sender is someone it mailed AND the subject is that letter's) are searched, fetched, stored or sent to Jev. Each reply is read once (its Gmail id is
stored). An automatic reply is ignored by rule. Otherwise Jev picks one option from the reply and the letter it
answers; an unsubscribe applies at `unsubscribe_min_probability`, a confirmation at `confirm_min_probability` and
only for someone with a pending membership; everything else waits in the queue with Jev's suggestion beside it.
A TypeSafe failure stores nothing, so the reply is read again next time.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from mailerlm.consent import states
from mailerlm.inbox import text as text_tools
from mailerlm.inbox.classifier import Classifier, ClassifierError, Judgment
from mailerlm.inbox.reader import MailReader, RawMessage

AUTOMATIC_SENDER = re.compile(r"(mailer-daemon|postmaster|no-?reply|do-not-reply)@", re.I)
_PREFIX = re.compile(r"^\s*(re|fwd?)\s*:\s*", re.I)


def normalized_subject(subject: str) -> str:
    """A subject without Re:/Fwd: prefixes, lower-cased, single-spaced: how a reply is matched to our letter."""
    previous = None
    while previous != subject:
        previous, subject = subject, _PREFIX.sub("", subject)
    return " ".join(subject.lower().split())


def letter_subjects(connection: sqlite3.Connection) -> list[str]:
    """The subjects of letters we really sent (live sends); only replies about these are read."""
    return [
        r[0]
        for r in connection.execute(
            "SELECT DISTINCT subject FROM send WHERE kind = 'live' ORDER BY subject"
        )
    ]


def _stamp(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def watched_senders(connection: sqlite3.Connection) -> dict[str, int]:
    """Addresses of people we have mailed (a live send that was not refused) -> contact id."""
    rows = connection.execute(
        "SELECT DISTINCT d.email, d.contact_id FROM delivery d JOIN send s ON s.id = d.send_id "
        "WHERE s.kind = 'live' AND d.status != 'failed' AND d.contact_id IS NOT NULL"
    ).fetchall()
    return {email.lower(): contact_id for email, contact_id in rows}


def first_live_send_day(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT MIN(created_at) FROM send WHERE kind = 'live'").fetchone()
    if not row or not row[0]:
        return None
    return (datetime.fromisoformat(row[0]) - timedelta(days=1)).strftime("%Y/%m/%d")


def _is_automatic(message: RawMessage) -> bool:
    headers = message.headers
    return (
        headers.get("auto-submitted", "no").lower() != "no"
        or headers.get("precedence", "").lower() in ("bulk", "junk", "auto_reply")
        or "x-autoreply" in headers
        or "x-autorespond" in headers
        or bool(
            AUTOMATIC_SENDER.search(
                message.from_email + "@" if "@" not in message.from_email else message.from_email
            )
        )
    )


def _our_last_message(connection: sqlite3.Connection, contact_id: int) -> dict:
    row = connection.execute(
        "SELECT s.subject, s.excerpt, s.created_at, s.kind FROM delivery d JOIN send s ON s.id = d.send_id "
        "WHERE d.contact_id = ? AND s.kind = 'live' ORDER BY s.id DESC LIMIT 1",
        (contact_id,),
    ).fetchone()
    return (
        {"subject": row[0], "text": row[1], "sent_at": row[2]}
        if row
        else {"subject": "", "text": "", "sent_at": ""}
    )


def _statuses(connection: sqlite3.Connection, contact_id: int) -> set[str]:
    return {
        r[0]
        for r in connection.execute(
            "SELECT status FROM membership WHERE contact_id = ?", (contact_id,)
        )
    }


def _store(
    connection: sqlite3.Connection,
    message: RawMessage,
    contact_id: int,
    body: str,
    excerpt: str,
    judgment: Judgment | None,
    decision: str,
    decided_by: str,
    changes: list[dict],
    now: Callable[[], datetime],
) -> None:
    connection.execute(
        "INSERT INTO reply (gmail_id, thread_id, contact_id, from_email, subject, received_at, body, our_excerpt, choice, "
        "probability, confidence, decision, decided_by, changes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message.message_id,
            message.thread_id,
            contact_id,
            message.from_email,
            message.subject[:200],
            message.received_at,
            body,
            excerpt[:600],
            judgment.choice if judgment else None,
            judgment.probability(judgment.choice) if judgment else None,
            judgment.confidence if judgment else None,
            decision,
            decided_by,
            json.dumps(changes),
            _stamp(now),
        ),
    )


def check(
    connection: sqlite3.Connection,
    reader: MailReader,
    classifier: Classifier,
    model: str,
    unsubscribe_min: float,
    confirm_min: float,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, int]:
    """One pass over the mailbox. Returns counts: new, confirmed, unsubscribed, queued, ignored, errors, other_mail_skipped."""
    counts = {
        "new": 0,
        "confirmed": 0,
        "unsubscribed": 0,
        "queued": 0,
        "ignored": 0,
        "errors": 0,
        "other_mail_skipped": 0,
    }
    senders = watched_senders(connection)
    after = first_live_send_day(connection)
    if not senders or after is None:
        return counts
    known = {r[0] for r in connection.execute("SELECT gmail_id FROM reply")}
    subjects = letter_subjects(connection)
    ours = {normalized_subject(subject) for subject in subjects}
    for message_id in reader.search(sorted(senders), after, subjects):
        if message_id in known:
            continue
        message = reader.fetch(message_id)
        contact_id = senders.get(message.from_email)
        if contact_id is None:
            continue
        if normalized_subject(message.subject) not in ours:
            counts["other_mail_skipped"] += (
                1  # not about our letter: never stored, never sent to Jev
            )
            continue
        counts["new"] += 1
        body = text_tools.strip_quoted(message.body)
        last = _our_last_message(connection, contact_id)
        if _is_automatic(message):
            _store(
                connection,
                message,
                contact_id,
                body,
                last["text"],
                None,
                "ignored",
                "rule",
                [],
                now,
            )
            counts["ignored"] += 1
            continue
        state = {
            "our_last_message": last,
            "their_reply": {"subject": message.subject[:200], "text": body},
            "their_membership_statuses": sorted(_statuses(connection, contact_id)),
        }
        try:
            judgment = classifier.classify(state, model)
        except ClassifierError:
            counts["errors"] += 1
            counts["new"] -= 1
            continue
        connection.execute("BEGIN")
        decision, changes = "queued", []
        probability = judgment.probability(judgment.choice)
        if judgment.choice == "unsubscribe" and probability >= unsubscribe_min:
            changes = states.unsubscribe(connection, contact_id, now)
            decision = "unsubscribed" if changes else "queued"
        elif (
            judgment.choice == "confirm_subscription"
            and probability >= confirm_min
            and "pending" in _statuses(connection, contact_id)
        ):
            changes = states.confirm(connection, contact_id, now)
            decision = "confirmed" if changes else "queued"
        _store(
            connection,
            message,
            contact_id,
            body,
            last["text"],
            judgment,
            decision,
            "jev" if decision != "queued" else "jev",
            changes,
            now,
        )
        connection.execute("COMMIT")
        counts[decision] += 1
    return counts


def list_replies(
    connection: sqlite3.Connection, only_queue: bool = False, limit: int = 50
) -> list[dict]:
    where = "WHERE r.decision = 'queued'" if only_queue else ""
    rows = connection.execute(
        "SELECT r.id, r.subject, r.received_at, r.body, r.choice, r.probability, r.decision, r.decided_by, r.changes, "
        "c.name, c.email, r.contact_id FROM reply r LEFT JOIN contact c ON c.id = r.contact_id "
        f"{where} ORDER BY r.id DESC LIMIT {int(limit)}"
    ).fetchall()
    keys = (
        "id",
        "subject",
        "received_at",
        "body",
        "choice",
        "probability",
        "decision",
        "decided_by",
        "changes",
        "name",
        "email",
        "contact_id",
    )
    out = []
    for row in rows:
        item = dict(zip(keys, row, strict=True))
        item["changes"] = json.loads(item["changes"])
        out.append(item)
    return out


def _get(connection: sqlite3.Connection, reply_id: int) -> tuple[int, list[dict], str]:
    from mailerlm.errors import NotFoundError

    row = connection.execute(
        "SELECT contact_id, changes, decision FROM reply WHERE id = ?", (reply_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no reply number {reply_id}")
    return row[0], json.loads(row[1]), row[2]


def undo(
    connection: sqlite3.Connection, reply_id: int, now: Callable[[], datetime] = datetime.now
) -> None:
    """Put the memberships back as they were and return the reply to the queue."""
    contact_id, changes, _ = _get(connection, reply_id)
    if contact_id is not None and changes:
        states.restore(connection, contact_id, changes, now)
    connection.execute(
        "UPDATE reply SET decision = 'queued', decided_by = 'owner', changes = '[]' WHERE id = ?",
        (reply_id,),
    )


def decide(
    connection: sqlite3.Connection,
    reply_id: int,
    decision: str,
    now: Callable[[], datetime] = datetime.now,
) -> dict:
    """The owner's word: confirmed, unsubscribed or ignored. An earlier automatic decision is undone first."""
    from mailerlm.errors import MailerlmError

    if decision not in ("confirmed", "unsubscribed", "ignored"):
        raise MailerlmError("decide as confirmed, unsubscribed or ignored")
    contact_id, changes, previous = _get(connection, reply_id)
    if previous != "queued" and changes:
        undo(connection, reply_id, now)
    if contact_id is None and decision != "ignored":
        raise MailerlmError("this reply is from someone no longer in your contacts")
    moved: list[dict] = []
    if decision == "confirmed":
        moved = states.confirm(connection, contact_id, now)
    elif decision == "unsubscribed":
        moved = states.unsubscribe(connection, contact_id, now)
    connection.execute(
        "UPDATE reply SET decision = ?, decided_by = 'owner', changes = ? WHERE id = ?",
        (decision, json.dumps(moved), reply_id),
    )
    return {"moved": len(moved)}
