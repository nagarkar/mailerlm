# mailerlm

@~/.claude/metalm/guidelines/index.md

Mailing-list and campaign tool for one author: contacts and lists with double opt-in by reply, Markdown campaigns (stored in the database, edited in a web app on the tailnet) sent through Resend, replies read from Gmail and classified by Jev (TypeSafe), and an assisted LinkedIn tracker. Public code; all data stays in the private workspace. Depends on `corpuslm` (shared CLI helpers, pinned git URL in `pyproject.toml`), Resend, Gmail (readonly) and TypeSafe; reference patterns come from `ytlm` (TypeSafe adapter) and `supplylm` (Gmail).

## Commands

- Install: `python3.12 -m venv .venv && .venv/bin/pip install -e ../llmlm -e ".[dev]" && .venv/bin/pre-commit install`
- Test: `.venv/bin/pytest`
- Lint: `.venv/bin/ruff check src tests`; format `.venv/bin/ruff format src tests`; types `.venv/bin/pyright`
- Web app: `.venv/bin/mailerlm serve` (real server on 127.0.0.1:8782 under a launch agent, `scripts/com.nagarkar.mailerlm.serve.plist`, proposed tailnet port 13443); throwaway copy: `scripts/sandbox_server.sh [port] [--fresh]`.
- Scheduled reply check: `scripts/replies-check.sh` under the launch agent `scripts/com.nagarkar.mailerlm.replies.plist` (every 10 minutes; install commands in its header); test with `.venv/bin/pytest tests/test_replies_script.py`.
- CLI: `.venv/bin/mailerlm <verb>` (`setup`, `doctor`, `gmail connect`, `lists`, `contacts`, `campaigns`, `sends`, `replies`, `serve`, `help`, `completion`); see `.claude/skills/mailerlm/SKILL.md`.

## Exceptions to metalm

- A picture upload is a raw `PUT` of up to 8 MB, not the 64 KB JSON limit: the `(owner)` rule in `src/mailerlm/server.py`.
- Campaigns, templates and sender defaults are database rows edited in the web app, not checked-in config: the `(owner)` rule in `src/mailerlm/campaigns/__init__.py`.

## Agent skills

- Issue tracker: GitHub issues on `nagarkar/mailerlm`. See `docs/agents/issue-tracker.md`.
- Triage labels: defaults. See `docs/agents/triage-labels.md`.
