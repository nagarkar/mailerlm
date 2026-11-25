"""test lists: the flag on a list, and the "Testing: " subject on every send whose audience is one."""

import sqlite3

import pytest

from mailerlm import api, cli, store
from mailerlm.campaigns import audience
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError


@pytest.fixture
def world(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(api.paths, "config_path", lambda: tmp_path / "config.toml")
    fake = FakeSender()
    monkeypatch.setattr(api, "sender_for_tests", fake)
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "1 Main St")
    api.lists_add("book-interest")
    api.lists_add("Testing")
    api.lists_test("Testing")
    api.campaigns_create("c", "confirmation")
    api.campaigns_save(
        "c", {"subject": "News on {{ book_title }}", "variables": {"book_title": "The Book"}}
    )
    api.contacts_import_text("Ada Lovelace: ada@example.com", "Testing")
    api.contacts_import_text("Grace Hopper: grace@example.com", "book-interest")
    return fake


def _flags() -> dict[str, bool]:
    return {item["name"]: item["test"] for item in api.lists_show()["lists"]}


def test_a_list_is_marked_as_a_test_list_and_unmarked(world):
    assert _flags() == {"Testing": True, "book-interest": False}
    assert api.lists_test("Testing", clear=True)["test"] is False
    assert _flags()["Testing"] is False


def test_a_dry_run_marks_nothing_and_an_unknown_list_names_the_fix(world):
    assert api.lists_test("book-interest", dry_run=True)["test"] is True
    assert _flags()["book-interest"] is False
    with pytest.raises(MailerlmError, match="mailerlm lists add nope"):
        api.lists_test("nope")


def test_renaming_a_list_keeps_the_flag(world):
    api.lists_edit("Testing", "Trial")
    assert _flags()["Trial"] is True


def test_the_listing_shows_the_flag(world):
    text = cli.format_board(api.lists_show(), as_json=False)
    lines = {line.split(":")[0].strip(): line for line in text.splitlines()}
    assert "[test list]" in lines["Testing"] and "[test list]" not in lines["book-interest"]


def test_a_send_to_a_test_list_carries_the_prefix_and_the_stored_subject_stays(world):
    api.campaigns_save("c", {"lists": ["Testing"]})
    plan = api.sends_plan("c")
    assert plan["subject"] == "Testing: News on The Book" and plan["test_list"] is True
    assert api.campaigns_check("c")["subject"] == "Testing: News on The Book"
    assert api.campaigns_preview("c", write_files=False)["subject"] == "Testing: News on The Book"
    board = api.sends_start("c", plan["fingerprint"])
    assert [(m.to, m.subject) for m in world.sent] == [
        ("ada@example.com", "Testing: News on The Book")
    ]
    assert api.sends_status(board["send"])["send"]["subject"] == "Testing: News on The Book"
    assert api.campaigns_get("c")["campaign"]["subject"] == "News on {{ book_title }}"


def test_a_test_send_of_a_test_list_campaign_carries_the_prefix_too(world):
    api.campaigns_save("c", {"lists": ["Testing"]})
    api.sends_test("c")
    assert world.sent[0].subject == "Testing: News on The Book"


def test_a_send_to_a_real_list_has_no_prefix(world):
    api.campaigns_save("c", {"lists": ["book-interest"]})
    plan = api.sends_plan("c")
    assert plan["subject"] == "News on The Book" and plan["test_list"] is False
    api.sends_start("c", plan["fingerprint"])
    assert world.sent[0].subject == "News on The Book"


def test_a_campaign_that_also_names_a_real_list_has_no_prefix(world):
    api.campaigns_save("c", {"lists": ["Testing", "book-interest"]})
    assert api.sends_plan("c")["subject"] == "News on The Book"


def test_the_same_campaign_moves_from_the_test_list_to_the_real_one(world):
    api.campaigns_save("c", {"lists": ["Testing"]})
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    api.campaigns_save("c", {"lists": ["book-interest"]})
    plan = api.sends_plan("c")
    assert plan["can_send"] and plan["subject"] == "News on The Book"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Hello", "Testing: Hello"),
        ("Testing: Hello", "Testing: Hello"),
        ("testing:  Hello", "testing:  Hello"),
        ("  Hello", "Testing: Hello"),
        ("Testing the waters", "Testing: Testing the waters"),
        ("", ""),
        ("   ", "   "),
    ],
)
def test_the_prefix_is_added_once_and_never_to_an_empty_subject(subject, expected):
    assert audience.test_subject(subject) == expected


def test_an_older_database_gains_the_flag_and_keeps_its_lists(tmp_path):
    path = tmp_path / "x.sqlite"
    older = store.MIGRATIONS[: store.MIGRATIONS.index(store.contacts_schema.TEST_LIST_MIGRATION)]
    connection = store.initialize(path, older)
    connection.execute("INSERT INTO mailing_list (name, created_at) VALUES ('old', '2026-10-10')")
    connection.close()
    connection = store.open_store(path)
    assert connection.execute("SELECT name, is_test FROM mailing_list").fetchall() == [("old", 0)]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE mailing_list SET is_test = 2")
