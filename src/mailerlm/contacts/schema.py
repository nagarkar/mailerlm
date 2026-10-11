"""The tables the contacts area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  contact:      (1, 'ada@example.com', 'Ada', NULL, 0, '2026-10-10T20:00:00')
                (2, NULL, 'Grace', 'https://www.linkedin.com/in/grace-example', 0, '...')
  mailing_list: (1, 'book-interest', NULL, NULL, '2026-10-10T20:00:00')
  membership:   (1, 1, 'imported', '2026-10-10T20:00:00')
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
