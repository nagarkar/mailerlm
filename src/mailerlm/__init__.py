"""mailerlm: a small mailing-list and campaign tool for an author.

Code, prompts and templates live in this public repository; all data (contacts, consent,
replies, campaigns, images, keys, the sender identity and postal address) lives in the private
workspace `~/.mailerlm` (pointer `MAILERLM_WORKSPACE` in the gitignored `.env`) and is never
committed. The store is one SQLite file in that workspace.

Decisions:
- Nothing that identifies the owner enters git: book title, sender domain and address and the
  postal address, the campaigns and the contacts live in the private database, set in the web app.
  Keys live in the gitignored `.env`, `config.toml` holds behaviour only, and the repo ships
  placeholders (`config.example.toml`, `env.example`). Why: the repo is public. (owner)
- A live send is the owner's act, from the review page or `mailerlm send --yes` in the owner's
  terminal; agents only dry-run and test-send to the owner's own address. (owner)
- Campaigns do not track opens or clicks unless the campaign sets the flag; the stats page says
  opens are unreliable. (owner)

Terms:
- **workspace**: the private directory holding the SQLite store (contacts, campaigns, templates, images), logs and token files.
"""
