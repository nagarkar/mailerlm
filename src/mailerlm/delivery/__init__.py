"""Delivery: sending mail, recording what happened to it, and reading the result back.

Owns the `MailSender` port, the Resend adapter (the only module that speaks HTTP to Resend), a fake
for tests, and the `send` and `delivery` tables (`schema.py`; example rows there). A send is planned,
recorded as one queued row per person, run through the sender, and later polled for delivered,
bounced and complained.

Never imports: inbox, linkedin (enforced by the import contract "channels apart" in
pyproject.toml).

Decisions:
- Resend is the sender, behind the `MailSender` port; SES can be added as a second adapter. Why: the
  simplest start, one key, an HTTPS call. (owner)
- A live send is the owner's act: the Send button on the campaign page, or `mailerlm sends start
  --fingerprint`. The send repeats the plan and stops when the fingerprint no longer matches, and the
  repo's permission settings deny agents the real-send verbs. A test send goes only to the reply-to
  address. (owner)
- Caps stop a send: `caps.max_recipients_per_send` and `caps.max_sends_per_day` raise, never clamp. (owner)
- Every message carries an idempotency key from the send and the address, so a retry or a resumed send
  never mails a person twice; only a 429 is retried. (owner)
- Delivery status is polled from Resend's API (no webhook receiver, no public endpoint); reading status
  needs a full-access key, and a bounce or complaint blocks that person's email in peoplelm
  (do-not-mail) and rejects their memberships with the reason in the note. (owner)
- Every live letter asks peoplelm's send guard first, per recipient (channel email, purpose
  mailing-list, scope the list); a held recipient's row is `failed` with the guard's reasons; a sent
  one is logged with `guard_send` (ref `delivery:<id>`, summary the subject). Without peoplelm the
  run stops and the rest stay queued for `sends resume`. Enforced by:
  tests/test_people.py::test_the_guard_is_asked_again_at_send_time_and_a_held_row_is_reported,
  tests/test_people.py::test_peoplelm_going_away_mid_send_stops_the_rest_queued. (owner)
- Tracking is a setting of the sending domain in Resend; the campaign flags are notes. (owner)

Terms:
- **send**: one campaign delivered (or tested) to one audience, with a row per recipient.
- **fingerprint**: a short code of the exact letter, sender and audience a plan showed.
"""
