"""Per-list sends: the owner picks the lists at send time; each letter is rendered with the settings
of the list its reader is reached through (footer, "Testing: ", sender); a person who would be mailed
through two chosen lists stops the send until the owner fixes the lists."""

import sqlite3

import pytest

from mailerlm import api, cli, paths, store
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError, NotFoundError

LINE = "If you'd rather not hear from me, just reply."


@pytest.fixture
def world(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(api.paths, "config_path", lambda: tmp_path / "config.toml")
    fake = FakeSender()
    monkeypatch.setattr(api, "sender_for_tests", fake)
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "1 Main St", LINE)
    for name in ("friends", "book", "Testing"):
        api.lists_add(name)
    api.lists_footer("friends", show=False)
    api.lists_test("Testing")
    api.campaigns_create("c", "confirmation")
    api.campaigns_save(
        "c",
        {
            "subject": "News",
            "body": "Dear friend,\n\nNews.\n\nAnn",
            "variables": {"book_title": "B"},
            "lists": ["friends", "book"],
        },
    )
    api.contacts_import_text("Ada Friend: ada@example.com", "friends")
    api.contacts_import_text("Grace Reader: grace@example.com", "book")
    api.contacts_import_text("Tess Tester: tess@example.com", "Testing")
    api.contacts_import_text("Bea Both: bea@example.com", "friends")
    return fake


def _set_status(email, list_name, status):
    connection = store.open_store(paths.database_path(paths.workspace_path()))
    try:
        connection.execute(
            "UPDATE membership SET status = ? WHERE contact_id = (SELECT id FROM contact WHERE email = ?) "
            "AND list_id = (SELECT id FROM mailing_list WHERE name = ?)",
            (status, email, list_name),
        )
    finally:
        connection.close()


def _by_address(fake):
    return {message.to: message for message in fake.sent}


def _deliveries(send_id):
    connection = store.open_store(paths.database_path(paths.workspace_path()))
    try:
        return dict(
            connection.execute(
                "SELECT email, list_name FROM delivery WHERE send_id = ?", (send_id,)
            ).fetchall()
        )
    finally:
        connection.close()


def test_each_person_gets_the_footer_of_the_list_they_are_reached_through(world):
    plan = api.sends_plan("c")
    board = api.sends_start("c", plan["fingerprint"])
    sent = _by_address(world)
    assert "1 Main St" not in sent["ada@example.com"].text
    assert "just reply" not in sent["ada@example.com"].html
    assert "1 Main St" in sent["grace@example.com"].text and LINE in sent["grace@example.com"].text
    for message in sent.values():
        assert message.unsubscribe_mailto == "mailto:ann+b@example.com?subject=unsubscribe"
    assert _deliveries(board["send"])["ada@example.com"] == "friends"
    assert _deliveries(board["send"])["grace@example.com"] == "book"


def test_only_the_people_reached_through_a_test_list_see_testing(world):
    plan = api.sends_plan("c", lists=["Testing", "book"])
    api.sends_start("c", plan["fingerprint"], lists=["Testing", "book"])
    sent = _by_address(world)
    assert sent["tess@example.com"].subject == "Testing: News"
    assert sent["grace@example.com"].subject == "News"
    assert api.campaigns_get("c")["campaign"]["subject"] == "News"


def test_a_person_mailed_through_two_chosen_lists_stops_the_send(world):
    api.contacts_join("bea@example.com", "book")
    plan = api.sends_plan("c", lists=["friends", "book"])
    assert plan["can_send"] is False
    [problem] = [p for p in plan["problems"] if "more than one chosen list" in p]
    assert "b***@e***.com" in problem and "friends (imported), book (imported)" in problem
    assert "mailerlm contacts leave" in problem and "drop a list from this send" in problem
    assert problem in plan["blockers"]
    check = api.campaigns_check("c")["problems"]
    assert any("b***@e***.com" in p and "more than one chosen list" in p for p in check)
    with pytest.raises(MailerlmError, match="more than one chosen list"):
        api.sends_start("c", plan["fingerprint"], lists=["friends", "book"])
    assert world.sent == []
    api.contacts_leave("bea@example.com", "book")
    assert api.sends_plan("c", lists=["friends", "book"])["can_send"] is True


def test_a_person_on_a_chosen_list_and_an_unchosen_one_is_fine(world):
    api.contacts_join("bea@example.com", "Testing")
    plan = api.sends_plan("c", lists=["friends", "book"])
    assert plan["can_send"] is True and plan["notes"] == []
    assert not [p for p in plan["problems"] if "chosen list" in p]


def test_an_overlap_that_would_not_be_mailed_twice_is_only_a_note(world):
    api.contacts_join("bea@example.com", "book")
    _set_status("bea@example.com", "book", "pending")
    plan = api.sends_plan("c", lists=["friends", "book"])
    assert plan["can_send"] is True
    [note] = plan["notes"]
    assert "b***@e***.com" in note and "friends (imported), book (pending)" in note
    assert not [p for p in plan["problems"] if "chosen list" in p]
    api.sends_start("c", plan["fingerprint"], lists=["friends", "book"])
    assert [m.to for m in world.sent].count("bea@example.com") == 1


