#!/usr/bin/env bash
# Runs the CI checks: install, pre-commit hooks, tests with the coverage gate.
#
# The runner's logs cannot be read through the Gitea API, so when a check fails the
# tail of its output is posted as a comment on the pull request.
set -uo pipefail

LOG="$(mktemp)"

{
  { pip install --user uv || pip install --user --break-system-packages uv; } &&
    export PATH="$HOME/.local/bin:$PATH" &&
    uv --version &&
    uv sync --frozen --python 3.14 &&
    SKIP=no-commit-to-branch uv run pre-commit run --all-files --show-diff-on-failure &&
    uv run pytest
} 2>&1 | tee "$LOG"
status="${PIPESTATUS[0]}"

DIAG="$(mktemp)"
if [ "$status" -ne 0 ]; then
  {
    echo "--- where the tests ran"
    pwd
    id -un
    git log -1 --format='%H %s' 2>&1 | head -1
    git status --short 2>&1 | head -10
    ls -la tests/snapshots 2>&1 | head -10
    "$HOME/.local/bin/uv" run python - <<'PY' 2>&1
import importlib.metadata as m, sys
print(sys.version.split()[0])
for name in ("syrupy", "pytest", "pytest-homeassistant-custom-component", "homeassistant"):
    print(name, m.version(name))
PY
    env | grep -E '^(LANG|LC_ALL|TZ|PYTHON[A-Z_]*|CI|GITEA_ACTIONS)=' | sort
  } > "$DIAG" 2>&1
  cat "$DIAG" >> "$LOG"
fi

if [ "$status" -ne 0 ] && [ -n "${PR_NUMBER:-}" ] && [ -n "${GITEA_TOKEN:-}" ] && [ -n "${API_URL:-}" ]; then
  python3 - "$LOG" "$status" <<'PY' |
import json
import sys

log, status = sys.argv[1], sys.argv[2]
with open(log, encoding="utf-8", errors="replace") as handle:
    tail = handle.read()[-3500:]
print(json.dumps({"body": f"CI failed with exit code {status}. End of the output:\n\n```\n{tail}\n```"}))
PY
    curl -sS -X POST -H "Authorization: token ${GITEA_TOKEN}" -H "Content-Type: application/json" \
      -d @- "${API_URL}/issues/${PR_NUMBER}/comments" >/dev/null || true
fi

if [ -s "$DIAG" ] && [ -n "${PR_NUMBER:-}" ] && [ -n "${GITEA_TOKEN:-}" ] && [ -n "${API_URL:-}" ]; then
  python3 - "$DIAG" <<'PY' |
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()[-3500:]
print(json.dumps({"body": "Where the failing run happened:\n\n```\n" + text + "\n```"}))
PY
    curl -sS -X POST -H "Authorization: token ${GITEA_TOKEN}" -H "Content-Type: application/json" \
      -d @- "${API_URL}/issues/${PR_NUMBER}/comments" >/dev/null || true
fi

exit "$status"
