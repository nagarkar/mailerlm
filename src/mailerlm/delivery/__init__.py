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
  needs a full-access key, and a bounce or complaint blocks that person (do-not-mail). (owner)
- Tracking is a setting of the sending domain in Resend; the campaign flags are notes. (owner)

Terms:
- **send**: one campaign delivered (or tested) to one audience, with a row per recipient.
- **fingerprint**: a short code of the exact letter, sender and audience a plan showed.
"""
