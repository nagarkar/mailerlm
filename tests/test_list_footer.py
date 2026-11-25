"""A list can hide the footer: when every list a campaign goes to hides it, the letter has no opt-out
line and no postal address (the List-Unsubscribe header stays)."""

import sqlite3

import pytest

from mailerlm import api, cli, store
from mailerlm.campaigns import render
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError

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
    api.lists_add("friends")
    api.lists_add("book-interest")
    api.campaigns_create("c", "confirmation")
    api.campaigns_save(
        "c",
        {
            "subject": "Hello",
            "body": "Dear friend,\n\nNews.\n\nAnn",
            "variables": {"book_title": "B"},
        },
    )
    api.contacts_import_text("Ada Lovelace: ada@example.com", "friends")
    api.contacts_import_text("Grace Hopper: grace@example.com", "book-interest")
    return fake


def _shown() -> dict[str, bool]:
    return {item["name"]: item["show_footer"] for item in api.lists_show()["lists"]}


def test_a_list_shows_the_footer_until_it_is_hidden_and_shown_again(world):
    assert _shown() == {"book-interest": True, "friends": True}
    assert api.lists_footer("friends", show=False)["show_footer"] is False
    assert _shown()["friends"] is False
    api.lists_footer("friends", show=True)
    assert _shown()["friends"] is True


def test_a_dry_run_changes_nothing_and_an_unknown_list_names_the_fix(world):
    assert api.lists_footer("friends", show=False, dry_run=True)["show_footer"] is False
    assert _shown()["friends"] is True
    with pytest.raises(MailerlmError, match="mailerlm lists add nope"):
        api.lists_footer("nope", show=False)


def test_the_command_line_hides_and_shows_and_the_listing_marks_it(world, capsys):
    assert cli.main(["lists", "footer", "friends", "--hide"]) == 0
    assert _shown()["friends"] is False
    text = cli.format_board(api.lists_show(), as_json=False)
    lines = {line.split(":")[0].strip(): line for line in text.splitlines()}
    assert "[footer hidden]" in lines["friends"] and "[footer hidden]" not in lines["book-interest"]
    assert cli.main(["lists", "footer", "friends", "--show"]) == 0
    assert _shown()["friends"] is True
    assert cli.main(["lists", "footer", "friends"]) != 0


def test_renaming_a_list_keeps_the_setting(world):
    api.lists_footer("friends", show=False)
    api.lists_edit("friends", "pals")
    assert _shown()["pals"] is False


def test_a_letter_to_lists_that_all_hide_the_footer_has_none(world):
    api.lists_footer("friends", show=False)
    api.campaigns_save("c", {"lists": ["friends"]})
    preview = api.campaigns_preview("c", write_files=False)
    for version in (preview["text"], preview["html"]):
        assert "just reply" not in version and "1 Main St" not in version
    assert preview["text"].rstrip().endswith("Ann") and "--" not in preview["text"]
    assert preview["text"].endswith("Ann\n")
    assert "border-top" not in preview["html"] and "News." in preview["html"]
    check = api.campaigns_check("c")
    assert check["footer"] == "hidden: every list is set to hide it"
    plan = api.sends_plan("c")
    assert plan["footer"] == "hidden: every list is set to hide it", plan
    assert plan["can_send"], plan["blockers"]
    api.sends_start("c", plan["fingerprint"])
    message = world.sent[0]
    assert "1 Main St" not in message.text and "just reply" not in message.html
    assert message.unsubscribe_mailto == "mailto:ann+b@example.com?subject=unsubscribe"


def test_one_list_that_shows_the_footer_keeps_it(world):
    api.lists_footer("friends", show=False)
    api.campaigns_save("c", {"lists": ["friends", "book-interest"]})
    preview = api.campaigns_preview("c", write_files=False)
    assert (
        LINE in preview["text"] and "1 Main St" in preview["text"] and "\n--\n" in preview["text"]
    )
    assert api.sends_plan("c")["footer"] == "shown"


def test_a_hidden_footer_needs_no_postal_address_or_opt_out_line(world):
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "", "")
    api.lists_footer("friends", show=False)
    api.campaigns_save("c", {"lists": ["friends"], "unsubscribe_line": "Bye."})
    plan = api.sends_plan("c")
    assert not [p for p in plan["problems"] if "postal" in p or "opt-out" in p]


TEXT_FRAME = (
    "{{ body_text }}\n\n--\n{{ unsubscribe_line }}\n{{ sender_name }}, {{ postal_address }}\n"
)


def _hidden(frame_html, frame_text=TEXT_FRAME):
    return render.render(
        subject="S",
        body="Hello.",
        preview_text="",
        frame_html=frame_html,
        frame_text=frame_text,
        variables={},
        sender_name="Ann",
        postal_address="1 Main St",
        reply_to="ann@example.com",
        unsubscribe_line=LINE,
        resolve_image=lambda _name: None,
        image_budget_bytes=0,
        show_footer=False,
    )


def test_an_edited_template_with_the_footer_in_two_places_loses_both_and_keeps_the_letter():
    html = (
        "<div><p>{{ unsubscribe_line }}</p>{{ body_html | safe }}"
        "<p class='addr'>{{ sender_name }}, {{ postal_address }}</p><p>Thanks!</p></div>"
    )
    text = "{{ unsubscribe_line }}\n\n{{ body_text }}\n-- \n{{ postal_address }}\n"
    email = _hidden(html, text)
    assert (
        email.html
        == '<div><p style="margin:0 0 16px;line-height:1.55;">Hello.</p>\n<p>Thanks!</p></div>'
    )
    assert email.text == "Hello.\n"


def test_a_footer_beside_other_text_loses_only_the_footer():
    email = _hidden(
        "<footer>{{ unsubscribe_line }} {{ sender_name }}, {{ postal_address }}</footer><p>P.S.</p>{{ body_html | safe }}"
    )
    assert email.html.startswith("<p>P.S.</p>") and LINE not in email.html


def test_an_older_database_gains_the_setting_shown(tmp_path):
    path = tmp_path / "x.sqlite"
    older = store.MIGRATIONS[: store.MIGRATIONS.index(store.contacts_schema.FOOTER_MIGRATION)]
    connection = store.initialize(path, older)
    connection.execute("INSERT INTO mailing_list (name, created_at) VALUES ('old', '2026-10-10')")
    connection.close()
    connection = store.open_store(path)
    assert connection.execute("SELECT name, show_footer FROM mailing_list").fetchall() == [
        ("old", 1)
    ]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE mailing_list SET show_footer = 2")
