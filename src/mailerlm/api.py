"""The typed API: one function per CLI verb; the CLI is a logic-free surface over it.

Every function returns a board (a plain dict of counts, paths and a `next` hint), never content.

Never imports: cli (enforced by the import contract "api below cli" in pyproject.toml).
"""

from __future__ import annotations

import base64
import contextlib
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from corpuslm import llm

from mailerlm import config, paths, store
from mailerlm.campaigns import audience, frontmatter, render, template_guard
from mailerlm.campaigns import repository as campaigns_repository
from mailerlm.consent import states as consent_states
from mailerlm.contacts import mask_email, parsing, repository
from mailerlm.delivery import resend, sending
from mailerlm.delivery.sender import InlinePicture, MailSender, OutgoingMessage, SenderError
from mailerlm.editor import service as editor_service
from mailerlm.errors import MailerlmError, NotFoundError
from mailerlm.inbox import classifier as inbox_classifier
from mailerlm.inbox import gmail
from mailerlm.inbox import service as inbox_service

Board = dict[str, Any]


def _managed_block(workspace_pointer: str) -> list[str]:
    return [paths.BLOCK_BEGIN, f"{paths.WORKSPACE_ENV}={workspace_pointer}", paths.BLOCK_END]


def _outside_block(lines: list[str]) -> list[str]:
    kept: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == paths.BLOCK_BEGIN:
            inside = True
        elif line.strip() == paths.BLOCK_END:
            inside = False
        elif not inside:
            kept.append(line)
    return kept


