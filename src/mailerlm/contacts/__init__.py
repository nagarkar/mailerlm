"""Contacts: people, the lists they belong to, and their tags.

Owns the `contact`, `mailing_list`, `membership` and `tag` tables (`schema.py`; example rows there). Since the
move to peoplelm (owner, 2026-10-25) a contact row is mailerlm's handle on a peoplelm person (`person_id`)
and carries the tags; who the person is, which lists they are on, their consent and do-not-mail live in
peoplelm (`mailerlm.people`). The `membership` table and the contact's email, name and do_not_mail
columns are history kept for old deliveries and are never read for a decision.

Never imports: consent, campaigns, delivery, inbox, linkedin (enforced by the import contract
"contacts at the bottom" in pyproject.toml); it reaches peoplelm through `mailerlm.people`.

Decisions:
- A contact is one person with an email, a LinkedIn URL, tags and a membership in any number
  of lists; the visual list editor edits memberships behind an Edit toggle. (owner)
- Imported contacts arrive with membership status `imported` (a proposed peoplelm task), which no
  campaign mails. (owner)
- Adding or importing a contact resolves the person in peoplelm, and joining or leaving a list is a
  peoplelm task move; a dry run only reads peoplelm. Enforced by:
  tests/test_people.py::test_a_dry_run_writes_nothing_to_peoplelm. (owner)
- The cutover: `mailerlm people link` stores the person id for each contact from the `people` map of
  `peoplelm import mailerlm <store> --json`; a contact never linked is never mailed. Enforced by:
  tests/test_people.py::test_the_link_stores_each_contacts_person_and_a_second_run_changes_nothing,
  tests/test_people.py::test_an_unlinked_contact_is_never_mailed. (owner)
- Deleting a list removes it here only; its peoplelm tasks stay as history. Deleting a contact takes
  them off every list in peoplelm and removes the row here; the person stays in peoplelm.
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
- **membership**: a contact's place on one list, with its consent status: a peoplelm `mailing-list` task.
- **tag**: a free label on a contact, such as `early-access`, used to narrow a campaign's audience.
"""


def mask_email(email: str | None) -> str:
    """`ada@example.com` -> `a***@e***.com`; a contact without an email shows `(no email)`."""
    if not email or "@" not in email:
        return "(no email)"
    local, domain = email.split("@", 1)
    host, _, suffix = domain.rpartition(".")
    return f"{local[:1]}***@{host[:1]}***.{suffix}"
