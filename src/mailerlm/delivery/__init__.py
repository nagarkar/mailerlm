"""Delivery: sending mail and reading back what happened to it.

Owns the `MailSender` port, the Resend adapter (the only module that speaks HTTP to Resend),
a fake for tests, and the poll that records delivered, bounced, complained, opened and
clicked events.

Never imports: inbox, linkedin (enforced by the import contract "channels apart" in
pyproject.toml).

Decisions:
- Resend is the sender, behind the `MailSender` port; SES can be added as a second adapter.
  Why: the simplest start, one key, an HTTPS call. (owner)
- Delivery events are polled from Resend's API; there is no webhook receiver (no public
  endpoint). (owner)
- Every send verb accepts `--dry-run` and stops at the recipient and daily caps in config;
  a live send is the owner's act. (owner)

Terms:
- **send**: one campaign delivered to one audience, recorded as a row per recipient.
"""
