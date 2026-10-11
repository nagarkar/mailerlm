"""Contacts: people, the lists they belong to, and their tags.

Owns the `contact`, `list` and `membership` tables. A contact is one person across channels
(an email address and a LinkedIn profile URL); consent and suppression are tracked per
membership, with one global do-not-mail flag per contact.

Never imports: consent, campaigns, delivery, inbox, linkedin (enforced by the import contract
"contacts at the bottom" in pyproject.toml).

Decisions:
- A contact is one person with an email, a LinkedIn URL, tags and a membership in any number
  of lists; the visual list editor edits memberships behind an Edit toggle. (owner)
- Imported contacts arrive with membership status `imported`, which no campaign mails. (owner)
- A list may override the sender name and address; the default sender comes from config. (owner)

Terms:
- **contact**: one person, identified by a normalized email address and optionally a LinkedIn profile URL.
- **list**: a named audience a campaign is sent to; a contact may be on several.
- **membership**: a contact's place on one list, with its consent status.
- **tag**: a free label on a contact, such as `early-access`, used to narrow a campaign's audience.
"""