def test_a_reader_reached_through_a_test_list_sees_testing_with_its_footer(world):
    plan = api.sends_plan("c", lists=["Testing", "friends"])
    board = api.sends_start("c", plan["fingerprint"], lists=["Testing", "friends"])
    sent = _by_address(world)
    assert sent["tess@example.com"].subject == "Testing: News"
    assert "1 Main St" in sent["tess@example.com"].text
    assert (
        sent["ada@example.com"].subject == "News"
        and "1 Main St" not in sent["ada@example.com"].text
    )
    assert _deliveries(board["send"])["tess@example.com"] == "Testing"


def test_the_plan_breaks_the_audience_down_per_list(world):
    plan = api.sends_plan("c", lists=["friends", "book"])
    rows = {row["list"]: row for row in plan["by_list"]}
    assert [row["list"] for row in plan["by_list"]] == ["friends", "book"]
    assert rows["friends"]["recipients"] == 2 and rows["friends"]["footer"] == "hidden"
    assert rows["friends"]["testing_prefix"] is False and rows["friends"]["subject"] == "News"
    assert rows["book"]["recipients"] == 1 and rows["book"]["footer"] == "shown"
    assert rows["book"]["from"] == "Ann <ann@mail.example.com>"
    assert plan["recipients"] == 3


def test_the_owner_picks_the_lists_at_send_time(world):
    plan = api.sends_plan("c", lists=["book"])
    assert plan["lists"] == ["book"] and plan["recipients"] == 1
    api.sends_start("c", plan["fingerprint"], lists=["book"])
    assert [m.to for m in world.sent] == ["grace@example.com"]
    assert sorted(api.campaigns_get("c")["campaign"]["lists"]) == ["book", "friends"]


def test_a_plan_cannot_be_started_with_other_lists(world):
    plan = api.sends_plan("c", lists=["book"])
    with pytest.raises(MailerlmError, match="changed"):
        api.sends_start("c", plan["fingerprint"])
    assert world.sent == []


def test_the_fingerprint_changes_with_the_lists_and_their_order(world):
    prints = {
        api.sends_plan("c", lists=lists)["fingerprint"]
        for lists in (["friends", "book"], ["book", "friends"], ["book"], None)
    }
    assert len(prints) == 3  # None is the campaign's own two lists, in one of those orders


def test_an_unknown_list_at_send_time_names_the_fix(world):
    with pytest.raises(NotFoundError, match="mailerlm lists add nope"):
        api.sends_plan("c", lists=["nope"])


def test_a_confirmation_marks_pending_on_the_lists_chosen_for_the_send(world):
    plan = api.sends_plan("c", lists=["Testing"])
    api.sends_start("c", plan["fingerprint"], lists=["Testing"])
    connection = store.open_store(paths.database_path(paths.workspace_path()))
    try:
        rows = connection.execute(
            "SELECT c.email, l.name, m.status FROM membership m JOIN contact c ON c.id = m.contact_id "
            "JOIN mailing_list l ON l.id = m.list_id WHERE m.status = 'pending'"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("tess@example.com", "Testing", "pending")]


def test_the_command_line_takes_lists_and_prints_the_breakdown(world, capsys):
    assert cli.main(["sends", "plan", "c", "--list", "book", "--list", "friends"]) == 0
    out = capsys.readouterr().out
    assert "lists: ['book', 'friends']" in out and "footer hidden" in out


def test_the_test_send_uses_the_first_chosen_list(world):
    api.sends_test("c", lists=["Testing"])
    assert world.sent[0].subject == "Testing: News" and world.sent[0].to == "ann+b@example.com"


def test_an_older_database_gains_the_columns_and_keeps_its_sends(tmp_path):
    path = tmp_path / "x.sqlite"
    older = store.MIGRATIONS[: store.MIGRATIONS.index(store.delivery_schema.LIST_MIGRATION)]
    connection = store.initialize(path, older)
    connection.execute(
        "INSERT INTO send (campaign_name, kind, status, subject, fingerprint, planned, created_at) "
        "VALUES ('c', 'live', 'done', 'S', 'f', 1, '2026-10-10')"
    )
    connection.execute(
        "INSERT INTO delivery (send_id, email, status, updated_at) VALUES (1, 'a@b.c', 'sent', 'x')"
    )
    connection.close()
    connection = store.open_store(path)
    assert connection.execute("SELECT lists FROM send").fetchall() == [("[]",)]
    assert connection.execute(
        "SELECT list_id, list_name, show_footer, test_prefix FROM delivery"
    ).fetchall() == [(None, None, None, None)]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE delivery SET show_footer = 2")
