#!/usr/bin/env bash
# Install the secret-scan pre-commit hook into .git/hooks.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
HOOK="$ROOT/.git/hooks/pre-commit"
cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/scripts/check_secrets.sh" staged
EOF
chmod +x "$HOOK"
echo "Installed pre-commit secret scanner at $HOOK"
