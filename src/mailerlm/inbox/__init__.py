"""Inbox: reading replies to list mail and classifying them.

Owns the Gmail reader port and adapter (scope `gmail.readonly`, its own OAuth client and
token), and the reply classifier (Jev through the TypeSafe adapter).

Never imports: delivery, linkedin (enforced by the import contract "channels apart" in
pyproject.toml).

Decisions:
- Replies arrive at a plus-address of the owner's Gmail; only mail to that address is
  queried, and handled message ids are remembered in the store. (owner)
- mailerlm has its own OAuth client and token with `gmail.readonly`; no other repo's token
  is reused. (owner)
- The classifier's options are `confirm_subscription`, `unsubscribe`, `change_email_address`,
  `question_or_comment`, `auto_reply`, `other`; its state is the thread, the list and the
  sender's current status. Requests are cached by payload and never reach the network in tests. (owner)

Terms:
- **reply**: an inbound message to the list's reply address, with its thread.
"""
