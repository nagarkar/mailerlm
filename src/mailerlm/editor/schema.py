"""The tables the template editor owns, as one migration (applied by `mailerlm.store`).

Example rows:
  template_draft:   (3, '<html>...', 'text frame', '2026-10-13T14:00:00')   -- one per template, or none
  template_message: (1, 3, 'user', 'ok', 'Make the footer 13px', NULL, NULL, NULL, NULL, '...', '2026-10-13T14:00:00')
                    (2, 3, 'assistant', 'ok', 'Footer text is now 13px.', 'gemini/gemini-3.6-flash', 'a1b2...', 1710, 905, 0.0078, ...)
A draft exists only while it differs from the saved template. `status` is ok (a reply applied to the draft),
rejected (the reply failed the guard or was not the expected JSON) or failed (the model call failed).
"""

MIGRATION = """
CREATE TABLE template_draft (
    template_id INTEGER PRIMARY KEY REFERENCES template(id) ON DELETE CASCADE,
    html TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE template_message (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES template(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    status TEXT NOT NULL CHECK (status IN ('ok', 'rejected', 'failed')),
    content TEXT NOT NULL,
    model TEXT,
    prompt_sha256 TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    created_at TEXT NOT NULL
) STRICT
"""
