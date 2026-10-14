"""The table the inbox area owns, as one migration (applied by `mailerlm.store`).

Example rows:
  reply: (1, '18f3a...', '18f3a...', 7, 'ada@example.com', 'Re: Do you still want to hear about the book?',
          '2026-10-13T15:00:00', 'Yes please!', 'Do you still want ... reply yes', 'confirm_subscription', 0.99, 0.98,
          'confirmed', 'jev', '[{"list_id": 1, "previous": "pending", "new": "subscribed"}]', '2026-10-13T15:05:00')
`decision` is confirmed, unsubscribed, queued (waiting for the owner) or ignored; `decided_by` is jev, owner or rule.
`changes` records each membership the decision moved, so it can be undone.
"""

MIGRATION = """
CREATE TABLE reply (
    id INTEGER PRIMARY KEY,
    gmail_id TEXT NOT NULL UNIQUE,
    thread_id TEXT NOT NULL,
    contact_id INTEGER REFERENCES contact(id) ON DELETE SET NULL,
    from_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    body TEXT NOT NULL,
    our_excerpt TEXT NOT NULL DEFAULT '',
    choice TEXT,
    probability REAL,
    confidence REAL,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'unsubscribed', 'queued', 'ignored')),
    decided_by TEXT NOT NULL CHECK (decided_by IN ('jev', 'owner', 'rule')),
    changes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
) STRICT
"""

# One row per reply check (a scheduled run or a button press), so the page can say when it last worked.
RUN_MIGRATION = """
CREATE TABLE check_run (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    new_replies INTEGER NOT NULL DEFAULT 0,
    waiting INTEGER NOT NULL DEFAULT 0,
    error TEXT
) STRICT
"""
