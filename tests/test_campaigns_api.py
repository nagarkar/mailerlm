"""campaign verbs on a throwaway workspace: new, list, check (audience), preview."""

import pytest
from PIL import Image

from mailerlm import api, paths
from mailerlm.errors import MailerlmError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text(
        '[book]\ntitle = "The Book"\n[sender]\nname = "Ann"\npostal_address = "1 Main St"\nreply_to = "a+b@example.com"\n'
    )
    return tmp_path / "ws"


def test_new_copies_a_starter_never_overwrites_and_dry_run_writes_nothing(workspace):
    assert api.campaigns_new("a", "update", dry_run=True)["dry_run"] is True
    assert not (workspace / "campaigns" / "a.md").exists()
    api.campaigns_new("a", "update")
    assert (workspace / "campaigns" / "a.md").exists()
    with pytest.raises(MailerlmError, match="already exists"):
        api.campaigns_new("a", "update")
    with pytest.raises(MailerlmError, match="Choose one of"):
        api.campaigns_new("b", "nope")


def test_every_starter_renders_cleanly(workspace):
    api.lists_add("book-interest")
    for starter in api.campaigns_list()["starters"]:
        api.campaigns_new(starter, starter)
        board = api.campaigns_check(starter)
        assert board["problems"] == [], (starter, board["problems"])


def test_check_counts_only_subscribed_members_and_says_why_others_are_skipped(workspace):
    api.lists_add("book-interest")
    api.contacts_add(email="a@example.com", list_name="book-interest")
    api.contacts_add(email="b@example.com", list_name="book-interest")
    api.campaigns_new("u", "update")
    assert api.campaigns_check("u")["would_reach"] == 0
    assert api.campaigns_check("u")["skipped"] == {"status imported": 2}
    api.campaigns_new("c", "confirmation")
    # the confirmation starter names no list: add one
    path = workspace / "campaigns" / "c.md"
    path.write_text(path.read_text().replace("lists: []", "lists: [book-interest]"))
    assert api.campaigns_check("c")["would_reach"] == 2


def test_check_reports_a_missing_list(workspace):
    api.campaigns_new("u", "update")
    board = api.campaigns_check("u")
    assert board["ok"] is False and "lists add book-interest" in board["problems"][0]


def test_preview_writes_html_with_inline_images(workspace):
    api.lists_add("book-interest")
    api.campaigns_new("u", "update")
    path = workspace / "campaigns" / "u.md"
    Image.new("RGB", (300, 100), "red").save(workspace / "campaigns" / "pic.png")
    path.write_text(path.read_text() + "\n![a red bar](pic.png)\n")
    board = api.campaigns_preview("u", first_name="Ada")
    html = (workspace / "previews" / "u.html").read_text()
    assert "data:image/" in html and "Hi Ada," in html and board["inline_images"] == 1


def test_an_unknown_campaign_names_the_fix(workspace):
    with pytest.raises(MailerlmError, match="campaigns list"):
        api.campaigns_check("ghost")
