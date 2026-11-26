"""preferred name: the name a person likes to be called by, apart from the official first name."""

import sqlite3

import pytest

from mailerlm import api, cli, store
from mailerlm.campaigns import render
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
    return fake


def _row(email):
    return next(
        c for c in api.contacts_find(show_full_addresses=True)["contacts"] if c["email"] == email
    )


def _render(body, **names):
    return render.render(
        subject="S",
        body=body,
        preview_text="",
        frame_html="{{ body_html | safe }}",
        frame_text="{{ body_text }}",
        variables={},
        sender_name="Ann",
        postal_address="1 Main St",
        reply_to="ann@example.com",
        unsubscribe_line="Reply to stop.",
        resolve_image=lambda _name: None,
        image_budget_bytes=0,
        **names,
    )


def test_preferred_name_fills_its_placeholder_and_first_name_keeps_the_official_one():
    email = _render(
        "Hi {{ preferred_name }} ({{ first_name }})", first_name="Grace", preferred_name="Hopper"
    )
    assert "Hi Hopper (Grace)" in email.text


def test_without_a_preferred_name_the_placeholder_is_the_first_name():
    assert "Hi Ada" in _render("Hi {{ preferred_name }}", first_name="Ada").text
    assert "Hi there" in _render("Hi {{ preferred_name }}").text


def test_preferred_name_is_a_reserved_value_name(world):
    api.campaigns_create("a", "update")
    with pytest.raises(MailerlmError, match="variable name"):
        api.campaigns_save("a", {"variables": {"preferred_name": "x"}})


def test_add_edit_and_clear_a_preferred_name(world):
    api.contacts_add(email="g@example.com", name="Grace Hopper", preferred_name="Hopper")
    assert _row("g@example.com")["preferred_name"] == "Hopper"
    api.contacts_edit("g@example.com", preferred_name="")
    row = _row("g@example.com")
    assert row["preferred_name"] is None and row["name"] == "Grace Hopper"
    api.contacts_edit("g@example.com", preferred_name="  Hopper ")
    row = _row("g@example.com")
    assert row["preferred_name"] == "Hopper" and row["name"] == "Grace Hopper"


def test_an_edit_changes_only_the_fields_given(world):
    api.contacts_add(
        email="g@example.com", name="Grace Hopper", linkedin_url="https://www.linkedin.com/in/g"
    )
    api.contacts_edit("g@example.com", name="Grace B Hopper")
    row = _row("g@example.com")
    assert (
        row["name"] == "Grace B Hopper" and row["linkedin_url"] == "https://www.linkedin.com/in/g"
    )


def test_an_edit_dry_run_changes_nothing(world):
    api.contacts_add(email="g@example.com", name="Grace Hopper")
    api.contacts_edit("g@example.com", preferred_name="Hopper", dry_run=True)
    assert _row("g@example.com")["preferred_name"] is None


def test_a_csv_import_reads_a_preferred_name_column_and_never_overwrites_one(world):
    api.contacts_import_text(
        "name,email,preferred_name\nGrace Hopper,g@example.com,Hopper\nAda Lovelace,ada@example.com,\n"
    )
    assert _row("g@example.com")["preferred_name"] == "Hopper"
    assert _row("ada@example.com")["preferred_name"] is None
    api.contacts_import_text("name,email,preferred_name\nGrace Hopper,g@example.com,Gracie\n")
    assert _row("g@example.com")["preferred_name"] == "Hopper"


def test_a_real_send_greets_each_person_by_their_preferred_name(world):
    api.campaigns_create("c", "confirmation")
    api.campaigns_save("c", {"lists": ["book-interest"], "variables": {"book_title": "The Book"}})
    api.contacts_add(
        email="g@example.com",
        name="Grace Hopper",
        preferred_name="Hopper",
        list_name="book-interest",
    )
    api.contacts_add(email="ada@example.com", name="Ada Lovelace", list_name="book-interest")
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    letters = {m.to: m.text for m in world.sent}
    assert "Hi Hopper," in letters["g@example.com"] and "Grace" not in letters["g@example.com"]
    assert "Hi Ada," in letters["ada@example.com"]


def test_the_preview_fills_both_names(world):
    api.campaigns_create("u", "update")
    api.campaigns_save(
        "u", {"body": "{{ preferred_name }}/{{ first_name }}", "variables": {"book_title": "T"}}
    )
    assert "Ada/Ada" in api.campaigns_preview("u", first_name="Ada", write_files=False)["text"]
    board = api.campaigns_preview(
        "u", first_name="Grace", preferred_name="Hopper", write_files=False
    )
    assert "Hopper/Grace" in board["text"]


def test_every_starter_and_the_sample_letter_greet_by_preferred_name():
    for path in sorted(api.STARTER_DIRECTORY.glob("*.md")):
        text = path.read_text()
        assert "Hi {{ preferred_name }}," in text and "{{ first_name }}" not in text, path.name
    assert api.SAMPLE_LETTER.startswith("Hi {{ preferred_name }},")


def test_the_cli_sets_and_clears_a_preferred_name(world, capsys):
    api.contacts_add(email="g@example.com", name="Grace Hopper")
    assert cli.main(["contacts", "edit", "g@example.com", "--preferred-name", "Hopper"]) == 0
    assert _row("g@example.com")["preferred_name"] == "Hopper"
    capsys.readouterr()
    assert cli.main(["contacts", "find"]) == 0
    assert "preferred 'Hopper'" in capsys.readouterr().out
    assert cli.main(["contacts", "edit", "g@example.com", "--preferred-name", ""]) == 0
    assert _row("g@example.com")["preferred_name"] is None
    assert (
        cli.main(
            [
                "contacts",
                "add",
                "--email",
                "s@example.com",
                "--name",
                "Sam Ng",
                "--preferred-name",
                "Sammy",
            ]
        )
        == 0
    )
    assert _row("s@example.com")["preferred_name"] == "Sammy"


def test_an_older_database_gains_an_empty_preferred_name_column(tmp_path):
    path = tmp_path / "x.sqlite"
    store.initialize(path, store.MIGRATIONS[:-1]).close()
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO contact (email, name, created_at) VALUES ('g@example.com', 'Grace Hopper', 'now')"
    )
    connection.commit()
    connection.close()
    store.open_store(path).close()
    assert store.version(path) == len(store.MIGRATIONS)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT name, preferred_name FROM contact").fetchone() == (
        "Grace Hopper",
        None,
    )
