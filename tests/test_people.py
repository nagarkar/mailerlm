"""peoplelm as the home of contacts and consent: the client, the cutover link, the send guard and the log.

Every test talks to `FakePeople` (conftest); the HTTP adapter is tested against a stdlib server on
loopback, so nothing leaves the machine.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mailerlm import api
from mailerlm.consent import states
from mailerlm.contacts import repository
from mailerlm.delivery import sending
from mailerlm.delivery.sender import FakeSender
from mailerlm.errors import MailerlmError
from mailerlm.people.client import HttpTransport, People, PeopleError, PeopleUnavailable


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
    api.campaigns_create("news", "update")
    api.campaigns_save(
        "news", {"lists": ["book-interest"], "variables": {"book_title": "The Book"}}
    )
    return fake


def person(people, email):
    found = People(people).lookup_email(email)
    assert found is not None
    return int(found["id"])


def subscribe(people, email, list_name="book-interest"):
    """Someone who confirmed: their mailing-list task is accepted."""
    client = People(people)
    task = client.find_task(person(people, email), list_name)
    client.advance(int(task["id"]), "accepted", "test")


def test_the_status_words_agree_between_contacts_and_consent():
    assert repository.STATUS_WORDS == states.STATUS_FOR_STATE
    assert all(
        states.STATUS_FOR_STATE[state] in (word, "unsubscribed")
        for word, state in states.STATE_FOR_STATUS.items()
    )


def test_list_members_and_consent_are_read_from_peoplelm(world, people):
    api.contacts_import_text("Ada: ada@example.com\nGrace: grace@example.com", "book-interest")
    assert {t["state"] for t in people.tasks.values()} == {"proposed"}
    assert {t["scope"] for t in people.tasks.values()} == {"book-interest"}
    subscribe(people, "ada@example.com")
    with api._transaction() as connection:  # the old columns no longer decide anything
        connection.execute(
            "UPDATE contact SET email = 'stale' || id || '@example.com', do_not_mail = 1"
        )
    assert api.lists_show()["lists"][0]["by_status"] == {"subscribed": 1, "imported": 1}
    plan = api.sends_plan("news")
    assert plan["recipients"] == 1 and plan["skipped"] == {"status imported": 1}


def test_do_not_mail_is_a_block_on_email(world, people):
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    subscribe(people, "ada@example.com")
    api.contacts_do_not_mail("ada@example.com")
    ada = person(people, "ada@example.com")
    assert people.persons[ada]["blocks"] == {"email"}
    assert people.persons[ada]["do_not_contact"] is False  # the person-wide flag stays separate
    assert api.sends_plan("news")["skipped"] == {"do-not-mail": 1}
    api.contacts_do_not_mail("ada@example.com", clear=True)
    assert api.sends_plan("news")["recipients"] == 1


def test_someone_the_guard_holds_back_is_skipped_and_counted(world, people):
    api.contacts_import_text("Ada: ada@example.com\nBea: bea@example.com", "book-interest")
    subscribe(people, "ada@example.com")
    subscribe(people, "bea@example.com")
    people.guard_blocks[person(people, "bea@example.com")] = "our email of today is unanswered"
    plan = api.sends_plan("news")
    assert plan["recipients"] == 1 and plan["skipped"] == {"held by the send guard": 1}


@pytest.mark.cuj(
    "a campaign reaches only accepted list members whose email is not blocked, each send logged in peoplelm"
)
def test_a_campaign_reaches_only_accepted_unblocked_members_each_send_logged(world, people):
    api.contacts_import_text(
        "Ada: ada@example.com\nGrace: grace@example.com\nAlan: alan@example.com\nEve: eve@example.com",
        "book-interest",
    )
    for email in ("ada@example.com", "grace@example.com", "alan@example.com"):
        subscribe(people, email)
    api.contacts_do_not_mail("grace@example.com")  # accepted, but email blocked
    # eve stays proposed (imported): never confirmed
    plan = api.sends_plan("news")
    assert plan["can_send"] and plan["recipients"] == 2
    assert plan["skipped"] == {"do-not-mail": 1, "status imported": 1}
    board = api.sends_start("news", plan["fingerprint"])
    assert board["counts"] == {"sent": 2}
    assert sorted(m.to for m in world.sent) == ["ada@example.com", "alan@example.com"]
    outs = [t for t in people.touches if t["direction"] == "out"]
    assert sorted(t["person_id"] for t in outs) == sorted(
        person(people, e) for e in ("ada@example.com", "alan@example.com")
    )
    with api._transaction() as connection:
        deliveries = dict(connection.execute("SELECT email, id FROM delivery"))
    for touch in outs:
        assert touch["channel"] == "email" and touch["producer"] == "mailerlm"
        assert touch["purpose"] == "mailing-list" and touch["scope"] == "book-interest"
        assert touch["summary"] == plan["subject"]
        assert touch["ref"] in {f"delivery:{i}" for i in deliveries.values()}
    assert people.calls.index("guard_check") < people.calls.index("guard_send")


def test_the_guard_is_asked_again_at_send_time_and_a_held_row_is_reported(world, people):
    api.contacts_import_text("Ada: ada@example.com\nAlan: alan@example.com", "book-interest")
    subscribe(people, "ada@example.com")
    subscribe(people, "alan@example.com")
    with api._transaction() as connection:  # a send recorded while the guard still said yes
        plan = api._prepare(connection, "news", api._people())
        reach = plan["reach"]
        recipients = [
            sending.Recipient(i, e, "x", n)
            for i, e, n in zip(reach.contact_ids, reach.emails, reach.lists, strict=True)
        ]
        send_id = sending.create_send(connection, "news", "live", "S", "f", recipients)
    people.guard_blocks[person(people, "alan@example.com")] = "2 messages already in 3 days"
    board = api.sends_resume(send_id)
    assert board["counts"] == {"sent": 1, "failed": 1}
    with api._transaction() as connection:
        error = connection.execute("SELECT error FROM delivery WHERE status = 'failed'").fetchone()
    assert "held by the send guard: 2 messages already in 3 days" in error[0]


def test_a_confirmation_letter_goes_through_the_guard_and_moves_people_to_requested(world, people):
    api.campaigns_create("c", "confirmation")
    api.campaigns_save("c", {"lists": ["book-interest"], "variables": {"book_title": "The Book"}})
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    api.sends_start("c", api.sends_plan("c")["fingerprint"])
    assert people.state(person(people, "ada@example.com"), "book-interest") == "requested"
    assert [t["direction"] for t in people.touches] == ["out"]


def test_without_peoplelm_nothing_live_goes_out_but_a_test_send_does(world, people):
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    subscribe(people, "ada@example.com")
    fingerprint = api.sends_plan("news")["fingerprint"]
    people.down = True
    with pytest.raises(MailerlmError, match="peoplelm doctor"):
        api.sends_start("news", fingerprint)
    with pytest.raises(MailerlmError, match="peoplelm doctor"):
        api.sends_plan("news")
    assert world.sent == []
    assert api.sends_test("news")["counts"] == {"sent": 1}
    assert [m.to for m in world.sent] == ["ann+b@example.com"]


def test_peoplelm_going_away_mid_send_stops_the_rest_queued(world, people, monkeypatch):
    api.contacts_import_text("Ada: ada@example.com\nAlan: alan@example.com", "book-interest")
    subscribe(people, "ada@example.com")
    subscribe(people, "alan@example.com")
    plan = api.sends_plan("news")
    original = people._guard_send

    def send_then_vanish(**params):
        result = original(**params)
        people.down = True
        return result

    monkeypatch.setattr(people, "_guard_send", send_then_vanish)
    with pytest.raises(PeopleUnavailable):
        api.sends_start("news", plan["fingerprint"])
    assert len(world.sent) == 1
    with api._transaction() as connection:
        rows = dict(connection.execute("SELECT status, COUNT(*) FROM delivery GROUP BY status"))
    assert rows == {"sent": 1, "queued": 1}


def test_a_bounce_blocks_email_and_rejects_with_the_reason(world, people):
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    subscribe(people, "ada@example.com")
    api.sends_start("news", api.sends_plan("news")["fingerprint"])
    world.statuses["fake_1"] = "bounced"
    assert api.sends_poll()["blocked"] == 1
    ada = person(people, "ada@example.com")
    assert people.persons[ada]["blocks"] == {"email"}
    assert people.state(ada, "book-interest") == "rejected"
    assert "bounced" in people.events[-1]["note"]


def test_joining_leaving_and_renaming_a_list_happen_in_peoplelm(world, people):
    api.lists_add("b")
    api.contacts_add(email="ada@example.com", list_name="book-interest")
    api.contacts_join("ada@example.com", "b")
    ada = person(people, "ada@example.com")
    assert people.state(ada, "b") == "proposed"
    api.contacts_leave("ada@example.com", "b")
    assert people.state(ada, "b") == "dropped"
    api.lists_edit("book-interest", "readers")
    assert people.state(ada, "readers") == "proposed"
    assert api.contacts_find()["contacts"][0]["lists"] == {"readers": "imported"}


def test_a_dry_run_writes_nothing_to_peoplelm(world, people):
    board = api.contacts_add(email="ada@example.com", list_name="book-interest", dry_run=True)
    assert (board["added"], board["memberships_added"]) == (1, 1)
    assert people.persons == {} and people.tasks == {}


def test_the_link_stores_each_contacts_person_and_a_second_run_changes_nothing(
    world, people, tmp_path
):
    with api._transaction() as connection:  # contacts as they were before the cutover
        connection.execute(
            "INSERT INTO contact (id, email, name, created_at) VALUES (7, 'ada@example.com', 'Ada', 'x')"
        )
        connection.execute(
            "INSERT INTO contact (id, email, name, created_at) VALUES (8, 'old@example.com', '', 'x')"
        )
    ada = people.add_person("ada@example.com", "Ada")
    People(people).propose(ada, "book-interest")
    mapping = tmp_path / "people.json"
    mapping.write_text(json.dumps({"contacts": 1, "people": {"7": ada, "99": 5}}))
    assert api.people_link(str(mapping), dry_run=True)["linked"] == 1
    board = api.people_link(str(mapping))
    assert (board["linked"], board["unknown_contacts"], board["not_linked"]) == (1, 1, 1)
    assert api.people_link(str(mapping))["already_linked"] == 1
    rows = api.contacts_find(show_full_addresses=True)["contacts"]
    assert [(r["id"], r["lists"]) for r in rows] == [(7, {"book-interest": "imported"})]


def test_a_bad_mapping_names_the_peoplelm_command(world, tmp_path):
    mapping = tmp_path / "people.json"
    mapping.write_text("{}")
    with pytest.raises(MailerlmError, match="peoplelm import mailerlm"):
        api.people_link(str(mapping))


def test_an_unlinked_contact_is_never_mailed(world, people):
    with api._transaction() as connection:
        connection.execute(
            "INSERT INTO contact (email, name, created_at) VALUES ('old@example.com', '', 'x')"
        )
    assert api.contacts_find()["count"] == 0
    assert api.sends_plan("news")["recipients"] == 0


def test_a_missing_url_names_the_setting_and_peoplelm_doctor(monkeypatch):
    monkeypatch.setattr(api, "people_for_tests", None)
    with pytest.raises(MailerlmError, match="PEOPLELM_URL is not set.*peoplelm doctor"):
        api._people()


# ---- the HTTP adapter against a loopback server ---------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((dict(self.headers), body))
        status, answer = {
            "audience": (200, {"result": [{"person_id": 1}]}),
            "task_move": (400, {"error": "task 1 is accepted"}),
        }.get(body["method"], (500, {"error": "RuntimeError"}))
        data = json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_the_adapter_posts_method_and_params_with_the_write_check_headers(server):
    client = People(HttpTransport(server))
    assert client.audience("book-interest", ["accepted"]) == [{"person_id": 1}]
    headers, body = _Handler.seen[-1]
    assert headers["X-Peoplelm"] == "1" and headers["Content-Type"] == "application/json"
    assert body == {
        "method": "audience",
        "params": {"purpose": "mailing-list", "scope": "book-interest", "states": ["accepted"]},
    }


def test_a_4xx_is_peoplelms_word_and_a_5xx_or_no_answer_is_unavailable(server):
    client = People(HttpTransport(server))
    with pytest.raises(PeopleError, match="task 1 is accepted"):
        client.move(1, "rejected", "owner")
    with pytest.raises(PeopleUnavailable, match="peoplelm doctor"):
        client.get(1)
    with pytest.raises(PeopleUnavailable, match="peoplelm doctor"):
        People(HttpTransport("http://127.0.0.1:9", timeout=1)).get(1)
