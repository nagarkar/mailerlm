"""One conversational edit of one template: build the call, run it, check the reply, update the draft.

The model sees only text: the system prompt (a versioned file), the conversation so far (each earlier request
and reply summary, in order), and the template as it stands now, wrapped in tags and called data. It has no
tools. Its reply must be one JSON object with exactly `html`, `text` and `summary`, and must pass the template
guard before the draft is touched; a reply that does not is recorded as rejected and changes nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from corpuslm import llm, prompts

from mailerlm.campaigns import repository as templates
from mailerlm.campaigns import template_guard
from mailerlm.errors import MailerlmError, NotFoundError

PROMPT_DIRECTORY = Path(__file__).parent.parent / "prompts"
REPLY_KEYS = {"html", "text", "summary"}
_in_flight: set[int] = set()
_in_flight_lock = threading.Lock()


class EditorError(MailerlmError):
    """The edit could not be attempted (not configured, over a limit, already running)."""


@dataclass(frozen=True, slots=True)
class Limits:
    model: str
    max_usd_per_day: float
    max_request_characters: int
    max_turns: int


def _stamp(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def _template_id(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute("SELECT id FROM template WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise NotFoundError(f"no template named '{name}'")
    return row[0]


def current_state(connection: sqlite3.Connection, template_id: int) -> tuple[str, str, bool]:
    """The draft if there is one, else the saved template: (html, text, is_draft)."""
    draft = connection.execute(
        "SELECT html, text FROM template_draft WHERE template_id = ?", (template_id,)
    ).fetchone()
    if draft:
        return draft[0], draft[1], True
    saved = connection.execute(
        "SELECT html, text FROM template WHERE id = ?", (template_id,)
    ).fetchone()
    return saved[0], saved[1], False


def save_draft(
    connection: sqlite3.Connection,
    name: str,
    html: str,
    text: str,
    now: Callable[[], datetime] = datetime.now,
) -> dict:
    """Store the one draft; a draft equal to the saved template is no draft. Returns the guard's problems."""
    template_id = _template_id(connection, name)
    saved = connection.execute(
        "SELECT html, text FROM template WHERE id = ?", (template_id,)
    ).fetchone()
    if (html, text) == (saved[0], saved[1]):
        connection.execute("DELETE FROM template_draft WHERE template_id = ?", (template_id,))
        return {"draft": False, "problems": []}
    connection.execute(
        "INSERT INTO template_draft (template_id, html, text, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(template_id) DO UPDATE SET html = excluded.html, text = excluded.text, updated_at = excluded.updated_at",
        (template_id, html, text, _stamp(now)),
    )
    return {"draft": True, "problems": template_guard.check(html, text)}


def discard_draft(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        "DELETE FROM template_draft WHERE template_id = ?", (_template_id(connection, name),)
    )


def publish(
    connection: sqlite3.Connection, name: str, now: Callable[[], datetime] = datetime.now
) -> None:
    """Make the draft the saved template. Only the owner's Save calls this; the guard must pass."""
    template_id = _template_id(connection, name)
    draft = connection.execute(
        "SELECT html, text FROM template_draft WHERE template_id = ?", (template_id,)
    ).fetchone()
    if draft is None:
        raise EditorError("there is no draft to save")
    problems = template_guard.check(draft[0], draft[1])
    if problems:
        raise EditorError("the draft cannot be saved: " + "; ".join(problems))
    templates.template_save(connection, name, draft[0], draft[1], now)
    connection.execute("DELETE FROM template_draft WHERE template_id = ?", (template_id,))


def thread(connection: sqlite3.Connection, name: str) -> list[dict]:
    rows = connection.execute(
        "SELECT id, role, status, content, model, tokens_in, tokens_out, cost_usd, created_at "
        "FROM template_message WHERE template_id = ? ORDER BY id",
        (_template_id(connection, name),),
    ).fetchall()
    keys = (
        "id",
        "role",
        "status",
        "content",
        "model",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "created_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def clear_thread(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        "DELETE FROM template_message WHERE template_id = ?", (_template_id(connection, name),)
    )


def spent_today(
    connection: sqlite3.Connection, now: Callable[[], datetime] = datetime.now
) -> float:
    day = now().date().isoformat()
    return float(
        connection.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM template_message WHERE created_at >= ?", (day,)
        ).fetchone()[0]
    )


def _history(rows: list[dict]) -> list[dict]:
    """The conversation as a chat client would resend it: each request, then each reply's summary."""
    messages: list[dict] = []
    for row in rows:
        if row["status"] == "failed":
            continue
        content = (
            row["content"]
            if row["role"] == "user"
            else (
                row["content"]
                if row["status"] == "ok"
                else f"I could not apply that: {row['content']}"
            )
        )
        messages.append({"role": row["role"], "content": content})
    return messages


