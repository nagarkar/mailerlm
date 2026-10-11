"""The typed API: one function per CLI verb; the CLI is a logic-free surface over it.

Every function returns a board (a plain dict of counts, paths and a `next` hint), never content.

Never imports: cli (enforced by the import contract "api below cli" in pyproject.toml).
"""

from __future__ import annotations

import difflib
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from mailerlm import config, paths, store
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
