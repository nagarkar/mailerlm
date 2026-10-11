"""contacts and lists through the typed API on a throwaway workspace."""

import pytest

from mailerlm import api
from mailerlm.errors import MailerlmError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path


def test_import_adds_joins_and_a_second_import_adds_nothing(workspace):
    api.lists_add("book-interest")
    source = workspace / "people.txt"
    source.write_text("Ada: ada@example.com\ngrace@example.com\njunk\n")
    first = api.contacts_import(str(source), "book-interest", ("early",))
    assert (first["added"], first["memberships_added"], first["tags_added"]) == (2, 2, 2)
    assert first["rejected_line_numbers"] == [3]
    second = api.contacts_import(str(source), "book-interest", ("early",))
    assert (
        second["added"],
        second["already_present"],
        second["memberships_added"],
        second["tags_added"],
    ) == (0, 2, 0, 0)


def test_imported_contacts_are_not_subscribed(workspace):
    api.lists_add("book-interest")
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    assert api.lists_show()["lists"][0]["by_status"] == {"imported": 1}


def test_dry_run_reports_counts_and_persists_nothing(workspace):
    api.lists_add("book-interest")
    board = api.contacts_add(email="ada@example.com", list_name="book-interest", dry_run=True)
    assert board["added"] == 1
    assert api.contacts_find()["count"] == 0


def test_a_rematch_fills_blanks_and_never_overwrites(workspace):
    api.contacts_add(email="ada@example.com", name="Ada")
    api.contacts_add(
        email="ada@example.com", name="Someone Else", linkedin_url="https://www.linkedin.com/in/ada"
    )
    row = api.contacts_find(show_full_addresses=True)["contacts"][0]
    assert row["name"] == "Ada" and row["linkedin_url"]


def test_find_masks_addresses_unless_asked(workspace):
    api.contacts_add(email="ada@example.com")
    assert api.contacts_find()["contacts"][0]["email"] == "a***@e***.com"
    assert api.contacts_find(show_full_addresses=True)["contacts"][0]["email"] == "ada@example.com"


def test_tags_joins_leaves_and_the_global_block(workspace):
    api.lists_add("a")
    api.lists_add("b")
    api.contacts_add(email="ada@example.com", list_name="a")
    api.contacts_join("ada@example.com", "b")
    api.contacts_tag("ADA@example.com", "early")
    api.contacts_leave("ada@example.com", "a")
    api.contacts_do_not_mail("ada@example.com")
    row = api.contacts_find()["contacts"][0]
    assert (
        row["lists"] == {"b": "imported"}
        and row["tags"] == ["early"]
        and row["do_not_mail"] is True
    )
    api.contacts_do_not_mail("ada@example.com", clear=True)
    assert api.contacts_find(tag="early")["contacts"][0]["do_not_mail"] is False


def test_an_unknown_list_or_contact_names_the_fix(workspace):
    with pytest.raises(MailerlmError, match="mailerlm lists add nope"):
        api.contacts_add(email="ada@example.com", list_name="nope")
    with pytest.raises(MailerlmError, match="contacts find"):
        api.contacts_tag("ghost@example.com", "x")


def test_a_failed_import_rolls_back_everything(workspace):
    with pytest.raises(MailerlmError):
        api.contacts_add(email="ada@example.com", list_name="missing")
    assert api.contacts_find()["count"] == 0


def test_a_linkedin_only_contact_is_allowed(workspace):
    api.contacts_add(linkedin_url="https://www.linkedin.com/in/someone")
    assert api.contacts_find()["contacts"][0]["email"] == "(no email)"