def build_messages(
    system: str, history: list[dict], html: str, text: str, request: str
) -> list[dict]:
    current = (
        f"<template_html>\n{html}\n</template_html>\n<template_text>\n{text}\n</template_text>\n"
        f"<request>\n{request}\n</request>"
    )
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": current}]


def parse_reply(raw: str) -> dict[str, str]:
    """One JSON object with exactly html, text and summary, all strings; anything else is a rejection."""
    body = raw.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in body else body
    try:
        reply = json.loads(body)
    except ValueError as error:
        raise ValueError("the reply was not one JSON object") from error
    if (
        not isinstance(reply, dict)
        or set(reply) != REPLY_KEYS
        or not all(isinstance(v, str) for v in reply.values())
    ):
        raise ValueError("the reply did not have exactly the html, text and summary strings")
    return reply


def _record(
    connection: sqlite3.Connection,
    template_id: int,
    role: str,
    status: str,
    content: str,
    now: Callable[[], datetime],
    **meta: Any,
) -> None:
    connection.execute(
        "INSERT INTO template_message (template_id, role, status, content, model, prompt_sha256, tokens_in, tokens_out, "
        "cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            template_id,
            role,
            status,
            content,
            meta.get("model"),
            meta.get("prompt_sha256"),
            meta.get("tokens_in"),
            meta.get("tokens_out"),
            meta.get("cost_usd"),
            _stamp(now),
        ),
    )


def edit(
    connection_factory: Callable[[], sqlite3.Connection],
    name: str,
    request: str,
    limits: Limits,
    transport: llm.Transport,
    now: Callable[[], datetime] = datetime.now,
) -> dict:
    """Ask the model to change this template. The model call runs with no database transaction open; the
    draft changes only when the reply passes. Returns the thread entries it added and the new draft state."""
    request = request.strip()
    if not request:
        raise EditorError("type what you want changed")
    if len(request) > limits.max_request_characters:
        raise EditorError(
            f"a request is at most {limits.max_request_characters} characters (template_editor.max_request_characters)"
        )
    connection = connection_factory()
    try:
        template_id = _template_id(connection, name)
        rows = thread(connection, name)
        if sum(1 for r in rows if r["role"] == "user") >= limits.max_turns:
            raise EditorError(
                f"this conversation has {limits.max_turns} turns; start a new conversation (template_editor.max_turns)"
            )
        if spent_today(connection, now) >= limits.max_usd_per_day:
            raise EditorError(
                f"today's spend reached the cap of ${limits.max_usd_per_day:.2f} (template_editor.max_usd_per_day)"
            )
        html, text, _ = current_state(connection, template_id)
        prompt = prompts.load("template_editor", directories=[PROMPT_DIRECTORY])
        messages = build_messages(prompt.body.strip(), _history(rows), html, text, request)
    finally:
        connection.close()
    with _in_flight_lock:
        if template_id in _in_flight:
            raise EditorError("an edit of this template is already running")
        _in_flight.add(template_id)
    try:
        status, summary, meta, reply = (
            "ok",
            "",
            {"model": limits.model, "prompt_sha256": prompt.sha256},
            None,
        )
        try:
            raw = transport.send(
                limits.model,
                messages,
                {"max_tokens": 8000, "timeout": 60},
                tag=f"template-edit-{template_id}",
            )
            meta |= {
                "tokens_in": raw.tokens_in,
                "tokens_out": raw.tokens_out,
                "cost_usd": raw.cost_usd,
            }
            reply = parse_reply(raw.text)
            problems = template_guard.check(reply["html"], reply["text"])
            if problems:
                status, summary = "rejected", "; ".join(problems)
            else:
                summary = reply["summary"].strip()[:600] or "Done."
        except ValueError as error:
            status, summary = "rejected", str(error)
        except (llm.VendorError, llm.TruncatedReply) as error:
            status, summary = (
                "failed",
                f"the model call failed ({type(error).__name__}); nothing changed",
            )
        connection = connection_factory()
        try:
            connection.execute("BEGIN")
            _record(connection, template_id, "user", "ok", request, now)
            _record(connection, template_id, "assistant", status, summary, now, **meta)
            if status == "ok" and reply is not None:
                save_draft(connection, name, reply["html"], reply["text"], now)
            connection.execute("COMMIT")
            current = current_state(connection, template_id)
            added = thread(connection, name)[-2:]
        finally:
            connection.close()
        return {
            "status": status,
            "added": added,
            "draft": {"html": current[0], "text": current[1], "is_draft": current[2]},
            "cost_usd": meta.get("cost_usd"),
        }
    finally:
        with _in_flight_lock:
            _in_flight.discard(template_id)
