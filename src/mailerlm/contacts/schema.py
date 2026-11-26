"""The tables the contacts area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  contact:      (1, 'ada@example.com', 'Ada', NULL, 0, '2026-10-10T20:00:00', NULL)
                (2, NULL, 'Grace', 'https://www.linkedin.com/in/grace-example', 0, '...', NULL)
                (3, 'g@example.com', 'Grace Hopper', NULL, 0, '...', 'Hopper')   preferred_name set
  mailing_list: (1, 'book-interest', NULL, NULL, '2026-10-10T20:00:00', 0, 1)
                (2, 'Testing', NULL, NULL, '2026-11-25T09:00:00', 1, 1)   is_test = 1: a test list
                (3, 'friends', NULL, NULL, '2026-11-25T09:00:00', 0, 0)   show_footer = 0: no footer
  membership:   (1, 1, 'imported', '2026-10-10T20:00:00', NULL, NULL)
                (2, 3, 'subscribed', '2026-11-25T12:00:00', 'owner', 'known personally')
  membership_event: (1, 2, 3, 'friends', 'imported', 'subscribed', 'confirmed by owner: known personally', '...')
  tag:          (1, 'early-access')
"""

MIGRATION = """
CREATE TABLE contact (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT UNIQUE,
    do_not_mail INTEGER NOT NULL DEFAULT 0 CHECK (do_not_mail IN (0, 1)),
    created_at TEXT NOT NULL,
    CHECK (email IS NOT NULL OR linkedin_url IS NOT NULL)
) STRICT;
CREATE TABLE mailing_list (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sender_name TEXT,
    sender_address TEXT,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE membership (
    contact_id INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    list_id INTEGER NOT NULL REFERENCES mailing_list(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN
        ('imported', 'pending', 'subscribed', 'unsubscribed', 'suppressed')),
    status_changed_at TEXT NOT NULL,
    PRIMARY KEY (contact_id, list_id)
) STRICT;
CREATE TABLE tag (
    contact_id INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    PRIMARY KEY (contact_id, name)
) STRICT
"""

# A test list: every send whose audience is only test lists carries "Testing: " before its subject.
TEST_LIST_MIGRATION = "ALTER TABLE mailing_list ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))"
FOOTER_MIGRATION = "ALTER TABLE mailing_list ADD COLUMN show_footer INTEGER NOT NULL DEFAULT 1 CHECK (show_footer IN (0, 1))"

# Owner-attested confirmation and the consent history. `confirmed_by` says how a `subscribed`
# membership was confirmed: 'reply' (they replied yes) or 'owner' (the owner vouched, with
# `confirm_reason`, e.g. 'known personally'); read only while the status is `subscribed` (kept
# otherwise, so an undone unsubscribe gets it back). `membership_event` keeps
# every status change of a membership with how it happened.
CONFIRMATION_MIGRATION = """
ALTER TABLE membership ADD COLUMN confirmed_by TEXT CHECK (confirmed_by IN ('reply', 'owner'));
ALTER TABLE membership ADD COLUMN confirm_reason TEXT;
CREATE TABLE membership_event (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    list_id INTEGER REFERENCES mailing_list(id) ON DELETE SET NULL,
    list_name TEXT NOT NULL,
    previous TEXT NOT NULL,
    new TEXT NOT NULL,
    how TEXT NOT NULL,
    at TEXT NOT NULL
) STRICT
"""

# The name a person likes to be called by (`{{ preferred_name }}`), apart from the official name;
# NULL means "the first word of `name`". See `mailerlm.contacts`.
PREFERRED_NAME_MIGRATION = "ALTER TABLE contact ADD COLUMN preferred_name TEXT"
