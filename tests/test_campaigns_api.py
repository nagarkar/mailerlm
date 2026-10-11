"""campaigns, templates, settings and images through the typed API on a throwaway workspace."""

import io

import pytest
from PIL import Image

from mailerlm import api
from mailerlm.campaigns import repository
from mailerlm.campaigns.repository import CampaignsError
from mailerlm.errors import MailerlmError, NotFoundError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "1 Main St")
    return tmp_path / "ws"


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 100), "red").save(buffer, "PNG")
    return buffer.getvalue()


def test_create_from_a_starter_in_the_database_and_never_overwrite(workspace):
    api.lists_add("book-interest")
    assert api.campaigns_create("a", "update", dry_run=True)["dry_run"] is True
    assert api.campaigns_list()["campaigns"] == []
    api.campaigns_create("a", "update")
    campaign = api.campaigns_get("a")["campaign"]
    assert campaign["lists"] == ["book-interest"] and campaign["variables"] == {"book_title": ""}
    assert campaign["template"] == "Plain letter"
    with pytest.raises(MailerlmError, match="already exists"):
        api.campaigns_create("a", "update")
    with pytest.raises(MailerlmError, match="Choose one of"):
        api.campaigns_create("b", "nope")


def test_values_belong_to_the_campaign(workspace):
    api.campaigns_create("a", "update")
    api.campaigns_create("b", "update")
    api.campaigns_save("a", {"variables": {"book_title": "Book A"}})
    api.campaigns_save("b", {"variables": {"book_title": "Book B"}, "sender_name": "Bea"})
    assert api.campaigns_preview("a", write_files=False)["subject"] == "An update on Book A"
    assert api.campaigns_preview("b", write_files=False)["subject"] == "An update on Book B"
    assert "Bea" in api.campaigns_preview("b", write_files=False)["html"]
    assert "Ann" in api.campaigns_preview("a", write_files=False)["html"]


def test_reserved_and_malformed_value_names_are_rejected(workspace):
    api.campaigns_create("a", "update")
    for bad in ("first_name", "Bad Name", "1x"):
        with pytest.raises(MailerlmError, match="variable name"):
            api.campaigns_save("a", {"variables": {bad: "x"}})


def test_every_starter_renders_with_its_values_filled(workspace):
    api.lists_add("book-interest")
    for starter in api.campaigns_starters()["starters"]:
        api.campaigns_create(starter, starter)
        api.campaigns_save(
            starter, {"lists": ["book-interest"], "variables": {"book_title": "The Book"}}
        )
        board = api.campaigns_check(starter)
        assert board["problems"] == [], (starter, board["problems"])


def test_check_counts_only_subscribed_members_and_says_why_others_are_skipped(workspace):
    api.lists_add("book-interest")
    api.contacts_add(email="a@example.com", list_name="book-interest")
    api.contacts_add(email="b@example.com", list_name="book-interest")
    api.campaigns_create("u", "update")
    api.campaigns_save("u", {"variables": {"book_title": "T"}})
    assert api.campaigns_check("u")["would_reach"] == 0
    assert api.campaigns_check("u")["skipped"] == {"status imported": 2}
    api.campaigns_create("c", "confirmation")
    api.campaigns_save("c", {"lists": ["book-interest"], "variables": {"book_title": "T"}})
    assert api.campaigns_check("c")["would_reach"] == 2


def test_check_reports_a_campaign_with_no_list(workspace):
    api.campaigns_create("c", "confirmation")
    assert any("no list" in p for p in api.campaigns_check("c")["problems"])


def test_images_are_uploaded_to_the_campaign_and_appear_inline(workspace):
    api.campaigns_create("u", "update")
    api.campaigns_save(
        "u", {"body": "Hello\n\n![a red bar](bar.png)\n", "variables": {"book_title": "T"}}
    )
    with pytest.raises(MailerlmError, match="not uploaded"):
        api.campaigns_preview("u", write_files=False)
    api.campaigns_image_add("u", "bar.png", png())
    board = api.campaigns_preview("u", first_name="Ada", write_files=False)
    assert "data:image/" in board["html"] and board["inline_images"] == 1
    assert api.campaigns_get("u")["campaign"]["images"][0]["filename"] == "bar.png"
    assert api.campaigns_image_data("u", "bar.png")[1] == "image/png"
    api.campaigns_image_remove("u", "bar.png")
    assert api.campaigns_get("u")["campaign"]["images"] == []


def test_a_bad_image_or_name_is_rejected(workspace):
    api.campaigns_create("u", "update")
    with pytest.raises(MailerlmError, match="not an image"):
        api.campaigns_image_add("u", "x.png", b"hello")
    with pytest.raises(MailerlmError, match="image name"):
        api.campaigns_image_add("u", "../x.png", png())


def test_duplicate_and_delete(workspace):
    api.campaigns_create("a", "update")
    api.campaigns_image_add("a", "bar.png", png())
    api.campaigns_duplicate("a", "b")
    assert api.campaigns_get("b")["campaign"]["images"][0]["filename"] == "bar.png"
    api.campaigns_delete("a")
    assert [c["name"] for c in api.campaigns_list()["campaigns"]] == ["b"]
    assert api.campaigns_image_data("b", "bar.png") is not None
    with pytest.raises(NotFoundError):
        api.campaigns_get("a")


def test_templates_are_editable_duplicable_and_the_built_in_resets(workspace):
    name = "Plain letter"
    template = api.templates_get(name)["template"]
    api.templates_save(name, template["html"].replace("#f6f6f4", "#ffffff"), template["text"])
    assert "#ffffff" in api.templates_get(name)["template"]["html"]
    api.templates_reset(name)
    assert "#f6f6f4" in api.templates_get(name)["template"]["html"]
    api.templates_duplicate(name, "Mine")
    api.campaigns_create("a", "update")
    api.campaigns_save("a", {"template": "Mine"})
    with pytest.raises(MailerlmError, match="use this template"):
        api.templates_delete("Mine")
    api.campaigns_save("a", {"template": name})
    api.templates_delete("Mine")
    with pytest.raises(MailerlmError, match="built-in"):
        api.templates_delete(name)


def test_settings_round_trip(workspace):
    assert api.settings_get()["settings"]["postal_address"] == "1 Main St"
    with pytest.raises(CampaignsError, match="unknown setting"):
        with api._transaction() as connection:
            repository.settings_set(connection, {"bogus": "x"})


def test_a_missing_thing_is_not_found(workspace):
    with pytest.raises(NotFoundError):
        api.campaigns_get("ghost")
    with pytest.raises(NotFoundError):
        api.contacts_tag("ghost@example.com", "x")
    with pytest.raises(NotFoundError):
        api.templates_get("ghost")
