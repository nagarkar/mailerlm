#!/usr/bin/env bash
# The scheduled reply check (metalm scheduling.md, launchd job safeguards). Run by the launch agent
# scripts/com.nagarkar.mailerlm.replies.plist every ten minutes; safe to run by hand.
#  - holds the Mac awake while it runs (re-execs once under caffeinate);
#  - every step is attempted (no `set -e`), logged to its own file, and a failure is alerted ONCE at the end through
#    SuperLM; a clean run is silent, a missing key or token is a logged skip, and a killed run says so;
#  - runs `doctor` first, then `replies check` (reads replies from people already mailed, asks Jev, queues the rest).
# Paths can be replaced for tests: MAILERLM_BIN, SUPERLM_NOTIFY, CAFFEINATE, MAILERLM_LOG_DIR.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="${MAILERLM_BIN:-$here/.venv/bin/mailerlm}"
notify="${SUPERLM_NOTIFY:-$HOME/.local/bin/superlm-notify}"
log_dir="${MAILERLM_LOG_DIR:-$HOME/.mailerlm/logs}"

if [ -z "${MAILERLM_CAFFEINATED:-}" ]; then
  export MAILERLM_CAFFEINATED=1
  exec "${CAFFEINATE:-caffeinate}" -i -s "$0" "$@"
fi

mkdir -p "$log_dir"
log="$log_dir/replies-check-$(date +%Y%m%d-%H%M%S).log"
find "$log_dir" -name 'replies-check-*.log' -mtime +14 -delete 2>/dev/null
failed=()
finished=""

step() {
  local name="$1"; shift
  echo "== $name: $*" >> "$log"
  "$@" >> "$log" 2>&1
  local code=$?
  echo "== $name: exit $code" >> "$log"
  [ "$code" -ne 0 ] && failed+=("$name")
  return 0
}

alert() {
  if [ "${#failed[@]}" -gt 0 ] && [ -x "$notify" ]; then
    "$notify" --repo mailerlm --title "Reply check failed" --body "Failed: ${failed[*]}. Log: $log"
  fi
}

on_exit() {
  if [ -z "$finished" ]; then failed+=("the run itself"); fi
  alert
}
trap on_exit EXIT
trap 'exit 1' TERM INT HUP

step doctor "$bin" doctor
step replies-check "$bin" replies check
finished=1
