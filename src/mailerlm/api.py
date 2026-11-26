"""The typed API: one function per CLI verb; the CLI is a logic-free surface over it.

Every function returns a board (a plain dict of counts, paths and a `next` hint), never content.

Never imports: cli (enforced by the import contract "api below cli" in pyproject.toml).
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
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

from llmlm import llm

from mailerlm import config, paths, store
from mailerlm.campaigns import audience, frontmatter, render, template_guard
from mailerlm.campaigns import repository as campaigns_repository
from mailerlm.consent import states as consent_states
from mailerlm.contacts import mask_email, parsing, repository
from mailerlm.delivery import resend, sending
from mailerlm.delivery.sender import InlinePicture, MailSender, OutgoingMessage, SenderError
from mailerlm.editor import service as editor_service
from mailerlm.errors import MailerlmError, NotConfiguredError, NotFoundError
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
            backup = env_backup_path(env_file, f"{now():%Y%m%d-%H%M%S}")
            backup.parent.mkdir(parents=True, exist_ok=True)
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
            "" if client else "Add them to .env (see env.example)",
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
    import subprocess as _subprocess

    try:
        loaded = (
            "com.nagarkar.mailerlm.replies"
            in _subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, timeout=5
            ).stdout
        )
    except (OSError, _subprocess.SubprocessError):
        loaded = False
    checks.append(
        _check(
            "scheduled reply check",
            "ok" if loaded else "missing",
            "the launch agent is loaded" if loaded else "no launch agent runs the reply check",
            ""
            if loaded
            else "Install it: see the header of scripts/com.nagarkar.mailerlm.replies.plist",
        )
    )
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
        raise MailerlmError(f"{error}. Add them to .env (see env.example)") from error
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


def lists_test(name: str, clear: bool = False, dry_run: bool = False) -> Board:
    """Mark a list as a test list (`clear` makes it a real list again). Every send whose lists are
    all test lists carries "Testing: " before its subject; the campaign's own subject is not edited."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            repository.set_test_list(connection, name, not clear)
        return {
            "dry_run": dry_run,
            "list": name,
            "test": not clear,
            "next": "Run: mailerlm lists show",
        }

    return _guard(work)


