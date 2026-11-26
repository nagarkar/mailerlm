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
- `mailerlm lists test <name> [--clear] [--dry-run]`: marks a list as a test list (`lists show` prints `[test list]`). A reader reached through a test list gets `Testing: ` before the subject, added at render time; the campaign's stored subject is never edited. A test list changes nothing about consent or about who may send.
- `mailerlm lists confirm <list> --reason R [--dry-run]`, `mailerlm contacts confirm <contact> <list> --reason R [--dry-run]`, and `--confirm-reason R` on `contacts add|import --list L`: the owner confirms memberships without a confirmation letter (imported or pending -> subscribed) with a required reason ("known personally", "confirmed by email", "confirmed in person"); per list only; never for do-not-mail, unsubscribed or suppressed people (listed with why). An agent runs these only when the owner asks, dry run first. `mailerlm contacts history <contact>` shows every consent change and how it happened; `contacts find` shows `confirmed={list: 'by owner: <reason>' | 'by reply'}`.
- `mailerlm lists footer <name> --hide|--show [--dry-run]`: hides or shows the footer (opt-out line and postal address) on letters to a list's readers; shown by default, `lists show` prints `[footer hidden]`. The `List-Unsubscribe: mailto:` header stays. Agents do not change this setting; it is the owner's (see `mailerlm.consent`).
- `mailerlm contacts import <file|-> [--list L] [--tag T] [--dry-run]`: pasted text ('Name: email', 'Name <email>', LinkedIn URLs) or a CSV with an email header (optional `name`, `preferred_name`, `tags` columns); re-import adds nothing and never overwrites a preferred name.
- `mailerlm contacts add --email E [--name N] [--preferred-name P] [--linkedin-url U] [--list L]`: one contact.
- `mailerlm contacts edit <contact> [--name N] [--preferred-name P] [--email E] [--linkedin-url U] [--dry-run]`: changes only the fields given. The preferred name is what a letter's greeting `{{ preferred_name }}` says (else the first word of the name, else "there"); `--preferred-name ""` clears it. `{{ first_name }}` stays the first word of the official name. `contacts find` prints `(preferred 'P')` after the name, and each contact's `#id`.
- `mailerlm contacts tag <email> <tag>` and `mailerlm contacts untag <email> <tag>`.
- `mailerlm contacts join <email> <list>` and `mailerlm contacts leave <email> <list>`: a joined contact starts `imported` and is not mailed.
- `mailerlm contacts do-not-mail <email> [--clear]`: the global block.
- Every mutating verb takes `--dry-run`.
- `mailerlm contacts find [--list L] [--tag T] [--status S]`: addresses are masked; `--show-full-addresses` is for the owner's own terminal, agents never use it.
- `mailerlm campaigns new <name> [--starter confirmation|update|early-access|launch] [--dry-run]`: creates a campaign in the database from a starter; never overwrites.
- `mailerlm campaigns list`, `mailerlm campaigns check <name>`, `mailerlm campaigns preview <name> [--list L] [--first-name F] [--preferred-name P]` (P defaults to F): check renders and counts who it would reach, per list; preview renders as a reader of one list (default: the campaign's first); neither sends.
- `mailerlm sends plan <name> [--list L ...]` and `mailerlm sends list`: read-only; show who a real send to those lists (default: the campaign's) would reach, per list (recipients, footer, `Testing: `, sender, skipped), any person who would be mailed through two chosen lists (a problem that stops the send; the owner takes them off a list or drops a list), overlaps that would not be mailed twice (notes), and recent results.
- `mailerlm sends test <name> [--list L]`: mails the campaign once to the owner's own reply-to address (an agent may run this only when the owner asks).
- `mailerlm sends start <name> [--list L ...] --fingerprint F`: the real send, to the same lists the plan used, the owner's act. Agents never run it (the repo's permission settings deny it); `mailerlm sends resume <send>` and `mailerlm sends poll [--send N]` finish and follow a send.
- `mailerlm drafts make <campaign> [--dry-run]`, `mailerlm drafts list <campaign>`, `mailerlm drafts show <campaign> <contact>`: one editable draft of a campaign per person, their values filled in. Remaking adds drafts for people who joined, follows the campaign in unedited drafts, keeps edited drafts, removes unedited drafts of people who left (an edited one is kept and shown as unreachable). The owner edits and sends them on the Drafts page (`#/campaigns/<campaign>/drafts`): Send one, or Send all remaining; a sent draft is locked. Agents run make, list and show only; there is no CLI send.
- Once per person: a person gets a campaign at most once, whichever path sends it. The whole-list send (`sends plan|start`) leaves out everyone a live send of the campaign already reached (draft sends included; only `failed` messages may go again), and a draft cannot be sent to anyone already reached. An edited draft not yet sent stops the whole-list send until it is sent or deleted.
- `mailerlm replies check`, `mailerlm replies list`, `mailerlm replies decide <reply> <decision>`, `mailerlm replies undo <reply>`: read replies from people already mailed, classify them with Jev, and settle the queue. Reply text is the owner's data: never print it in chat.
- `mailerlm serve [--port N]`: runs the web app (the launch agent runs it; the owner uses the page to edit campaigns, templates, settings and people).
- `mailerlm help [verb]`, `mailerlm completion bash`.

Rules that already hold:

- Contact data (emails, replies, the footer address) lives only in the private workspace; never print it in chat, issues, PRs or commits.
- A live send is the owner's act (Send on the review page, or `mailerlm sends start <campaign> --fingerprint <fingerprint>` in the owner's own terminal, with the fingerprint from `mailerlm sends plan <campaign>`). Agents run `sends plan` (it sends nothing) and `sends test` to the owner's own address only.
- Consent changes come from replies or the owner; an agent never edits a contact's consent by hand.

## Merging

- Standing authorization (owner, 2026-10-10): this repo has one owner, so the agent merges its own PR once the pre-commit hooks and the test suite pass. It still waits for the owner's word on a PR that changes a rule marked `(owner)` or a metalm guideline, and on one whose first run spends money or migrates live data.
- Revisit trigger: when this repo gets a second contributor, a second user, or runs as a shared service, stop merging on this authorization and ask the owner to update this section.
