"""sending: plan, test send, real send, caps, fingerprint, resume, idempotency, polling, the Resend adapter."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from mailerlm import api
from mailerlm.delivery import resend, sending
from mailerlm.delivery.sender import FakeSender, OutgoingMessage, SenderError
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
    api.campaigns_create("c", "confirmation")
    api.campaigns_save("c", {"lists": ["book-interest"], "variables": {"book_title": "The Book"}})
    for person in ("Ada Lovelace: ada@example.com", "Grace Hopper: grace@example.com"):
        api.contacts_import_text(person, "book-interest")
    return fake


def test_the_plan_names_who_and_blocks_what_cannot_be_sent(world, tmp_path):
    plan = api.sends_plan("c")
    assert (
        plan["recipients"] == 2
        and plan["can_send"]
        and plan["from"] == "Ann <ann@mail.example.com>"
    )
    api.settings_save("", "", "", "")
    assert api.sends_plan("c")["can_send"] is False


def test_a_test_send_goes_only_to_the_reply_to_address(world):
    board = api.sends_test("c")
    assert [m.to for m in world.sent] == ["ann+b@example.com"]
    assert board["counts"] == {"sent": 1}


def test_a_real_send_needs_the_matching_fingerprint(world):
    with pytest.raises(MailerlmError, match="changed since you reviewed"):
        api.sends_start("c", "0000000000000000")
    assert world.sent == []
    plan = api.sends_plan("c")
    board = api.sends_start("c", plan["fingerprint"])
    assert board["counts"] == {"sent": 2}
    assert sorted(m.to for m in world.sent) == ["ada@example.com", "grace@example.com"]
    first = next(m for m in world.sent if m.to == "ada@example.com")
    assert "Hi Ada," in first.text and first.idempotency_key.endswith("ada@example.com")
    assert first.unsubscribe_mailto.startswith("mailto:ann+b@example.com")


def test_the_same_letter_is_not_sent_to_the_same_audience_twice(world):
    plan = api.sends_plan("c")
    api.sends_start("c", plan["fingerprint"])
    again = api.sends_plan("c")
    assert again["can_send"] is False and any(
        "already sent" in b or "nobody" in b for b in again["blockers"]
    )


def test_a_confirmation_send_moves_people_to_pending(world):
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    assert api.lists_show()["lists"][0]["by_status"] == {"pending": 2}


def test_a_failed_message_is_recorded_and_the_rest_are_sent(world):
    world.fail_for.add("grace@example.com")
    board = api.sends_start("c", api.sends_plan("c")["fingerprint"])
    assert board["counts"] == {"sent": 1, "failed": 1}


def test_the_caps_raise_and_never_clamp(world, tmp_path):
    (tmp_path / "config.toml").write_text("[caps]\nmax_recipients_per_send = 1\n")
    plan = api.sends_plan("c")
    assert plan["can_send"] is False and any(
        "max_recipients_per_send" in b for b in plan["blockers"]
    )
    with pytest.raises(MailerlmError, match="cannot send"):
        api.sends_start("c", plan["fingerprint"])
    (tmp_path / "config.toml").write_text("[caps]\nmax_sends_per_day = 1\n")
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    api.campaigns_save("c", {"subject": "Different"})
    assert any("max_sends_per_day" in b for b in api.sends_plan("c")["blockers"])


def test_an_interrupted_send_resumes_without_mailing_anyone_twice(world, monkeypatch):
    with api._transaction() as connection:
        plan = api._prepare(connection, "c", api._people())
        reach = plan["reach"]
        recipients = [
            sending.Recipient(i, e, "x", n)
            for i, e, n in zip(reach.contact_ids, reach.emails, reach.lists, strict=True)
        ]
        send_id = sending.create_send(connection, "c", "live", "S", plan["fingerprint"], recipients)
        connection.execute(
            "UPDATE delivery SET status = 'sent', provider_id = 'fake_0' WHERE email = 'ada@example.com'"
        )
    api.sends_resume(send_id)
    assert [m.to for m in world.sent] == ["grace@example.com"]
    assert api.sends_status(send_id)["send"]["status"] == "done"


def test_the_sender_is_hidden_behind_the_key_check(monkeypatch):
    monkeypatch.setattr(api, "sender_for_tests", None)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(MailerlmError, match="RESEND_API_KEY is not set"):
        api._sender()


def test_polling_blocks_people_who_bounce_or_complain(world):
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    world.statuses["fake_1"] = "bounced"
    world.statuses["fake_2"] = "complained"
    assert api.sends_poll()["blocked"] == 2
    rows = api.contacts_find(show_full_addresses=True)["contacts"]
    assert all(r["do_not_mail"] for r in rows)
    assert api.sends_list()["sends"][0]["counts"] == {"bounced": 1, "complained": 1}


def test_inline_images_ride_with_the_message(world, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (50, 50), "blue").save(buffer, "PNG")
    api.campaigns_image_add("c", "pic.png", buffer.getvalue())
    api.campaigns_save("c", {"body": "Hi\n\n![a picture](pic.png)\n"})
    api.sends_test("c")
    assert world.sent[0].pictures[0].content_id == "image1" and "cid:image1" in world.sent[0].html


# ---- the Resend adapter, with the HTTP calls injected --------------------------------------


def response(status, body=None, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: body or {})


def message():
    return OutgoingMessage(
        "Ann",
        "ann@mail.example.com",
        "ada@example.com",
        "S",
        "<p>h</p>",
        "t",
        "ann+b@example.com",
        "mailto:ann+b@example.com?subject=unsubscribe",
        idempotency_key="k-1",
        tag="c",
    )


def test_resend_posts_the_message_with_an_idempotency_key():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response(200, {"id": "re_1"})

    sender = resend.ResendSender(post=post, get=None)
    assert sender.send(message()) == "re_1"
    url, kwargs = calls[0]
    assert url.endswith("/emails") and kwargs["headers"]["Idempotency-Key"] == "k-1"
    assert kwargs["json"]["from"] == "Ann <ann@mail.example.com>"
    assert (
        kwargs["json"]["headers"]["List-Unsubscribe"]
        == "<mailto:ann+b@example.com?subject=unsubscribe>"
    )


def test_resend_retries_only_a_429_and_says_so(capsys):
    sleeps, queue = [], [response(429, headers={"retry-after": "1"}), response(200, {"id": "re_2"})]
    sender = resend.ResendSender(post=lambda url, **k: queue.pop(0), get=None, sleep=sleeps.append)
    assert sender.send(message()) == "re_2" and sleeps == [1.0]
    assert "retry" in capsys.readouterr().err


@pytest.mark.parametrize("status", [400, 401, 422, 500, 503])
def test_any_other_failure_stops_at_once_and_never_echoes_the_message(status):
    calls = []
    sender = resend.ResendSender(
        post=lambda url, **k: (calls.append(1), response(status, {"echo": "secret body"}))[1],
        get=None,
    )
    with pytest.raises(SenderError) as error:
        sender.send(message())
    assert (
        "secret body" not in str(error.value)
        and str(status) in str(error.value)
        and len(calls) == 1
    )


def test_resend_status_maps_the_last_event():
    sender = resend.ResendSender(
        post=None, get=lambda url, **k: response(200, {"last_event": "bounced"})
    )
    assert sender.status("re_3").status == "bounced"


def test_a_refusal_carries_resends_own_reason_but_not_our_message():
    sender = resend.ResendSender(
        post=lambda url, **k: response(
            403, {"name": "validation_error", "message": "The example.com domain is not verified."}
        ),
        get=None,
    )
    with pytest.raises(SenderError) as error:
        sender.send(message())
    assert "domain is not verified" in str(error.value) and "<p>h</p>" not in str(error.value)
