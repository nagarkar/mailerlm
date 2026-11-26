"""Person drafts: making, remaking without losing edits, the once-per-person rule across the draft
sends and the whole-list send, sending one, sending all remaining, and locking after a send."""

import pytest

from mailerlm import api, cli
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError

ADA, GRACE, ALAN = "ada@example.com", "grace@example.com", "alan@example.com"


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
    api.lists_add("friends")
    api.lists_footer("friends", show=False)
    api.campaigns_create("t", "update")
    api.campaigns_save(
        "t",
        {
            "lists": ["friends"],
            "variables": {"book_title": "The Book"},
            "subject": "Happy Thanksgiving, {{ preferred_name }}",
            "body": "Hi {{ preferred_name }},\n\nGrateful for you.\n",
        },
    )
    api.contacts_import_text(f"Ada Lovelace: {ADA}\nGrace Hopper: {GRACE}", "friends")
    api.lists_confirm("friends", "known personally")
    return fake


def _ids():
    return {d["name"]: d["contact"] for d in api.drafts_list("t")["drafts"]}


def test_making_drafts_fills_in_each_person(world):
    board = api.drafts_make("t")
    assert board["drafts"] == 2 and len(board["added"]) == 2
    drafts = {d["name"]: d for d in api.drafts_list("t")["drafts"]}
    assert drafts["Ada Lovelace"]["subject"] == "Happy Thanksgiving, Ada"
    assert drafts["Ada Lovelace"]["body"].startswith("Hi Ada,")
    assert not drafts["Ada Lovelace"]["edited"] and drafts["Ada Lovelace"]["state"] == "ready"
    assert "***" in drafts["Ada Lovelace"]["email"]  # masked unless asked
    assert world.sent == []


def test_remaking_keeps_edits_follows_the_campaign_and_tracks_who_joined_and_left(world):
    api.drafts_make("t")
    ids = _ids()
    api.drafts_save("t", ids["Ada Lovelace"], "For Ada", "Dear Ada, a note just for you.")
    api.campaigns_save("t", {"body": "Hello {{ preferred_name }}!\n"})
    api.contacts_import_text(f"Alan Turing: {ALAN}", "friends")
    api.lists_confirm("friends", "known personally")
    api.contacts_leave(GRACE, "friends")
    board = api.drafts_make("t")
    assert [p["name"] for p in board["added"]] == ["Alan Turing"]
    assert [p["name"] for p in board["kept_edited"]] == ["Ada Lovelace"]
    assert [p["name"] for p in board["removed"]] == ["Grace Hopper"]
    drafts = {d["name"]: d for d in api.drafts_list("t")["drafts"]}
    assert (
        drafts["Ada Lovelace"]["body"] == "Dear Ada, a note just for you."
        and drafts["Ada Lovelace"]["edited"]
    )
    assert drafts["Alan Turing"]["body"].startswith("Hello Alan!")


def test_an_edited_draft_of_someone_who_left_is_kept_and_cannot_be_sent(world):
    api.drafts_make("t")
    grace = _ids()["Grace Hopper"]
    api.drafts_save("t", grace, "For Grace", "Dear Grace")
    api.contacts_leave(GRACE, "friends")
    board = api.drafts_make("t")
    assert [p["name"] for p in board["kept_not_in_audience"]] == ["Grace Hopper"]
    row = next(d for d in api.drafts_list("t")["drafts"] if d["contact"] == grace)
    assert row["state"] == "unreachable"
    with pytest.raises(MailerlmError, match="cannot send this draft"):
        api.drafts_send("t", grace)
    assert world.sent == []


def test_sending_one_draft_sends_its_text_records_it_and_locks_it(world):
    api.drafts_make("t")
    ada = _ids()["Ada Lovelace"]
    api.drafts_save("t", ada, "For Ada", "Dear Ada, a note just for you.")
    board = api.drafts_send("t", ada)
    assert board["counts"] == {"sent": 1}
    [message] = world.sent
    assert message.to == ADA and message.subject == "For Ada"
    assert (
        "a note just for you" in message.text and "1 Main St" not in message.text
    )  # footer hidden
    sends = api.sends_list("t")["sends"]
    assert sends[0]["kind"] == "live" and sends[0]["letters"] == "drafts"
    row = next(d for d in api.drafts_list("t")["drafts"] if d["contact"] == ada)
    assert row["state"] == "sent" and row["sent"]["via"] == "this draft" and row["sent"]["at"]
    with pytest.raises(MailerlmError, match="locked"):
        api.drafts_save("t", ada, "x", "y")
    with pytest.raises(MailerlmError, match="locked"):
        api.drafts_send("t", ada)
    with pytest.raises(MailerlmError, match="locked"):
        api.drafts_delete("t", ada)
    api.campaigns_save("t", {"body": "Changed\n"})
    api.drafts_make("t")  # remaking leaves a sent draft alone
    row = next(d for d in api.drafts_list("t")["drafts"] if d["contact"] == ada)
    assert row["body"] == "Dear Ada, a note just for you."
    assert len(world.sent) == 1


