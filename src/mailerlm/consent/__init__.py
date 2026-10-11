"""Consent: the state machine that decides whether a membership may be mailed.

Statuses: `imported` -> `pending` (confirmation sent) -> `subscribed` (confirmed) ->
`unsubscribed`; `suppressed` (bounce or complaint) is terminal. Only `subscribed` is mailed.

Never imports: delivery, inbox, linkedin (enforced by the import contract "domain below
channels" in pyproject.toml).

Decisions:
- Everyone, including the first imported contacts, confirms by double opt-in before any
  campaign reaches them. (owner)
- Confirmation and unsubscribe happen by reply, not by a public link: no public endpoint.
  Every campaign carries a `List-Unsubscribe: mailto:` header and a "reply UNSUBSCRIBE" line. (owner)
- Jev (TypeSafe) reads each reply with its thread and picks one option. An unsubscribe is
  applied at probability 0.80 or higher, a confirmation at 0.95 or higher and only as the
  answer to our confirmation email; every other option, and anything below the bar, goes to
  the owner's queue with Jev's suggestion shown. Thresholds are config. (owner)
- Bounces and complaints suppress automatically. (owner)

Terms:
- **double opt-in**: a contact is mailed campaigns only after replying yes to a confirmation email.
- **needs-you queue**: replies the classifier did not settle, waiting for the owner.
"""
