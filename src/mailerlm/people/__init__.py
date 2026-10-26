"""People: the client of peoplelm, the people service that owns contact identity and consent.

Owns the `Transport` port (one call: a method name and its params), the HTTP adapter (the only
module that speaks to peoplelm, `POST {PEOPLELM_URL}/api/peoplelm`), a typed client `People` over
it, and `FakePeople`, an in-memory peoplelm for tests. Tests inject the fake through
`mailerlm.api.people_for_tests` (a module attribute defaulting to None), never an environment
switch.

Never imports: any other mailerlm area (enforced by the import contract "people at the bottom" in
pyproject.toml).

Decisions:
- mailerlm keeps lists, campaigns, sends and deliveries; who a person is and whether they agreed
  to be mailed come from peoplelm, over HTTP only. Enforced by:
  tests/test_people.py::test_list_members_and_consent_are_read_from_peoplelm. (owner)
- Each mailing list is a peoplelm `mailing-list` task scoped by the list's name: imported is
  proposed, pending is requested, subscribed is accepted (the only state a campaign mails),
  unsubscribed is rejected, and a bounce or complaint is rejected with the reason in the note.
  Enforced by: tests/test_people.py::test_a_bounce_blocks_email_and_rejects_with_the_reason. (owner)
- Do-not-mail is a peoplelm block on the `email` channel; bounces and complaints set it. The
  person-wide do-not-contact is peoplelm's own and separate. Enforced by:
  tests/test_people.py::test_do_not_mail_is_a_block_on_email. (owner)
- When peoplelm does not answer, nothing live goes out: a live send or confirmation letter stops
  with an error naming `peoplelm doctor`; a test send to the owner's own address still works.
  Enforced by: tests/test_people.py::test_without_peoplelm_nothing_live_goes_out_but_a_test_send_does. (owner)
- Every outbound letter asks the send guard first (`guard_check`, channel email, purpose
  mailing-list, scope the list), skips and reports whom it blocks, and after a successful send
  logs it with `guard_send` (producer mailerlm, ref `delivery:<id>`, summary the subject); every
  reply read is logged with `touch_log` (direction in, kind reply, ref the Gmail id). Enforced by:
  tests/test_people.py::test_a_campaign_reaches_only_accepted_unblocked_members_each_send_logged. (owner)

Terms:
- **person**: peoplelm's record of one human; a mailerlm contact row points at it by `person_id`.
- **task**: peoplelm's record of one purpose with one person; here, a membership of one list.
- **send guard**: peoplelm's answer to "may I write to this person now, for this purpose?".
"""

PURPOSE = "mailing-list"
CHANNEL = "email"
PRODUCER = "mailerlm"
URL_ENV = "PEOPLELM_URL"
DOCTOR = "Run: peoplelm doctor"