def lists_footer(name: str, show: bool, dry_run: bool = False) -> Board:
    """Show or hide the footer (opt-out line and postal address) on letters to this list. A letter
    loses its footer only when every list it goes to hides it; the List-Unsubscribe header stays."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            repository.set_show_footer(connection, name, show)
        return {
            "dry_run": dry_run,
            "list": name,
            "show_footer": show,
            "next": "Run: mailerlm lists show",
        }

    return _guard(work)


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
    confirm_reason: str | None = None,
    preferred_name: str | None = None,
) -> Board:
    """Add one contact (an email, a LinkedIn URL, or both) and optionally join a list; with
    `confirm_reason`, the owner also confirms them on that list (see `contacts_confirm`).
    `preferred_name` is the name they like to be called by (blank: their first name)."""

    def work() -> Board:
        text = " ".join(part for part in (name and f"{name}:", email, linkedin_url) if part)
        candidates, _ = parsing.parse(text)
        if not candidates:
            raise MailerlmError(
                "give an email or a LinkedIn profile URL. Run: mailerlm help contacts add"
            )
        _confirm_needs_list(list_name, confirm_reason)
        candidate = dataclasses.replace(candidates[0], preferred_name=preferred_name or "")
        ids: list[int] = []
        with _transaction(dry_run) as connection:
            counts = repository.import_candidates(
                connection, [candidate], list_name, tags, contact_ids=ids
            )
            confirmed = _confirm_on_import(connection, ids, list_name, confirm_reason)
        return {
            "dry_run": dry_run,
            **_counts(counts),
            **confirmed,
            "next": "Run: mailerlm contacts find",
        }

    return _guard(work)


def contacts_import(
    source: str,
    list_name: str | None = None,
    tags: tuple[str, ...] = (),
    dry_run: bool = False,
    confirm_reason: str | None = None,
) -> Board:
    """Import pasted text or a CSV from a file (`-` reads standard input)."""

    def work() -> Board:
        text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
        return contacts_import_text(text, list_name, tags, dry_run, confirm_reason)

    return _guard(work)


def contacts_import_text(
    text: str,
    list_name: str | None = None,
    tags: tuple[str, ...] = (),
    dry_run: bool = False,
    confirm_reason: str | None = None,
) -> Board:
    """Import pasted text or CSV given as text (the web page's form); with `confirm_reason`, the
    owner also confirms everyone read on that list (see `contacts_confirm`)."""

    def work() -> Board:
        candidates, rejected_lines = parsing.parse(text)
        _confirm_needs_list(list_name, confirm_reason)
        ids: list[int] = []
        with _transaction(dry_run) as connection:
            counts = repository.import_candidates(
                connection, candidates, list_name, tags, contact_ids=ids
            )
            confirmed = _confirm_on_import(connection, ids, list_name, confirm_reason)
        return {
            "dry_run": dry_run,
            "read": len(candidates),
            **_counts(counts),
            **confirmed,
            "rejected_line_numbers": rejected_lines,
            "next": "Run: mailerlm contacts find" + (f" --list {list_name}" if list_name else ""),
        }

    return _guard(work)


def _confirm_needs_list(list_name: str | None, reason: str | None) -> None:
    if reason is not None and not list_name:
        raise MailerlmError("confirming needs a list: add --list <name>")


def _confirm_on_import(
    connection: sqlite3.Connection, ids: list[int], list_name: str | None, reason: str | None
) -> Board:
    """Confirm just-imported contacts on the list when the owner gave a reason; one who cannot be
    confirmed (do-not-mail, unsubscribed, suppressed) is listed, and the import still stands."""
    if reason is None or not list_name:
        return {}
    outcome = _confirm_many(connection, ids, repository.list_id(connection, list_name), reason)
    return {
        "confirmed": len(outcome["confirmed"]),
        "already_subscribed": outcome["already_subscribed"],
        "declined": outcome["declined"],
    }


def _who(connection: sqlite3.Connection, contact_id: int) -> dict:
    email, name, preferred_name = connection.execute(
        "SELECT email, name, preferred_name FROM contact WHERE id = ?", (contact_id,)
    ).fetchone()
    who: Board = {"contact": contact_id, "name": name, "email": mask_email(email)}
    return {**who, "preferred_name": preferred_name} if preferred_name else who


def _confirm_many(
    connection: sqlite3.Connection, ids: list[int], list_identifier: int, reason: str
) -> dict:
    confirmed, declined, already = [], [], 0
    for contact_id in dict.fromkeys(ids):
        try:
            changes = consent_states.confirm_by_owner(
                connection, contact_id, list_identifier, reason
            )
        except consent_states.NotConfirmable as error:
            if "reason" in str(error):
                raise MailerlmError(str(error)) from error
            declined.append({**_who(connection, contact_id), "why": str(error)})
            continue
        if changes:
            confirmed.append(_who(connection, contact_id))
        else:
            already += 1
    return {"confirmed": confirmed, "declined": declined, "already_subscribed": already}


def contacts_confirm(
    contact: str | int, list_name: str, reason: str, dry_run: bool = False
) -> Board:
    """The owner confirms one person on one list without a confirmation letter (imported or pending
    -> subscribed), with a required reason such as "known personally", "confirmed by email" or
    "confirmed in person". Other lists are untouched. Never for someone marked do-not-mail,
    unsubscribed or suppressed."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            contact_id = repository.contact_by_ref(connection, contact)
            try:
                changes = consent_states.confirm_by_owner(
                    connection, contact_id, repository.list_id(connection, list_name), reason
                )
            except consent_states.NotConfirmable as error:
                raise MailerlmError(f"not confirmed: {error}") from error
        board: Board = {"dry_run": dry_run, "list": list_name, "confirmed": bool(changes)}
        if not changes:
            board["note"] = "already subscribed on this list; nothing changed"
        return {**board, "next": f"Run: mailerlm contacts history {contact}"}

    return _guard(work)


def lists_confirm(list_name: str, reason: str, dry_run: bool = False) -> Board:
    """The owner confirms everyone on a list who is imported or pending, with one required reason.
    `dry_run` shows who would change. People marked do-not-mail, unsubscribed or suppressed are
    left as they are and listed with why; other lists are untouched."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            list_identifier = repository.list_id(connection, list_name)
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT contact_id FROM membership WHERE list_id = ? ORDER BY contact_id",
                    (list_identifier,),
                )
            ]
            outcome = _confirm_many(connection, ids, list_identifier, reason)
        changed = "would_confirm" if dry_run else "confirmed"
        return {
            "dry_run": dry_run,
            "list": list_name,
            "reason": " ".join(reason.split()),
            changed: outcome["confirmed"],
            "already_subscribed": outcome["already_subscribed"],
            "declined": outcome["declined"],
            "next": f"Run: mailerlm lists confirm {list_name} --reason ... (without --dry-run)"
            if dry_run
            else f"Run: mailerlm contacts find --list {list_name}",
        }

    return _guard(work)


def contacts_history(contact: str | int) -> Board:
    """Every consent change of this person's memberships, with how it happened (a confirmation
    letter, their reply, the owner's word and reason, a request to stop)."""

    def work() -> Board:
        with _transaction() as connection:
            contact_id = repository.contact_by_ref(connection, contact)
            return {
                **_who(connection, contact_id),
                "history": repository.history(connection, contact_id),
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
    contact: str | int,
    email: str | None = None,
    name: str | None = None,
    linkedin_url: str | None = None,
    preferred_name: str | None = None,
    dry_run: bool = False,
) -> Board:
    """Change the fields given (None leaves one as it is; "" clears an email, LinkedIn URL or
    preferred name). A blank preferred name means "call them by their first name"."""

    def work() -> Board:
        with _transaction(dry_run) as connection:
            contact_id = repository.contact_by_ref(connection, contact)
            repository.edit_contact(
                connection, contact_id, email, name, linkedin_url, preferred_name
            )
            first, preferred = repository.greeting_names(connection, contact_id)
        return {
            "dry_run": dry_run,
            "edited": True,
            "first_name": first,
            "greeted_as": preferred,
            "next": "Run: mailerlm contacts find",
        }

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
    sender_name: str = "",
    sender_address: str = "",
    reply_to: str = "",
    postal_address: str = "",
    unsubscribe_line: str = "",
) -> Board:
    """The defaults every campaign uses unless it sets its own. An empty `unsubscribe_line`
    (the footer's opt-out line) means the built-in wording."""

    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.settings_set(
                connection,
                {
                    "sender_name": sender_name,
                    "sender_address": sender_address,
                    "reply_to": reply_to,
                    "postal_address": postal_address,
                    "unsubscribe_line": unsubscribe_line,
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


SAMPLE_LETTER = """Hi {{ preferred_name }},

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
                unsubscribe_line=defaults["unsubscribe_line"],
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
    variables, template, sender_name, sender_address, reply_to, postal_address, unsubscribe_line,
    track_clicks, track_opens."""

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


def _list_look(
    connection: sqlite3.Connection, campaign: dict, list_name: str | None
) -> audience.ListSettings:
    """The settings of `list_name`, else of the campaign's first list, else a plain real list."""
    name = list_name or (campaign["lists"][0] if campaign["lists"] else None)
    if name is None:
        return audience.ListSettings("", False, True)
    try:
        return audience.list_settings(connection, name)
    except NotFoundError:
        if list_name is None:
            raise
        return audience.ListSettings(list_name, False, True)  # a deleted list of an old send


def _render(
    connection: sqlite3.Connection,
    name: str,
    first_name: str,
    draft: dict | None = None,
    list_name: str | None = None,
    test_prefix: bool | None = None,
    show_footer: bool | None = None,
    preferred_name: str | None = None,
) -> tuple[dict, render.RenderedEmail]:
    """Render the saved campaign as a reader reached through `list_name` gets it (default: the
    campaign's first list); `test_prefix` and `show_footer` override that list's settings for one
    person (see `audience.per_list`). `draft` fields (and `frame_html`/`frame_text`) overlay it unsaved."""
    campaign = campaigns_repository.campaign_get(connection, name)
    frame = campaigns_repository.template_get(connection, campaign["template"])
    draft = draft or {}
    if "frame_html" in draft:
        frame = {**frame, "html": str(draft["frame_html"])}
    if "frame_text" in draft:
        frame = {**frame, "text": str(draft["frame_text"])}
    for key in (
        "subject",
        "preview_text",
        "body",
        "sender_name",
        "postal_address",
        "reply_to",
        "unsubscribe_line",
    ):
        if key in draft and isinstance(draft[key], str):
            campaign[key] = draft[key]
    if isinstance(draft.get("variables"), dict):
        campaign["variables"] = {str(k): str(v) for k, v in draft["variables"].items()}
    settings = config.load(paths.config_path())
    look = _list_look(connection, campaign, list_name)
    who = _effective_sender(connection, campaign, look)
    prefix = look.is_test if test_prefix is None else test_prefix
    footer = look.show_footer if show_footer is None else show_footer
    # Only the rendered letter changes, never the campaign row.
    campaign["look"] = {"list": look.name, "test_prefix": prefix, "show_footer": footer}
    subject = campaign["subject"]
    try:
        email = render.render(
            subject=audience.test_subject(subject) if prefix else subject,
            body=campaign["body"],
            preview_text=campaign["preview_text"],
            frame_html=frame["html"],
            frame_text=frame["text"],
            variables=campaign["variables"],
            sender_name=who["sender_name"],
            postal_address=who["postal_address"],
            reply_to=who["reply_to"],
            unsubscribe_line=who["unsubscribe_line"],
            show_footer=footer,
            resolve_image=lambda image_name: campaigns_repository.image_bytes(
                connection, name, image_name
            ),
            image_budget_bytes=settings.get("caps", "max_image_kilobytes_per_campaign") * 1024,
            first_name=first_name,
            preferred_name=preferred_name,
        )
    except render.RenderError as error:
        raise MailerlmError(str(error)) from error
    return campaign, email


def campaigns_check(name: str) -> Board:
    """Render the campaign for each of its lists and count who it would reach; sends nothing."""

    def work() -> Board:
        with _transaction() as connection:
            plan = _prepare(connection, name, None)
        campaign, email = plan["campaign"], plan["email"]
        problems = list(plan["render_problems"]) + plan["overlaps"]
        if not campaign["lists"]:
            problems.append("the campaign names no list")
        if not campaign["subject"].strip():
            problems.append("the campaign has no subject")
        problems = sorted(set(problems))
        return {
            "campaign": name,
            "kind": campaign["kind"],
            "subject": email.subject,
            "lists": campaign["lists"],
            "tags": campaign["tags"],
            "would_reach": len(plan["reach"].recipients),
            "skipped": plan["reach"].skipped_by_reason,
            "by_list": plan["by_list"],
            "notes": plan["notes"],
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
    name: str,
    first_name: str = "Friend",
    write_files: bool = True,
    draft: dict | None = None,
    list_name: str | None = None,
    preferred_name: str | None = None,
) -> Board:
    """The rendered email (images inline as data addresses) as a reader reached through `list_name`
    gets it (default: the campaign's first list), greeting `preferred_name` (default: `first_name`).
    From the command line it is also written under the workspace's previews folder to open in a
    browser."""

    def work() -> Board:
        with _transaction() as connection:
            campaign, email = _render(
                connection, name, first_name, draft, list_name, preferred_name=preferred_name
            )
        page = email.html
        for image in email.images:
            uri = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
            page = page.replace(f"cid:{image.content_id}", uri)
        board: Board = {
            "campaign": name,
            "as_reached_through": campaign["look"]["list"] or "no list",
            "footer": "shown" if campaign["look"]["show_footer"] else "hidden",
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
        raise MailerlmError(f"{resend.KEY_ENV} is not set. Add it to .env (see env.example)")
    return resend.ResendSender()


def _pause(sender: MailSender) -> float:
    return resend.SECONDS_BETWEEN_MESSAGES if sender.name == "resend" else 0.0


def _effective_sender(
    connection: sqlite3.Connection, campaign: dict, look: audience.ListSettings
) -> dict[str, str]:
    """The campaign's own sender fields, else the list's override (name and address), else the
    Settings defaults."""
    defaults = campaigns_repository.settings_get(connection)
    override = {"sender_name": look.sender_name, "sender_address": look.sender_address}
    return {key: campaign[key] or override.get(key, "") or defaults[key] for key in defaults}


def _message(
    connection: sqlite3.Connection, name: str, recipient: sending.Recipient, key: str
) -> OutgoingMessage:
    campaign, email = _render(
        connection,
        name,
        recipient.first_name,
        list_name=recipient.list_name,
        test_prefix=recipient.test_prefix,
        show_footer=recipient.show_footer,
        preferred_name=recipient.preferred_name,
    )
    who = _effective_sender(
        connection, campaign, _list_look(connection, campaign, recipient.list_name)
    )
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


def _prepare(connection: sqlite3.Connection, name: str, lists: list[str] | None = None) -> dict:
    """Plan a send to `lists` (default: the campaign's lists), in that order: who each list reaches,
    one letter per person in the look of the list they are reached through, and a fingerprint of
    the lists, the letters and the audience."""
    campaign = campaigns_repository.campaign_get(connection, name)
    chosen = tuple(dict.fromkeys(lists)) if lists else tuple(campaign["lists"])
    reach = audience.per_list(connection, chosen, tuple(campaign["tags"]), campaign["kind"])
    looks = {(r.list_name, r.test_prefix, r.show_footer) for r in reach.recipients}
    looks |= {(x.settings.name, x.settings.is_test, x.settings.show_footer) for x in reach.lists}
    renders: dict[tuple, render.RenderedEmail] = {}
    for look in sorted(looks):
        campaign, renders[look] = _render(connection, name, "Friend", None, *look)
    first = reach.lists[0].settings if reach.lists else None
    first_look = (first.name, first.is_test, first.show_footer) if first else None
    if first_look is None:
        campaign, email = _render(connection, name, "Friend")
    else:
        email = renders[first_look]
    senders = {
        x.settings.name: _effective_sender(connection, campaign, x.settings) for x in reach.lists
    }
    who = (
        senders[first.name]
        if first
        else _effective_sender(connection, campaign, audience.ListSettings("", False, True))
    )
    render_problems = sorted(
        {w for e in renders.values() for w in e.warnings} | set(email.warnings)
    )
    problems = list(render_problems)
    labels = (
        ("sender_name", "sender name"),
        ("sender_address", "sender address"),
        ("reply_to", "reply-to address"),
        ("postal_address", "postal address"),
    )
    footer_somewhere = any(f for (_, _, f) in looks) or not reach.lists
    for each in senders.values() or [who]:
        for key, label in labels:
            if key == "postal_address" and not footer_somewhere:
                continue
            if not each[key]:
                problems.append(f"no {label} set (Settings, or this campaign's Sender section)")
    if not chosen:
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
    overlaps = [_overlap_text(o, mailed=True) for o in reach.blocking]
    problems += overlaps
    by_list = [
        {
            "list": x.settings.name,
            "recipients": x.recipients,
            "footer": "shown" if x.settings.show_footer else "hidden",
            "testing_prefix": x.settings.is_test,
            "subject": renders[
                (x.settings.name, x.settings.is_test, x.settings.show_footer)
            ].subject,
            "from": f"{senders[x.settings.name]['sender_name']} <{senders[x.settings.name]['sender_address']}>",
            "skipped": x.skipped_by_reason,
        }
        for x in reach.lists
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "lists": list(chosen),
                "letters": {
                    "|".join(map(str, look)): [e.subject, e.html, e.text]
                    for look, e in renders.items()
                },
                "senders": senders,
                "kind": campaign["kind"],
                "recipients": [
                    [r.email, r.list_name, r.test_prefix, r.show_footer] for r in reach.recipients
                ],
                "images": [hashlib.sha256(i.data).hexdigest() for i in email.images],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return {
        "campaign": campaign,
        "lists": list(chosen),
        "email": email,
        "renders": renders,
        "sender": who,
        "reach": reach,
        "by_list": by_list,
        "overlaps": overlaps,
        "notes": [_overlap_text(o, mailed=False) for o in reach.notes],
        "render_problems": render_problems,
        "problems": problems,
        "fingerprint": fingerprint,
    }


def _overlap_text(overlap: audience.Overlap, mailed: bool) -> str:
    """One overlap in words, with the owner's two fixes."""
    on = ", ".join(f"{name} ({status})" for name, status in overlap.on.items())
    who = f"{mask_email(overlap.email)} (contact {overlap.contact_id})"
    leave = next(iter(overlap.on))
    if mailed:
        return (
            f"{who} would be mailed through more than one chosen list: {on}. Take them off all but "
            f"one (mailerlm contacts leave {overlap.contact_id} {leave}, or the list's page) or "
            "drop a list from this send"
        )
    return (
        f"{who} is on more than one chosen list: {on}; only one would mail them now. To tidy it "
        f"early: mailerlm contacts leave {overlap.contact_id} {leave}, or the list's page"
    )


def sends_plan(name: str, lists: list[str] | None = None) -> Board:
    """What a real send to `lists` (default: the campaign's lists, in order) would do right now, per
    list, with the fingerprint that `sends_start` must repeat with the same lists."""

    def work() -> Board:
        settings = config.load(paths.config_path())
        with _transaction() as connection:
            plan = _prepare(connection, name, lists)
            today = sending.live_sends_today(connection)
            repeated = sending.already_sent(connection, name, plan["fingerprint"])
        reach, who = plan["reach"], plan["sender"]
        blockers = [p for p in plan["problems"] if not p.startswith("tracking is set")]
        max_recipients = settings.get("caps", "max_recipients_per_send")
        max_sends = settings.get("caps", "max_sends_per_day")
        if not reach.recipients:
            blockers.append("nobody would receive it")
        if len(reach.recipients) > max_recipients:
            blockers.append(
                f"{len(reach.recipients)} recipients is over the cap of {max_recipients} (caps.max_recipients_per_send)"
            )
        if today >= max_sends:
            blockers.append(
                f"{today} live sends today reached the cap of {max_sends} (caps.max_sends_per_day)"
            )
        if repeated:
            blockers.append("this exact letter was already sent to this exact audience")
        chosen = " ".join(f"--list {json.dumps(n) if ' ' in n else n}" for n in plan["lists"])
        return {
            "campaign": name,
            "kind": plan["campaign"]["kind"],
            "subject": plan["email"].subject,
            "from": f"{who['sender_name']} <{who['sender_address']}>",
            "reply_to": who["reply_to"],
            "lists": plan["lists"],
            "campaign_lists": plan["campaign"]["lists"],
            "tags": plan["campaign"]["tags"],
            "recipients": len(reach.recipients),
            "skipped": reach.skipped_by_reason,
            "by_list": plan["by_list"],
            "notes": plan["notes"],
            "fingerprint": plan["fingerprint"],
            "problems": plan["problems"],
            "blockers": blockers,
            "can_send": not blockers,
            "next": f"Run: mailerlm sends start {name} {chosen} --fingerprint {plan['fingerprint']}"
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
                chosen = sending.send_lists(connection, send_id) or tuple(campaign["lists"])
                list_ids = [
                    row[0]
                    for n in chosen
                    if (
                        row := connection.execute(
                            "SELECT id FROM mailing_list WHERE name = ?", (n,)
                        ).fetchone()
                    )
                ]
                consent_states.mark_pending(connection, sent, list_ids)
        return result
    finally:
        running_sends.discard(send_id)
        connection.close()


def sends_test(name: str, lists: list[str] | None = None) -> Board:
    """Send this campaign once, to the reply-to address (your own inbox), as a reader of the first
    of `lists` (default: the campaign's first list) gets it; nobody else is mailed."""

    def work() -> Board:
        sender = _sender()
        with _transaction() as connection:
            plan = _prepare(connection, name, lists)
            blockers = [
                p
                for p in plan["problems"]
                if not p.startswith("tracking is set") and "names no list" not in p
            ]
            if blockers:
                raise MailerlmError("cannot send yet: " + "; ".join(blockers))
            target = plan["sender"]["reply_to"]
            first = plan["reach"].lists[0].settings if plan["reach"].lists else None
            send_id = sending.create_send(
                connection,
                name,
                "test",
                plan["email"].subject,
                plan["fingerprint"],
                [
                    sending.Recipient(
                        None,
                        target,
                        "there",
                        first.name if first else None,
                        first.show_footer if first else None,
                        first.is_test if first else None,
                    )
                ],
                excerpt=plan["email"].text,
                lists=tuple(plan["lists"]),
            )
        result = _execute(send_id, name, "test")
        return {
            "send": send_id,
            "sent_to": "your reply-to address",
            "as_reached_through": first.name if first else "no list",
            "counts": result,
            "sender": sender.name,
            "next": "Check your inbox, then run: mailerlm sends poll",
        }

    return _guard(work)


def sends_start(
    name: str, fingerprint: str, lists: list[str] | None = None, background: bool = False
) -> Board:
    """The real send to `lists` (default: the campaign's lists). It repeats the plan for the same
    lists and stops unless the fingerprint still matches."""
    plan_board = sends_plan(name, lists)
    if plan_board["fingerprint"] != fingerprint:
        raise MailerlmError(
            "the letter, the lists or the audience changed since you reviewed it. Plan again"
        )
    if not plan_board["can_send"]:
        raise MailerlmError("cannot send: " + "; ".join(plan_board["blockers"]))

    def work() -> Board:
        _sender()  # fail before anything is recorded when the key is missing
        with _transaction() as connection:
            plan = _prepare(connection, name, lists)
            recipients = [
                sending.Recipient(
                    r.contact_id, r.email, "there", r.list_name, r.show_footer, r.test_prefix
                )
                for r in plan["reach"].recipients
            ]
            send_id = sending.create_send(
                connection,
                name,
                "live",
                plan["email"].subject,
                plan["fingerprint"],
                recipients,
                excerpt=plan["email"].text,
                lists=tuple(plan["lists"]),
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
        raise NotConfiguredError(str(error)) from error


def _classifier() -> Any:
    if classifier_for_tests is not None:
        return classifier_for_tests
    if not os.environ.get(inbox_classifier.KEY_ENV):
        raise NotConfiguredError(
            f"{inbox_classifier.KEY_ENV} is not set. Add it to .env (see env.example)"
        )
    return inbox_classifier.TypeSafeClassifier()


def _record_run(started: datetime, ok: bool, counts: dict | None, error: str | None) -> None:
    with _transaction() as connection:
        connection.execute(
            "INSERT INTO check_run (started_at, finished_at, ok, new_replies, waiting, error) VALUES (?, ?, ?, ?, ?, ?)",
            (
                started.isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"),
                int(ok),
                (counts or {}).get("new", 0),
                (counts or {}).get("queued", 0),
                error,
            ),
        )


def replies_check() -> Board:
    """Read new replies from people you have mailed; apply the safe ones, queue the rest. Every run is recorded."""
    started = datetime.now()
    try:
        board = _replies_check()
    except NotConfiguredError:
        raise  # a skip, not a run: nothing was attempted
    except Exception as error:
        _record_run(started, False, None, type(error).__name__)
        raise
    _record_run(
        started,
        board["errors"] == 0,
        board,
        None if board["errors"] == 0 else "some replies could not be read",
    )
    return board


def _replies_check() -> Board:
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
        last = connection.execute(
            "SELECT started_at, ok, error FROM check_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_good = connection.execute(
            "SELECT started_at FROM check_run WHERE ok = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "last_check": {"at": last[0], "ok": bool(last[1]), "error": last[2]} if last else None,
        "last_good_check": last_good[0] if last_good else None,
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


def env_backup_path(env_file: Path, stamp: str) -> Path:
    """Where `setup` backs up `.env` before editing it: outside the checkout, in
    `$XDG_STATE_HOME/mailerlm/env-backups/` (default `~/.local/state`), never beside `.env`, so
    no backup holding keys can enter git (metalm coding.md#security-and-secrets)."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "mailerlm" / "env-backups" / f"{env_file.name}.bak.{stamp}"
