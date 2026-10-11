---
name: mailerlm
description: House rules for driving mailerlm, the author's mailing-list and campaign tool. Use when importing contacts, writing or previewing a campaign, checking replies, or looking at the review page. Contact data never enters the chat window.
---

# mailerlm

Stub: the CLI does not exist yet (verbs arrive with the implementation issues). Rules that already hold:

- Contact data (emails, replies, the footer address) lives only in the private workspace; never print it in chat, issues, PRs or commits.
- A live send is the owner's act (Send on the review page, or `mailerlm send <campaign> --yes` in the owner's own terminal). Agents run `--dry-run` and test sends to the owner's own address only.
- Consent changes come from replies or the owner; an agent never edits a contact's consent by hand.

## Merging

- Merge only on the owner's word.
