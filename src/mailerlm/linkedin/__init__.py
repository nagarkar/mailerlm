"""LinkedIn: an assisted, manual tracker that moves people from LinkedIn onto the email list.

Owns the LinkedIn status per contact, the message drafts, and the import of LinkedIn's own
data export (`Connections.csv`, downloaded by the owner).

Never imports: delivery, inbox (enforced by the import contract "channels apart" in
pyproject.toml).

Decisions:
- Nothing in this repo touches LinkedIn automatically: no scraping, no browser automation, no
  API messaging. The owner pastes and sends every message. Why: LinkedIn's terms ban
  automation and restrict accounts that use it. (owner)
- Statuses: `interested`, `asked-for-email`, `emailed`, `confirmed`; a contact who gives an
  address enters the double opt-in flow. (owner)

Terms:
- **linkedin status**: where a contact stands in the move from a LinkedIn conversation to a confirmed email subscription.
"""
