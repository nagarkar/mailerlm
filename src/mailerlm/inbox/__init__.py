"""Inbox: reading replies to list mail and classifying them.

Owns the Gmail reader port and adapter (scope `gmail.readonly`, its own OAuth client and
token), and the reply classifier (Jev through the TypeSafe adapter).

Never imports: delivery, linkedin (enforced by the import contract "channels apart" in
pyproject.toml).

Decisions:
- Only replies about a letter mailerlm really sent are read: the sender is someone it mailed in a live send and
  the subject names one of those letters (Re:/Fwd: ignored), in the query and again on the fetched message; any
  other mail, including the owner's own sent mail, is never listed, stored or sent to Jev. One readonly query per 20
  addresses; each Gmail message id is read once. Enforced by:
  tests/test_replies.py::test_other_mail_from_the_same_person_is_never_stored_or_sent_to_jev. (owner)
- mailerlm has its own OAuth client and token with `gmail.readonly`; no other repo's token is reused. (owner)
- Jev (TypeSafe's System One model) reads each reply and the letter it answers and picks one of: confirm_subscription,
  unsubscribe, change_email_address, question_or_comment, auto_reply, other. An unsubscribe applies at
  `[classifier].unsubscribe_min_probability` (0.80); a confirmation applies at `confirm_min_probability` (0.95) and
  only for someone with a pending membership; everything else, and anything below its bar, waits in the queue
  with Jev's pick beside it. An automatic reply (by header or sender) is ignored by rule and never sent to Jev. (owner)
- A TypeSafe failure stores nothing, so the reply is read again on the next check. Reply text and Jev's reasoning
  inputs are the owner's data and never enter the chat window. (owner)
- An automatic decision can be undone; the memberships go back as they were and the reply returns to the queue. (owner)

Terms:
- **reply**: a message from someone mailerlm has mailed, stored with the letter it answers and what was decided.
"""
