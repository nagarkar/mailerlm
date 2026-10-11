"""The typed API: one function per CLI verb; the CLI is a logic-free surface over it.

Every function returns a board (a plain dict of counts, paths and a `next` hint), never content.

Never imports: cli (enforced by the import contract "api below cli" in pyproject.toml).
"""

from __future__ import annotations

import difflib
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from mailerlm import config, paths, store
from mailerlm.contacts import mask_email, parsing, repository
from mailerlm.errors import MailerlmError
from mailerlm.inbox import gmail

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
    workspace_pointer: str = paths.DEFAULT_WORKSPACE,
    now: Callable[[], datetime] = datetime.now,
) -> Board:
    """Record the workspace pointer in `.env` (inside the managed block only), then create the
    workspace folder and the database. A second run changes nothing and says so."""
    env_file = env_file or paths.env_path()
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
    workspace.mkdir(parents=True, exist_ok=True)
    store.initialize(database).close()
    board["workspace_exists"] = board["database_exists"] = True
    board["changed"] = changed
    board["next"] = "Run: mailerlm doctor"
    return board


def _check(name: str, state: str, detail: str, fix: str = "", required: bool = False) -> Board:
    return {"name": name, "state": state, "detail": detail, "fix": fix, "required": required}


def doctor(config_file: Path | None = None) -> Board:
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
                "missing",
                "no config.toml; defaults apply",
                "Copy config.example.toml to config.toml and fill it in",
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


# ---- contacts and lists ------------------------------------------------------------------


def _open(dry_run: bool) -> sqlite3.Connection:
    """The store; for a dry run an in-memory copy, so the change is real but nothing persists."""
    live = store.open_store(paths.database_path(paths.workspace_path()))
    if not dry_run:
        return live
    copy = sqlite3.connect(":memory:", isolation_level=None)
    live.backup(copy)
    live.close()
    copy.execute("PRAGMA foreign_keys = ON")
    return copy


def _guard(work: Callable[[], Board]) -> Board:
    try:
        return work()
    except repository.ContactsError as error:
        raise MailerlmError(str(error)) from error


def lists_add(
    name: str,
    sender_name: str | None = None,
    sender_address: str | None = None,
    dry_run: bool = False,
) -> Board:
    """Create a list, optionally with its own sender (otherwise config's sender is used)."""

    def work() -> Board:
        connection = _open(dry_run)
        created = repository.add_list(connection, name, sender_name, sender_address)
        connection.close()
        return {
            "dry_run": dry_run,
            "list": name,
            "created": created,
            "next": f"Run: mailerlm contacts import <file> --list {name}",
        }

    return _guard(work)


def lists_show() -> Board:
    connection = _open(False)
    summary = repository.lists_summary(connection)
    connection.close()
    return {
        "lists": summary,
        "next": "Run: mailerlm lists add <name>"
        if not summary
        else "Run: mailerlm contacts find --list <name>",
    }


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
        connection = _open(dry_run)
        counts = repository.import_candidates(connection, candidates[:1], list_name, tags)
        connection.close()
        return {"dry_run": dry_run, **_counts(counts), "next": "Run: mailerlm contacts find"}

    return _guard(work)


def _counts(counts: repository.ImportCounts) -> Board:
    return {name: getattr(counts, name) for name in counts.__slots__}


def contacts_import(
    source: str,
    list_name: str | None = None,
    tags: tuple[str, ...] = (),
    dry_run: bool = False,
) -> Board:
    """Import pasted text or a CSV from a file (`-` reads standard input)."""

    def work() -> Board:
        text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
        candidates, rejected_lines = parsing.parse(text)
        connection = _open(dry_run)
        counts = repository.import_candidates(connection, candidates, list_name, tags)
        connection.close()
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
        connection = _open(False)
        rows = repository.find(connection, list_name, tag, status)
        connection.close()
        for row in rows:
            row["email"] = row["email"] if show_full_addresses else mask_email(row["email"])
            row["linkedin_url"] = row["linkedin_url"] and "(linkedin)"
        return {"count": len(rows), "contacts": rows, "next": "Run: mailerlm lists show"}

    return _guard(work)


def _change(
    email: str, dry_run: bool, action: Callable[[sqlite3.Connection, int], bool], verb: str
) -> Board:
    def work() -> Board:
        connection = _open(dry_run)
        connection.execute("BEGIN")
        try:
            changed = action(connection, repository.contact_by_email(connection, email))
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        connection.close()
        return {"dry_run": dry_run, verb: changed, "next": "Run: mailerlm contacts find"}

    return _guard(work)


def contacts_tag(email: str, tag: str, dry_run: bool = False) -> Board:
    return _change(email, dry_run, lambda c, i: repository.add_tag(c, i, tag), "tag_added")


def contacts_untag(email: str, tag: str, dry_run: bool = False) -> Board:
    return _change(email, dry_run, lambda c, i: repository.remove_tag(c, i, tag), "tag_removed")


def contacts_join(email: str, list_name: str, dry_run: bool = False) -> Board:
    return _change(
        email,
        dry_run,
        lambda c, i: repository.join_list(c, i, repository.list_id(c, list_name)),
        "joined",
    )


def contacts_leave(email: str, list_name: str, dry_run: bool = False) -> Board:
    return _change(
        email,
        dry_run,
        lambda c, i: repository.leave_list(c, i, repository.list_id(c, list_name)),
        "left",
    )


def contacts_do_not_mail(email: str, clear: bool = False, dry_run: bool = False) -> Board:
    def action(connection: sqlite3.Connection, contact_id: int) -> bool:
        repository.set_do_not_mail(connection, contact_id, not clear)
        return True

    return _change(email, dry_run, action, "do_not_mail_cleared" if clear else "do_not_mail_set")
