"""Contacts: people, the lists they belong to, and their tags.

Owns the `contact`, `mailing_list`, `membership` and `tag` tables (`schema.py`; example rows there). A contact is one person across channels
(an email address and a LinkedIn profile URL); consent and suppression are tracked per
membership, with one global do-not-mail flag per contact.

Never imports: consent, campaigns, delivery, inbox, linkedin (enforced by the import contract
"contacts at the bottom" in pyproject.toml).

Decisions:
- A contact is one person with an email, a LinkedIn URL, tags and a membership in any number
  of lists; the visual list editor edits memberships behind an Edit toggle. (owner)
- Imported contacts arrive with membership status `imported`, which no campaign mails. (owner)
- A list may override the sender name and address; the default sender comes from config. (owner)

- Boards show addresses masked (`a***@e***.com`); `--show-full-addresses` prints them, and is for
  the owner's own terminal. Why: an agent running a verb must not read the list. (owner)
- Every mutating verb takes `--dry-run`, which runs the change on an in-memory copy of the store
  and reports the counts. (owner)
- Importing never overwrites what the owner already has: a match fills a blank name, email or
  LinkedIn URL only, and a re-import adds nothing. (owner)

Terms:
- **contact**: one person, identified by a normalized email address and optionally a LinkedIn profile URL.
- **list**: a named audience a campaign is sent to; a contact may be on several.
- **membership**: a contact's place on one list, with its consent status.
- **tag**: a free label on a contact, such as `early-access`, used to narrow a campaign's audience.
"""


def mask_email(email: str | None) -> str:
    """`ada@example.com` -> `a***@e***.com`; a contact without an email shows `(no email)`."""
    if not email or "@" not in email:
        return "(no email)"
    local, domain = email.split("@", 1)
    host, _, suffix = domain.rpartition(".")
    return f"{local[:1]}***@{host[:1]}***.{suffix}"
