"""Owner-attested confirmation: the owner moves a membership straight to `subscribed`, per list, with a
required reason, never for someone who said no or bounced; it shows in `contacts find` and the history."""

import sqlite3

import pytest

from mailerlm import api, cli, paths, store
from mailerlm.consent import states
from mailerlm.errors import MailerlmError


@pytest.fixture
def world(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(api.paths, "config_path", lambda: tmp_path / "config.toml")
    api.lists_add("friends")
    api.lists_add("book")
    api.contacts_import_text("Ada Friend: ada@example.com\nBea Friend: bea@example.com", "friends")
    api.contacts_join("ada@example.com", "book")


def _db():
    return store.open_store(paths.database_path(paths.workspace_path()))


def _status(email, list_name):
    connection = _db()
    try:
        return connection.execute(
            "SELECT m.status FROM membership m JOIN contact c ON c.id = m.contact_id "
            "JOIN mailing_list l ON l.id = m.list_id WHERE c.email = ? AND l.name = ?",
            (email, list_name),
        ).fetchone()[0]
    finally:
        connection.close()


def _set(email, list_name, status):
    connection = _db()
    try:
        connection.execute(
            "UPDATE membership SET status = ? WHERE contact_id = (SELECT id FROM contact WHERE email = ?) "
            "AND list_id = (SELECT id FROM mailing_list WHERE name = ?)",
            (status, email, list_name),
        )
    finally:
        connection.close()


def _find(email):
    rows = api.contacts_find(show_full_addresses=True)["contacts"]
    return next(row for row in rows if row["email"] == email)


def test_the_owner_confirms_one_membership_with_a_reason_and_only_on_that_list(world):
    board = api.contacts_confirm("ada@example.com", "friends", reason="known personally")
    assert board["confirmed"] is True
    assert _status("ada@example.com", "friends") == "subscribed"
    assert _status("ada@example.com", "book") == "imported"
    row = _find("ada@example.com")
    assert row["lists"]["friends"] == "subscribed"
    assert row["confirmed"] == {"friends": "by owner: known personally"}


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_reason_is_required(world, reason):
    with pytest.raises(MailerlmError, match="reason"):
        api.contacts_confirm("ada@example.com", "friends", reason=reason)
    assert _status("ada@example.com", "friends") == "imported"


def test_pending_is_confirmed_too_and_a_second_confirm_changes_nothing(world):
    _set("bea@example.com", "friends", "pending")
    assert api.contacts_confirm("bea@example.com", "friends", reason="confirmed by email")[
        "confirmed"
    ]
    again = api.contacts_confirm("bea@example.com", "friends", reason="confirmed by email")
    assert again["confirmed"] is False and "already subscribed" in again["note"]


@pytest.mark.parametrize(
    ("status", "why"), [("unsubscribed", "unsubscribed"), ("suppressed", "suppressed")]
)
def test_someone_who_said_no_or_bounced_is_never_confirmed(world, status, why):
    _set("ada@example.com", "friends", status)
    with pytest.raises(MailerlmError, match=why):
        api.contacts_confirm("ada@example.com", "friends", reason="known personally")
    assert _status("ada@example.com", "friends") == status


def test_a_do_not_mail_contact_is_never_confirmed(world):
    api.contacts_do_not_mail("ada@example.com")
    with pytest.raises(MailerlmError, match="do-not-mail"):
        api.contacts_confirm("ada@example.com", "friends", reason="known personally")
    assert _status("ada@example.com", "friends") == "imported"


def test_someone_not_on_the_list_names_the_fix(world):
    with pytest.raises(MailerlmError, match="not on the list"):
        api.contacts_confirm("bea@example.com", "book", reason="known personally")


def test_a_whole_list_is_confirmed_after_a_dry_run_that_shows_who(world):
    api.contacts_import_text("Cy Gone: cy@example.com", "friends")
    _set("cy@example.com", "friends", "unsubscribed")
    dry = api.lists_confirm("friends", reason="known personally", dry_run=True)
    assert dry["dry_run"] is True
    assert sorted(p["name"] for p in dry["would_confirm"]) == ["Ada Friend", "Bea Friend"]
    assert dry["declined"] == [
        {
            "contact": dry["declined"][0]["contact"],
            "name": "Cy Gone",
            "email": "c***@e***.com",
            "why": "they unsubscribed",
        }
    ]
    assert _status("ada@example.com", "friends") == "imported"
    done = api.lists_confirm("friends", reason="known personally")
    assert len(done["confirmed"]) == 2 and done["already_subscribed"] == 0
    assert _status("bea@example.com", "friends") == "subscribed"
    assert _status("ada@example.com", "book") == "imported"
    assert _status("cy@example.com", "friends") == "unsubscribed"


def test_the_history_tells_owner_confirmation_from_reply_confirmation(world):
    api.contacts_confirm("ada@example.com", "friends", reason="known personally")
    _set("bea@example.com", "friends", "pending")
    connection = _db()
    try:
        bea = connection.execute(
            "SELECT id FROM contact WHERE email = 'bea@example.com'"
        ).fetchone()[0]
        states.confirm(connection, bea)
    finally:
        connection.close()
    assert _find("bea@example.com")["confirmed"] == {"friends": "by reply"}
    events = api.contacts_history("ada@example.com")["history"]
    assert [(e["list"], e["previous"], e["new"], e["how"]) for e in events] == [
        ("friends", "imported", "subscribed", "confirmed by owner: known personally")
    ]
    assert api.contacts_history("bea@example.com")["history"][-1]["how"] == "confirmed by reply"


def test_adding_people_can_confirm_them_in_the_same_step_when_asked(world):
    api.contacts_add(
        "dee@example.com", "Dee", list_name="friends", confirm_reason="known personally"
    )
    assert _status("dee@example.com", "friends") == "subscribed"
    api.contacts_import_text(
        "Eve: eve@example.com", "friends", confirm_reason="confirmed in person"
    )
    assert _find("eve@example.com")["confirmed"] == {"friends": "by owner: confirmed in person"}
    api.contacts_import_text("Fay: fay@example.com", "friends")
    assert _status("fay@example.com", "friends") == "imported"
    with pytest.raises(MailerlmError, match="list"):
        api.contacts_add("gus@example.com", "Gus", confirm_reason="known personally")


def test_the_command_line_needs_a_reason(world):
    assert cli.main(["contacts", "confirm", "ada@example.com", "friends"]) != 0
    assert _status("ada@example.com", "friends") == "imported"
    assert (
        cli.main(
            ["contacts", "confirm", "ada@example.com", "friends", "--reason", "known personally"]
        )
        == 0
    )
    assert (
        cli.main(["lists", "confirm", "friends", "--reason", "known personally", "--dry-run"]) == 0
    )
    assert _status("bea@example.com", "friends") == "imported"


def test_a_confirmed_friend_is_reached_by_a_campaign(world):
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "1 Main St")
    api.campaigns_create("c", "update")
    api.campaigns_save("c", {"variables": {"book_title": "B"}})
    assert api.sends_plan("c", lists=["friends"])["recipients"] == 0
    api.lists_confirm("friends", reason="known personally")
    assert api.sends_plan("c", lists=["friends"])["recipients"] == 2


def test_an_older_database_gains_the_columns_and_the_history(tmp_path):
    path = tmp_path / "x.sqlite"
    older = store.MIGRATIONS[: store.MIGRATIONS.index(store.contacts_schema.CONFIRMATION_MIGRATION)]
    connection = store.initialize(path, older)
    connection.execute("INSERT INTO contact (email, created_at) VALUES ('a@b.c', 'x')")
    connection.execute("INSERT INTO mailing_list (name, created_at) VALUES ('l', 'x')")
    connection.execute("INSERT INTO membership VALUES (1, 1, 'subscribed', 'x')")
    connection.close()
    connection = store.open_store(path)
    assert connection.execute("SELECT confirmed_by, confirm_reason FROM membership").fetchall() == [
        (None, None)
    ]
    assert connection.execute("SELECT COUNT(*) FROM membership_event").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE membership SET confirmed_by = 'someone'")
