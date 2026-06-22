#!/usr/bin/env bash
# Fail if obvious secrets appear in tracked/staged files.
# Used both as a git pre-commit hook (scans staged) and in CI (scans tree).
set -euo pipefail

MODE="${1:-tree}"   # "staged" or "tree"

if [[ "$MODE" == "staged" ]]; then
  FILES=$(git diff --cached --name-only --diff-filter=ACM)
else
  FILES=$(git ls-files)
fi

# Never scan the example file (it intentionally contains placeholders) or this script.
FILES=$(echo "$FILES" | grep -vE '(^|/)(\.env\.example|scripts/check_secrets\.sh)$' || true)

if [[ -z "${FILES}" ]]; then
  echo "secret-scan: no files to scan"
  exit 0
fi

# Patterns for obvious, high-signal secrets. Tuned to avoid false positives.
PATTERNS=(
  'AKIA[0-9A-Z]{16}'                                  # AWS access key id
  '-----BEGIN[A-Z ]*PRIVATE KEY-----'                 # private keys
  'sk-[A-Za-z0-9]{20,}'                               # OpenAI-style secret keys
  'xox[baprs]-[A-Za-z0-9-]{10,}'                      # Slack tokens
  'ghp_[A-Za-z0-9]{36}'                              # GitHub PAT
  'gh[oprs]_[A-Za-z0-9]{36}'                         # other GitHub tokens
  '[Pp][Vv][Ee][A-Za-z0-9]*[Tt]oken[A-Za-z0-9_]*=[0-9a-f-]{20,}'  # PVE token-ish
)

FAIL=0
for f in $FILES; do
  [[ -f "$f" ]] || continue
  # skip binaries
  if file "$f" | grep -qiE 'binary|image|compiled'; then continue; fi
  for p in "${PATTERNS[@]}"; do
    if grep -nIEq -e "$p" -- "$f"; then
      echo "secret-scan: POSSIBLE SECRET in $f (pattern: $p)"
      grep -nIE -e "$p" -- "$f" | sed 's/^/    /'
      FAIL=1
    fi
  done
  # flag a populated .env that's about to be committed
  if [[ "$(basename "$f")" == ".env" ]]; then
    echo "secret-scan: refusing to allow a committed .env file: $f"
    FAIL=1
  fi
done

if [[ "$FAIL" -ne 0 ]]; then
  echo "secret-scan: FAILED — remove secrets before committing."
  exit 1
fi
echo "secret-scan: clean"
