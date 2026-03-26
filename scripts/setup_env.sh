#!/bin/bash
# DriveDesk .env setup script
# Restores .env files from Bitwarden (AIG-Secrets vault)
# Usage: ./scripts/setup_env.sh

set -euo pipefail

echo "=== DriveDesk Environment Setup ==="
echo "Authenticating with Bitwarden..."

# Check bw is installed
if ! command -v bw &> /dev/null; then
    echo "Error: bitwarden-cli not installed. Run: brew install bitwarden-cli"
    exit 1
fi

# Login check
bw status | grep -q '"authenticated"' || {
    echo "Please login first: bw login"
    exit 1
}

# Unlock
export BW_SESSION=$(bw unlock --raw)
if [ -z "$BW_SESSION" ]; then
    echo "Error: Failed to unlock vault"
    exit 1
fi

echo "Vault unlocked."

# Restore DriveDesk karas .env
KARAS_DIR="$(dirname "$0")/../clients/karas"
mkdir -p "$KARAS_DIR"
echo "Restoring clients/karas/.env..."
bw get notes drivedesk-karas-env --session "$BW_SESSION" > "$KARAS_DIR/.env"
echo "  ✅ clients/karas/.env restored"

# Restore ClientDesk .env (if client-desk repo exists alongside)
CLIENTDESK_DIR="$(dirname "$0")/../../client-desk"
if [ -d "$CLIENTDESK_DIR" ]; then
    echo "Restoring client-desk .env..."
    bw get notes clientdesk-karas-env --session "$BW_SESSION" > "$CLIENTDESK_DIR/.env"
    echo "  ✅ client-desk/.env restored"
fi

echo ""
echo "=== Setup complete ==="
echo "Don't forget to also restore token files:"
echo "  bw get notes freee-token-json > ~/.config/drivedesk/freee_token.json"
echo "  bw get notes gogcli-credentials-json > ~/Library/Application Support/gogcli/credentials.json"