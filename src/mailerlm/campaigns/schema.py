"""The tables the campaigns area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  setting:        ('sender_name', 'Ada Example')       -- account-wide defaults
  template:       (1, 'Plain letter', '<html>...', 'text frame', 1, '2026-10-11T12:00:00')
  campaign:       (1, 'october-update', 'campaign', 'An update on {{ book_title }}', ...,
                   '{"book_title": "The Book"}', ...)  -- sender fields NULL = use the defaults
  campaign_list:  (1, 1)      campaign_tag: (1, 'early-access')
  asset:          ('9f2c...', 'image/png', 48211, <bytes>, '2026-10-11T12:00:00')
  campaign_image: (1, 'cover.png', '9f2c...')   -- the name the Markdown uses
"""

MIGRATION = """
CREATE TABLE setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE template (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    html TEXT NOT NULL,
    text TEXT NOT NULL,
    built_in INTEGER NOT NULL DEFAULT 0 CHECK (built_in IN (0, 1)),
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE campaign (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'campaign' CHECK (kind IN ('campaign', 'confirmation')),
    subject TEXT NOT NULL DEFAULT '',
    preview_text TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    template_id INTEGER REFERENCES template(id),
    sender_name TEXT,
    sender_address TEXT,
    reply_to TEXT,
    postal_address TEXT,
    track_clicks INTEGER NOT NULL DEFAULT 0 CHECK (track_clicks IN (0, 1)),
    track_opens INTEGER NOT NULL DEFAULT 0 CHECK (track_opens IN (0, 1)),
    variables TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE campaign_list (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    list_id INTEGER NOT NULL REFERENCES mailing_list(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, list_id)
) STRICT;
CREATE TABLE campaign_tag (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    PRIMARY KEY (campaign_id, name)
) STRICT;
CREATE TABLE asset (
    sha256 TEXT PRIMARY KEY,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE campaign_image (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL REFERENCES asset(sha256),
    PRIMARY KEY (campaign_id, filename)
) STRICT
"""
