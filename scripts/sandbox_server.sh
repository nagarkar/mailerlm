#!/usr/bin/env bash
# A throwaway mailerlm web server on a spare port, with its own copy of the data (metalm
# operations.md, "Sandbox script"). Declines the real port and any workspace inside the real one.
#   scripts/sandbox_server.sh [port] [--fresh]
# --fresh starts from an empty store instead of a copy of the real one. Credentials that act outside
# this Mac (Resend, TypeSafe, Gmail) are removed from the environment, so nothing tapped here reaches anywhere;
# model keys are kept so the template editor can be tried (each edit costs about a cent).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="${1:-8790}"; fresh=""
[ "${2:-}" = "--fresh" ] && fresh=1
[ "$port" = "8782" ] && { echo "declined: 8782 is the real server's port" >&2; exit 1; }
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/mailerlm-sandbox.XXXXXX")"
real="$(cd "$here" && .venv/bin/python -c 'from mailerlm import paths; paths.load_env(); print(paths.workspace_path())')"
case "$sandbox" in "$real"*) echo "declined: the sandbox must be outside the real workspace" >&2; exit 1;; esac
mkdir -p "$sandbox/ws" && : > "$sandbox/.env"
env -u RESEND_API_KEY -u TYPESAFE_API_KEY -u GMAIL_CLIENT_ID -u GMAIL_CLIENT_SECRET \
  MAILERLM_ENV="$sandbox/.env" MAILERLM_WORKSPACE="$sandbox/ws" "$here/.venv/bin/mailerlm" setup >/dev/null
if [ -z "$fresh" ] && [ -f "$real/mailerlm.sqlite" ]; then
  rm -f "$sandbox/ws/mailerlm.sqlite"
  sqlite3 "$real/mailerlm.sqlite" ".backup '$sandbox/ws/mailerlm.sqlite'"
fi
# Model-vendor keys stay (they only spend a little on template drafts); read from the real .env, never printed.
for key in ANTHROPIC_API_KEY GEMINI_API_KEY; do
  value="$(grep -E "^$key=" "$here/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
  [ -n "$value" ] && export "$key=$value"
done
echo "sandbox workspace: $sandbox/ws"
echo "sandbox server: http://127.0.0.1:$port/ (Ctrl-C to stop; data is thrown away)"
exec env -u RESEND_API_KEY -u TYPESAFE_API_KEY -u GMAIL_CLIENT_ID -u GMAIL_CLIENT_SECRET \
  MAILERLM_ENV="$sandbox/.env" MAILERLM_WORKSPACE="$sandbox/ws" "$here/.venv/bin/mailerlm" serve --port "$port"
