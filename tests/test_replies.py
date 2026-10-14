"""Reading replies: who is searched, what Jev sees, the asymmetric gate, the queue, undo, and the adapters."""

from types import SimpleNamespace

import pytest

from mailerlm import api
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError
from mailerlm.inbox import classifier, text
from mailerlm.inbox.classifier import FakeClassifier
from mailerlm.inbox.reader import FakeReader, RawMessage


def reply(
    message_id, sender, body, headers=None, subject="Re: Do you still want to hear about The Book?"
):
    return RawMessage(
        message_id, "t-" + message_id, sender, subject, "2026-10-13T15:00:00", body, headers or {}
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(api.paths, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(api, "sender_for_tests", FakeSender())
    api.settings_save("Ann", "ann@mail.example.com", "ann+b@example.com", "1 Main St")
    api.lists_add("book-interest")
    api.campaigns_create("c", "confirmation")
    api.campaigns_save("c", {"lists": ["book-interest"], "variables": {"book_title": "The Book"}})
    for person in (
        "Ada Lovelace: ada@example.com",
        "Grace Hopper: grace@example.com",
        "Alan Turing: alan@example.com",
    ):
        api.contacts_import_text(person, "book-interest")
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    return SimpleNamespace(tmp=tmp_path, monkeypatch=monkeypatch)


def statuses():
    rows = api.contacts_find(show_full_addresses=True)["contacts"]
    return {r["email"]: r["lists"]["book-interest"] for r in rows}


def use(world, messages, answers=None, fail=False):
    reader, judge = FakeReader(messages), FakeClassifier(answers, fail)
    world.monkeypatch.setattr(api, "reader_for_tests", reader)
    world.monkeypatch.setattr(api, "classifier_for_tests", judge)
    return reader, judge


def test_the_confirmation_send_left_everyone_pending(world):
    assert set(statuses().values()) == {"pending"}


def test_a_sure_yes_subscribes_and_a_sure_stop_unsubscribes(world):
    use(
        world,
        [
            reply("1", "ada@example.com", "Yes please!"),
            reply("2", "grace@example.com", "please remove me"),
        ],
        {"yes please": ("confirm_subscription", 0.99), "remove me": ("unsubscribe", 0.9)},
    )
    board = api.replies_check()
    assert (board["confirmed"], board["unsubscribed"], board["queued"]) == (1, 1, 0)
    assert statuses() == {
        "ada@example.com": "subscribed",
        "grace@example.com": "unsubscribed",
        "alan@example.com": "pending",
    }


def test_the_gate_is_asymmetric_a_wrong_yes_costs_more_than_a_wrong_stop(world):
    use(
        world,
        [
            reply("1", "ada@example.com", "yes i think"),
            reply("2", "grace@example.com", "maybe stop"),
        ],
        {"yes i think": ("confirm_subscription", 0.9), "maybe stop": ("unsubscribe", 0.85)},
    )
    board = api.replies_check()
    assert (board["confirmed"], board["unsubscribed"], board["queued"]) == (0, 1, 1)
    assert statuses()["ada@example.com"] == "pending"


def test_a_yes_from_someone_who_was_never_asked_waits_for_the_owner(world):
    api.contacts_import_text("Eve: eve@example.com", "book-interest")  # imported, never mailed
    api.sends_start("c", api.sends_plan("c")["fingerprint"]) if False else None
    with api._transaction() as connection:
        connection.execute(
            "UPDATE membership SET status = 'imported' WHERE contact_id = (SELECT id FROM contact WHERE email = 'ada@example.com')"
        )
    use(world, [reply("1", "ada@example.com", "yes")], {"yes": ("confirm_subscription", 0.99)})
    assert api.replies_check()["queued"] == 1
    assert statuses()["ada@example.com"] == "imported"


def test_questions_and_comments_always_wait_and_automatic_replies_never_reach_jev(world):
    _, judge = use(
        world,
        [
            reply("1", "ada@example.com", "Who is this?"),
            reply("2", "grace@example.com", "I am away", {"auto-submitted": "auto-replied"}),
        ],
        {"who is this": ("question_or_comment", 0.99)},
    )
    board = api.replies_check()
    assert (board["queued"], board["ignored"]) == (1, 1) and len(judge.seen) == 1


def test_a_reply_is_read_once_and_only_people_we_mailed_are_searched(world):
    reader, _ = use(
        world,
        [reply("1", "ada@example.com", "yes"), reply("9", "stranger@example.com", "yes")],
        {"yes": ("confirm_subscription", 0.99)},
    )
    assert api.replies_check()["new"] == 1
    assert api.replies_check()["new"] == 0
    assert (
        "stranger@example.com" not in reader.searches[0][0]
        and reader.searches[0][1].count("/") == 2
    )
    assert len(api.replies_list()["replies"]) == 1


def test_a_typesafe_failure_stores_nothing_so_the_reply_is_read_again(world):
    use(
        world,
        [reply("1", "ada@example.com", "yes")],
        {"yes": ("confirm_subscription", 0.99)},
        fail=True,
    )
    assert api.replies_check()["errors"] == 1 and api.replies_list()["replies"] == []
    use(world, [reply("1", "ada@example.com", "yes")], {"yes": ("confirm_subscription", 0.99)})
    assert api.replies_check()["confirmed"] == 1


def test_jev_sees_the_thread_without_the_quoted_letter(world):
    _, judge = use(
        world,
        [
            reply(
                "1",
                "ada@example.com",
                "Sure, count me in.\n\nOn Mon, Oct 12, 2026 at 9:00 AM Ann wrote:\n> Do you still want to hear?\n> Reply yes",
            )
        ],
        {"count me in": ("confirm_subscription", 0.99)},
    )
    api.replies_check()
    state = judge.seen[0]
    assert state["their_reply"]["text"] == "Sure, count me in."
    assert (
        "Do you still want to hear" in state["our_last_message"]["subject"]
        and "reply" in state["our_last_message"]["text"].lower()
    )
    assert state["their_membership_statuses"] == ["pending"]


def test_the_owner_settles_the_queue_and_can_undo_an_automatic_pick(world):
    use(
        world,
        [reply("1", "ada@example.com", "hmm"), reply("2", "grace@example.com", "stop it")],
        {"stop it": ("unsubscribe", 0.95)},
    )
    api.replies_check()
    queued = next(r for r in api.replies_list()["replies"] if r["decision"] == "queued")
    api.replies_decide(queued["id"], "confirmed")
    assert statuses()["ada@example.com"] == "subscribed"
    auto = next(r for r in api.replies_list()["replies"] if r["decision"] == "unsubscribed")
    api.replies_undo(auto["id"])
    assert statuses()["grace@example.com"] == "pending"
    assert (
        next(r for r in api.replies_list()["replies"] if r["id"] == auto["id"])["decision"]
        == "queued"
    )
    api.replies_decide(auto["id"], "ignored")
    with pytest.raises(MailerlmError, match="confirmed, unsubscribed or ignored"):
        api.replies_decide(auto["id"], "maybe")


def test_nothing_is_read_before_the_first_live_send(tmp_path, monkeypatch):
    env_file = tmp_path / "env" / ".env"
    env_file.parent.mkdir()
    api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"))
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(api.paths, "config_path", lambda: tmp_path / "config.toml")
    reader = FakeReader([])
    monkeypatch.setattr(api, "reader_for_tests", reader)
    monkeypatch.setattr(api, "classifier_for_tests", FakeClassifier())
    assert api.replies_check()["new"] == 0 and reader.searches == []


# ---- text and the TypeSafe adapter -------------------------------------------------------


def test_quoted_text_and_headers_are_cut():
    assert text.strip_quoted("Yes!\n\n> quoted\nmore") == "Yes!"
    assert text.strip_quoted("Yes!\n-----Original Message-----\nFrom: x") == "Yes!"
    assert (
        text.strip_quoted(
            "Yes!\nOn Mon, Oct 12, 2026 at 9:00 AM Ann Example\n<ann@example.com> wrote:\n> hi"
        )
        == "Yes!"
    )
    assert len(text.strip_quoted("a" * 5000)) == 2000


def response(status, body=None, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: body or {})


def good(choice="unsubscribe"):
    probabilities = {o: 0.02 for o in classifier.OPTIONS}
    probabilities[choice] = 0.9
    return response(
        200,
        {
            "answers": {
                "intent": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probabilities,
                    "confidence": 0.9,
                }
            }
        },
    )


def test_the_adapter_posts_one_choice_question_with_every_option_described():
    calls = []
    judge = classifier.TypeSafeClassifier(post=lambda url, **k: (calls.append(k), good())[1])
    result = judge.classify({"their_reply": {"text": "stop"}}, "jev-latest")
    body = calls[0]["json"]
    assert result.choice == "unsubscribe" and result.probability("unsubscribe") == 0.9
    assert body["model"] == "jev-latest" and body["questions"]["intent"]["type"] == "choice"
    assert set(body["questions"]["intent"]["criteria"]) == set(classifier.OPTIONS)
    assert all(body["questions"]["intent"]["criteria"].values())


def test_only_429_and_529_are_retried_and_errors_never_echo_the_reply(capsys):
    sleeps, queue = [], [response(429, headers={"retry-after": "1"}), good("other")]
    judge = classifier.TypeSafeClassifier(post=lambda url, **k: queue.pop(0), sleep=sleeps.append)
    assert judge.classify({}, "m").choice == "other" and sleeps == [1.0]
    for status in (400, 401, 500):
        failing = classifier.TypeSafeClassifier(
            post=lambda url, status=status, **k: response(status, {"echo": "secret reply"})
        )
        with pytest.raises(classifier.ClassifierError) as error:
            failing.classify({"their_reply": {"text": "secret reply"}}, "m")
        assert "secret reply" not in str(error.value) and str(status) in str(error.value)


def test_an_unexpected_answer_is_an_error():
    odd = classifier.TypeSafeClassifier(
        post=lambda url, **k: response(
            200, {"answers": {"intent": {"choice": "bogus", "probabilities": {"bogus": 1}}}}
        )
    )
    with pytest.raises(classifier.ClassifierError, match="option we did not offer"):
        odd.classify({}, "m")
    broken = classifier.TypeSafeClassifier(post=lambda url, **k: response(200, {"nope": 1}))
    with pytest.raises(classifier.ClassifierError, match="expected shape"):
        broken.classify({}, "m")


def test_an_html_only_reply_is_read_as_text():
    assert text.html_to_text(
        "<p>Yes <b>please</b></p><script>x()</script><div>thanks</div>"
    ).split() == ["Yes", "please", "thanks"]


def test_other_mail_from_the_same_person_is_never_stored_or_sent_to_jev(world):
    reader, judge = use(
        world,
        [
            reply("1", "ada@example.com", "Yes please!"),
            reply(
                "2",
                "ada@example.com",
                "Here are the payment terms, private",
                subject="Payment plan structure",
            ),
            reply("3", "ada@example.com", "also private", subject="Re: Welcome Call"),
        ],
        {"yes please": ("confirm_subscription", 0.99)},
    )
    board = api.replies_check()
    assert (board["confirmed"], board["other_mail_skipped"]) == (1, 2)
    assert len(judge.seen) == 1 and all("private" not in str(state) for state in judge.seen)
    assert [r["subject"] for r in api.replies_list()["replies"]] == [
        "Re: Do you still want to hear about The Book?"
    ]
    assert reader.searches[0][2] == ["Do you still want to hear about The Book?"]


def test_a_reply_with_re_and_fwd_prefixes_still_matches_our_letter():
    from mailerlm.inbox.service import normalized_subject

    assert normalized_subject("RE: Fwd:  Do you still   want?") == "do you still want?"
