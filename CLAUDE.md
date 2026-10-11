# mailerlm

@~/.claude/metalm/guidelines/index.md

Mailing-list and campaign tool for one author: contacts and lists with double opt-in by reply, Markdown campaigns sent through Resend, replies read from Gmail and classified by Jev (TypeSafe), and an assisted LinkedIn tracker. Public code; all data stays in the private workspace. Depends on Resend, Gmail (readonly) and TypeSafe; reference patterns come from `ytlm` (TypeSafe adapter) and `supplylm` (Gmail).

## Commands

- Install: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pre-commit install`
- Test: `.venv/bin/pytest`
- Lint: `.venv/bin/ruff check src tests`; format `.venv/bin/ruff format src tests`; types `.venv/bin/pyright`
- CLI: `.venv/bin/mailerlm <verb>` (not built yet); see `.claude/skills/mailerlm/SKILL.md`.

## Exceptions to metalm

- None.

## Agent skills

- Issue tracker: GitHub issues on `nagarkar/mailerlm`. See `docs/agents/issue-tracker.md`.
- Triage labels: defaults. See `docs/agents/triage-labels.md`.