def test_send_all_remaining_sends_each_ready_draft_once_and_checks_the_count(world):
    api.drafts_make("t")
    ada = _ids()["Ada Lovelace"]
    api.drafts_send("t", ada)
    with pytest.raises(MailerlmError, match="not the 2 you confirmed"):
        api.drafts_send_remaining("t", 2)
    board = api.drafts_send_remaining("t", 1)
    assert board["counts"] == {"sent": 1}
    assert sorted(m.to for m in world.sent) == [ADA, GRACE]
    assert api.drafts_list("t")["remaining"] == 0
    with pytest.raises(MailerlmError, match="no draft is ready"):
        api.drafts_send_remaining("t", 0)
    assert len(world.sent) == 2


def test_a_whole_list_send_leaves_out_people_mailed_by_draft(world):
    api.drafts_make("t")
    api.drafts_send("t", _ids()["Ada Lovelace"])
    plan = api.sends_plan("t")
    assert plan["recipients"] == 1 and plan["can_send"], plan["blockers"]
    assert any("already received" in n for n in plan["notes"])
    api.sends_start("t", plan["fingerprint"])
    assert sorted(m.to for m in world.sent) == [ADA, GRACE]
    assert api.sends_plan("t")["can_send"] is False  # nobody left


def test_a_draft_cannot_go_to_someone_the_whole_list_send_reached(world):
    api.drafts_make("t")
    api.sends_start("t", api.sends_plan("t")["fingerprint"])
    assert len(world.sent) == 2
    rows = api.drafts_list("t")["drafts"]
    assert all(r["state"] == "sent" and r["sent"]["via"] == "the whole-list send" for r in rows)
    with pytest.raises(MailerlmError, match="locked"):
        api.drafts_send("t", rows[0]["contact"])
    with pytest.raises(MailerlmError, match="no draft is ready"):
        api.drafts_send_remaining("t", 0)
    assert len(world.sent) == 2


def test_an_edited_unsent_draft_stops_the_whole_list_send(world):
    api.drafts_make("t")
    api.drafts_save("t", _ids()["Ada Lovelace"], "For Ada", "Dear Ada")
    plan = api.sends_plan("t")
    assert plan["can_send"] is False and any("edited draft" in b for b in plan["blockers"])


def test_a_failed_draft_message_can_be_sent_again(world):
    api.drafts_make("t")
    ada = _ids()["Ada Lovelace"]
    world.fail_for.add(ADA)
    assert api.drafts_send("t", ada)["counts"] == {"failed": 1}
    world.fail_for.clear()
    assert api.drafts_send("t", ada)["counts"] == {"sent": 1}


def test_a_draft_test_goes_only_to_the_reply_to_address_and_sends_nothing_live(world):
    api.drafts_make("t")
    ada = _ids()["Ada Lovelace"]
    api.drafts_save("t", ada, "For Ada", "Dear {{ preferred_name }}, hello")
    board = api.drafts_test("t", ada)
    assert board["counts"] == {"sent": 1}
    [message] = world.sent
    assert message.to == "ann+b@example.com" and "Dear Ada, hello" in message.text
    assert api.drafts_list("t")["remaining"] == 2  # a test locks nothing


def test_preview_renders_unsaved_text(world):
    api.drafts_make("t")
    out = api.drafts_preview("t", _ids()["Ada Lovelace"], body="Unsaved words")
    assert "Unsaved words" in out["html"] and out["subject"] == "Happy Thanksgiving, Ada"


def test_a_resumed_draft_send_renders_the_drafts(world, monkeypatch):
    api.drafts_make("t")
    ada = _ids()["Ada Lovelace"]
    api.drafts_save("t", ada, "For Ada", "Only for Ada")
    real = api._execute
    monkeypatch.setattr(api, "_execute", lambda *a, **k: {})  # stop before anything is sent
    send = api.drafts_send("t", ada)["send"]
    monkeypatch.setattr(api, "_execute", real)
    assert api.drafts_list("t")["drafts"][0]["state"] == "sent"  # queued counts as mailed
    api.sends_resume(send)
    assert [m.subject for m in world.sent] == ["For Ada"]


def test_the_cli_lists_and_shows_drafts_without_full_addresses(world, capsys):
    assert cli.main(["drafts", "make", "t"]) == 0
    assert cli.main(["drafts", "list", "t"]) == 0
    out = capsys.readouterr().out
    assert "Ada Lovelace" in out and ADA not in out and "ready" in out
    assert cli.main(["drafts", "show", "t", str(_ids()["Ada Lovelace"])]) == 0
    assert "Happy Thanksgiving, Ada" in capsys.readouterr().out
