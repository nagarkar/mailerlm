"""The tables the delivery area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  send:     (1, 1, 'october-update', 'live', 'done', 'An update on The Book', 'a3f9...', 10,
             '2026-10-11T15:00:00', '2026-10-11T15:00:02', '2026-10-11T15:00:09')
  delivery: (1, 1, 7, 'ada@example.com', 'delivered', 're_123...', NULL, '2026-10-11T15:01:00')
A deleted campaign leaves its sends (campaign_id becomes NULL, campaign_name stays).
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
