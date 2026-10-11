"""Reads and writes of the campaigns tables; every function takes an open connection.

Validation lives here, so the command line and the web page enforce the same rules.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from mailerlm.errors import NotFoundError

Clock = Callable[[], datetime]
TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
BUILT_IN_TEMPLATE = "Plain letter"
SENDER_SETTINGS = ("sender_name", "sender_address", "reply_to", "postal_address")
RESERVED_VARIABLES = {"first_name", "sender_name"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")
VARIABLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CAMPAIGN_FIELDS = (
    "kind",
    "subject",
    "preview_text",
    "body",
    "sender_name",
    "sender_address",
    "reply_to",
    "postal_address",
    "track_clicks",
    "track_opens",
)


class CampaignsError(ValueError):
    """A request the campaigns area cannot satisfy; the message names the fix."""


def _stamp(now: Clock) -> str:
    return now().isoformat(timespec="seconds")


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not NAME.match(value.strip()):
        raise CampaignsError(
            f"{what} must be 1-60 letters, digits, spaces, dots, dashes or underscores"
        )
    return value.strip()


# ---- settings ----------------------------------------------------------------------------


def settings_get(connection: sqlite3.Connection) -> dict[str, str]:
    stored = dict(connection.execute("SELECT key, value FROM setting"))
    return {key: stored.get(key, "") for key in SENDER_SETTINGS}


def settings_set(connection: sqlite3.Connection, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in SENDER_SETTINGS or not isinstance(value, str):
            raise CampaignsError(f"unknown setting '{key}'; known: {', '.join(SENDER_SETTINGS)}")
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value.strip()),
        )


# ---- templates ---------------------------------------------------------------------------


def _built_in_source() -> tuple[str, str]:
    return (
        (TEMPLATE_DIRECTORY / "base.html.j2").read_text(),
        (TEMPLATE_DIRECTORY / "base.txt.j2").read_text(),
    )


def ensure_default_template(connection: sqlite3.Connection, now: Clock = datetime.now) -> None:
    if connection.execute("SELECT 1 FROM template WHERE built_in = 1").fetchone():
        return
    html, text = _built_in_source()
    connection.execute(
        "INSERT OR IGNORE INTO template (name, html, text, built_in, updated_at) VALUES (?, ?, ?, 1, ?)",
        (BUILT_IN_TEMPLATE, html, text, _stamp(now)),
    )


def template_list(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT t.name, t.built_in, t.updated_at, "
        "(SELECT COUNT(*) FROM campaign c WHERE c.template_id = t.id) FROM template t ORDER BY t.name"
    ).fetchall()
    return [
        {"name": n, "built_in": bool(b), "updated_at": u, "campaigns_using": c}
        for n, b, u, c in rows
    ]


def template_get(connection: sqlite3.Connection, name: str) -> dict:
    row = connection.execute(
        "SELECT name, html, text, built_in, updated_at FROM template WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no template named '{name}'")
    return {
        "name": row[0],
        "html": row[1],
        "text": row[2],
        "built_in": bool(row[3]),
        "updated_at": row[4],
    }


def template_save(
    connection: sqlite3.Connection, name: str, html: str, text: str, now: Clock = datetime.now
) -> None:
    name = _name(name, "a template name")
    if not isinstance(html, str) or not re.search(r"\{\{\s*body_html", html):
        raise CampaignsError(
            "the HTML frame must contain {{ body_html | safe }} where the letter goes"
        )
    if not isinstance(text, str) or not re.search(r"\{\{\s*body_text", text):
        raise CampaignsError("the text frame must contain {{ body_text }} where the letter goes")
    connection.execute(
        "INSERT INTO template (name, html, text, built_in, updated_at) VALUES (?, ?, ?, 0, ?) "
        "ON CONFLICT(name) DO UPDATE SET html = excluded.html, text = excluded.text, "
        "updated_at = excluded.updated_at",
        (name, html, text, _stamp(now)),
    )


def template_duplicate(connection: sqlite3.Connection, name: str, new_name: str) -> None:
    source = template_get(connection, name)
    if connection.execute(
        "SELECT 1 FROM template WHERE name = ?", (_name(new_name, "a template name"),)
    ).fetchone():
        raise CampaignsError(f"a template named '{new_name}' already exists")
    template_save(connection, new_name, source["html"], source["text"])


def template_reset(connection: sqlite3.Connection, name: str, now: Clock = datetime.now) -> None:
    if not template_get(connection, name)["built_in"]:
        raise CampaignsError("only the built-in template can be reset")
    html, text = _built_in_source()
    connection.execute(
        "UPDATE template SET html = ?, text = ?, updated_at = ? WHERE name = ?",
        (html, text, _stamp(now), name),
    )


def template_delete(connection: sqlite3.Connection, name: str) -> None:
    found = template_get(connection, name)
    if found["built_in"]:
        raise CampaignsError("the built-in template cannot be deleted; edit it or reset it")
    using = connection.execute(
        "SELECT COUNT(*) FROM campaign WHERE template_id = (SELECT id FROM template WHERE name = ?)",
        (name,),
    ).fetchone()[0]
    if using:
        raise CampaignsError(f"{using} campaign(s) use this template; switch them first")
    connection.execute("DELETE FROM template WHERE name = ?", (name,))


# ---- campaigns ---------------------------------------------------------------------------


def _campaign_id(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute("SELECT id FROM campaign WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise NotFoundError(f"no campaign named '{name}'")
    return row[0]


def _variables(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CampaignsError("variables must be name: value pairs")
    clean: dict[str, str] = {}
    for key, text in value.items():
        if not isinstance(key, str) or not VARIABLE.match(key) or key in RESERVED_VARIABLES:
            raise CampaignsError(
                f"variable name '{key}' must be lower-case letters, digits and underscores, "
                f"and not one of {', '.join(sorted(RESERVED_VARIABLES))}"
            )
        clean[key] = str(text)
    return clean


def campaign_create(
    connection: sqlite3.Connection,
    name: str,
    fields: dict,
    variables: dict | None = None,
    lists: list[str] | None = None,
    tags: list[str] | None = None,
    now: Clock = datetime.now,
) -> None:
    name = _name(name, "a campaign name")
    if connection.execute("SELECT 1 FROM campaign WHERE name = ?", (name,)).fetchone():
        raise CampaignsError(f"a campaign named '{name}' already exists")
    template_id = connection.execute("SELECT id FROM template WHERE built_in = 1").fetchone()[0]
    stamp = _stamp(now)
    connection.execute(
        "INSERT INTO campaign (name, template_id, variables, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, template_id, json.dumps(_variables(variables or {})), stamp, stamp),
    )
    campaign_save(connection, name, {**fields, "lists": lists or [], "tags": tags or []}, now=now)


def campaign_save(
    connection: sqlite3.Connection, name: str, fields: dict, now: Clock = datetime.now
) -> None:
    """Update the given fields only. `lists`, `tags`, `variables` and `template` are replaced whole."""
    identifier = _campaign_id(connection, name)
    sets, values = [], []
    for key, value in fields.items():
        if key in CAMPAIGN_FIELDS:
            if key == "kind" and value not in ("campaign", "confirmation"):
                raise CampaignsError("kind must be campaign or confirmation")
            if key in ("track_clicks", "track_opens"):
                value = int(bool(value))
            elif key in ("sender_name", "sender_address", "reply_to", "postal_address"):
                value = (str(value).strip() or None) if value is not None else None
            elif not isinstance(value, str):
                raise CampaignsError(f"{key} must be text")
            sets.append(f"{key} = ?")
            values.append(value)
        elif key == "variables":
            sets.append("variables = ?")
            values.append(json.dumps(_variables(value)))
        elif key == "template":
            row = connection.execute("SELECT id FROM template WHERE name = ?", (value,)).fetchone()
            if row is None:
                raise NotFoundError(f"no template named '{value}'")
            sets.append("template_id = ?")
            values.append(row[0])
        elif key == "lists":
            connection.execute("DELETE FROM campaign_list WHERE campaign_id = ?", (identifier,))
            for list_name in value:
                row = connection.execute(
                    "SELECT id FROM mailing_list WHERE name = ?", (list_name,)
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"no list named '{list_name}'")
                connection.execute(
                    "INSERT OR IGNORE INTO campaign_list VALUES (?, ?)", (identifier, row[0])
                )
        elif key == "tags":
            connection.execute("DELETE FROM campaign_tag WHERE campaign_id = ?", (identifier,))
            for tag in value:
                if not isinstance(tag, str) or not tag.strip():
                    raise CampaignsError("tags are non-empty words")
                connection.execute(
                    "INSERT OR IGNORE INTO campaign_tag VALUES (?, ?)", (identifier, tag.strip())
                )
        else:
            raise CampaignsError(f"unknown campaign field '{key}'")
    sets.append("updated_at = ?")
    values.append(_stamp(now))
    connection.execute(f"UPDATE campaign SET {', '.join(sets)} WHERE id = ?", (*values, identifier))


def campaign_get(connection: sqlite3.Connection, name: str) -> dict:
    identifier = _campaign_id(connection, name)
    row = connection.execute(
        "SELECT c.name, c.kind, c.subject, c.preview_text, c.body, t.name, c.sender_name, c.sender_address, "
        "c.reply_to, c.postal_address, c.track_clicks, c.track_opens, c.variables, c.created_at, c.updated_at "
        "FROM campaign c LEFT JOIN template t ON t.id = c.template_id WHERE c.id = ?",
        (identifier,),
    ).fetchone()
    keys = (
        "name",
        "kind",
        "subject",
        "preview_text",
        "body",
        "template",
        "sender_name",
        "sender_address",
        "reply_to",
        "postal_address",
        "track_clicks",
        "track_opens",
        "variables",
        "created_at",
        "updated_at",
    )
    campaign: dict[str, Any] = dict(zip(keys, row, strict=True))
    campaign["track_clicks"] = bool(campaign["track_clicks"])
    campaign["track_opens"] = bool(campaign["track_opens"])
    campaign["variables"] = json.loads(campaign["variables"])
    campaign["lists"] = [
        r[0]
        for r in connection.execute(
            "SELECT l.name FROM campaign_list cl JOIN mailing_list l ON l.id = cl.list_id "
            "WHERE cl.campaign_id = ? ORDER BY l.name",
            (identifier,),
        )
    ]
    campaign["tags"] = [
        r[0]
        for r in connection.execute(
            "SELECT name FROM campaign_tag WHERE campaign_id = ? ORDER BY name", (identifier,)
        )
    ]
    campaign["images"] = [
        {"filename": f, "sha256": s, "mime": m, "size": z}
        for f, s, m, z in connection.execute(
            "SELECT ci.filename, a.sha256, a.mime, a.size FROM campaign_image ci "
            "JOIN asset a ON a.sha256 = ci.sha256 WHERE ci.campaign_id = ? ORDER BY ci.filename",
            (identifier,),
        )
    ]
    return campaign


def campaign_list(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT id, name, kind, subject, updated_at FROM campaign ORDER BY updated_at DESC"
    ).fetchall()
    return [
        {
            "name": n,
            "kind": k,
            "subject": s,
            "updated_at": u,
            "lists": [
                r[0]
                for r in connection.execute(
                    "SELECT l.name FROM campaign_list cl JOIN mailing_list l ON l.id = cl.list_id "
                    "WHERE cl.campaign_id = ? ORDER BY l.name",
                    (i,),
                )
            ],
        }
        for i, n, k, s, u in rows
    ]


def campaign_delete(connection: sqlite3.Connection, name: str) -> None:
    identifier = _campaign_id(connection, name)
    connection.execute("DELETE FROM campaign WHERE id = ?", (identifier,))
    connection.execute("DELETE FROM asset WHERE sha256 NOT IN (SELECT sha256 FROM campaign_image)")


def campaign_duplicate(
    connection: sqlite3.Connection, name: str, new_name: str, now: Clock = datetime.now
) -> None:
    source = campaign_get(connection, name)
    fields = {k: source[k] for k in CAMPAIGN_FIELDS if k not in ("kind",)} | {
        "kind": source["kind"]
    }
    campaign_create(
        connection, new_name, fields, source["variables"], source["lists"], source["tags"], now
    )
    campaign_save(connection, new_name, {"template": source["template"]}, now)
    for image in source["images"]:
        connection.execute(
            "INSERT INTO campaign_image VALUES ((SELECT id FROM campaign WHERE name = ?), ?, ?)",
            (new_name, image["filename"], image["sha256"]),
        )


# ---- images ------------------------------------------------------------------------------


def image_add(
    connection: sqlite3.Connection,
    campaign_name: str,
    filename: str,
    data: bytes,
    now: Clock = datetime.now,
) -> dict:
    identifier = _campaign_id(connection, campaign_name)
    if not FILENAME.match(filename or ""):
        raise CampaignsError(
            "an image name is letters, digits, dots, dashes and underscores, like cover.png"
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise CampaignsError("an image can be at most 8 MB; it is shrunk for the email later")
    try:
        mime = Image.MIME.get(Image.open(io.BytesIO(data)).format or "", "")
    except Exception as error:  # Pillow raises many types for a bad file
        raise CampaignsError(
            f"that file is not an image Pillow can read ({type(error).__name__})"
        ) from error
    digest = hashlib.sha256(data).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO asset (sha256, mime, size, data, created_at) VALUES (?, ?, ?, ?, ?)",
        (digest, mime, len(data), data, _stamp(now)),
    )
    connection.execute(
        "INSERT INTO campaign_image (campaign_id, filename, sha256) VALUES (?, ?, ?) "
        "ON CONFLICT(campaign_id, filename) DO UPDATE SET sha256 = excluded.sha256",
        (identifier, filename, digest),
    )
    connection.execute("DELETE FROM asset WHERE sha256 NOT IN (SELECT sha256 FROM campaign_image)")
    return {"filename": filename, "sha256": digest, "mime": mime, "size": len(data)}


def image_remove(connection: sqlite3.Connection, campaign_name: str, filename: str) -> bool:
    identifier = _campaign_id(connection, campaign_name)
    removed = (
        connection.execute(
            "DELETE FROM campaign_image WHERE campaign_id = ? AND filename = ?",
            (identifier, filename),
        ).rowcount
        == 1
    )
    connection.execute("DELETE FROM asset WHERE sha256 NOT IN (SELECT sha256 FROM campaign_image)")
    return removed


def image_bytes(connection: sqlite3.Connection, campaign_name: str, filename: str) -> bytes | None:
    row = connection.execute(
        "SELECT a.data FROM campaign_image ci JOIN asset a ON a.sha256 = ci.sha256 "
        "WHERE ci.campaign_id = (SELECT id FROM campaign WHERE name = ?) AND ci.filename = ?",
        (campaign_name, filename),
    ).fetchone()
    return row[0] if row else None