def setup(
    dry_run: bool = False,
    env_file: Path | None = None,
    workspace_pointer: str | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> Board:
    """Record the workspace pointer in `.env` (inside the managed block only), then create the
    workspace folder and the database. A second run changes nothing and says so."""
    env_file = env_file or paths.env_path()
    workspace_pointer = (
        workspace_pointer or os.environ.get(paths.WORKSPACE_ENV) or paths.DEFAULT_WORKSPACE
    )
    old_lines = env_file.read_text().splitlines() if env_file.exists() else []
    existing = paths.read_env_file_lines(old_lines)
    outside = paths.read_env_file_lines(_outside_block(old_lines))
    if paths.WORKSPACE_ENV in outside:
        pointer, new_lines = outside[paths.WORKSPACE_ENV], old_lines
        env_state = "pointer already set outside the managed block; left alone"
    else:
        pointer = existing.get(paths.WORKSPACE_ENV, workspace_pointer)
        block = _managed_block(pointer)
        stripped = _outside_block(old_lines)
        new_lines = [*stripped, *([""] if stripped and stripped[-1] else []), *block]
        env_state = "pointer written in the managed block"
    diff = list(
        difflib.unified_diff(old_lines, new_lines, str(env_file), str(env_file), lineterm="")
    )
    changed = new_lines != old_lines
    workspace = Path(pointer).expanduser()
    database = paths.database_path(workspace)
    board: Board = {
        "dry_run": dry_run,
        "env_file": str(env_file),
        "env": env_state if changed or "outside" in env_state else "unchanged",
        "env_diff": diff,
        "workspace": str(workspace),
        "database": str(database),
        "workspace_exists": workspace.exists(),
        "database_exists": database.exists(),
    }
    if dry_run:
        board["next"] = "Run: mailerlm setup (nothing was written)"
        return board
    if changed:
        if env_file.exists():
            backup = env_file.with_name(f"{env_file.name}.bak.{now():%Y%m%d-%H%M%S}")
            shutil.copy2(env_file, backup)
            board["backup"] = str(backup)
        env_file.write_text("\n".join(new_lines) + "\n")
    (workspace / "logs").mkdir(parents=True, exist_ok=True)
    store.initialize(database).close()
    board["workspace_exists"] = board["database_exists"] = True
    board["changed"] = changed
    board["next"] = "Run: mailerlm doctor"
    return board


def _check(name: str, state: str, detail: str, fix: str = "", required: bool = False) -> Board:
    return {"name": name, "state": state, "detail": detail, "fix": fix, "required": required}


def _web_app_health(port: int = 8782) -> dict | None:
    """The running web app's /health answer, or None when nothing answers."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def _newest_source_commit() -> datetime | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", "src"],
            cwd=paths.project_dir(),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return datetime.fromisoformat(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def doctor(
    config_file: Path | None = None,
    web_app_health: Callable[[], dict | None] = _web_app_health,
) -> Board:
    """Each check with its state, detail and fix; `ok` is false when a required check fails."""
    checks: list[Board] = []
    env_file = paths.env_path()
    chosen = f" (chosen by {paths.ENV_OVERRIDE})" if os.environ.get(paths.ENV_OVERRIDE) else ""
    checks.append(
        _check(
            "env file",
            "ok" if env_file.exists() else "missing",
            f"{env_file}{chosen}",
            "" if env_file.exists() else "Run: mailerlm setup",
        )
    )
    try:
        workspace = paths.workspace_path()
    except MailerlmError as error:
        checks.append(_check("workspace", "fail", str(error), "Run: mailerlm setup", True))
        workspace = None
    if workspace is not None:
        exists = workspace.is_dir()
        checks.append(
            _check(
                "workspace",
                "ok" if exists else "fail",
                str(workspace),
                "" if exists else "Run: mailerlm setup",
                True,
            )
        )
        database = paths.database_path(workspace)
        if database.exists():
            try:
                store.open_store(database).close()
                checks.append(
                    _check(
                        "database",
                        "ok",
                        f"{database} (version {store.version(database)})",
                        "",
                        True,
                    )
                )
            except MailerlmError as error:
                checks.append(_check("database", "fail", str(error), "", True))
        else:
            checks.append(
                _check(
                    "database", "fail", f"no database at {database}", "Run: mailerlm setup", True
                )
            )
    config_file = config_file or paths.config_path()
    if config_file.exists():
        try:
            config.load(config_file)
            checks.append(_check("config", "ok", str(config_file)))
        except MailerlmError as error:
            checks.append(_check("config", "fail", str(error), "Fix config.toml"))
    else:
        checks.append(
            _check(
                "config",
                "ok",
                "no config.toml; defaults apply (the file is optional: caps and thresholds only)",
            )
        )
    for key, what in (("RESEND_API_KEY", "sending"), ("TYPESAFE_API_KEY", "reply classifier")):
        present = bool(os.environ.get(key))
        checks.append(
            _check(
                key,
                "ok" if present else "missing",
                f"{what}: {'set' if present else 'not set'}",
                "" if present else f"Add {key} to .env",
            )
        )
    client = bool(os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_CLIENT_SECRET"))
    checks.append(
        _check(
            "Gmail client",
            "ok" if client else "missing",
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET",
            "" if client else "Add them to .env (see .env.example)",
        )
    )
    token = workspace is not None and gmail.token_path(workspace).exists()
    checks.append(
        _check(
            "Gmail token",
            "ok" if token else "missing",
            "readonly token in the workspace",
            "" if token else "Run: mailerlm gmail connect",
        )
    )
    health = web_app_health()
    restart = "launchctl kickstart -k gui/$(id -u)/com.nagarkar.mailerlm.serve"
    if health is None:
        checks.append(
            _check(
                "web app",
                "missing",
                "nothing answers on 127.0.0.1:8782",
                "Install the launch agent: see scripts/com.nagarkar.mailerlm.serve.plist",
            )
        )
    else:
        started = datetime.fromisoformat(health["started_at"])
        newest = _newest_source_commit()
        if newest is not None and started < newest:
            checks.append(_check("web app", "fail", "server is older than HEAD", f"Run: {restart}"))
        else:
            checks.append(_check("web app", "ok", f"running since {health['started_at']}"))
    ok = all(c["state"] == "ok" for c in checks if c["required"])
    return {
        "ok": ok,
        "checks": checks,
        "next": "Run: mailerlm help" if ok else "Fix the failed checks above",
    }


def gmail_connect() -> Board:
    """Open a browser for readonly Gmail consent and store the token in the workspace."""
    workspace = paths.workspace_path()
    try:
        path = gmail.connect(workspace, dict(os.environ))
    except gmail.GmailNotConfigured as error:
        raise MailerlmError(f"{error}. Add them to .env (see .env.example)") from error
    return {"token": str(path), "scope": gmail.SCOPES[0], "next": "Run: mailerlm doctor"}


# ---- shared helpers ----------------------------------------------------------------------


def _open(dry_run: bool = False) -> sqlite3.Connection:
    """The store; for a dry run an in-memory copy, so the change is real but nothing persists."""
    live = store.open_store(paths.database_path(paths.workspace_path()))
    campaigns_repository.ensure_default_template(live)
    if not dry_run:
        return live
    copy = sqlite3.connect(":memory:", isolation_level=None)
    live.backup(copy)
    live.close()
    copy.execute("PRAGMA foreign_keys = ON")
    return copy


@contextlib.contextmanager
def _transaction(dry_run: bool = False):
    """One connection, one transaction: all of a call's writes land or none do."""
    connection = _open(dry_run)
    connection.execute("BEGIN")
    try:
        yield connection
    except Exception:
        connection.execute("ROLLBACK")
        connection.close()
        raise
    connection.execute("COMMIT")
    connection.close()


def _guard(work: Callable[[], Board]) -> Board:
    try:
        return work()
    except repository.ContactsNotFound as error:
        raise NotFoundError(str(error)) from error
    except (repository.ContactsError, campaigns_repository.CampaignsError) as error:
        raise MailerlmError(str(error)) from error


def _counts(counts: repository.ImportCounts) -> Board:
    return {name: getattr(counts, name) for name in counts.__slots__}


# ---- contacts and lists ------------------------------------------------------------------


def lists_add(
    name: str,
    sender_name: str | None = None,
    sender_address: str | None = None,
    dry_run: bool = False,
) -> Board:
    """Create a list, optionally with its own sender (otherwise the defaults in Settings apply)."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            created = repository.add_list(connection, name, sender_name, sender_address)
        return {
            "dry_run": dry_run,
            "list": name,
            "created": created,
            "next": f"Run: mailerlm contacts import <file> --list {name}",
        }

    return _guard(work)


def lists_show() -> Board:
    with _transaction() as connection:
        summary = repository.lists_summary(connection)
    return {
        "lists": summary,
        "next": "Run: mailerlm lists add <name>"
        if not summary
        else "Run: mailerlm contacts find --list <name>",
    }


def lists_edit(
    name: str, new_name: str, sender_name: str | None = None, sender_address: str | None = None
) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            repository.rename_list(connection, name, new_name, sender_name, sender_address)
        return {"list": new_name, "next": "Run: mailerlm lists show"}

    return _guard(work)


def lists_delete(name: str) -> Board:
    """Delete a list and its memberships; the contacts themselves stay."""

    def work() -> Board:
        with _transaction() as connection:
            repository.delete_list(connection, name)
        return {"deleted": name, "next": "Run: mailerlm lists show"}

    return _guard(work)


def contacts_add(
    email: str | None = None,
    name: str = "",
    linkedin_url: str | None = None,
    list_name: str | None = None,
    tags: tuple[str, ...] = (),
    dry_run: bool = False,
) -> Board:
    """Add one contact (an email, a LinkedIn URL, or both) and optionally join a list."""

    def work() -> Board:
        text = " ".join(part for part in (name and f"{name}:", email, linkedin_url) if part)
        candidates, _ = parsing.parse(text)
        if not candidates:
            raise MailerlmError(
                "give an email or a LinkedIn profile URL. Run: mailerlm help contacts add"
            )
        with _transaction(dry_run) as connection:
            counts = repository.import_candidates(connection, candidates[:1], list_name, tags)
        return {"dry_run": dry_run, **_counts(counts), "next": "Run: mailerlm contacts find"}

    return _guard(work)


def contacts_import(
    source: str, list_name: str | None = None, tags: tuple[str, ...] = (), dry_run: bool = False
) -> Board:
    """Import pasted text or a CSV from a file (`-` reads standard input)."""

    def work() -> Board:
        text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
        return contacts_import_text(text, list_name, tags, dry_run)

    return _guard(work)


def contacts_import_text(
    text: str, list_name: str | None = None, tags: tuple[str, ...] = (), dry_run: bool = False
) -> Board:
    """Import pasted text or CSV given as text (the web page's form)."""

    def work() -> Board:
        candidates, rejected_lines = parsing.parse(text)
        with _transaction(dry_run) as connection:
            counts = repository.import_candidates(connection, candidates, list_name, tags)
        return {
            "dry_run": dry_run,
            "read": len(candidates),
            **_counts(counts),
            "rejected_line_numbers": rejected_lines,
            "next": "Run: mailerlm contacts find" + (f" --list {list_name}" if list_name else ""),
        }

    return _guard(work)


def contacts_find(
    list_name: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    show_full_addresses: bool = False,
) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            rows = repository.find(connection, list_name, tag, status)
        for row in rows:
            if not show_full_addresses:
                row["email"] = mask_email(row["email"])
                row["linkedin_url"] = row["linkedin_url"] and "(linkedin)"
        return {"count": len(rows), "contacts": rows, "next": "Run: mailerlm lists show"}

    return _guard(work)


def contacts_edit(
    contact: str | int, email: str | None, name: str = "", linkedin_url: str | None = None
) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            repository.edit_contact(
                connection,
                repository.contact_by_ref(connection, contact),
                email,
                name,
                linkedin_url,
            )
        return {"edited": True, "next": "Run: mailerlm contacts find"}

    return _guard(work)


def contacts_delete(contact: str | int) -> Board:
    """Remove a contact from every list and tag; the web page asks first."""

    def work() -> Board:
        with _transaction() as connection:
            repository.delete_contact(connection, repository.contact_by_ref(connection, contact))
        return {"deleted": True, "next": "Run: mailerlm contacts find"}

    return _guard(work)


def _change(
    contact: str | int, dry_run: bool, action: Callable[[sqlite3.Connection, int], bool], verb: str
) -> Board:
    def work() -> Board:
        with _transaction(dry_run) as connection:
            changed = action(connection, repository.contact_by_ref(connection, contact))
        return {"dry_run": dry_run, verb: changed, "next": "Run: mailerlm contacts find"}

    return _guard(work)


def contacts_tag(email: str | int, tag: str, dry_run: bool = False) -> Board:
    return _change(email, dry_run, lambda c, i: repository.add_tag(c, i, tag), "tag_added")


def contacts_untag(email: str | int, tag: str, dry_run: bool = False) -> Board:
    return _change(email, dry_run, lambda c, i: repository.remove_tag(c, i, tag), "tag_removed")


def contacts_join(email: str | int, list_name: str, dry_run: bool = False) -> Board:
    return _change(
        email,
        dry_run,
        lambda c, i: repository.join_list(c, i, repository.list_id(c, list_name)),
        "joined",
    )


def contacts_leave(email: str | int, list_name: str, dry_run: bool = False) -> Board:
    return _change(
        email,
        dry_run,
        lambda c, i: repository.leave_list(c, i, repository.list_id(c, list_name)),
        "left",
    )


def contacts_do_not_mail(email: str | int, clear: bool = False, dry_run: bool = False) -> Board:
    def action(connection: sqlite3.Connection, contact_id: int) -> bool:
        repository.set_do_not_mail(connection, contact_id, not clear)
        return True

    return _change(email, dry_run, action, "do_not_mail_cleared" if clear else "do_not_mail_set")


# ---- settings and templates --------------------------------------------------------------


def settings_get() -> Board:
    with _transaction() as connection:
        return {
            "settings": campaigns_repository.settings_get(connection),
            "next": "Set the sender and footer address on the Settings page",
        }


def settings_save(
    sender_name: str = "", sender_address: str = "", reply_to: str = "", postal_address: str = ""
) -> Board:
    """The defaults every campaign uses unless it sets its own."""

    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.settings_set(
                connection,
                {
                    "sender_name": sender_name,
                    "sender_address": sender_address,
                    "reply_to": reply_to,
                    "postal_address": postal_address,
                },
            )
        return {"saved": True, "next": "Run: mailerlm doctor"}

    return _guard(work)


def templates_list() -> Board:
    with _transaction() as connection:
        return {"templates": campaigns_repository.template_list(connection)}


editor_transport_for_tests: llm.Transport | None = None  # tests set this; a real run leaves it None


def _editor_state(connection: sqlite3.Connection) -> dict:
    """Whether the editor can run, and why not."""
    section = config.load(paths.config_path()).section("template_editor")
    if section is None:
        return {
            "enabled": False,
            "model": None,
            "reason": "the editor is off: add a [template_editor] section to config.toml (see config.example.toml)",
        }
    key_env = llm.vendor_key_env(section["model"])
    if editor_transport_for_tests is None and key_env and not os.environ.get(key_env):
        return {
            "enabled": False,
            "model": section["model"],
            "reason": f"{key_env} is not set in .env",
        }
    return {
        "enabled": True,
        "model": section["model"],
        "reason": "",
        "spent_today_usd": round(editor_service.spent_today(connection), 4),
        "max_usd_per_day": section["max_usd_per_day"],
        "max_request_characters": section["max_request_characters"],
        "max_turns": section["max_turns"],
    }


def templates_get(name: str) -> Board:
    """A template's saved version, its draft (if any), its thread and whether the editor can run."""

    def work() -> Board:
        with _transaction() as connection:
            saved = campaigns_repository.template_get(connection, name)
            html, text, is_draft = editor_service.current_state(
                connection, editor_service._template_id(connection, name)
            )
            return {
                "template": saved,
                "draft": {"html": html, "text": text, "problems": template_guard.check(html, text)}
                if is_draft
                else None,
                "thread": editor_service.thread(connection, name),
                "editor": _editor_state(connection),
            }

    return _guard(work)


def templates_draft_save(name: str, html: str, text: str) -> Board:
    """Store the owner's typing as the one draft (no draft remains when it equals the saved version)."""

    def work() -> Board:
        with _transaction() as connection:
            return editor_service.save_draft(connection, name, html, text)

    return _guard(work)


def templates_draft_discard(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            editor_service.discard_draft(connection, name)
        return {"discarded": name}

    return _guard(work)


def templates_publish(name: str) -> Board:
    """Save: the draft becomes the saved template. The owner's act; the model cannot reach it."""

    def work() -> Board:
        with _transaction() as connection:
            editor_service.publish(connection, name)
        return {"saved": name}

    return _guard(work)


def templates_ai_edit(name: str, request: str) -> Board:
    """Ask the model to change this template; the reply goes into the draft only if the guard accepts it."""

    def work() -> Board:
        section = config.load(paths.config_path()).section("template_editor")
        with _transaction() as connection:
            state = _editor_state(connection)
        if section is None or not state["enabled"]:
            raise MailerlmError(state["reason"])
        limits = editor_service.Limits(
            section["model"],
            section["max_usd_per_day"],
            section["max_request_characters"],
            section["max_turns"],
        )
        transport = editor_transport_for_tests or llm.LiteLLMTransport()
        return editor_service.edit(_open, name, request, limits, transport)

    return _guard(work)


def templates_thread_clear(name: str) -> Board:
    """Start a new conversation about this template (the draft stays)."""

    def work() -> Board:
        with _transaction() as connection:
            editor_service.clear_thread(connection, name)
        return {"cleared": name}

    return _guard(work)


SAMPLE_LETTER = """Hi {{ first_name }},

This is a sample letter, so you can see how the template looks without a campaign.

## A heading

A paragraph with **bold**, *italic* and a [link](https://example.com).

- First point
- Second point

| Chapter | Status |
|---|---|
| One | Draft |

> A short quotation sits here.

{{ sender_name }}
"""


def templates_preview(html: str, text: str) -> Board:
    """Render a template (as typed, unsaved) around a built-in sample letter."""

    def work() -> Board:
        with _transaction() as connection:
            defaults = campaigns_repository.settings_get(connection)
        try:
            email = render.render(
                subject="A sample subject",
                body=SAMPLE_LETTER,
                preview_text="A sample preview line",
                frame_html=html,
                frame_text=text,
                variables={},
                sender_name=defaults["sender_name"] or "Your Name",
                postal_address=defaults["postal_address"],
                reply_to=defaults["reply_to"],
                resolve_image=lambda _name: None,
                image_budget_bytes=0,
                first_name="Friend",
            )
        except render.RenderError as error:
            raise MailerlmError(str(error)) from error
        return {"html": email.html, "text": email.text, "warnings": list(email.warnings)}

    return _guard(work)


def templates_duplicate(name: str, new_name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.template_duplicate(connection, name, new_name)
        return {"created": new_name}

    return _guard(work)


def templates_reset(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.template_reset(connection, name)
            editor_service.discard_draft(connection, name)
        return {"reset": name}

    return _guard(work)


def templates_delete(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.template_delete(connection, name)
        return {"deleted": name}

    return _guard(work)


# ---- campaigns ---------------------------------------------------------------------------

STARTER_DIRECTORY = Path(__file__).parent / "campaigns" / "starters"


def campaigns_starters() -> Board:
    return {"starters": sorted(p.stem for p in STARTER_DIRECTORY.glob("*.md"))}


def campaigns_list() -> Board:
    with _transaction() as connection:
        campaigns = campaigns_repository.campaign_list(connection)
    return {
        "campaigns": campaigns,
        "starters": campaigns_starters()["starters"],
        "next": "Run: mailerlm campaigns new <name> --starter update"
        if not campaigns
        else "Run: mailerlm campaigns check <name>",
    }


def campaigns_get(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            return {"campaign": campaigns_repository.campaign_get(connection, name)}

    return _guard(work)


def campaigns_create(name: str, starter: str = "update", dry_run: bool = False) -> Board:
    """Create a campaign in the database from a starter (the starter's text, values to fill in)."""

    def work() -> Board:
        source = STARTER_DIRECTORY / f"{starter}.md"
        if not source.exists():
            names = ", ".join(sorted(p.stem for p in STARTER_DIRECTORY.glob("*.md")))
            raise MailerlmError(f"no starter '{starter}'. Choose one of: {names}")
        parsed = frontmatter.parse(source.read_text())
        with _transaction(dry_run) as connection:
            existing = {r[0] for r in connection.execute("SELECT name FROM mailing_list")}
            campaigns_repository.campaign_create(
                connection,
                name,
                {
                    "kind": parsed.kind,
                    "subject": parsed.subject,
                    "preview_text": parsed.preview_text,
                    "body": parsed.body,
                    "track_clicks": parsed.track_clicks,
                    "track_opens": parsed.track_opens,
                },
                variables={"book_title": ""},
                lists=[n for n in parsed.lists if n in existing],
                tags=list(parsed.tags),
            )
        return {
            "dry_run": dry_run,
            "campaign": name,
            "starter": starter,
            "next": f"Open the campaign in the web app, or run: mailerlm campaigns check {name}",
        }

    return _guard(work)


def campaigns_save(name: str, fields: dict) -> Board:
    """Update the given fields of a campaign: subject, preview_text, body, kind, lists, tags,
    variables, template, sender_name, sender_address, reply_to, postal_address, track_clicks,
    track_opens."""

    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.campaign_save(connection, name, fields)
        return {"saved": name}

    return _guard(work)


def campaigns_duplicate(name: str, new_name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.campaign_duplicate(connection, name, new_name)
        return {"created": new_name}

    return _guard(work)


def campaigns_delete(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.campaign_delete(connection, name)
        return {"deleted": name}

    return _guard(work)


def campaigns_image_add(name: str, filename: str, data: bytes) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            image = campaigns_repository.image_add(connection, name, filename, data)
        return {"image": image}

    return _guard(work)


def campaigns_image_remove(name: str, filename: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            removed = campaigns_repository.image_remove(connection, name, filename)
        return {"removed": removed}

    return _guard(work)


def campaigns_image_data(name: str, filename: str) -> tuple[bytes, str] | None:
    """The stored bytes and mime of an uploaded image (the page's thumbnails)."""
    with _transaction() as connection:
        data = campaigns_repository.image_bytes(connection, name, filename)
        if data is None:
            return None
        mime = connection.execute(
            "SELECT a.mime FROM asset a JOIN campaign_image ci ON ci.sha256 = a.sha256 "
            "WHERE ci.filename = ? AND ci.campaign_id = (SELECT id FROM campaign WHERE name = ?)",
            (filename, name),
        ).fetchone()[0]
    return data, mime


def _render(
    connection: sqlite3.Connection, name: str, first_name: str, draft: dict | None = None
) -> tuple[dict, render.RenderedEmail]:
    """Render the saved campaign; `draft` fields (and `frame_html`/`frame_text`) overlay it unsaved."""
    campaign = campaigns_repository.campaign_get(connection, name)
    defaults = campaigns_repository.settings_get(connection)
    frame = campaigns_repository.template_get(connection, campaign["template"])
    draft = draft or {}
    if "frame_html" in draft:
        frame = {**frame, "html": str(draft["frame_html"])}
    if "frame_text" in draft:
        frame = {**frame, "text": str(draft["frame_text"])}
    for key in ("subject", "preview_text", "body", "sender_name", "postal_address", "reply_to"):
        if key in draft and isinstance(draft[key], str):
            campaign[key] = draft[key]
    if isinstance(draft.get("variables"), dict):
        campaign["variables"] = {str(k): str(v) for k, v in draft["variables"].items()}
    settings = config.load(paths.config_path())

    def pick(field: str) -> str:
        return campaign[field] or defaults[field]

    try:
        email = render.render(
            subject=campaign["subject"],
            body=campaign["body"],
            preview_text=campaign["preview_text"],
            frame_html=frame["html"],
            frame_text=frame["text"],
            variables=campaign["variables"],
            sender_name=pick("sender_name"),
            postal_address=pick("postal_address"),
            reply_to=pick("reply_to"),
            resolve_image=lambda image_name: campaigns_repository.image_bytes(
                connection, name, image_name
            ),
            image_budget_bytes=settings.get("caps", "max_image_kilobytes_per_campaign") * 1024,
            first_name=first_name,
        )
    except render.RenderError as error:
        raise MailerlmError(str(error)) from error
    return campaign, email


def campaigns_check(name: str) -> Board:
    """Render the campaign and count who it would reach; sends nothing."""

    def work() -> Board:
        with _transaction() as connection:
            campaign, email = _render(connection, name, "there")
            reach = audience.resolve(
                connection, tuple(campaign["lists"]), tuple(campaign["tags"]), campaign["kind"]
            )
        problems = list(email.warnings)
        if not campaign["lists"]:
            problems.append("the campaign names no list")
        if not campaign["subject"].strip():
            problems.append("the campaign has no subject")
        return {
            "campaign": name,
            "kind": campaign["kind"],
            "subject": email.subject,
            "lists": campaign["lists"],
            "tags": campaign["tags"],
            "would_reach": len(reach.emails),
            "skipped": reach.skipped_by_reason,
            "inline_images": len(email.images),
            "inline_image_kilobytes": round(sum(len(i.data) for i in email.images) / 1024, 1),
            "track_clicks": campaign["track_clicks"],
            "track_opens": campaign["track_opens"],
            "problems": problems,
            "ok": not problems,
            "next": f"Run: mailerlm campaigns preview {name}",
        }

    return _guard(work)


def campaigns_preview(
    name: str, first_name: str = "Friend", write_files: bool = True, draft: dict | None = None
) -> Board:
    """The rendered email (images inline as data addresses). From the command line it is also
    written under the workspace's previews folder to open in a browser."""

    def work() -> Board:
        with _transaction() as connection:
            _, email = _render(connection, name, first_name, draft)
        page = email.html
        for image in email.images:
            uri = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
            page = page.replace(f"cid:{image.content_id}", uri)
        board: Board = {
            "campaign": name,
            "subject": email.subject,
            "html": page,
            "text": email.text,
            "inline_images": len(email.images),
            "warnings": list(email.warnings),
        }
        if write_files:
            directory = paths.workspace_path() / "previews"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{name}.html").write_text(page)
            (directory / f"{name}.txt").write_text(email.text)
            board = {
                **board,
                "html": str(directory / f"{name}.html"),
                "text": str(directory / f"{name}.txt"),
                "next": f"Open {directory / f'{name}.html'} in a browser",
            }
        return board

    return _guard(work)


def overview() -> Board:
    """The home page's numbers."""
    with _transaction() as connection:
        lists = repository.lists_summary(connection)
        contacts = connection.execute("SELECT COUNT(*) FROM contact").fetchone()[0]
        campaigns = campaigns_repository.campaign_list(connection)
        templates = campaigns_repository.template_list(connection)
        defaults = campaigns_repository.settings_get(connection)
        replies_waiting = connection.execute(
            "SELECT COUNT(*) FROM reply WHERE decision = 'queued'"
        ).fetchone()[0]
    return {
        "replies_waiting": replies_waiting,
        "lists": lists,
        "contacts": contacts,
        "campaigns": campaigns,
        "templates": len(templates),
        "sender_set": bool(
            defaults["sender_name"] and defaults["postal_address"] and defaults["reply_to"]
        ),
    }


# ---- sending -----------------------------------------------------------------------------

sender_for_tests: MailSender | None = None  # tests set this; a real run leaves it None
running_sends: set[int] = set()


def _sender() -> MailSender:
    if sender_for_tests is not None:
        return sender_for_tests
    if not os.environ.get(resend.KEY_ENV):
        raise MailerlmError(f"{resend.KEY_ENV} is not set. Add it to .env (see .env.example)")
    return resend.ResendSender()


def _pause(sender: MailSender) -> float:
    return resend.SECONDS_BETWEEN_MESSAGES if sender.name == "resend" else 0.0


def _effective_sender(connection: sqlite3.Connection, campaign: dict) -> dict[str, str]:
    """The campaign's own sender fields, else the one list's override, else the Settings defaults."""
    defaults = campaigns_repository.settings_get(connection)
    list_override = {"sender_name": "", "sender_address": ""}
    if len(campaign["lists"]) == 1:
        row = connection.execute(
            "SELECT sender_name, sender_address FROM mailing_list WHERE name = ?",
            (campaign["lists"][0],),
        ).fetchone()
        if row:
            list_override = {"sender_name": row[0] or "", "sender_address": row[1] or ""}
    return {key: campaign[key] or list_override.get(key, "") or defaults[key] for key in defaults}


def _message(
    connection: sqlite3.Connection, name: str, recipient: sending.Recipient, key: str
) -> OutgoingMessage:
    campaign, email = _render(connection, name, recipient.first_name)
    who = _effective_sender(connection, campaign)
    return OutgoingMessage(
        sender_name=who["sender_name"],
        sender_address=who["sender_address"],
        to=recipient.email,
        subject=email.subject,
        html=email.html,
        text=email.text,
        reply_to=who["reply_to"],
        unsubscribe_mailto=email.unsubscribe_mailto,
        pictures=tuple(
            InlinePicture(i.content_id, i.filename, i.mime, i.data) for i in email.images
        ),
        idempotency_key=key,
        tag=re.sub(r"[^A-Za-z0-9_-]", "-", name)[:60],
    )


def _prepare(connection: sqlite3.Connection, name: str) -> dict:
    campaign, email = _render(connection, name, "Friend")
    who = _effective_sender(connection, campaign)
    reach = audience.resolve(
        connection, tuple(campaign["lists"]), tuple(campaign["tags"]), campaign["kind"]
    )
    problems = list(email.warnings)
    for key, label in (
        ("sender_name", "sender name"),
        ("sender_address", "sender address"),
        ("reply_to", "reply-to address"),
        ("postal_address", "postal address"),
    ):
        if not who[key]:
            problems.append(f"no {label} set (Settings, or this campaign's Sender section)")
    if not campaign["lists"]:
        problems.append("the campaign names no list")
    if not campaign["subject"].strip():
        problems.append("the campaign has no subject")
    if not campaign["body"].strip():
        problems.append("the letter is empty")
    if campaign["track_clicks"] or campaign["track_opens"]:
        problems.append(
            "tracking is set per sending domain in Resend, not per campaign; the flags here are only notes"
        )
    problems = sorted(set(problems))
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "subject": email.subject,
                "html": email.html,
                "text": email.text,
                "sender": who,
                "kind": campaign["kind"],
                "recipients": list(reach.emails),
                "images": [hashlib.sha256(i.data).hexdigest() for i in email.images],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return {
        "campaign": campaign,
        "email": email,
        "sender": who,
        "reach": reach,
        "problems": problems,
        "fingerprint": fingerprint,
    }


def sends_plan(name: str) -> Board:
    """What a real send would do right now, with the fingerprint that `sends_start` must repeat."""

    def work() -> Board:
        settings = config.load(paths.config_path())
        with _transaction() as connection:
            plan = _prepare(connection, name)
            today = sending.live_sends_today(connection)
            repeated = sending.already_sent(connection, name, plan["fingerprint"])
        reach, who = plan["reach"], plan["sender"]
        blockers = [p for p in plan["problems"] if not p.startswith("tracking is set")]
        max_recipients = settings.get("caps", "max_recipients_per_send")
        max_sends = settings.get("caps", "max_sends_per_day")
        if not reach.emails:
            blockers.append("nobody would receive it")
        if len(reach.emails) > max_recipients:
            blockers.append(
                f"{len(reach.emails)} recipients is over the cap of {max_recipients} (caps.max_recipients_per_send)"
            )
        if today >= max_sends:
            blockers.append(
                f"{today} live sends today reached the cap of {max_sends} (caps.max_sends_per_day)"
            )
        if repeated:
            blockers.append("this exact letter was already sent to this exact audience")
        return {
            "campaign": name,
            "kind": plan["campaign"]["kind"],
            "subject": plan["email"].subject,
            "from": f"{who['sender_name']} <{who['sender_address']}>",
            "reply_to": who["reply_to"],
            "lists": plan["campaign"]["lists"],
            "tags": plan["campaign"]["tags"],
            "recipients": len(reach.emails),
            "skipped": reach.skipped_by_reason,
            "fingerprint": plan["fingerprint"],
            "problems": plan["problems"],
            "blockers": blockers,
            "can_send": not blockers,
            "next": f"Run: mailerlm sends start {name} --fingerprint {plan['fingerprint']}"
            if not blockers
            else "Fix the blockers above, then plan again",
        }

    return _guard(work)


def _execute(send_id: int, name: str, kind: str) -> dict[str, int]:
    sender = _sender()
    connection = _open()
    running_sends.add(send_id)
    try:
        result = sending.run_send(
            connection,
            send_id,
            sender,
            lambda recipient, key: _message(connection, name, recipient, key),
            pause_seconds=_pause(sender),
        )
        if kind == "live":
            campaign = campaigns_repository.campaign_get(connection, name)
            if campaign["kind"] == "confirmation":
                sent = [
                    r[0]
                    for r in connection.execute(
                        "SELECT contact_id FROM delivery WHERE send_id = ? AND status = 'sent' AND contact_id IS NOT NULL",
                        (send_id,),
                    )
                ]
                list_ids = [repository.list_id(connection, n) for n in campaign["lists"]]
                consent_states.mark_pending(connection, sent, list_ids)
        return result
    finally:
        running_sends.discard(send_id)
        connection.close()


def sends_test(name: str) -> Board:
    """Send this campaign once, to the reply-to address (your own inbox); nobody else is mailed."""

    def work() -> Board:
        sender = _sender()
        with _transaction() as connection:
            plan = _prepare(connection, name)
            blockers = [
                p
                for p in plan["problems"]
                if not p.startswith("tracking is set") and "names no list" not in p
            ]
            if blockers:
                raise MailerlmError("cannot send yet: " + "; ".join(blockers))
            target = plan["sender"]["reply_to"]
            send_id = sending.create_send(
                connection,
                name,
                "test",
                plan["email"].subject,
                plan["fingerprint"],
                [sending.Recipient(None, target, "there")],
                excerpt=plan["email"].text,
            )
        result = _execute(send_id, name, "test")
        return {
            "send": send_id,
            "sent_to": "your reply-to address",
            "counts": result,
            "sender": sender.name,
            "next": "Check your inbox, then run: mailerlm sends poll",
        }

    return _guard(work)


def sends_start(name: str, fingerprint: str, background: bool = False) -> Board:
    """The real send. It repeats the plan and refuses unless the fingerprint still matches."""
    plan_board = sends_plan(name)
    if plan_board["fingerprint"] != fingerprint:
        raise MailerlmError("the letter or the audience changed since you reviewed it. Plan again")
    if not plan_board["can_send"]:
        raise MailerlmError("cannot send: " + "; ".join(plan_board["blockers"]))

    def work() -> Board:
        _sender()  # fail before anything is recorded when the key is missing
        with _transaction() as connection:
            plan = _prepare(connection, name)
            recipients = [
                sending.Recipient(i, e, "there")
                for i, e in zip(plan["reach"].contact_ids, plan["reach"].emails, strict=True)
            ]
            send_id = sending.create_send(
                connection,
                name,
                "live",
                plan["email"].subject,
                plan["fingerprint"],
                recipients,
                excerpt=plan["email"].text,
            )
            kind = "live"
        if background:
            threading.Thread(target=_execute, args=(send_id, name, kind), daemon=True).start()
            return {
                "send": send_id,
                "started": True,
                "recipients": len(recipients),
                "next": "Watch the progress on the campaign page",
            }
        result = _execute(send_id, name, kind)
        return {"send": send_id, "counts": result, "next": "Run: mailerlm sends poll"}

    return _guard(work)


def sends_resume(send_id: int, background: bool = False) -> Board:
    """Send the rows of an interrupted send that are still queued; people already mailed are not mailed again."""

    def work() -> Board:
        with _transaction() as connection:
            info = sending.get_send(connection, send_id)
        if send_id in running_sends:
            raise MailerlmError("that send is still running")
        if info["status"] == "done":
            return {"send": send_id, "counts": info["counts"], "next": "Nothing left to send"}
        if background:
            threading.Thread(
                target=_execute, args=(send_id, info["campaign_name"], info["kind"]), daemon=True
            ).start()
            return {"send": send_id, "started": True}
        return {"send": send_id, "counts": _execute(send_id, info["campaign_name"], info["kind"])}

    return _guard(work)


def sends_status(send_id: int) -> Board:
    with _transaction() as connection:
        info = sending.get_send(connection, send_id)
    return {"send": {**info, "running": send_id in running_sends}}


def sends_list(campaign: str | None = None) -> Board:
    with _transaction() as connection:
        found = sending.list_sends(connection, campaign)
    return {
        "sends": [{**item, "running": item["id"] in running_sends} for item in found],
        "next": "Run: mailerlm sends poll",
    }


def sends_poll(send: int | None = None) -> Board:
    """Ask Resend how each sent message ended up; a bounce or complaint blocks that person."""

    def work() -> Board:
        sender = _sender()
        try:
            with _transaction() as connection:
                changed = sending.poll(connection, sender, send)
        except SenderError as error:
            hint = (
                " Resend only lets a full-access key read delivery status; this key is send-only."
                if "401" in str(error) or "403" in str(error)
                else ""
            )
            raise MailerlmError(f"{error}.{hint}") from error
        return {**changed, "next": "Run: mailerlm sends list"}

    return _guard(work)


# ---- replies -----------------------------------------------------------------------------

reader_for_tests: Any = None  # tests set these; a real run leaves them None
classifier_for_tests: Any = None


def _reader() -> Any:
    if reader_for_tests is not None:
        return reader_for_tests
    try:
        return gmail.GmailReader(gmail.credentials(paths.workspace_path()))
    except gmail.GmailNotConfigured as error:
        raise MailerlmError(str(error)) from error


def _classifier() -> Any:
    if classifier_for_tests is not None:
        return classifier_for_tests
    if not os.environ.get(inbox_classifier.KEY_ENV):
        raise MailerlmError(
            f"{inbox_classifier.KEY_ENV} is not set. Add it to .env (see .env.example)"
        )
    return inbox_classifier.TypeSafeClassifier()


def replies_check() -> Board:
    """Read new replies from people you have mailed; apply the safe ones, queue the rest."""

    def work() -> Board:
        settings = config.load(paths.config_path())
        reader, classifier = _reader(), _classifier()
        connection = (
            _open()
        )  # no transaction around the mailbox and TypeSafe calls; each reply commits on its own
        try:
            counts = inbox_service.check(
                connection,
                reader,
                classifier,
                settings.get("classifier", "model"),
                settings.get("classifier", "unsubscribe_min_probability"),
                settings.get("classifier", "confirm_min_probability"),
            )
        finally:
            connection.close()
        return {
            **counts,
            "next": "Open the Replies page for anything queued"
            if counts["queued"]
            else "Nothing needs you",
        }

    return _guard(work)


def replies_list(queue_only: bool = False) -> Board:
    with _transaction() as connection:
        replies = inbox_service.list_replies(connection, queue_only)
    return {
        "replies": replies,
        "waiting": sum(1 for r in replies if r["decision"] == "queued"),
        "next": "Run: mailerlm replies check",
    }


def replies_decide(reply: int, decision: str) -> Board:
    """Your word on a reply: confirmed, unsubscribed or ignored (an earlier automatic pick is undone first)."""

    def work() -> Board:
        with _transaction() as connection:
            return inbox_service.decide(connection, reply, decision)

    return _guard(work)


def replies_undo(reply: int) -> Board:
    """Undo an automatic decision: memberships go back as they were and the reply returns to the queue."""

    def work() -> Board:
        with _transaction() as connection:
            inbox_service.undo(connection, reply)
        return {"undone": reply}

    return _guard(work)
