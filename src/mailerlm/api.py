"""The typed API: one function per CLI verb; the CLI is a logic-free surface over it.

Every function returns a board (a plain dict of counts, paths and a `next` hint), never content.

Never imports: cli (enforced by the import contract "api below cli" in pyproject.toml).
"""

from __future__ import annotations

import base64
import contextlib
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
from mailerlm.campaigns import audience, frontmatter, render
from mailerlm.campaigns import repository as campaigns_repository
from mailerlm.contacts import mask_email, parsing, repository
from mailerlm.errors import MailerlmError, NotFoundError
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


def templates_get(name: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            return {"template": campaigns_repository.template_get(connection, name)}

    return _guard(work)


def templates_save(name: str, html: str, text: str) -> Board:
    def work() -> Board:
        with _transaction() as connection:
            campaigns_repository.template_save(connection, name, html, text)
        return {"saved": name}

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
    connection: sqlite3.Connection, name: str, first_name: str
) -> tuple[dict, render.RenderedEmail]:
    campaign = campaigns_repository.campaign_get(connection, name)
    defaults = campaigns_repository.settings_get(connection)
    frame = campaigns_repository.template_get(connection, campaign["template"])
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


def campaigns_preview(name: str, first_name: str = "Friend", write_files: bool = True) -> Board:
    """The rendered email (images inline as data addresses). From the command line it is also
    written under the workspace's previews folder to open in a browser."""

    def work() -> Board:
        with _transaction() as connection:
            _, email = _render(connection, name, first_name)
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
    return {
        "lists": lists,
        "contacts": contacts,
        "campaigns": campaigns,
        "templates": len(templates),
        "sender_set": bool(
            defaults["sender_name"] and defaults["postal_address"] and defaults["reply_to"]
        ),
    }
