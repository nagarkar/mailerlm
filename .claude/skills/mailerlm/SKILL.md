---
name: mailerlm
description: House rules for driving mailerlm, the author's mailing-list and campaign tool. Use when importing contacts, writing or previewing a campaign, checking replies, or looking at the review page. Contact data never enters the chat window.
---

# mailerlm

Verbs today (more arrive with the implementation issues; each prints a board, `--json` for JSON):

- `mailerlm setup [--dry-run]`: writes the workspace pointer in the managed block of `.env`, creates the workspace and database.
- `mailerlm doctor`: checks the environment; exits 1 when a required check fails.
- `mailerlm gmail connect`: owner's act; opens a browser for readonly consent and stores the token.
- `mailerlm lists add <name> [--sender-name N --sender-address A] [--dry-run]`, `mailerlm lists show`: lists and their member counts by status.
- `mailerlm contacts import <file|-> [--list L] [--tag T] [--dry-run]`: pasted text ('Name: email', 'Name <email>', LinkedIn URLs) or a CSV with an email header; re-import adds nothing.
- `mailerlm contacts add --email E [--name N] [--linkedin-url U] [--list L]`: one contact.
- `mailerlm contacts tag <email> <tag>` and `mailerlm contacts untag <email> <tag>`.
- `mailerlm contacts join <email> <list>` and `mailerlm contacts leave <email> <list>`: a joined contact starts `imported` and is not mailed.
- `mailerlm contacts do-not-mail <email> [--clear]`: the global block.
- Every mutating verb takes `--dry-run`.
- `mailerlm contacts find [--list L] [--tag T] [--status S]`: addresses are masked; `--show-full-addresses` is for the owner's own terminal, agents never use it.
- `mailerlm help [verb]`, `mailerlm completion bash`.

Rules that already hold:

- Contact data (emails, replies, the footer address) lives only in the private workspace; never print it in chat, issues, PRs or commits.
- A live send is the owner's act (Send on the review page, or `mailerlm send <campaign> --yes` in the owner's own terminal). Agents run `--dry-run` and test sends to the owner's own address only.
- Consent changes come from replies or the owner; an agent never edits a contact's consent by hand.

## Merging

- Standing authorization (owner, 2026-10-10): this repo has one owner, so the agent merges its own PR once the pre-commit hooks and the test suite pass. It still waits for the owner's word on a PR that changes a rule marked `(owner)` or a metalm guideline, and on one whose first run spends money or migrates live data.
- Revisit trigger: when this repo gets a second contributor, a second user, or runs as a shared service, stop merging on this authorization and ask the owner to update this section.
