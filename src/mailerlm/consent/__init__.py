"""Consent: the state machine that decides whether a membership may be mailed.

Statuses: `imported` -> `pending` (confirmation sent) -> `subscribed` (confirmed) ->
`unsubscribed`; `suppressed` (bounce or complaint) is terminal. Only `subscribed` is mailed.

Never imports: delivery, inbox, linkedin (enforced by the import contract "domain below
channels" in pyproject.toml).

Decisions:
- Everyone, including the first imported contacts, confirms by double opt-in before any campaign reaches them:
  a confirmation letter moves `imported` to `pending`, and their reply moves `pending` to `subscribed`. (owner)
- Confirmation and unsubscribe happen by reply, not by a public link: no public endpoint. Every campaign carries a
  `List-Unsubscribe: mailto:` header and an opt-out line in its footer. The line's wording is the owner's setting
  (Settings, overridable per campaign); it must still tell the reader they can stop the emails by replying. (owner)
- A list can hide the footer (the opt-out line and the postal address); it is shown by default. Each reader's
  letter follows the list they are reached through (see `mailerlm.campaigns`): a reader reached through a list that
  hides it gets no footer. The `List-Unsubscribe: mailto:` header stays on every letter, so mail clients still
  offer an opt-out. In the owner's words: "If someone hasn't subscribed to a mailing list, I don't want them to
  have to see that thing because it's very impersonal. So for some mailing lists, we should have a configuration
  option that says show footer or hide footer. And the show hide option should be defaulted to show ... and hide
  should be the override". Trade-off: for mail that promotes the book, US law (CAN-SPAM) expects a visible opt-out
  and a postal address; hiding them is the owner's choice per list. (owner)
- Jev reads each reply; an unsubscribe applies at probability 0.80 or higher, a confirmation at 0.95 or higher and
  only for someone with a pending membership; every other option, and anything below its bar, waits for the owner
  (see `mailerlm.inbox`). A wrong yes mails someone who did not consent, so its bar is higher. (owner)
- Every transition returns what it changed, so an automatic decision can be undone exactly. (owner)
- Bounces and complaints block the person (do-not-mail) when delivery status is polled. (owner)

Terms:
- **double opt-in**: a contact is mailed campaigns only after replying yes to a confirmation email.
- **needs-you queue**: replies the classifier did not settle, waiting for the owner.
"""
