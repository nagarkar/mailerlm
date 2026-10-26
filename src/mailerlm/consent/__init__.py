"""Consent: the state machine that decides whether a membership may be mailed, kept in peoplelm.

Statuses: `imported` -> `pending` (confirmation sent) -> `subscribed` (confirmed) ->
`unsubscribed`; `suppressed` (bounce or complaint) is terminal. Only `subscribed` is mailed.
Each status is a peoplelm `mailing-list` task state scoped by the list's name (see
`mailerlm.people`): proposed, requested, accepted, rejected.

Never imports: delivery, inbox, linkedin (enforced by the import contract "domain below
channels" in pyproject.toml).

Decisions:
- Everyone, including the first imported contacts, confirms by double opt-in before any campaign reaches them:
  a confirmation letter moves `imported` to `pending`, and their reply moves `pending` to `subscribed`. (owner)
- Confirmation and unsubscribe happen by reply, not by a public link: no public endpoint. Every campaign carries a
  `List-Unsubscribe: mailto:` header and a "reply UNSUBSCRIBE" line. (owner)
- Jev reads each reply; an unsubscribe applies at probability 0.80 or higher, a confirmation at 0.95 or higher and
  only for someone with a pending membership; every other option, and anything below its bar, waits for the owner
  (see `mailerlm.inbox`). A wrong yes mails someone who did not consent, so its bar is higher. (owner)
- Every transition returns what it changed, so an automatic decision can be undone exactly. (owner)
  Since the move to peoplelm this does not hold: peoplelm's task moves are one-way, so `replies
  undo` declines with the reason until peoplelm can reverse a move (awaiting the owner's ruling).
  Enforced by: tests/test_replies.py::test_an_automatic_pick_cannot_be_undone_once_peoplelm_has_it.
- Bounces and complaints block the person's email in peoplelm (do-not-mail) and reject their
  memberships with the reason in the note, when delivery status is polled. (owner)

Terms:
- **double opt-in**: a contact is mailed campaigns only after replying yes to a confirmation email.
- **needs-you queue**: replies the classifier did not settle, waiting for the owner.
"""
