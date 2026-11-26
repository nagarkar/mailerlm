"""The tables the delivery area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  send:     (1, 1, 'october-update', 'live', 'done', 'An update on The Book', 'a3f9...', 10,
             '2026-10-11T15:00:00', '2026-10-11T15:00:02', '2026-10-11T15:00:09')
           ... excerpt, lists = '["friends", "book-interest"]' (the lists chosen for it, in order)
  delivery: (1, 1, 7, 'ada@example.com', 'delivered', 're_123...', NULL, '2026-10-11T15:01:00',
             3, 'friends', 1, 0)   -- reached through list 3 'friends', footer shown, no "Testing: "
A deleted campaign leaves its sends (campaign_id becomes NULL, campaign_name stays); a deleted list
leaves its deliveries (list_id becomes NULL, list_name stays). Rows from before per-list sends have
NULL list columns and `lists = '[]'`.
"""

MIGRATION = """
CREATE TABLE send (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaign(id) ON DELETE SET NULL,
    campaign_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('test', 'live')),
    status TEXT NOT NULL CHECK (status IN ('sending', 'done')),
    subject TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    planned INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
) STRICT;
CREATE TABLE delivery (
    id INTEGER PRIMARY KEY,
    send_id INTEGER NOT NULL REFERENCES send(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contact(id) ON DELETE SET NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'sent', 'failed', 'delivered', 'delivery_delayed', 'bounced', 'complained',
         'opened', 'clicked')),
    provider_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (send_id, email)
) STRICT
"""

# What the letter said, kept so a reply can be read in its thread (the first recipient's plain text, trimmed).
EXCERPT_MIGRATION = "ALTER TABLE send ADD COLUMN excerpt TEXT NOT NULL DEFAULT ''"

# Per-list sends: the lists a send was planned for, and per delivery the list the person was reached
# through and how their letter looked (so a resumed send renders it the same way).
LIST_MIGRATION = """
ALTER TABLE send ADD COLUMN lists TEXT NOT NULL DEFAULT '[]';
ALTER TABLE delivery ADD COLUMN list_id INTEGER REFERENCES mailing_list(id) ON DELETE SET NULL;
ALTER TABLE delivery ADD COLUMN list_name TEXT;
ALTER TABLE delivery ADD COLUMN show_footer INTEGER CHECK (show_footer IN (0, 1));
ALTER TABLE delivery ADD COLUMN test_prefix INTEGER CHECK (test_prefix IN (0, 1))
"""

# Where a send's letters come from: the campaign (one letter rendered per person) or each person's
# draft (`mailerlm.campaigns.drafts`), so a resumed draft send renders the drafts again.
LETTERS_MIGRATION = (
    "ALTER TABLE send ADD COLUMN letters TEXT NOT NULL DEFAULT 'campaign' "
    "CHECK (letters IN ('campaign', 'drafts'))"
)
